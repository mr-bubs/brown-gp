import json
import os
import re
import sys
import threading
import time
import urllib.parse
from collections import deque
from datetime import datetime, timezone

import requests
from signalrcore.hub_connection_builder import HubConnectionBuilder


TOKEN_FILE = os.path.expanduser("~/.config/brown-gp/f1-token")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CAPTURE_DIR = os.path.join(SCRIPT_DIR, "f1-captures")
RECONNECT_DELAY = 5
FSYNC_INTERVAL = 2.0
PRE_ROLL_SECONDS = 30
TOKEN_CHECK_INTERVAL = 5

NEGOTIATE_URL = "https://livetiming.formula1.com/signalrcore/negotiate"
WS_URL = "wss://livetiming.formula1.com/signalrcore"

TOPICS = [
    "Heartbeat",
    "CarData.z",
    "Position.z",
    "ExtrapolatedClock",
    "TopThree",
    "RcmSeries",
    "TimingStats",
    "TimingAppData",
    "WeatherData",
    "TrackStatus",
    "DriverList",
    "RaceControlMessages",
    "SessionInfo",
    "SessionData",
    "LapCount",
    "TimingData",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0 Safari/537.36"
    ),
    "Origin": "https://www.formula1.com",
    "Referer": "https://www.formula1.com/",
}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def slugify(value):
    value = str(value or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def build_final_capture_path(session_info):
    if not isinstance(session_info, dict):
        return None

    meeting = session_info.get("Meeting")
    if not isinstance(meeting, dict):
        meeting = {}

    event_name = (
        meeting.get("Name")
        or meeting.get("Location")
        or (meeting.get("Circuit") or {}).get("ShortName")
    )
    session_name = session_info.get("Name") or session_info.get("Type")
    start_date = session_info.get("StartDate")

    event_slug = slugify(event_name)
    session_slug = slugify(session_name)

    try:
        date_slug = datetime.fromisoformat(
            str(start_date).replace("Z", "+00:00")
        ).date().isoformat()
    except Exception:
        date_slug = datetime.now().date().isoformat()

    if not event_slug:
        return None

    parts = ["capture", event_slug]
    if session_slug:
        parts.append(session_slug)
    parts.append(date_slug)

    base = os.path.join(CAPTURE_DIR, "-".join(parts) + ".jsonl")
    if not os.path.exists(base):
        return base

    stem, ext = os.path.splitext(base)
    suffix = 2
    while os.path.exists(f"{stem}-{suffix}{ext}"):
        suffix += 1
    return f"{stem}-{suffix}{ext}"


def token_signature():
    stat = os.stat(TOKEN_FILE)
    return (stat.st_mtime_ns, stat.st_size)


def load_access_token():
    if not os.path.exists(TOKEN_FILE):
        raise RuntimeError(f"Token file not found: {TOKEN_FILE}")

    with open(TOKEN_FILE, "r", encoding="utf-8") as f:
        raw = f.read().strip()

    if not raw:
        raise RuntimeError("Token file is empty")

    if raw.startswith("eyJ"):
        return raw

    try:
        decoded = urllib.parse.unquote(raw)
        obj = json.loads(decoded)
    except Exception as exc:
        raise RuntimeError(
            "Could not decode login-session cookie as JSON"
        ) from exc

    candidates = []
    if isinstance(obj, dict):
        candidates.extend([
            obj.get("subscriptionToken"),
            obj.get("accessToken"),
            obj.get("activationToken"),
            obj.get("token"),
        ])
        data = obj.get("data")
        if isinstance(data, dict):
            candidates.extend([
                data.get("subscriptionToken"),
                data.get("accessToken"),
                data.get("activationToken"),
                data.get("token"),
            ])

    for candidate in candidates:
        if isinstance(candidate, str) and candidate:
            return candidate

    raise RuntimeError(
        "Decoded login-session cookie but could not find an access token"
    )


class JsonlWriter:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.file = open(path, "a", encoding="utf-8", buffering=1)
        self.last_fsync = 0.0

    def write(self, record):
        line = json.dumps(record, separators=(",", ":"), ensure_ascii=False)
        with self.lock:
            self.file.write(line + "\n")
            self.file.flush()

            now = time.monotonic()
            if now - self.last_fsync >= FSYNC_INTERVAL:
                os.fsync(self.file.fileno())
                self.last_fsync = now

    def close(self):
        with self.lock:
            try:
                self.file.flush()
                os.fsync(self.file.fileno())
            finally:
                self.file.close()


def get_aws_cookie():
    headers = dict(HEADERS)
    try:
        r = requests.options(
            NEGOTIATE_URL,
            headers=headers,
            timeout=10,
        )
        cookie = r.cookies.get("AWSALBCORS")
        if cookie:
            print("[COOKIE]      AWSALBCORS received ✓")
            headers["Cookie"] = f"AWSALBCORS={cookie}"
        else:
            print(f"[COOKIE]      No AWSALBCORS cookie (OPTIONS {r.status_code})")
    except Exception as exc:
        print(f"[COOKIE]      Pre-negotiate warning: {exc}")

    return headers


def main():
    os.makedirs(CAPTURE_DIR, exist_ok=True)

    state_lock = threading.RLock()
    pre_roll = deque()

    session_writer = None
    session_capture_path = None
    session_counts = {}
    session_info = {}
    observed_session_info = {}

    attempt = 0

    def merge_session_info(current, update):
        if not isinstance(update, dict):
            return current

        if update.get("_kf") is True:
            return dict(update)

        merged = dict(current or {})
        merged.update(update)
        return merged

    def trim_pre_roll(now_monotonic):
        cutoff = now_monotonic - PRE_ROLL_SECONDS
        while pre_roll and pre_roll[0][0] < cutoff:
            pre_roll.popleft()

    def write_session_record(record):
        nonlocal session_counts

        if session_writer is None:
            return

        session_writer.write(record)

        if record.get("record_type") == "feed":
            feed = str(record.get("feed", "unknown"))
            session_counts[feed] = session_counts.get(feed, 0) + 1

    def start_session_capture(info):
        nonlocal session_writer
        nonlocal session_capture_path
        nonlocal session_counts
        nonlocal session_info

        with state_lock:
            if session_writer is not None:
                return

            stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            session_capture_path = os.path.join(
                CAPTURE_DIR,
                f"capture-in-progress_{stamp}.jsonl",
            )
            session_writer = JsonlWriter(session_capture_path)
            session_counts = {}
            session_info = dict(info or {})

            buffered = [record for _when, record in pre_roll]
            pre_roll.clear()

            for record in buffered:
                write_session_record(record)

            write_session_record({
                "record_type": "event",
                "event": "session_capture_started",
                "received_at": now_iso(),
                "authenticated": True,
                "pre_roll_seconds": PRE_ROLL_SECONDS,
            })

            meeting = session_info.get("Meeting") or {}
            event_name = meeting.get("Name") or meeting.get("Location") or "Unknown event"
            session_name = session_info.get("Name") or session_info.get("Type") or "Unknown session"

            print("=" * 72)
            print(f"[SESSION START] {event_name} — {session_name}")
            print(f"[CAPTURE]       {session_capture_path}")
            print("=" * 72)

    def finalize_session_capture(reason):
        nonlocal session_writer
        nonlocal session_capture_path
        nonlocal session_counts
        nonlocal session_info

        with state_lock:
            if session_writer is None:
                return

            write_session_record({
                "record_type": "event",
                "event": "session_capture_finished",
                "received_at": now_iso(),
                "reason": reason,
                "counts": dict(session_counts),
            })

            session_writer.close()

            old_path = session_capture_path
            final_path = build_final_capture_path(session_info)

            if final_path and final_path != old_path:
                try:
                    os.replace(old_path, final_path)
                except Exception as exc:
                    print(f"[RENAME WARN]   Could not rename capture: {exc}")
                    final_path = old_path
            else:
                final_path = old_path

            print("\n[SESSION SAVED]")
            for name, count in sorted(
                session_counts.items(),
                key=lambda item: item[1],
                reverse=True,
            ):
                print(f"  {name:<24} {count}")
            print(f"  File: {final_path}\n")
            print("[WAITING]       Recorder still running for the next session")

            session_writer = None
            session_capture_path = None
            session_counts = {}
            session_info = {}

    print("=" * 72)
    print("BROWN GP — AUTHENTICATED F1 WEEKEND CAPTURE")
    print("=" * 72)
    print("[MODE]         One long-running process, one file per live session")
    print(f"[PRE-ROLL]     {PRE_ROLL_SECONDS}s")
    print(f"[RECONNECT]    {RECONNECT_DELAY}s after ordinary disconnects")
    print("[TOKEN]        File is monitored; replacement triggers re-authentication")
    print("[WAITING]      Waiting for SessionInfo.SessionStatus = Started")
    print("=" * 72)

    try:
        while True:
            attempt += 1
            disconnected = threading.Event()
            hub = None
            token_refresh_requested = False

            try:
                access_token = load_access_token()
                connected_token_signature = token_signature()

                print(f"[CONNECT]      Attempt #{attempt}")
                headers = get_aws_cookie()

                hub = (
                    HubConnectionBuilder()
                    .with_url(
                        WS_URL,
                        options={
                            "verify_ssl": True,
                            "access_token_factory": lambda: access_token,
                            "headers": headers,
                        },
                    )
                    .build()
                )

                def on_open(*_args):
                    print(f"[WEBSOCKET]    CONNECTED ✓ (attempt #{attempt})")
                    with state_lock:
                        if session_writer is not None:
                            write_session_record({
                                "record_type": "event",
                                "event": "connected",
                                "received_at": now_iso(),
                                "attempt": attempt,
                            })

                def on_close(*args):
                    print("[WEBSOCKET]    Connection closed")
                    with state_lock:
                        if session_writer is not None:
                            write_session_record({
                                "record_type": "event",
                                "event": "disconnected",
                                "received_at": now_iso(),
                                "attempt": attempt,
                                "detail": str(args) if args else None,
                            })
                    disconnected.set()

                def on_error(error):
                    print(f"[ERROR]        {error}")
                    with state_lock:
                        if session_writer is not None:
                            write_session_record({
                                "record_type": "event",
                                "event": "connection_error",
                                "received_at": now_iso(),
                                "attempt": attempt,
                                "error": str(error),
                            })

                def on_feed(message):
                    nonlocal observed_session_info
                    nonlocal session_info

                    try:
                        received_at = now_iso()

                        if isinstance(message, list) and message:
                            feed = str(message[0])
                            content = message[1] if len(message) > 1 else None
                            extra = message[2:] if len(message) > 2 else None
                        else:
                            feed = "unknown"
                            content = message
                            extra = None

                        record = {
                            "record_type": "feed",
                            "received_at": received_at,
                            "feed": feed,
                            "data": content,
                            "extra": extra,
                        }

                        marker = ""
                        if feed == "CarData.z":
                            marker = "  <<< TELEMETRY"
                        elif feed == "Position.z":
                            marker = "  <<< POSITION"

                        with state_lock:
                            if feed == "SessionInfo" and isinstance(content, dict):
                                observed_session_info = merge_session_info(
                                    observed_session_info,
                                    content,
                                )

                                if session_writer is not None:
                                    session_info = merge_session_info(
                                        session_info,
                                        content,
                                    )

                            if session_writer is None:
                                now_mono = time.monotonic()
                                pre_roll.append((now_mono, record))
                                trim_pre_roll(now_mono)

                                status = observed_session_info.get("SessionStatus")
                                if status == "Started":
                                    start_session_capture(observed_session_info)

                                    feed_count = session_counts.get(feed, 0)
                                    print(
                                        f"[FEED]         {feed:<24} "
                                        f"#{feed_count}{marker}"
                                    )
                                return

                            write_session_record(record)
                            feed_count = session_counts.get(feed, 0)
                            print(
                                f"[FEED]         {feed:<24} "
                                f"#{feed_count}{marker}"
                            )

                            if (
                                feed == "SessionInfo"
                                and isinstance(content, dict)
                                and session_info.get("SessionStatus") == "Finalised"
                            ):
                                finalize_session_capture("SessionStatus Finalised")
                                observed_session_info = dict(session_info)

                    except Exception as exc:
                        print(f"[WRITE ERROR]  {exc}")

                hub.on_open(on_open)
                hub.on_close(on_close)
                hub.on_error(on_error)
                hub.on("feed", on_feed)

                hub.start()
                time.sleep(1)

                print(f"[SUBSCRIBE]     Requesting {len(TOPICS)} feeds...")
                hub.send("Subscribe", [TOPICS])
                print("[LISTENING]     Connected; waiting/capturing automatically")

                next_token_check = time.monotonic() + TOKEN_CHECK_INTERVAL

                while not disconnected.wait(1):
                    now_mono = time.monotonic()
                    if now_mono < next_token_check:
                        continue

                    next_token_check = now_mono + TOKEN_CHECK_INTERVAL

                    try:
                        current_signature = token_signature()
                    except Exception as exc:
                        print(f"[TOKEN WARN]   Could not stat token file: {exc}")
                        continue

                    if current_signature != connected_token_signature:
                        print("[TOKEN]        Token file changed — reconnecting with new token")
                        with state_lock:
                            if session_writer is not None:
                                write_session_record({
                                    "record_type": "event",
                                    "event": "token_file_changed",
                                    "received_at": now_iso(),
                                })
                        token_refresh_requested = True
                        disconnected.set()
                        break

            except Exception as exc:
                print(f"[DISCONNECTED]  {type(exc).__name__}: {exc}")
                with state_lock:
                    if session_writer is not None:
                        write_session_record({
                            "record_type": "event",
                            "event": "connection_error",
                            "received_at": now_iso(),
                            "attempt": attempt,
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                        })

            finally:
                if hub is not None:
                    try:
                        hub.stop()
                    except Exception:
                        pass

            if token_refresh_requested:
                print("[RECONNECT]     Re-authenticating now...")
                continue

            print(f"[RECONNECT]     Trying again in {RECONNECT_DELAY}s...")
            time.sleep(RECONNECT_DELAY)

    except KeyboardInterrupt:
        print("\n[STOP]         Weekend recorder stopped by user")
        with state_lock:
            if session_writer is not None:
                finalize_session_capture("recorder stopped by user")
            else:
                print("[STOP]         No active session file was open")




if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[FATAL] {type(exc).__name__}: {exc}")
        sys.exit(1)
