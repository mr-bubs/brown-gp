import json
import os
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timezone

import requests
from signalrcore.hub_connection_builder import HubConnectionBuilder


TOKEN_FILE = os.path.expanduser("~/.config/brown-gp/f1-token")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CAPTURE_DIR = os.path.join(SCRIPT_DIR, "f1-captures")
RECONNECT_DELAY = 5
FSYNC_INTERVAL = 2.0

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

    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    capture_path = os.path.join(
        CAPTURE_DIR,
        f"f1-auth-capture_{stamp}.jsonl",
    )

    access_token = load_access_token()
    writer = JsonlWriter(capture_path)

    counts = {}
    attempt = 0

    writer.write({
        "record_type": "event",
        "event": "recorder_started",
        "received_at": now_iso(),
        "authenticated": True,
    })

    print("=" * 72)
    print("BROWN GP — AUTHENTICATED F1 AUTO-RECONNECT CAPTURE")
    print("=" * 72)
    print(f"[CAPTURE]     {capture_path}")
    print("[TOKEN]       Loaded securely ✓ (never printed or written)")
    print(f"[RECONNECT]   {RECONNECT_DELAY} second delay")
    print("=" * 72)

    try:
        while True:
            attempt += 1
            disconnected = threading.Event()
            hub = None

            try:
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
                    writer.write({
                        "record_type": "event",
                        "event": "connected",
                        "received_at": now_iso(),
                        "attempt": attempt,
                    })

                def on_close(*args):
                    print("[WEBSOCKET]    Connection closed")
                    writer.write({
                        "record_type": "event",
                        "event": "disconnected",
                        "received_at": now_iso(),
                        "attempt": attempt,
                        "detail": str(args) if args else None,
                    })
                    disconnected.set()

                def on_error(error):
                    print(f"[ERROR]        {error}")

                def on_feed(message):
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

                        counts[feed] = counts.get(feed, 0) + 1

                        writer.write({
                            "record_type": "feed",
                            "received_at": received_at,
                            "feed": feed,
                            "data": content,
                            "extra": extra,
                        })

                        marker = ""
                        if feed == "CarData.z":
                            marker = "  <<< TELEMETRY"
                        elif feed == "Position.z":
                            marker = "  <<< POSITION"

                        print(
                            f"[FEED]         {feed:<24} "
                            f"#{counts[feed]}{marker}"
                        )

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

                writer.write({
                    "record_type": "event",
                    "event": "subscription_sent",
                    "received_at": now_iso(),
                    "attempt": attempt,
                    "feeds": TOPICS,
                })

                print("[RECORDING]     Listening + saving authenticated F1 data")

                while not disconnected.wait(1):
                    pass

            except Exception as exc:
                print(f"[DISCONNECTED]  {type(exc).__name__}: {exc}")
                writer.write({
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

            print(f"[RECONNECT]     Trying again in {RECONNECT_DELAY}s...")
            time.sleep(RECONNECT_DELAY)

    except KeyboardInterrupt:
        print("\n[STOP]         Recorder stopped by user")
        writer.write({
            "record_type": "event",
            "event": "recorder_stopped",
            "received_at": now_iso(),
            "counts": counts,
        })

    finally:
        writer.close()

        print("\nFeed totals:")
        for name, count in sorted(
            counts.items(),
            key=lambda item: item[1],
            reverse=True,
        ):
            print(f"  {name:<24} {count}")

        print(f"\nSaved to: {capture_path}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[FATAL] {type(exc).__name__}: {exc}")
        sys.exit(1)
