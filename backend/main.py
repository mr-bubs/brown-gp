import asyncio
import json
import requests
import fastf1
import numpy as np
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import uvicorn
import os
import time
import re

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

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

@app.get("/")
def read_root():
    return {"status": "Brown GP Middleman is Live!"}

# --- FIX #1: RESTORED THE MISSING API ROUTES ---
@app.get("/api/timing")
def get_timing():
    try:
        url = "https://livetiming.formula1.com/static/TimingData.json"
        response = requests.get(url, headers=HEADERS, timeout=2.0)
        return response.json()
    except Exception:
        return {"error": "Failed to fetch timing data"}

@app.get("/api/session")
def get_session():
    try:
        url = "https://livetiming.formula1.com/static/SessionInfo.json"
        response = requests.get(url, headers=HEADERS, timeout=2.0)
        return response.json()
    except Exception:
        return {"error": "Failed to fetch session info"}

# --- FIX #2: ISOLATING THE HEAVY FASTF1 MATH ---
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

async def fetch_api(endpoint):
    try:
        url = f"https://livetiming.formula1.com/static/{endpoint}.json"
        resp = await asyncio.to_thread(requests.get, url, headers=HEADERS, timeout=2.0)
        return resp.json()
    except: return {}

async def data_engine():
    track_x, track_y, track_dist, m_sectors, total_laps = [], [], [], [], None
    live_circuit = ""
    send_track = False
    
    last_heavy_fetch = 0
    drivers_dict, timing_data, app_data, messages = {}, {}, {}, []
    session_title, lap_display_str = "📍 Waiting...", "Lap ?"
    
    last_rcm_count = 0
    active_yellow_sectors = set()
    active_toast = ""
    
    while True:
        now = time.time()
        try:
            pos_data = await fetch_api("Position")
            raw_pos = pos_data.get('Position', pos_data)
            latest_update = raw_pos[-1] if isinstance(raw_pos, list) and len(raw_pos) > 0 else raw_pos
            cars_pos = latest_update.get('Entries', {}) if isinstance(latest_update, dict) else {}

            if now - last_heavy_fetch > 1.0: 
                endpoints = ["SessionInfo", "DriverList", "TimingData", "TimingAppData", "RaceControlMessages"]
                results = await asyncio.gather(*(fetch_api(ep) for ep in endpoints))
                session_req, driver_req, timing_req, app_req, rcm_req = results

                info = session_req.get('SessionInfo', session_req) if isinstance(session_req, dict) else {}
                if info:
                    new_circuit = info.get('Meeting', {}).get('Name', 'Bahrain')
                    live_session = info.get('Name', 'Race')
                    live_year = int(info.get('StartDate', '2024')[:4])
                    
                    if new_circuit and new_circuit != live_circuit:
                        print(f"Loading track map for {new_circuit} in background...")
                        live_circuit = new_circuit
                        # Fix #2 Implementation: Run FastF1 in the background!
                        track_x, track_y, track_dist, m_sectors, total_laps = await asyncio.to_thread(get_track_background_sync, live_year, live_circuit, live_session)
                        send_track = True

                    session_title = f"{live_circuit} | {live_session}" if live_year == 2026 else f"{live_circuit}, {live_year} | {live_session}"

                if driver_req: drivers_dict = driver_req.get('Lines', driver_req)
                if timing_req: timing_data = timing_req.get('Lines', timing_req)
                if app_req: app_data = app_req.get('Lines', app_req)
                
                if rcm_req: 
                    messages = rcm_req.get('Messages', rcm_req.get('RaceControlMessages', {}).get('Messages', []))

                last_heavy_fetch = now
                
                if isinstance(messages, list) and len(messages) > last_rcm_count:
                    new_msgs = messages[last_rcm_count:]
                    last_rcm_count = len(messages)
                    
                    for msg in new_msgs:
                        text = str(msg.get('Message', '')).upper()
                        if 'YELLOW IN TRACK SECTOR' in text or 'YELLOW IN SECTOR' in text:
                            match = re.search(r'SECTOR[S]? ([0-9]+(?: AND [0-9]+)?)', text)
                            if match:
                                nums = re.findall(r'\d+', match.group(1))
                                for n in nums: active_yellow_sectors.add(int(n))
                        if 'CLEAR' in text:
                            match = re.search(r'SECTOR[S]? ([0-9]+(?: AND [0-9]+)?)', text)
                            if match:
                                nums = re.findall(r'\d+', match.group(1))
                                for n in nums: 
                                    if int(n) in active_yellow_sectors: active_yellow_sectors.remove(int(n))
                            elif 'SECTOR' not in text: 
                                active_yellow_sectors.clear()
                                
                        toast_keywords = ['YELLOW', 'RED', 'BLACK AND WHITE', 'PENALTY', 'VIRTUAL SAFETY CAR', 'SAFETY CAR']
                        if any(kw in text for kw in toast_keywords):
                            active_toast = msg.get('Message', '')

            tower_payload, map_drivers_payload, current_lap = [], [], 0

            if isinstance(drivers_dict, dict):
                for car_num, driver_info in drivers_dict.items():
                    if not isinstance(driver_info, dict) or 'Tla' not in driver_info: continue
                    
                    car_str = str(car_num)
                    drv_name = driver_info.get('Tla', 'UNK') 
                    clean_color = f"#{driver_info.get('TeamColour', 'A9A9A9')}".replace('##', '#')
                    
                    car_timing = timing_data.get(car_str, {}) if isinstance(timing_data, dict) else {}
                    if car_timing.get('Retired') or car_timing.get('Stopped'): continue
                    
                    driver_lap = car_timing.get('NumberOfLaps', 0)
                    if driver_lap and str(driver_lap).isdigit(): current_lap = max(current_lap, int(driver_lap))
                    
                    pos = int(car_timing.get('Position', 99))
                    raw_gap = car_timing.get('GapToLeader', '')
                    raw_interval = car_timing.get('IntervalToPositionAhead', {}).get('Value', '')
                    
                    gap_seconds = 0.0 if pos == 1 else parse_gap(raw_gap)
                    interval_str = "Leader" if pos == 1 else f"+{str(raw_interval).replace('+', '').strip() if raw_interval else str(raw_gap).replace('+', '').strip()}"
                    gap_str = '' if pos == 1 else f"+{str(raw_gap).replace('+', '').strip()}"

                    tyre_color, tyre_age, pit_stops = '#ffffff', 0, 0
                    try:
                        stints = app_data.get(car_str, {}).get('Stints', [])
                        if stints and isinstance(stints, list):
                            pit_stops = max(0, len(stints) - 1) 
                            c = str(stints[-1].get('Compound', '')).upper()
                            if c == 'SOFT': tyre_color = '#FF0000'
                            elif c == 'MEDIUM': tyre_color = '#FFFF00'
                            elif c == 'HARD': tyre_color = '#FFFFFF'
                            elif c == 'INTERMEDIATE': tyre_color = '#00FF00'
                            elif c == 'WET': tyre_color = '#00BFFF'
                            raw_age = stints[-1].get('TotalLaps', stints[-1].get('Laps', 0))
                            tyre_age = int(raw_age) if str(raw_age).isdigit() else 0
                    except: pass

                    if pos != 99 and (pos == 1 or gap_seconds > 0.0):
                        tower_payload.append({
                            "car": car_str, "name": drv_name, "color": clean_color, "pos": pos,
                            "gap_secs": gap_seconds, "interval": interval_str, "gap": gap_str,
                            "t_color": tyre_color, "t_age": tyre_age, "stops": pit_stops
                        })

                    car_map_data = cars_pos.get(car_str, {}) if isinstance(cars_pos, dict) else {}
                    if 'X' in car_map_data and 'Y' in car_map_data:
                        map_drivers_payload.append({
                            "car_num": car_str, "name": drv_name, "x": car_map_data['X'], "y": car_map_data['Y'],
                            "color": clean_color, "t_color": tyre_color, "pos": str(pos) if pos != 99 else ""
                        })

            tower_payload.sort(key=lambda x: x['pos'])
            running_max = 0.0
            for d in tower_payload:
                if d['pos'] == 1: d['gap_secs'] = 0.0
                else:
                    if d['gap_secs'] <= running_max: d['gap_secs'] = running_max + 1.0 
                running_max = d['gap_secs']
                
            lap_display_str = f"Lap {current_lap}/{total_laps}" if total_laps else f"Lap {current_lap}"

            rcm_payload = []
            if isinstance(messages, list):
                for msg in reversed(messages):
                    rcm_text = msg.get('Message', '...')
                    raw_time = msg.get('Utc', '')
                    time_str = raw_time.split('T')[1][:8] if 'T' in raw_time else ''
                    rcm_payload.append({"time": time_str, "msg": rcm_text})

            state = {
                "session": {"title": session_title, "lap": lap_display_str},
                "map_drivers": map_drivers_payload,
                "tower": tower_payload,
                "rcm": rcm_payload,
                "yellow_sectors": list(active_yellow_sectors),
                "active_toast": active_toast
            }
            
            if send_track:
                state["track"] = {"x": track_x, "y": track_y, "dist": track_dist, "m_sectors": m_sectors}
                send_track = False
            else: state["track"] = {}
            
            active_toast = "" 
            
            for client in list(connected_clients):
                await client.send_text(json.dumps(state))

        except Exception as e: print(f"Engine Error: {e}")
        
        # --- FIX #3: SLOWED DOWN TO PREVENT IP BAN ---
        await asyncio.sleep(0.5) 

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(data_engine())

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=10000)
