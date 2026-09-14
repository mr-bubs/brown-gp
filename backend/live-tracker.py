import asyncio
import json
import fastf1
import numpy as np
import os
import gc
import traceback
import websockets
import pandas as pd
import aiohttp
import zlib
import base64
from urllib.parse import quote
from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import uvicorn

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Safe background ingestion initialization
    asyncio.create_task(f1_signalr_client())
    print("[STARTUP] Isolated Live Tracker Worker alive on port 10001.")
    yield
    gc.collect()

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

if not os.path.exists('f1cache'):
    os.makedirs('f1cache')
fastf1.Cache.enable_cache('f1cache')

LIVE_DATA = {
    "SessionInfo": {},
    "Position": {},
    "TimingData": {},
    "DriverList": {}
}

connected_clients = set()

def deep_update(mapping, update_dict):
    for k, v in update_dict.items():
        if isinstance(v, dict) and k in mapping and isinstance(mapping[k], dict):
            deep_update(mapping[k], v)
        else:
            mapping[k] = v
    return mapping

def smooth_array(arr, window_size=3):
    res = np.zeros_like(arr, dtype=float)
    for i in range(len(arr)):
        start = max(0, i - window_size)
        end = min(len(arr), i + window_size + 1)
        res[i] = np.mean(arr[start:end])
    return res.tolist()

@app.get("/live-tracker")
def read_live_tracker():
    with open("live-tracker.html", "r") as f:
        return HTMLResponse(content=f.read())

@app.get("/api/track")
def get_track(event_name: str, year: int = None):
    if not year:
        year = pd.Timestamp.utcnow().year
        
    try:
        print(f"[TRACKER] Extracting 3D geometry matrix for {year} {event_name}...")
        try:
            s = fastf1.get_session(year, event_name, 'FP1')
            s.load(telemetry=True, laps=True, weather=False, messages=False)
        except Exception:
            print(f"[TRACKER] Current year fallback active. Sourcing previous season configuration...")
            s = fastf1.get_session(year - 1, event_name, 'R')
            s.load(telemetry=True, laps=True, weather=False, messages=False)
            
        if s.laps.empty:
            return JSONResponse({"error": "Target stage lacks coordinate records."})
            
        fastest_lap = s.laps.pick_fastest()
        if pd.isna(fastest_lap['Time']):
            fastest_lap = s.laps.iloc[0]
            
        tel = fastest_lap.get_telemetry()
        if tel.empty:
            return JSONResponse({"error": "Telemetry array compilation failed."})
            
        track_x = smooth_array(tel['X'].values)
        track_y = smooth_array(tel['Y'].values)
        track_z = smooth_array(tel['Z'].values) if 'Z' in tel else [0] * len(track_x)
        
        print(f"[TRACKER] Success! 3D Rail vectors established for {event_name}.")
        return {
            "x": track_x,
            "y": track_y,
            "z": track_z,
            "event_name": event_name
        }
    except Exception as e:
        print(f"[TRACKER] Blueprint extraction error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

@app.websocket("/ws/live-tracker")
async def live_tracker_ws(websocket: WebSocket):
    await websocket.accept()
    connected_clients.add(websocket)
    try:
        # Immediate context synchronization packet on frame handshake
        await websocket.send_text(json.dumps({
            "type": "live_update",
            "SessionInfo": LIVE_DATA.get("SessionInfo", {}),
            "TimingData": LIVE_DATA.get("TimingData", {}),
            "DriverList": LIVE_DATA.get("DriverList", {})
        }))
        while True:
            await websocket.receive_text()
    except:
        connected_clients.discard(websocket)

async def f1_signalr_client():
    global LIVE_DATA
    base_url = 'https://livetiming.formula1.com'
    negotiate_url = f'{base_url}/signalrcore/negotiate'
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Origin": "https://www.formula1.com",
        "Referer": "https://www.formula1.com/",
    }
    
    cookie = os.environ.get("F1_COOKIE", "")
    if cookie:
        headers["Cookie"] = cookie

    while True:
        try:
            print("[SIGNALR] Negotiating connection with F1 SignalR Core Servers...")
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.post(negotiate_url) as resp:
                    if resp.status in [401, 403]:
                        print("\n" + "="*80)
                        print(f"[!] {resp.status} UNAUTHORIZED/FORBIDDEN: ANTI-BOT FIREWALL DETECTED [!]")
                        print("="*80)
                        print("Formula 1's secure gateway requires browser cookies to authenticate.")
                        print("\nHOW TO EXTRACT AND RUN WITH COOKIES IN TERMUX:")
                        print("1. Open Chrome/Safari on your PC/Phone and go to formula1.com")
                        print("2. Open Developer Tools (F12) -> Network Tab -> click on any request.")
                        print("3. Look at 'Request Headers' -> find the 'Cookie' value.")
                        print("4. Copy that long text string completely.")
                        print("\n5. Inside your Termux console, type the following command:")
                        print('   export F1_COOKIE="paste_your_entire_copied_cookie_string_here"')
                        print("\n6. Restart the engine: python live-tracker.py")
                        print("="*80 + "\n")
                        await asyncio.sleep(10)
                        continue
                    
                    raw_text = await resp.text()
                    try:
                        data = json.loads(raw_text)
                        token = quote(data.get('connectionToken', data.get('ConnectionToken', '')))
                    except json.JSONDecodeError:
                        print("\n[!] CLOUDFLARE HTML TRAP DETECTED [!]")
                        print("F1 sent a bot-challenge webpage instead of JSON. Your F1_COOKIE is likely empty or expired.")
                        print("Please extract a fresh Cookie from your browser and try again.\n")
                        await asyncio.sleep(10)
                        continue

                ws_url = f"wss://livetiming.formula1.com/signalrcore?id={token}"
                
                # 🚨 FIX: Reverted to `extra_headers` for backwards compatibility with Termux package versions!
                async with websockets.connect(ws_url, extra_headers=headers) as ws:
                    print("[SIGNALR] Connected to Core! Dispatching handshake...")
                    
                    handshake = {"protocol": "json", "version": 1}
                    await ws.send(json.dumps(handshake) + '\x1e')
                    
                    await ws.recv()
                    print("[SIGNALR] Handshake successful. Subscribing to live telemetry feeds...")
                    
                    sub_payload = {
                        "type": 1,
                        "target": "Subscribe",
                        "arguments": [["Heartbeat", "CarData.z", "Position.z", "ExtrapolatedClock", "TopThree", "RcmSeries", "TimingStats", "TimingAppData", "WeatherData", "TrackStatus", "DriverList", "RaceControlMessages", "SessionInfo", "SessionData", "LapCount", "TimingData"]],
                        "invocationId": "1"
                    }
                    await ws.send(json.dumps(sub_payload) + '\x1e')
                    print("[SIGNALR] Payloads delivered. Listening for data firehose...")
                    
                    while True:
                        try:
                            frame_raw = await ws.recv()
                            
                            # SignalR Core binary frame defense
                            if isinstance(frame_raw, bytes):
                                frame_raw = frame_raw.decode('utf-8', errors='ignore')
                                
                            for raw_msg in frame_raw.split('\x1e'):
                                if not raw_msg: 
                                    continue
                                    
                                try:
                                    payload = json.loads(raw_msg)
                                except json.JSONDecodeError: 
                                    continue
                                
                                # Ignore Keep-Alive Pings
                                if not payload or payload.get('type') == 6: 
                                    continue
                                    
                                if payload.get('type') == 1:
                                    target = payload.get('target', '').lower()
                                    args = payload.get('arguments', [])
                                    
                                    if target == 'feed' and len(args) >= 2:
                                        feed, content = args[0], args[1]
                                        
                                        if isinstance(content, str) and len(content) > 50 and feed in ['CarData.z', 'Position.z']:
                                            try:
                                                content = json.loads(zlib.decompress(base64.b64decode(content), -zlib.MAX_WBITS))
                                                feed = feed.replace('.z', '')
                                            except Exception: pass
                                        
                                        if feed in ['DriverList', 'SessionInfo', 'TimingData']:
                                            print(f"[FIREHOSE] Routing streaming matrix frame: {feed} ({len(str(content))} bytes)")

                                        if feed in LIVE_DATA:
                                            if isinstance(content, dict): 
                                                deep_update(LIVE_DATA[feed], content)
                                            elif isinstance(content, list): 
                                                LIVE_DATA[feed] = content
                                        
                                        broadcast_frame = json.dumps({"type": "live_update", feed: content})
                                        for client in list(connected_clients):
                                            try: await client.send_text(broadcast_frame)
                                            except Exception: pass
                                            
                        except Exception as e:
                            # 🚨 FIX: Inner exception shield. Prevents total pipeline crash on a single bad frame.
                            if isinstance(e, websockets.exceptions.ConnectionClosed):
                                raise e # Re-raise only if the connection is physically dead
                            print(f"[SIGNALR] Ignored malformed stream frame: {e}")
                            continue

        except Exception as e:
            print(f"\n[SIGNALR] Ingestion pipeline dropped link context: {type(e).__name__}: {e}")
            traceback.print_exc()
            print("[SIGNALR] Re-instantiating tunnel link in 5s...\n")
            await asyncio.sleep(5)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=10001)
