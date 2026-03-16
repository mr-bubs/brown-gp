import asyncio
import json
import requests
import fastf1
import numpy as np
from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse
import uvicorn
import os

app = FastAPI()

if not os.path.exists('f1cache'):
    os.makedirs('f1cache')
fastf1.Cache.enable_cache('f1cache')

@app.get("/")
async def serve_dashboard():
    with open("index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())

def get_track_background(year, circuit, session_name):
    try:
        session = fastf1.get_session(year, circuit, session_name)
        session.load(telemetry=True, laps=True, weather=False, messages=False)
        fastest_lap = session.laps.pick_fastest()
        tel = fastest_lap.get_telemetry()
        total_laps = getattr(session, 'total_laps', None)
        return tel['X'].values.tolist(), tel['Y'].values.tolist(), total_laps
    except Exception:
        return [], [], None

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
        while True:
            await websocket.receive_text()
    except:
        connected_clients.remove(websocket)

async def data_engine():
    track_x, track_y, total_laps = [], [], None
    live_circuit = ""
    
    # NEW: Caches to prevent API throttling!
    last_heavy_fetch = 0
    drivers_dict, timing_data, app_data, messages = {}, {}, {}, []
    session_title = "📍 Waiting for Session..."
    lap_display_str = "Lap ?"
    
    while True:
        now = time.time()
        try:
            # --- THE FAST LOOP (20 fps) ---
            # ONLY fetch GPS coordinates at high speed
            pos_data = requests.get("http://localhost:10101/api/v1/live-timing/Position").json()
            raw_pos = pos_data.get('Position', pos_data)
            latest_update = raw_pos[-1] if isinstance(raw_pos, list) and len(raw_pos) > 0 else raw_pos
            cars_pos = latest_update.get('Entries', {}) if isinstance(latest_update, dict) else {}

            # --- THE SLOW LOOP (2 fps) ---
            # Fetch the heavy tyre/gap/message data safely twice a second
            if now - last_heavy_fetch > 0.5:
                session_req = requests.get("http://localhost:10101/api/v1/live-timing/SessionInfo").json()
                info = session_req.get('SessionInfo', session_req)
                new_circuit = info.get('Meeting', {}).get('Name', 'Bahrain')
                live_session = info.get('Name', 'Race')
                live_year = int(info.get('StartDate', '2024')[:4])
                
                if new_circuit != live_circuit:
                    live_circuit = new_circuit
                    track_x, track_y, total_laps = get_track_background(live_year, live_circuit, live_session)

                if live_year == 2026:
                    session_title = f"{live_circuit} | {live_session}"
                else:
                    session_title = f"{live_circuit}, {live_year} | {live_session}"

                if not drivers_dict:
                    driver_req = requests.get("http://localhost:10101/api/v1/live-timing/DriverList").json()
                    drivers_dict = driver_req.get('Lines', driver_req)
                
                timing_req = requests.get("http://localhost:10101/api/v1/live-timing/TimingData").json()
                timing_data = timing_req.get('Lines', timing_req)

                try:
                    app_req = requests.get("http://localhost:10101/api/v1/live-timing/TimingAppData").json()
                    app_data = app_req.get('Lines', app_req)
                except: app_data = {}

                try:
                    rcm_req = requests.get("http://localhost:10101/api/v1/live-timing/RaceControlMessages").json()
                    messages = rcm_req.get('Messages', rcm_req.get('RaceControlMessages', {}).get('Messages', []))
                except: messages = []

                last_heavy_fetch = now

            # --- BUILD THE PAYLOADS ---
            tower_payload = []
            map_drivers_payload = []
            current_lap = 0

            for car_num, driver_info in drivers_dict.items():
                if not isinstance(driver_info, dict) or 'Tla' not in driver_info: continue
                
                car_str = str(car_num)
                drv_name = driver_info.get('Tla', 'UNK') 
                clean_color = f"#{driver_info.get('TeamColour', 'A9A9A9')}".replace('##', '#')
                
                car_timing = timing_data.get(car_str, {})
                if car_timing.get('Retired') or car_timing.get('Stopped'): continue
                
                driver_lap = car_timing.get('NumberOfLaps', 0)
                if driver_lap and str(driver_lap).isdigit(): 
                    current_lap = max(current_lap, int(driver_lap))
                
                pos = int(car_timing.get('Position', 99))
                raw_gap = car_timing.get('GapToLeader', '')
                raw_interval = car_timing.get('IntervalToPositionAhead', {}).get('Value', '')
                
                if pos == 1:
                    gap_seconds, interval_str, gap_str = 0.0, "Leader", ''
                else:
                    gap_seconds = parse_gap(raw_gap)
                    interval_str = f"+{str(raw_interval).replace('+', '').strip() if raw_interval else str(raw_gap).replace('+', '').strip()}"
                    gap_str = f"+{str(raw_gap).replace('+', '').strip()}"

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

                car_map_data = cars_pos.get(car_str, {})
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
            for msg in reversed(messages):
                rcm_text = msg.get('Message', '...')
                raw_time = msg.get('Utc', '')
                time_str = raw_time.split('T')[1][:8] if 'T' in raw_time else ''
                rcm_payload.append({"time": time_str, "msg": rcm_text})

            state = {
                "session": {"title": session_title, "lap": lap_display_str},
                "track": {"x": track_x, "y": track_y},
                "map_drivers": map_drivers_payload,
                "tower": tower_payload,
                "rcm": rcm_payload
            }
            
            for client in connected_clients:
                await client.send_text(json.dumps(state))

        except Exception as e:
            print(f"Engine Error: {e}")
            
        await asyncio.sleep(0.05)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(data_engine())

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
