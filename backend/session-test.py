import asyncio
import copy
import json
import os
import threading
import time
import urllib.parse
from datetime import datetime, timezone

import requests
import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from signalrcore.hub_connection_builder import HubConnectionBuilder

# Standalone, isolated live timing test. No recorder process or JSONL dependency.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SESSION_HTML = os.path.join(SCRIPT_DIR, 'index.html')
TOKEN_FILE = os.path.expanduser('~/.config/brown-gp/f1-token')
NEGOTIATE_URL = 'https://livetiming.formula1.com/signalrcore/negotiate'
WS_URL = 'wss://livetiming.formula1.com/signalrcore'
RECONNECT_DELAY = 5
TOKEN_CHECK_INTERVAL = 5
TOPICS = [
    'Heartbeat', 'CarData.z', 'Position.z', 'ExtrapolatedClock',
    'TopThree', 'RcmSeries', 'TimingStats', 'TimingAppData',
    'WeatherData', 'TrackStatus', 'DriverList', 'RaceControlMessages',
    'SessionInfo', 'SessionData', 'LapCount', 'TimingData',
]
HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36'
    ),
    'Origin': 'https://www.formula1.com',
    'Referer': 'https://www.formula1.com/',
}

app = FastAPI(title='Brown GP Session Timing Test')
app.add_middleware(
    CORSMiddleware, allow_origins=['*'], allow_credentials=True,
    allow_methods=['*'], allow_headers=['*'],
)
TEST_DATA = {
    'SessionInfo': {}, 'TimingData': {}, 'TimingAppData': {}, 'DriverList': {},
    'RaceControlMessages': {'Messages': []}, 'ExtrapolatedClock': {},
    'SessionData': {}, 'LapCount': {}, 'TimingStats': {}, 'TopThree': {},
    'TrackStatus': {}, 'WeatherData': {}, 'Heartbeat': {},
}
connected_clients = set()
stop_event = threading.Event()
worker_thread = None
feed_task = None
feed_queue = None
event_loop = None
active_hub = None
status_lock = threading.Lock()
connection_status = {
    'connected': False, 'subscribed': False, 'attempts': 0,
    'feeds_received': 0, 'last_feed': None, 'last_feed_at': None,
    'last_error': None,
}


def set_status(**changes):
    with status_lock:
        connection_status.update(changes)


def load_access_token():
    if not os.path.exists(TOKEN_FILE):
        raise RuntimeError('Token file not found: ' + TOKEN_FILE)
    with open(TOKEN_FILE, 'r', encoding='utf-8') as f:
        raw = f.read().strip()
    if not raw:
        raise RuntimeError('Token file is empty')
    if raw.startswith('eyJ'):
        return raw
    try:
        obj = json.loads(urllib.parse.unquote(raw))
    except Exception as exc:
        raise RuntimeError('Could not decode login-session cookie') from exc
    candidates = []
    if isinstance(obj, dict):
        candidates.extend(obj.get(k) for k in (
            'subscriptionToken', 'accessToken', 'activationToken', 'token'
        ))
        data = obj.get('data')
        if isinstance(data, dict):
            candidates.extend(data.get(k) for k in (
                'subscriptionToken', 'accessToken', 'activationToken', 'token'
            ))
    for candidate in candidates:
        if isinstance(candidate, str) and candidate:
            return candidate
    raise RuntimeError('No access token found in login-session cookie')


def token_signature():
    stat = os.stat(TOKEN_FILE)
    return stat.st_mtime_ns, stat.st_size


def get_aws_cookie():
    headers = dict(HEADERS)
    try:
        response = requests.options(
            NEGOTIATE_URL, headers=headers, timeout=10,
        )
        cookie = response.cookies.get('AWSALBCORS')
        if cookie:
            headers['Cookie'] = 'AWSALBCORS=' + cookie
            print('[SESSION TEST] AWSALBCORS cookie received')
        else:
            print('[SESSION TEST] No AWSALBCORS cookie (OPTIONS %s)' % response.status_code)
    except Exception as exc:
        print('[SESSION TEST] Pre-negotiate warning: %s' % exc)
    return headers


def deep_update(target, patch):
    if not isinstance(target, dict) or not isinstance(patch, dict):
        return
    for key, value in patch.items():
        if isinstance(value, dict):
            if not isinstance(target.get(key), dict):
                target[key] = {}
            deep_update(target[key], value)
        else:
            target[key] = value


async def broadcast(payload):
    raw = json.dumps(payload, separators=(',', ':'))
    dead = []
    for ws in list(connected_clients):
        try:
            await ws.send_text(raw)
        except Exception:
            dead.append(ws)
    for ws in dead:
        connected_clients.discard(ws)


async def apply_feed(feed, content):
    if feed in TEST_DATA:
        if isinstance(content, dict):
            deep_update(TEST_DATA[feed], content)
        else:
            TEST_DATA[feed] = content
    if feed in ('SessionInfo', 'TimingData', 'TimingAppData', 'DriverList'):
        await broadcast({'type': 'live_update', feed: content})
    elif feed == 'ExtrapolatedClock' and isinstance(content, dict):
        remaining = content.get('Remaining')
        if remaining is not None:
            patch = {'SessionTimeToSession': remaining}
            deep_update(TEST_DATA['SessionInfo'], patch)
            await broadcast({'type': 'live_update', 'SessionInfo': patch})


async def consume_feeds():
    while True:
        feed, content = await feed_queue.get()
        try:
            await apply_feed(feed, content)
        except Exception as exc:
            print('[SESSION TEST] Feed processing error: %s' % exc)
        finally:
            feed_queue.task_done()


def signalr_worker():
    global active_hub
    attempt = 0
    while not stop_event.is_set():
        attempt += 1
        disconnected = threading.Event()
        hub = None
        refresh_token = False
        try:
            access_token = load_access_token()
            connected_signature = token_signature()
            set_status(attempts=attempt, connected=False, subscribed=False, last_error=None)
            print('[SESSION TEST] Authenticated SignalR attempt #%s' % attempt)
            headers = get_aws_cookie()
            hub = (
                HubConnectionBuilder()
                .with_url(
                    WS_URL,
                    options={
                        'verify_ssl': True,
                        'access_token_factory': lambda: access_token,
                        'headers': headers,
                    },
                )
                .build()
            )
            active_hub = hub

            def on_open(*_args):
                set_status(connected=True)
                print('[SESSION TEST] F1 WebSocket connected')

            def on_close(*_args):
                set_status(connected=False, subscribed=False)
                print('[SESSION TEST] F1 WebSocket closed')
                disconnected.set()

            def on_error(error):
                # Never log tokens, cookies, headers, or raw connection objects.
                set_status(last_error='SignalR callback error')
                print('[SESSION TEST] SignalR callback error')

            def on_feed(message):
                try:
                    if isinstance(message, list) and message:
                        feed = str(message[0])
                        content = message[1] if len(message) > 1 else None
                    else:
                        return
                    received_at = datetime.now(timezone.utc).isoformat()
                    with status_lock:
                        connection_status['feeds_received'] += 1
                        connection_status['last_feed'] = feed
                        connection_status['last_feed_at'] = received_at
                    if event_loop is not None and feed_queue is not None:
                        event_loop.call_soon_threadsafe(
                            feed_queue.put_nowait, (feed, content)
                        )
                except Exception:
                    set_status(last_error='Feed dispatch error')
                    print('[SESSION TEST] Feed dispatch error')

            hub.on_open(on_open)
            hub.on_close(on_close)
            hub.on_error(on_error)
            hub.on('feed', on_feed)
            hub.start()
            if stop_event.wait(1):
                break
            print('[SESSION TEST] Subscribing to %s feeds' % len(TOPICS))
            hub.send('Subscribe', [TOPICS])
            set_status(subscribed=True)
            print('[SESSION TEST] Subscription request sent; awaiting feed messages')
            next_check = time.monotonic() + TOKEN_CHECK_INTERVAL
            while not stop_event.is_set() and not disconnected.wait(1):
                if time.monotonic() < next_check:
                    continue
                next_check = time.monotonic() + TOKEN_CHECK_INTERVAL
                try:
                    changed = token_signature() != connected_signature
                except OSError:
                    changed = False
                if changed:
                    print('[SESSION TEST] Token file changed; re-authenticating')
                    refresh_token = True
                    break
        except Exception as exc:
            # Avoid printing exception strings that might contain sensitive URL/token data.
            set_status(last_error=type(exc).__name__, connected=False, subscribed=False)
            print('[SESSION TEST] Connection error: %s' % type(exc).__name__)
        finally:
            if hub is not None:
                try:
                    hub.stop()
                except Exception:
                    pass
            active_hub = None
            set_status(connected=False, subscribed=False)
        if stop_event.is_set():
            break
        if refresh_token:
            continue
        print('[SESSION TEST] Reconnecting in %ss' % RECONNECT_DELAY)
        stop_event.wait(RECONNECT_DELAY)


@app.on_event('startup')
async def startup():
    global feed_queue, feed_task, event_loop, worker_thread
    if not os.path.exists(SESSION_HTML):
        raise RuntimeError('Missing Session Timing HTML: ' + SESSION_HTML)
    event_loop = asyncio.get_running_loop()
    feed_queue = asyncio.Queue()
    stop_event.clear()
    feed_task = asyncio.create_task(consume_feeds())
    worker_thread = threading.Thread(
        target=signalr_worker, name='f1-session-test-signalr', daemon=True
    )
    worker_thread.start()
    print('=' * 68)
    print('BROWN GP — ISOLATED SESSION TIMING TEST (DIRECT SIGNALR)')
    print('main.py and f1-auth-capture.py remain untouched')
    print('Open: http://<phone-ip>:10001/session-timing')
    print('=' * 68)


@app.on_event('shutdown')
async def shutdown():
    stop_event.set()
    if active_hub is not None:
        try:
            active_hub.stop()
        except Exception:
            pass
    if worker_thread is not None:
        await asyncio.to_thread(worker_thread.join, 5)
    if feed_task is not None:
        feed_task.cancel()
        try:
            await feed_task
        except asyncio.CancelledError:
            pass


def read_html():
    with open(SESSION_HTML, 'r', encoding='utf-8') as f:
        return f.read()


@app.get('/')
def root():
    return HTMLResponse(read_html())


@app.get('/session-timing')
def session_timing():
    return HTMLResponse(read_html())


@app.get('/api/session')
def api_session():
    return JSONResponse(copy.deepcopy(TEST_DATA['SessionInfo']))


@app.get('/api/timing')
def api_timing():
    return JSONResponse(copy.deepcopy(TEST_DATA['TimingData']))


@app.get('/api/timingapp')
def api_timingapp():
    return JSONResponse(copy.deepcopy(TEST_DATA['TimingAppData']))


@app.get('/api/drivers')
def api_drivers():
    return JSONResponse(copy.deepcopy(TEST_DATA['DriverList']))


@app.get('/api/messages')
def api_messages():
    return JSONResponse(copy.deepcopy(TEST_DATA['RaceControlMessages']))


@app.get('/api/session-test-status')
def api_status():
    with status_lock:
        connection = dict(connection_status)
    return JSONResponse({
        'capture_process_running': False,
        'capture_file': None,
        'browser_clients': len(connected_clients),
        'timing_rows': len(TEST_DATA.get('TimingData', {}).get('Lines', {})),
        'drivers': len(TEST_DATA.get('DriverList', {})),
        'signalr': connection,
    })


@app.websocket('/ws')
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_clients.add(websocket)
    try:
        # Send the current state to browsers joining after the first live feed.
        await websocket.send_text(json.dumps({
            'type': 'live_update',
            'SessionInfo': TEST_DATA['SessionInfo'],
            'TimingData': TEST_DATA['TimingData'],
            'TimingAppData': TEST_DATA['TimingAppData'],
            'DriverList': TEST_DATA['DriverList'],
        }, separators=(',', ':')))
        while True:
            await websocket.receive_text()
    except Exception:
        pass
    finally:
        connected_clients.discard(websocket)


if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=10001)
