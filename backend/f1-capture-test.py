import asyncio
import aiohttp
import websockets
import json
import base64
import zlib
import time
import os
from datetime import datetime, timezone

BASE = "https://livetiming.formula1.com"
NEGOTIATE = f"{BASE}/signalrcore/negotiate"
WEBSOCKET = "wss://livetiming.formula1.com/signalrcore"
RECONNECT_DELAY = 5

TOPICS = [
    "Heartbeat", "CarData.z", "Position.z", "ExtrapolatedClock",
    "TopThree", "RcmSeries", "TimingStats", "TimingAppData",
    "WeatherData", "TrackStatus", "DriverList", "RaceControlMessages",
    "SessionInfo", "SessionData", "LapCount", "TimingData",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36",
    "Origin": "https://www.formula1.com",
    "Referer": "https://www.formula1.com/",
}

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CAPTURE_DIR = os.path.join(SCRIPT_DIR, "f1-captures")
os.makedirs(CAPTURE_DIR, exist_ok=True)
capture_started = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
CAPTURE_FILE = os.path.join(CAPTURE_DIR, f"f1-capture_{capture_started}.jsonl")

def line():
    print("=" * 72)

def log(stage, message):
    print(f"[{time.strftime('%H:%M:%S')}] [{stage:<12}] {message}", flush=True)

def utc_timestamp():
    return datetime.now(timezone.utc).isoformat()

def append_record(record):
    with open(CAPTURE_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())

def save_feed(feed, content):
    append_record({"record_type": "feed", "received_at": utc_timestamp(), "feed": feed, "data": content})

def save_event(event, details=None):
    record = {"record_type": "event", "received_at": utc_timestamp(), "event": event}
    if details is not None:
        record["details"] = details
    append_record(record)

def decode_compressed(feed, content):
    if feed not in ("CarData.z", "Position.z") or not isinstance(content, str):
        return None
    try:
        compressed = base64.b64decode(content)
        decompressed = zlib.decompress(compressed, -zlib.MAX_WBITS)
        return json.loads(decompressed)
    except Exception as exc:
        log("DECODE", f"{feed} decode failed: {exc}")
        return None

async def connect_and_record(connection_number, counters):
    cookie = None
    timeout = aiohttp.ClientTimeout(total=15)

    async with aiohttp.ClientSession(headers=HEADERS, timeout=timeout) as session:
        log("OPTIONS", "Requesting pre-negotiate cookie...")
        try:
            async with session.options(NEGOTIATE) as resp:
                log("OPTIONS", f"HTTP {resp.status} {resp.reason}")
                if "AWSALBCORS" in resp.cookies:
                    cookie = "AWSALBCORS=" + resp.cookies["AWSALBCORS"].value
                    log("COOKIE", "AWSALBCORS received ✓")
        except Exception as exc:
            log("OPTIONS", f"{type(exc).__name__}: {exc}")

        request_headers = dict(HEADERS)
        if cookie:
            request_headers["Cookie"] = cookie

        log("NEGOTIATE", "Sending POST request...")
        async with session.post(NEGOTIATE, headers=request_headers) as resp:
            log("NEGOTIATE", f"HTTP {resp.status} {resp.reason}")
            text = await resp.text()
            if resp.status != 200:
                raise RuntimeError(f"Negotiate failed: HTTP {resp.status} {text[:300]}")
            data = json.loads(text)
            token = data.get("connectionToken") or data.get("connectionId")
            if not token:
                raise RuntimeError("No SignalR connection token/id returned.")
            log("TOKEN", f"Received ✓ ({len(token)} chars)")

        ws_url = f"{WEBSOCKET}?id={token}"
        ws_headers = dict(HEADERS)
        if cookie:
            ws_headers["Cookie"] = cookie

        log("WEBSOCKET", f"Opening connection #{connection_number}...")
        async with websockets.connect(
            ws_url,
            additional_headers=ws_headers,
            open_timeout=15,
            ping_interval=None,
        ) as ws:
            log("WEBSOCKET", f"CONNECTED ✓ (connection #{connection_number})")
            save_event("connected", {"connection": connection_number})

            await ws.send(json.dumps({"protocol": "json", "version": 1}) + "\x1e")
            log("HANDSHAKE", "Sent.")
            handshake_response = await asyncio.wait_for(ws.recv(), timeout=10)
            if isinstance(handshake_response, bytes):
                handshake_response = handshake_response.decode("utf-8", errors="replace")
            log("HANDSHAKE", f"Response: {repr(handshake_response)}")

            subscription = {
                "type": 1,
                "target": "Subscribe",
                "arguments": [TOPICS],
                "invocationId": "1",
            }
            await ws.send(json.dumps(subscription) + "\x1e")
            log("SUBSCRIBE", f"Requested {len(TOPICS)} feeds.")
            save_event("subscription_sent", {"connection": connection_number, "feeds": TOPICS})
            line()
            log("RECORDING", "Listening for F1 data...")
            line()

            while True:
                try:
                    frame = await asyncio.wait_for(ws.recv(), timeout=60)
                except asyncio.TimeoutError:
                    log("TIMEOUT", "No WebSocket frame for 60 seconds.")
                    continue

                if isinstance(frame, bytes):
                    frame = frame.decode("utf-8", errors="replace")

                for raw in frame.split("\x1e"):
                    if not raw:
                        continue
                    counters["protocol"] += 1
                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError:
                        log("RAW", f"Non-JSON ({len(raw)} bytes)")
                        continue

                    msg_type = payload.get("type")
                    if msg_type == 6:
                        log("PING", "SignalR keepalive")
                        continue

                    if msg_type == 1:
                        target = payload.get("target", "")
                        args = payload.get("arguments", [])
                        if target.lower() == "feed" and len(args) >= 2:
                            feed, content = args[0], args[1]
                            save_feed(feed, content)
                            counters["saved"] += 1
                            counters["feeds"][feed] = counters["feeds"].get(feed, 0) + 1
                            count = counters["feeds"][feed]
                            log("FEED", f"{feed:<24} {len(str(content)):>8} bytes #{count}")

                            if feed in ("CarData.z", "Position.z"):
                                if decode_compressed(feed, content) is not None:
                                    log("DECODE", f"{feed} decoded ✓")

                    elif msg_type == 3:
                        if payload.get("error"):
                            error = str(payload["error"])
                            log("INVOCATION", f"ERROR: {error}")
                            save_event("subscription_error", {"connection": connection_number, "error": error})
                        else:
                            log("INVOCATION", "Subscription completed ✓")
                            save_event("subscription_completed", {"connection": connection_number})
                    else:
                        log("MESSAGE", f"SignalR type={msg_type}")

async def main():
    line()
    print("BROWN GP — F1 AUTO-RECONNECT CAPTURE")
    line()
    log("CAPTURE", "Capture file:")
    print(f"          {CAPTURE_FILE}")
    log("MODE", "Auto-reconnect ENABLED")
    log("RECONNECT", f"{RECONNECT_DELAY} second delay")
    line()

    counters = {"protocol": 0, "saved": 0, "feeds": {}}
    connection_number = 0
    save_event("recorder_started", {"feeds": TOPICS})

    try:
        while True:
            connection_number += 1
            try:
                log("CONNECT", f"Attempt #{connection_number}")
                await connect_and_record(connection_number, counters)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error_text = f"{type(exc).__name__}: {exc}"
                line()
                log("DISCONNECTED", error_text)
                log("STATS", f"{counters['saved']} feed records saved")
                if counters["feeds"]:
                    log("FEEDS", ", ".join(f"{name}:{count}" for name, count in counters["feeds"].items()))
                save_event("disconnected", {
                    "connection": connection_number,
                    "error": error_text,
                    "saved_records": counters["saved"],
                })
                log("RECONNECT", f"Trying again in {RECONNECT_DELAY}s...")
                line()
                await asyncio.sleep(RECONNECT_DELAY)
    except KeyboardInterrupt:
        print()
        line()
        log("STOP", "Stopped manually.")
        log("TOTAL", f"{counters['saved']} feed records saved.")
        if counters["feeds"]:
            log("FEEDS", ", ".join(f"{name}:{count}" for name, count in counters["feeds"].items()))
        save_event("recorder_stopped", {"saved_records": counters["saved"], "feeds": counters["feeds"]})
        log("CAPTURE", CAPTURE_FILE)
        line()

if __name__ == "__main__":
    asyncio.run(main())
