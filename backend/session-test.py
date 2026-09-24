import asyncio
import copy
import glob
import json
import os
import subprocess
import sys
import time

import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SESSION_HTML = os.path.join(SCRIPT_DIR, 'index.html')
CAPTURE_SCRIPT = os.path.join(SCRIPT_DIR, 'f1-auth-capture.py')
CAPTURE_DIR = os.path.join(SCRIPT_DIR, 'f1-captures')

app = FastAPI(title='Brown GP Session Timing Test')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_credentials=True, allow_methods=['*'], allow_headers=['*'])

TEST_DATA = {
    'SessionInfo': {}, 'TimingData': {}, 'TimingAppData': {}, 'DriverList': {},
    'RaceControlMessages': {'Messages': []}, 'ExtrapolatedClock': {},
    'SessionData': {}, 'LapCount': {}, 'TimingStats': {}, 'TopThree': {},
    'TrackStatus': {}, 'WeatherData': {}, 'Heartbeat': {},
}

connected_clients = set()
capture_process = None
active_capture_file = None


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


def newest_capture():
    files = glob.glob(os.path.join(CAPTURE_DIR, 'f1-auth-capture_*.jsonl'))
    return max(files, key=os.path.getmtime) if files else None


async def wait_for_capture_file(started_at):
    while True:
        path = newest_capture()
        if path and os.path.getmtime(path) >= started_at - 2:
            return path
        if capture_process and capture_process.poll() is not None:
            raise RuntimeError(f'f1-auth-capture.py exited with code {capture_process.returncode}')
        await asyncio.sleep(0.25)


async def tail_capture(path):
    global active_capture_file
    active_capture_file = path
    print(f'[SESSION TEST] Reading live feed from {path}')

    with open(path, 'r', encoding='utf-8') as f:
        while True:
            line = f.readline()
            if not line:
                await asyncio.sleep(0.05)
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get('record_type') != 'feed':
                continue
            feed = record.get('feed')
            content = record.get('data')
            if feed:
                await apply_feed(feed, content)


async def capture_and_feed():
    global capture_process
    os.makedirs(CAPTURE_DIR, exist_ok=True)
    started_at = time.time()
    print('[SESSION TEST] Starting isolated authenticated F1 capture...')
    capture_process = subprocess.Popen([sys.executable, CAPTURE_SCRIPT], cwd=SCRIPT_DIR)
    path = await wait_for_capture_file(started_at)
    await tail_capture(path)


@app.on_event('startup')
async def startup():
    if not os.path.exists(SESSION_HTML):
        raise RuntimeError(f'Missing Session Timing HTML: {SESSION_HTML}')
    if not os.path.exists(CAPTURE_SCRIPT):
        raise RuntimeError(f'Missing capture script: {CAPTURE_SCRIPT}')
    asyncio.create_task(capture_and_feed())
    print('=' * 68)
    print('BROWN GP — ISOLATED SESSION TIMING TEST')
    print('main.py remains untouched')
    print('Open: http://<phone-ip>:10001/session-timing')
    print('=' * 68)


@app.on_event('shutdown')
async def shutdown():
    global capture_process
    if capture_process and capture_process.poll() is None:
        capture_process.terminate()
        try:
            capture_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            capture_process.kill()


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
    return JSONResponse({
        'capture_process_running': bool(capture_process and capture_process.poll() is None),
        'capture_file': active_capture_file,
        'browser_clients': len(connected_clients),
        'timing_rows': len(TEST_DATA.get('TimingData', {}).get('Lines', {})),
        'drivers': len(TEST_DATA.get('DriverList', {})),
    })


@app.websocket('/ws')
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_clients.add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except Exception:
        pass
    finally:
        connected_clients.discard(websocket)


if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=10001)
