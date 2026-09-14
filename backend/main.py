import asyncio
import json
import fastf1
import numpy as np
import os
import gc
import traceback
import websockets
import pandas as pd
import time
import aiohttp
import zlib
import base64
import warnings
from urllib.parse import quote
from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import uvicorn
import datetime

# Suppress FastF1 deprecation warnings to keep logs clean
warnings.simplefilter(action='ignore', category=FutureWarning)

app = FastAPI()

# Mount the F1_Frames directory for the landing page
app.mount("/F1_Frames", StaticFiles(directory="F1_Frames"), name="frames")

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

if not os.path.exists('f1cache'): os.makedirs('f1cache')
if not os.path.exists('replay_cache'): os.makedirs('replay_cache')

fastf1.Cache.enable_cache('f1cache')

LIVE_DATA = {
    "SessionInfo": {}, "TimingData": {}, "TimingAppData": {},
    "Position": {}, "RaceControlMessages": {"Messages": []}, "DriverList": {}
}

ALL_RACES = [
    (2026, "Japan"), (2026, "China"), (2026, "Australia"),
    (2025, "Abu Dhabi"), (2025, "Qatar"), (2025, "Las Vegas"), (2025, "Brazil"), (2025, "Mexico"),
    (2025, "United States"), (2025, "Singapore"), (2025, "Azerbaijan"), (2025, "Italy"), (2025, "Netherlands"), 
    (2025, "Hungary"), (2025, "Belgium"), (2025, "Great Britain"), (2025, "Austria"), (2025, "Canada"), 
    (2025, "Spain"), (2025, "Monaco"), (2025, "Emilia Romagna"), (2025, "Miami"), (2025, "Saudi Arabia"), 
    (2025, "Bahrain"), (2025, "Japan"), (2025, "China"), (2025, "Australia"),
    (2024, "Abu Dhabi"), (2024, "Qatar"), (2024, "Las Vegas"), (2024, "Brazil"), (2024, "Mexico"),
    (2024, "United States"), (2024, "Singapore"), (2024, "Azerbaijan"), (2024, "Italy"), (2024, "Netherlands"), 
    (2024, "Belgium"), (2024, "Hungary"), (2024, "Great Britain"), (2024, "Austria"), (2024, "Spain"), 
    (2024, "Canada"), (2024, "Monaco"), (2024, "Emilia Romagna"), (2024, "Miami"), (2024, "China"), 
    (2024, "Japan"), (2024, "Australia"), (2024, "Saudi Arabia"), (2024, "Bahrain")
]

CACHE_VERSION = 6
COMPUTE_CHUNK_SIZE = 300
RACES_PER_DAY = 4
PRECOMPUTE_INTERVAL_HOURS = 6
CACHE_STATUS = {}

def deep_update(mapping, update_dict):
    for k, v in update_dict.items():
        if isinstance(v, dict) and k in mapping and isinstance(mapping[k], dict):
            deep_update(mapping[k], v)
        else:
            mapping[k] = v
    return mapping

def cache_key(year, circuit): return f"{year}_{circuit.replace(' ', '_').replace('/', '-')}"
def cache_path(year, circuit): return os.path.join("replay_cache", f"{cache_key(year, circuit)}.jsonl")
def index_path(year, circuit): return os.path.join("replay_cache", f"{cache_key(year, circuit)}.index.json")
def meta_path(year, circuit): return os.path.join("replay_cache", f"{cache_key(year, circuit)}.meta.json")

def is_fully_cached(year, circuit):
    for p in [cache_path(year, circuit), index_path(year, circuit), meta_path(year, circuit)]:
        if not os.path.exists(p): return False
    try:
        if load_meta(year, circuit).get("cache_version", 1) < CACHE_VERSION: return False
    except: return False
    return True

def save_meta(year, circuit, meta: dict):
    meta["cache_version"] = CACHE_VERSION
    with open(meta_path(year, circuit), 'w') as f: json.dump(meta, f)

def load_meta(year, circuit) -> dict:
    with open(meta_path(year, circuit), 'r') as f: return json.load(f)

def load_index(year, circuit) -> dict:
    with open(index_path(year, circuit), 'r') as f: return {float(k): v for k, v in json.load(f).items()}

def append_frames_to_cache(year, circuit, frames_dict: dict, byte_index: dict):
    with open(cache_path(year, circuit), 'ab') as f:
        for t_sec in sorted(frames_dict.keys()):
            byte_index[t_sec] = f.tell()
            f.write((json.dumps({"t": t_sec, **frames_dict[t_sec]}) + '\n').encode('utf-8'))

def save_index(year, circuit, byte_index: dict):
    with open(index_path(year, circuit), 'w') as f: json.dump(byte_index, f)

def read_chunk_from_cache(year, circuit, start_sec, end_sec) -> list:
    frames = []
    try: idx = load_index(year, circuit)
    except: idx = {}
    try:
        with open(cache_path(year, circuit), 'rb') as f:
            seek_t = start_sec
            while seek_t >= 0 and seek_t not in idx: seek_t -= 0.25
            if seek_t >= 0 and seek_t in idx: f.seek(idx[seek_t])
            for raw_line in f:
                try:
                    obj = json.loads(raw_line)
                    t = obj['t']
                    if t < start_sec: continue
                    if t > end_sec: break
                    frames.append(obj)
                except: continue
    except: pass
    return frames

def get_cache_size_mb(year, circuit) -> float:
    total = sum(os.path.getsize(p) for p in [cache_path(year, circuit), index_path(year, circuit), meta_path(year, circuit)] if os.path.exists(p))
    return round(total / (1024*1024), 1)

def wipe_partial_cache(year, circuit):
    for p in [cache_path(year, circuit), index_path(year, circuit), meta_path(year, circuit)]:
        if os.path.exists(p): os.remove(p)

def load_custom_pitlane(year, circuit):
    try:
        with open('pitlanes.json', 'r') as f: data = json.load(f)
        key = f"{year}_{circuit}"
        if key in data: return data[key]['x'], data[key]['y']
    except: pass
    return [], []

def load_session_data(year, circuit, status_cb=None):
    def _s(m):
        if status_cb: status_cb(m)
    _s(f"Downloading {year} {circuit}...")
    session = fastf1.get_session(year, circuit, 'R')
    session.load(telemetry=False, laps=True, weather=False, messages=True)
    session.load(telemetry=True)

    start_time = session.session_start_time
    race_length_secs = int(session.laps['Time'].max().total_seconds())
    try: t0 = int(session.laps['LapStartTime'].dropna().min().total_seconds())
    except: t0 = 0

    _s("Extracting Map & Laps...")
    leader_times = session.laps.groupby('LapNumber')['Time'].min().dt.total_seconds().to_dict()
    leader_start_times = session.laps.groupby('LapNumber')['LapStartTime'].min().dt.total_seconds().to_dict()
    
    leader_laps = session.laps[session.laps['Position'] == 1]
    leader_times_arr = leader_laps['Time'].dt.total_seconds().dropna().values
    leader_times_arr.sort()
    total_laps_race = len(leader_times_arr)
    
    fastest_lap = session.laps.pick_fastest()
    tel = fastest_lap.get_telemetry()
    
    track_x, track_y = tel['X'].values.tolist(), tel['Y'].values.tolist()
    track_z = tel['Z'].values.tolist() if 'Z' in tel else [0] * len(track_x)
    
    custom_px, custom_py = load_custom_pitlane(year, circuit)
    pit_x, pit_y = (custom_px, custom_py) if len(custom_px) > 0 else ([], [])

    _s("Finalizing Drivers...")
    driver_profiles, driver_pos_cache, driver_laps_cache = {}, {}, {}
    race_end_time = leader_times_arr[-1] if len(leader_times_arr) > 0 else 999999

    for drv in session.drivers:
        try:
            info = session.get_driver(drv)
            grid_pos = int(info.get('GridPosition', 20)) if pd.notna(info.get('GridPosition', 20)) else 20
            if grid_pos == 0: grid_pos = 20
            pos_data = session.pos_data[drv]
            drv_laps = session.laps.pick_driver(drv)
            valid_lap_times = drv_laps['Time'].dt.total_seconds().dropna().values

            is_dns = len(valid_lap_times) == 0 or (len(valid_lap_times) <= 2 and valid_lap_times[-1] < t0 + 120)
            if is_dns: continue

            driver_profiles[drv] = {
                "name": str(info['Abbreviation']), "num": str(drv),
                "grid": grid_pos, "color": "#" + str(info['TeamColor']).replace('#', '')
            }

            driver_pos_cache[drv] = {
                'time': pos_data['Time'].dt.total_seconds().values,
                'x': pos_data['X'].values, 'y': pos_data['Y'].values,
                'z': pos_data['Z'].values if 'Z' in pos_data else np.zeros_like(pos_data['X'].values)
            }
            
            pit_ins = drv_laps['PitInTime'].dropna().dt.total_seconds().values
            pit_outs = drv_laps['PitOutTime'].dropna().dt.total_seconds().values
            pit_intervals = [(p_in, pit_outs[pit_outs > p_in][0] if len(pit_outs[pit_outs > p_in]) > 0 else p_in + 60) for p_in in pit_ins]

            driver_laps_cache[drv] = {
                'times': drv_laps['Time'].dt.total_seconds().values,
                'start_times': drv_laps['LapStartTime'].dt.total_seconds().values,
                'pos': drv_laps['Position'].values,
                'compounds': drv_laps['Compound'].values,
                'life': drv_laps['TyreLife'].values,
                'pits': pd.notna(drv_laps['PitOutTime']).values,
                'pit_intervals': pit_intervals,
                'total_laps': len(drv_laps)
            }
        except: continue

    _s("Generating Race Control Messages...")
    rcms = []
    for _, row in session.race_control_messages.iterrows():
        try:
            t = row.get('Time') if pd.notna(row.get('Time')) else row.get('Date')
            if pd.isna(t): continue
            ft = int(t.total_seconds()) if hasattr(t, 'total_seconds') else int(t.timestamp() - start_time.timestamp())
            rcms.append({"frame": ft, "msg": str(row['Message'])})
        except: continue

    for drv, l_cache in driver_laps_cache.items():
        valid_t = [x for x in l_cache['times'] if pd.notna(x)]
        if valid_t and (race_end_time - valid_t[-1]) > 300:
            rcms.append({"frame": int(valid_t[-1] + 10), "msg": f"RETIREMENT: {driver_profiles.get(drv, {}).get('name', 'Unknown')} has stopped."})

    rcms.sort(key=lambda x: x['frame'])

    return {
        "race_length_secs": race_length_secs, "t0": t0, "track_x": track_x, "track_y": track_y, "track_z": track_z,
        "pit_x": pit_x, "pit_y": pit_y, "rcms": rcms, "leader_times": leader_times,
        "leader_start_times": leader_start_times, "leader_times_arr": leader_times_arr,
        "total_laps_race": total_laps_race,
        "driver_profiles": driver_profiles, "driver_pos_cache": driver_pos_cache, 
        "driver_laps_cache": driver_laps_cache
    }

def compute_frames_for_range(sd, t_start, t_end) -> dict:
    frames = {}
    leader_times, leader_start_times, leader_times_arr = sd["leader_times"], sd["leader_start_times"], sd["leader_times_arr"]
    driver_profiles, driver_pos_cache, driver_laps_cache = sd["driver_profiles"], sd["driver_pos_cache"], sd["driver_laps_cache"]
    pit_x, pit_y, t0 = sd["pit_x"], sd["pit_y"], sd["t0"]
    race_end_time = leader_times_arr[-1] if len(leader_times_arr) > 0 else 999999

    steps = int((t_end - t_start) * 4) + 1
    
    for step in range(steps):
        t_sec = round(t_start + step * 0.25, 2)
        frame_data = {"telemetry": [], "tower": []}
        temp_tower = []
        leader_laps_completed = int(np.searchsorted(leader_times_arr, t_sec, side='right'))

        for drv, profile in driver_profiles.items():
            l = driver_laps_cache.get(drv)
            p = driver_pos_cache.get(drv)
            if not l or not p or l['total_laps'] == 0: continue
            
            valid_times = [x for x in l['times'] if pd.notna(x)]
            last_lap_t = valid_times[-1] if valid_times else t0
            is_dnf = (race_end_time - last_lap_t) > 300
            
            if is_dnf and t_sec > last_lap_t + 45 and len(p['time']) > 0:
                cx_now, cy_now = float(np.interp(t_sec, p['time'], p['x'])), float(np.interp(t_sec, p['time'], p['y']))
                cx_past, cy_past = float(np.interp(t_sec - 30, p['time'], p['x'])), float(np.interp(t_sec - 30, p['time'], p['y']))
                if (cx_now - cx_past)**2 + (cy_now - cy_past)**2 < 1000: continue

            completed = int(np.searchsorted(l['times'], t_sec, side='right'))
            is_finished = completed >= l['total_laps']
            idx = min(completed, l['total_laps'] - 1)
            is_stopped = is_dnf and t_sec > last_lap_t
            math_t = last_lap_t if (is_finished and not is_dnf) else t_sec

            compound = str(l['compounds'][idx])[0] if str(l['compounds'][idx]) != 'nan' else 'U'
            tyre_age = int(l['life'][idx]) if not np.isnan(l['life'][idx]) else 0
            stops = int(l['pits'][idx])

            T_next = l['times'][idx]
            T_prev = l['start_times'][idx]
            if np.isnan(T_prev): T_prev = t0 if idx == 0 else l['times'][idx-1]
            if np.isnan(T_next): T_next = math_t + 100

            target_lap = idx + 1
            lead_T_next = leader_times.get(target_lap, race_end_time)
            lead_T_prev = leader_start_times.get(target_lap, t0)
            
            gap_next, gap_prev = max(0.0, T_next - lead_T_next), max(0.0, T_prev - lead_T_prev)
            if idx == 0: gap_prev = profile['grid'] * 1.5

            denom = max(0.1, T_next - T_prev)
            fraction = max(0.0, min(1.0, (math_t - T_prev) / denom))
            dynamic_gap = gap_prev + fraction * (gap_next - gap_prev)

            laps_down = max(0, leader_laps_completed - completed)
            sort_gap = dynamic_gap + (laps_down * 1000.0) + (50000.0 if is_stopped else 0)

            if len(p['time']) > 0:
                in_pit_lane = False
                cz = float(np.interp(math_t, p['time'], p['z'])) if 'z' in p else 0.0
                
                for (p_in, p_out) in l.get('pit_intervals', []):
                    if p_in <= math_t <= p_out:
                        in_pit_lane = True
                        if len(pit_x) > 0:
                            frac = max(0.0, min(1.0, (math_t - p_in) / max(0.1, p_out - p_in)))
                            idx_f = frac * (len(pit_x) - 1)
                            i1, i2 = int(np.floor(idx_f)), min(int(np.floor(idx_f)) + 1, len(pit_x) - 1)
                            sub = idx_f - i1
                            cx, cy = pit_x[i1] + sub * (pit_x[i2] - pit_x[i1]), pit_y[i1] + sub * (pit_y[i2] - pit_y[i1])
                        else:
                            cx, cy = float(np.interp(math_t, p['time'], p['x'])), float(np.interp(math_t, p['time'], p['y']))
                        break
                
                if not in_pit_lane: cx, cy = float(np.interp(math_t, p['time'], p['x'])), float(np.interp(math_t, p['time'], p['y']))
                frame_data["telemetry"].append({"name": profile["name"], "color": profile["color"], "x": cx, "y": cy, "z": cz})

            tc = ("#ffffff" if compound == 'H' else "#ffe600" if compound == 'M' else "#ff2a2a" if compound == 'S' else "#00e640" if compound == 'I' else "#00aaff")
            temp_tower.append({
                "name": profile["name"], "num": profile["num"], "color": profile["color"],
                "dynamic_gap": dynamic_gap, "sort_gap": sort_gap, "laps_comp": completed, 
                "is_stopped": is_stopped, "is_finished": (is_finished and not is_dnf),
                "tyre_color": tc, "tyre_age": tyre_age, "stops": stops
            })

        temp_tower.sort(key=lambda x: x['sort_gap'])
        
        if temp_tower:
            p1_gap, p1_laps = temp_tower[0]['dynamic_gap'], temp_tower[0]['laps_comp']
            pos = 1
            for car in temp_tower:
                gap_to_p1 = car['dynamic_gap'] - p1_gap
                laps_down = p1_laps - car['laps_comp']
                
                if car['is_stopped']: gap_str = "OUT"
                elif pos == 1: gap_str = "Winner" if car['is_finished'] else "Leader"
                elif laps_down >= 1 and gap_to_p1 > 70: gap_str = f"+{laps_down} LAP"
                else: gap_str = f"+{gap_to_p1:.3f}"

                frame_data["tower"].append({
                    "name": car["name"], "num": car["num"], "color": car["color"],
                    "pos": pos, "gap_secs": gap_to_p1 + (max(0, laps_down) * 85.0), "gap_str": gap_str,
                    "tyre_color": car["tyre_color"], "tyre_age": car["tyre_age"], "stops": car["stops"], "laps_comp": car["laps_comp"]
                })
                pos += 1

        frames[t_sec] = frame_data
    return frames

def compute_and_cache_race(year, circuit, status_cb=None, trigger_type="Auto (Background)"):
    compute_start = time.time()
    wipe_partial_cache(year, circuit)
    sd = load_session_data(year, circuit, status_cb=status_cb)
    byte_index = {}

    for chunk_start in range(sd["t0"], sd["race_length_secs"] + 1, COMPUTE_CHUNK_SIZE):
        chunk_end = min(chunk_start + COMPUTE_CHUNK_SIZE - 1, sd["race_length_secs"])
        frames = compute_frames_for_range(sd, chunk_start, chunk_end)
        append_frames_to_cache(year, circuit, frames, byte_index)
        del frames
        gc.collect()

    save_index(year, circuit, byte_index)
    save_meta(year, circuit, {
        "year": year, "circuit": circuit, "t0": sd["t0"], "total_seconds": sd["race_length_secs"],
        "total_laps": sd["total_laps_race"],
        "track": {"x": sd["track_x"], "y": sd["track_y"], "z": sd["track_z"], "px": sd["pit_x"], "py": sd["pit_y"]},
        "rcm": sd["rcms"],
        "compute_meta": {"trigger": trigger_type, "duration": round(time.time() - compute_start, 1), "timestamp": time.strftime("%b %d, %Y %H:%M")}
    })

async def background_precompute():
    await asyncio.sleep(15)
    races_done_today = 0
    for (year, circuit) in ALL_RACES:
        if is_fully_cached(year, circuit): continue
        if races_done_today >= RACES_PER_DAY:
            await asyncio.sleep(max(3600, (24 - races_done_today * PRECOMPUTE_INTERVAL_HOURS) * 3600))
            races_done_today = 0
        try:
            CACHE_STATUS[cache_key(year, circuit)] = "computing"
            await asyncio.to_thread(compute_and_cache_race, year, circuit, None, "Auto (Background)")
            CACHE_STATUS[cache_key(year, circuit)] = "done"
            races_done_today += 1
        except:
            CACHE_STATUS[cache_key(year, circuit)] = "failed"
            continue 
        if races_done_today < RACES_PER_DAY: await asyncio.sleep(PRECOMPUTE_INTERVAL_HOURS * 3600)

def _fmt_time(td):
    if pd.isna(td): return "-"
    return f"{int(td.total_seconds()//60)}:{td.total_seconds()%60:06.3f}"

def _extract_race(year, event, session_name='Race'):
    try:
        s = fastf1.get_session(year, event, session_name)
        s.load(telemetry=False, laps=True, weather=False, messages=False)
        res = []
        fastest_driver = None
        try: fastest_driver = s.laps.pick_fastest()['DriverNumber']
        except: pass
        for index, (_, row) in enumerate(s.results.iterrows()):
            drv = row['DriverNumber']
            stints, stops = [], 0
            try:
                drv_laps = s.laps.pick_driver(drv)
                if not drv_laps.empty:
                    for _, group in drv_laps.groupby('Stint'):
                        compound = group['Compound'].iloc[0]
                        compound = str(compound)[0] if pd.notna(compound) else 'U'
                        stints.append({"tyre": compound, "laps": len(group)})
                    stops = max(0, len(stints) - 1)
            except: pass
            
            # Position Fallback
            pos = row.get('Position')
            if pd.isna(pos): pos = row.get('ClassifiedPosition')
            try: pos = int(float(pos))
            except: pos = index + 1

            status = str(row.get('Status', ''))
            is_dsq = (status == 'Disqualified')

            if pos == 1: 
                gap_str = "Winner"
            elif is_dsq:
                gap_str = "DSQ"
            elif "Lap" in status:
                gap_str = status
            else:
                gap = row.get('Time')
                if pd.isna(gap): gap_str = status
                else: gap_str = f"+{gap.total_seconds():.3f}s"
                
            res.append({"pos": pos, "driver": str(row['Abbreviation']), "color": "#" + str(row['TeamColor']).replace('#', ''), "grid": int(row['GridPosition']) if pd.notna(row['GridPosition']) else 0, "gap": gap_str, "stops": stops, "fastest": (drv == fastest_driver), "stints": stints, "is_dsq": is_dsq})
        return res
    except: return []

def _extract_quali(year, event, session_name):
    """Segmented extraction with manual telemetry fallbacks for Sprint Qualifying"""
    try:
        # Step 1: Force accurate identifier for Sprints vs Normal Quali
        identifier = 'SQ' if 'Sprint' in session_name or 'SQ' in session_name else 'Q'
        s = fastf1.get_session(year, event, identifier)
        # Step 2: Critical Fix - messages=True allows segmented Q1/Q2/Q3 calculation
        s.load(telemetry=False, laps=True, weather=False, messages=True)
        
        res = []
        for index, (_, row) in enumerate(s.results.iterrows()):
            # Position Fallback
            pos = row.get('Position')
            if pd.isna(pos): pos = row.get('ClassifiedPosition')
            try: pos = int(float(pos))
            except: pos = index + 1

            status = str(row.get('Status', ''))
            is_dsq = (status == 'Disqualified')
            
            # Segment Column Logic (standard Q1 vs Sprint SQ1)
            cols = s.results.columns
            if 'Q1' in cols:
                q1, q2, q3 = _fmt_time(row.get('Q1')), _fmt_time(row.get('Q2')), _fmt_time(row.get('Q3'))
            elif 'SQ1' in cols:
                q1, q2, q3 = _fmt_time(row.get('SQ1')), _fmt_time(row.get('SQ2')), _fmt_time(row.get('SQ3'))
            elif 'BestLapTime' in cols:
                q1, q2, q3 = _fmt_time(row.get('BestLapTime')), "-", "-"
            else:
                q1, q2, q3 = "-", "-", "-"

            res.append({
                "pos": pos, 
                "driver": str(row['Abbreviation']), 
                "color": "#" + str(row['TeamColor']).replace('#', ''), 
                "q1": q1, "q2": q2, "q3": q3, 
                "is_dsq": is_dsq
            })
        return res
    except: return []

def _extract_fp_top3(year, event, session_name):
    try:
        s = fastf1.get_session(year, event, session_name)
        s.load(telemetry=False, laps=True, weather=False, messages=False)
        res = []
        results_df = s.results.dropna(subset=['Position']).sort_values(by='Position') if 'Position' in s.results else s.results.head(3)
        for _, row in results_df.head(3).iterrows():
            pos = row.get('Position')
            pos = int(pos) if pd.notna(pos) else 99
            res.append({"pos": pos, "driver": str(row['Abbreviation']), "color": "#" + str(row['TeamColor']).replace('#', ''), "time": _fmt_time(row.get('BestLapTime'))})
        return res
    except: return []

def _extract_fp_full(year, event, session_name):
    try:
        s = fastf1.get_session(year, event, session_name)
        s.load(telemetry=False, laps=True, weather=False, messages=False)
        res = []
        sorted_results = s.results.sort_values(by='Position')
        for _, row in sorted_results.iterrows():
            res.append({"pos": int(row['Position']) if pd.notna(row['Position']) else 99, "driver": str(row['Abbreviation']), "color": "#" + str(row['TeamColor']).replace('#', ''), "time": _fmt_time(row.get('BestLapTime')), "gap": str(row.get('Status', ''))})
        return res
    except: return []

def _generate_and_save_recap():
    print("[RECAP] Building Recap Data...")
    now = pd.Timestamp.utcnow().tz_localize(None)
    schedule = fastf1.get_event_schedule(now.year)
    past_events = schedule[schedule['Session1DateUtc'] + pd.Timedelta(hours=2, minutes=30) < now]
    if past_events.empty: return
    event = past_events.iloc[-1]
    sessions_finished = []
    for i in range(1, 6):
        date_col = f'Session{i}DateUtc'
        if date_col in event and pd.notna(event[date_col]):
            if event[date_col] + pd.Timedelta(hours=2, minutes=30) < now:
                sessions_finished.append(event[f'Session{i}'])
    if not sessions_finished: return
    last_session = sessions_finished[-1]
    is_off_week = (last_session == "Race")
    recap = {"is_recap": True, "recap_mode": "off_week" if is_off_week else "mid_weekend", "event_name": event['EventName'], "last_session": last_session, "timestamp": str(now)}
    
    if is_off_week:
        recap["race"] = _extract_race(now.year, event['EventName'], 'Race')
        recap["quali"] = _extract_quali(now.year, event['EventName'], 'Qualifying')
        
        fp_mapping = {'Practice 1': 'fp1', 'Practice 2': 'fp2', 'Practice 3': 'fp3'}
        for s_name in sessions_finished:
            if s_name in ['Sprint Shootout', 'Sprint Qualifying', 'SQ']:
                recap["sprint_quali"] = _extract_quali(now.year, event['EventName'], s_name)
            elif s_name == 'Sprint':
                recap["sprint"] = _extract_race(now.year, event['EventName'], s_name)
            elif s_name in fp_mapping:
                recap[fp_mapping[s_name]] = _extract_fp_top3(now.year, event['EventName'], s_name)
    else:
        if last_session in ['Qualifying', 'Sprint Shootout', 'Sprint Qualifying', 'SQ']:
            recap["results"] = _extract_quali(now.year, event['EventName'], last_session)
            recap["session_type"] = "Qualifying"
        elif last_session in ['Race', 'Sprint']:
            recap["results"] = _extract_race(now.year, event['EventName'], last_session)
            recap["session_type"] = "Race"
        else:
            recap["results"] = _extract_fp_full(now.year, event['EventName'], last_session)
            recap["session_type"] = "Practice"
            
    with open("recap_cache.json", "w") as f: json.dump(recap, f)
    print("[RECAP] Recap Data Saved!")

async def build_recap_data():
    while True:
        try: await asyncio.to_thread(_generate_and_save_recap)
        except Exception as e: print(f"[RECAP] Error generating recap: {e}")
        await asyncio.sleep(1800)

@app.get("/")
def read_root():
   with open("landing.html", "r") as f: return HTMLResponse(content=f.read())
@app.get("/session-timing")
def read_session_timing():
   with open("index.html", "r") as f: return HTMLResponse(content=f.read())
@app.get("/replay")
def read_replay():
   with open("replay.html", "r") as f: return HTMLResponse(content=f.read())
@app.get("/mapper")
def read_mapper():
   with open("mapper.html", "r") as f: return HTMLResponse(content=f.read())
@app.get("/test")
def read_test():
   with open("test.html", "r") as f: return HTMLResponse(content=f.read())

@app.get("/api/session")
def get_session(): 
    session_info = LIVE_DATA.get("SessionInfo", {}).copy()
    timing_data = LIVE_DATA.get("TimingData", {})
    has_live_timing = bool(timing_data and timing_data.get("Lines"))
    real_status = session_info.get("SessionStatus", "Offline")
    is_offline = real_status in ["Offline", "Finished"] or not session_info
    
    if has_live_timing and real_status != "Finished":
        is_offline = False
        session_info["SessionStatus"] = "Active"
        session_info["Type"] = session_info.get("Type", "Practice")
        
    if is_offline and os.path.exists("recap_cache.json"):
        if not has_live_timing:
            try:
                with open("recap_cache.json", "r") as f: session_info["RecapData"] = json.load(f)
            except: pass
    return session_info

@app.get("/api/timing")
def get_timing(): return LIVE_DATA.get("TimingData", {})
@app.get("/api/timingapp")
def get_timing_app(): return LIVE_DATA.get("TimingAppData", {})
@app.get("/api/messages")
def get_messages(): return LIVE_DATA.get("RaceControlMessages", {})
@app.get("/api/drivers")
def get_drivers(): return LIVE_DATA.get("DriverList", {})
@app.get("/api/cache-status")
def get_cache_status():
    by_year = {}
    for (year, circuit) in ALL_RACES:
        by_year.setdefault(year, [])
        key = cache_key(year, circuit)
        if is_fully_cached(year, circuit):
            meta = load_meta(year, circuit)
            cm = meta.get("compute_meta", {"trigger": "Unknown", "duration": 0, "timestamp": "Unknown"})
            by_year[year].append({"circuit": circuit, "status": "cached", "size_mb": get_cache_size_mb(year, circuit), "trigger": cm["trigger"], "duration": cm["duration"], "timestamp": cm["timestamp"]})
        else:
            by_year[year].append({"circuit": circuit, "status": CACHE_STATUS.get(key, "pending"), "size_mb": 0, "trigger": "-", "duration": 0, "timestamp": "-"})
    return JSONResponse({str(y): by_year[y] for y in sorted(by_year.keys(), reverse=True)})

connected_clients = set()
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_clients.add(websocket)
    try:
        while True: await websocket.receive_text()
    except: connected_clients.discard(websocket)

@app.websocket("/ws/replay")
async def replay_ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    meta, year, circuit = None, None, None
    async def send(obj):
        try: await websocket.send_text(json.dumps(obj))
        except: pass
    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            action = msg.get("action")
            if action == "init":
                year, circuit = msg['year'], msg['circuit']
                if is_fully_cached(year, circuit):
                    await send({"type": "status", "message": "Loading from cache..."})
                    try:
                        meta = await asyncio.to_thread(load_meta, year, circuit)
                        await send({"type": "init_data", "track": meta["track"], "rcm": meta["rcm"], "total_seconds": meta["total_seconds"], "start_t0": meta["t0"], "total_laps": meta.get("total_laps", 50)})
                    except Exception: await send({"type": "error", "message": traceback.format_exc()})
                else:
                    status_log = []
                    def status_cb(m): status_log.append(m)
                    task = asyncio.create_task(asyncio.to_thread(compute_and_cache_race, year, circuit, status_cb, "User Requested (Slow Path)"))
                    last_sent = ""
                    while not task.done():
                        if status_log and status_log[-1] != last_sent:
                            last_sent = status_log[-1]
                            await send({"type": "status", "message": last_sent})
                        await asyncio.sleep(3)
                    try: task.result()
                    except Exception:
                        await send({"type": "error", "message": traceback.format_exc()})
                        continue
                    meta = await asyncio.to_thread(load_meta, year, circuit)
                    await send({"type": "init_data", "track": meta["track"], "rcm": meta["rcm"], "total_seconds": meta["total_seconds"], "start_t0": meta["t0"], "total_laps": meta.get("total_laps", 50)})
            elif action == "get_chunk":
                if meta is None: continue
                start_sec, end_sec = msg['start_sec'], min(msg['end_sec'], meta["total_seconds"])
                frames = await asyncio.to_thread(read_chunk_from_cache, year, circuit, start_sec, end_sec)
                frames_dict = {f["t"]: f for f in frames}
                padded = []
                steps = int((end_sec - start_sec) * 4) + 1
                for step in range(steps):
                    t_sec = round(start_sec + step * 0.25, 2)
                    padded.append(frames_dict.get(t_sec, {"t": t_sec, "telemetry": [], "tower": []}))
                await send({"type": "chunk_data", "start_sec": start_sec, "end_sec": end_sec, "frames": padded})
                del frames, padded
                gc.collect()
    except websockets.exceptions.ConnectionClosed: pass
    finally: meta = None; gc.collect()

def _build_fallback_roster():
    print("[ROSTER] Building offline decoder ring from FastF1 cache...")
    try:
        session = fastf1.get_session(2026, 'Japan', 'R')
        session.load(telemetry=False, weather=False, messages=False, laps=False)
        roster = {}
        for drv in session.drivers:
            try:
                info = session.get_driver(drv)
                roster[str(drv)] = {"Tla": str(info['Abbreviation']), "TeamColour": str(info['TeamColor']).replace('#', ''), "FirstName": str(info['FirstName']), "LastName": str(info['LastName']), "Line": int(drv)}
            except: pass
        if roster:
            LIVE_DATA["DriverList"] = roster
            print("[ROSTER] Offline decoder ring successfully locked in!")
    except Exception as e:
        print(f"[ROSTER] Failed to build fallback roster: {e}")

async def f1_signalr_client():
    global LIVE_DATA
    url = 'https://livetiming.formula1.com/signalr'
    while True:
        try:
            print("[SIGNALR] Negotiating connection with F1 Servers...")
            ws_headers = {"User-Agent": "BestHTTP"}
            async with aiohttp.ClientSession(headers=ws_headers) as session:
                async with session.get(f"{url}/negotiate?clientProtocol=1.5&connectionData=[%7B%22name%22:%22Streaming%22%7D]") as resp:
                    data = await resp.json()
                    token = quote(data['ConnectionToken'])
                    cookie_header = "; ".join([f"{key}={val.value}" for key, val in resp.cookies.items()])
                ws_url = f"wss://livetiming.formula1.com/signalr/connect?clientProtocol=1.5&transport=webSockets&connectionToken={token}&connectionData=[%7B%22name%22:%22Streaming%22%7D]"
                connect_headers = {"User-Agent": "BestHTTP"}
                if cookie_header:
                    connect_headers["Cookie"] = cookie_header
                async with websockets.connect(ws_url, additional_headers=connect_headers) as ws:
                    print("[SIGNALR] Connected! Subscribing to live telemetry feeds...")
                    sub_msg = {"H": "Streaming", "M": "Subscribe", "A": [["Heartbeat", "CarData.z", "Position.z", "ExtrapolatedClock", "TopThree", "RcmSeries", "TimingStats", "TimingAppData", "WeatherData", "TrackStatus", "DriverList", "RaceControlMessages", "SessionInfo", "SessionData", "LapCount", "TimingData"]], "I": 1}
                    await ws.send(json.dumps(sub_msg))
                    while True:
                        msg = await ws.recv()
                        data = json.loads(msg)
                        if 'M' in data:
                            for m in data['M']:
                                if m['A']:
                                    feed, content = m['A'][0], m['A'][1]
                                    if type(content) == str and len(content) > 50 and feed in ['CarData.z', 'Position.z']:
                                        try:
                                            content = json.loads(zlib.decompress(base64.b64decode(content), -zlib.MAX_WBITS))
                                            feed = feed.replace('.z', '')
                                        except: pass
                                    if feed in LIVE_DATA:
                                        if type(content) == dict: deep_update(LIVE_DATA[feed], content)
                                        elif type(content) == list: LIVE_DATA[feed] = content
                                    out_msg = json.dumps({"type": "live_update", feed: content})
                                    for client in list(connected_clients):
                                        try: await client.send_text(out_msg)
                                        except: pass
        except Exception as e:
            print(f"[SIGNALR] Disconnected or Error: {e}. Retrying in 5s...")
            await asyncio.sleep(5)

async def data_engine(): pass

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(asyncio.to_thread(_build_fallback_roster))
    asyncio.create_task(f1_signalr_client())
    asyncio.create_task(data_engine())
    asyncio.create_task(background_precompute())
    asyncio.create_task(build_recap_data())
    print("[STARTUP] Server ready. Background tasks launched.")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=10000)
