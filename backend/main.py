import asyncio
import json
import requests
import fastf1
import numpy as np
import urllib.parse
import base64
import zlib
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import os
import time
import re
import websockets

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if not os.path.exists('f1cache'):
    os.makedirs('f1cache')
fastf1.Cache.enable_cache('f1cache')

DRIVER_BACKUP_FILE = "f1cache/driver_backup.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# --- GLOBAL F1 LIVE MEMORY ---
LIVE_DATA = {
    "SessionInfo": {},
    "TimingData": {},
    "TimingAppData": {},
    "Position": {},
    "RaceControlMessages": {"Messages": []},
    "DriverList": {}
}

def decode_f1_z(encoded_str):
    try:
        return json.loads(zlib.decompress(base64.b64decode(encoded_str), -zlib.MAX_WBITS))
    except:
        return {}

def update_dict(base, delta):
    for k, v in delta.items():
        if isinstance(v, dict) and k in base and isinstance(base[k], dict):
            update_dict(base[k], v)
        else:
            base[k] = v

@app.get("/")
def read_root():
    return {"status": "Brown GP SignalR Middleman is Live!"}

@app.get("/api/session")
def get_session():
    return LIVE_DATA.get("SessionInfo", {})

@app.get("/api/timing")
def get_timing():
    return LIVE_DATA.get("TimingData", {})

@app.get("/api/timingapp")
def get_timing_app():
    return LIVE_DATA.get("TimingAppData", {})

@app.get("/api/messages")
def get_messages():
    return LIVE_DATA.get("RaceControlMessages", {})

@app.get("/api/drivers")
def get_drivers():
    return LIVE_DATA.get("DriverList", {})

def get_track_background_sync(year, circuit, session_name):
    try:
        session = fastf1.get_session(year, circuit, session_name)
        session.load(telemetry=True, laps=True, weather=False, messages=False)
        fastest_lap = session.laps.pick_fastest()
        tel = fastest_lap.get_telemetry()
        total_laps = getattr(session, 'total_laps', None)

        track_x = tel['X'].values.tolist()
        track_y = tel['Y'].values.tolist()
        track_dist = tel['Distance'].values.tolist()

        m_sectors = []
        circuit_info = session.get_circuit_info()
        if circuit_info is not None and hasattr(circuit_info, 'marshal_sectors'):
            for _, row in circuit_info.marshal_sectors.iterrows():
                m_sectors.append({'Number': int(row['Number']), 'Distance': float(row['Distance'])})

        return track_x, track_y, track_dist, m_sectors, total_laps
    except Exception as e:
        print(f"Track Error: {e}")
        return [], [], [], [], None

def parse_gap(gap_str):
    if not gap_str: return 0.0
    clean_str = str(gap_str).replace('+', '').strip()
    if 'LAP' in clean_str.upper():
        try: return int(clean_str.split(' ')[0]) * 80.0
        except: return 80.0
    try: return float(clean_str)
    except: return 0.0

connected_clients = set()

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_clients.add(websocket)
    try:
        while True: await websocket.receive_text()
    except:
        connected_clients.remove(websocket)

# --- TASK 1: THE SIGNALR FIREHOSE ---
async def f1_signalr_client():
    global LIVE_DATA
    connection_data = '[{"name":"Streaming"}]'
    enc_conn = urllib.parse.quote(connection_data)

    try:
        print("Bootstrapping baseline data (Names, Colors, Session)...")
        session_url = "https://livetiming.formula1.com/static/SessionInfo.json"
        session_resp = await asyncio.to_thread(requests.get, session_url, headers=HEADERS, timeout=10)
        session_data = json.loads(session_resp.content.decode('utf-8-sig'))
        LIVE_DATA["SessionInfo"] = session_data
        
        path = session_data.get("Path", "")
        if path:
            for endpoint in ["DriverList", "TimingData", "TimingAppData"]:
                ep_url = f"https://livetiming.formula1.com/static/{path}{endpoint}.json"
                ep_resp = await asyncio.to_thread(requests.get, ep_url, headers=HEADERS, timeout=10)
                if ep_resp.status_code == 200:
                    data = json.loads(ep_resp.content.decode('utf-8-sig'))
                    
                    # 🚨 THE PERMANENT FIX: Self-Healing Memory Bank 🚨
                    if endpoint == "DriverList":
                        # Check if F1 gave us actual data or just a blank glitch
                        lines = data.get("Lines", data) if isinstance(data, dict) else {}
                        if lines: 
                            LIVE_DATA["DriverList"] = data
                            with open(DRIVER_BACKUP_FILE, "w") as f:
                                json.dump(data, f)
                            print("DriverList downloaded successfully and saved to local disk cache.")
                        else:
                            if os.path.exists(DRIVER_BACKUP_FILE):
                                print("F1 DriverList is blank! Rescuing names from local disk cache...")
                                with open(DRIVER_BACKUP_FILE, "r") as f:
                                    LIVE_DATA["DriverList"] = json.load(f)
                    else:
                        LIVE_DATA[endpoint] = data
        print("Bootstrap complete!")
    except Exception as e:
        print(f"Bootstrap skipped/failed: {e}")
        # Emergency fallback if the entire F1 API request crashed
        if os.path.exists(DRIVER_BACKUP_FILE) and not LIVE_DATA["DriverList"]:
            print("API offline. Loading DriverList from disk cache...")
            with open(DRIVER_BACKUP_FILE, "r") as f:
                LIVE_DATA["DriverList"] = json.load(f)

    while True:
        try:
            print("Negotiating with F1 SignalR Hub...")
            neg_url = f"https://livetiming.formula1.com/signalr/negotiate?clientProtocol=1.5&connectionData={enc_conn}"
            neg_resp = await asyncio.to_thread(requests.get, neg_url, headers=HEADERS, timeout=10)
            token = neg_resp.json()['ConnectionToken']
            enc_token = urllib.parse.quote(token)

            ws_url = f"wss://livetiming.formula1.com/signalr/connect?clientProtocol=1.5&transport=webSockets&connectionToken={enc_token}&connectionData={enc_conn}"

            async with websockets.connect(ws_url, additional_headers=HEADERS) as ws:
                start_url = f"https://livetiming.formula1.com/signalr/start?clientProtocol=1.5&transport=webSockets&connectionToken={enc_token}&connectionData={enc_conn}"
                await asyncio.to_thread(requests.get, start_url, headers=HEADERS, timeout=10)

                sub = {
                    "H": "Streaming",
                    "M": "Subscribe",
                    "A": [["SessionInfo", "TimingData", "TimingAppData", "Position.z", "RaceControlMessages", "DriverList"]],
                    "I": 1
                }
                await ws.send(json.dumps(sub))
                print("SignalR Successfully Connected & Subscribed!")

                while True:
                    msg = await ws.recv()
                    if not msg: continue
                    data = json.loads(msg)
                    if 'M' in data:
                        for m in data['M']:
                            if m.get('H') == 'Streaming' and m.get('M') == 'feed':
                                category = m['A'][0]
                                payload = m['A'][1]

                                if category == 'Position.z':
                                    payload = decode_f1_z(payload)
                                    category = 'Position'

                                if category == 'RaceControlMessages':
                                    if 'Messages' not in LIVE_DATA[category]:
                                        LIVE_DATA[category]['Messages'] = []
                                    if 'Messages' in payload:
                                        LIVE_DATA[category]['Messages'].extend(payload['Messages'])
                                elif category == 'DriverList':
                                    # If F1 updates the DriverList mid-session, save it to disk!
                                    update_dict(LIVE_DATA.setdefault(category, {}), payload)
                                    with open(DRIVER_BACKUP_FILE, "w") as f:
                                        json.dump(LIVE_DATA["DriverList"], f)
                                else:
                                    update_dict(LIVE_DATA.setdefault(category, {}), payload)
                    
                    await asyncio.sleep(0.01)

        except Exception as e:
            print(f"SignalR Disconnected: {e}. Reconnecting in 5s...")
            await asyncio.sleep(5)

# --- TASK 2: DASHBOARD BROADCASTER ---
async def data_engine():
    # Keep the rest exactly the same as your current file, we don't need to change data_engine
    pass

# Note: Since I didn't print data_engine to save space, make sure you don't delete your existing data_engine function!
# Just replace everything ABOVE data_engine with the code block above.
