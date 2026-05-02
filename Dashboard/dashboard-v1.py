import streamlit as st
import requests
import pandas as pd
import time
import matplotlib.pyplot as plt
import os
import fastf1 
import numpy as np
import re

# --- 1. SETUP & LAYOUT ---
st.set_page_config(page_title="Brown GP | LIVE", layout="wide")

# Custom HTML placeholder for the hovering Toast notification
toast_placeholder = st.empty()

st.title("Brown GP | Live Dashboard")
session_header = st.empty()

# --- MAIN LAYOUT (OUTSIDE THE LOOP) ---
left_col, right_col = st.columns([1, 2])

with left_col:
    st.subheader("Live Race Worm")
    tower_placeholder = st.empty() 

with right_col:
    st.subheader("Driver Tracker")
    sc1, sc2 = st.columns(2)
    with sc1:
        rot_val = st.slider("Track Rotation", min_value=0, max_value=360, value=0, step=5, key="rot_slider")
    with sc2:
        tilt_val = st.slider("X-Axis Tilt (Angle)", min_value=0, max_value=85, value=50, step=5, key="tilt_slider")
        
    tracker_placeholder = st.empty() 
    
    st.subheader("Race Control")
    # Anchor ID so the javascript toast knows where to scroll!
    st.markdown('<div id="race-control-board"></div>', unsafe_allow_html=True)
    with st.container(border=True, height=400):
        rcm_placeholder = st.empty() 

# --- CACHE & HELPERS ---
if not os.path.exists('f1cache'):
    os.makedirs('f1cache')
fastf1.Cache.enable_cache('f1cache')

def parse_gap(gap_str):
    if not gap_str: return 0.0
    clean_str = str(gap_str).replace('+', '').strip()
    if 'LAP' in clean_str.upper():
        try: return int(clean_str.split(' ')[0]) * 80.0 
        except: return 80.0
    try: return float(clean_str)
    except: return 0.0

@st.cache_data(show_spinner=False)
def get_track_background(year, circuit, session_name):
    try:
        session = fastf1.get_session(year, circuit, session_name)
        session.load(telemetry=True, laps=True, weather=False, messages=False)
        fastest_lap = session.laps.pick_fastest()
        
        # Switched to telemetry to get the Distance metric for mapping Marshal Sectors
        tel = fastest_lap.get_telemetry()
        track_x = tel['X'].values
        track_y = tel['Y'].values
        track_dist = tel['Distance'].values
        
        total_laps = getattr(session, 'total_laps', None)
        
        circuit_info = session.get_circuit_info()
        turn_x, turn_y, turn_num = [], [], []
        m_sectors = []
        
        if circuit_info is not None:
            if hasattr(circuit_info, 'corners'):
                turn_x = circuit_info.corners['X'].values
                turn_y = circuit_info.corners['Y'].values
                turn_num = circuit_info.corners['Number'].astype(str).values
            if hasattr(circuit_info, 'marshal_sectors'):
                # Extract the distances where each Marshal Sector begins
                for _, row in circuit_info.marshal_sectors.iterrows():
                    m_sectors.append({'Number': int(row['Number']), 'Distance': float(row['Distance'])})
                    
        return track_x, track_y, track_dist, total_laps, turn_x, turn_y, turn_num, m_sectors
    except Exception:
        return [], [], [], None, [], [], [], []

# --- VARIABLES FOR THE POP-UP TOAST ---
last_notified_msg = ""
toast_show_until = 0
current_toast_msg = ""
toast_keywords = ['YELLOW', 'RED FLAG', 'BLACK AND WHITE', 'PENALTY', 'VIRTUAL SAFETY CAR', 'SAFETY CAR']

# --- 2. THE LIVE LOOP ---
while True:
    # 1. FETCH SESSION & RACE CONTROL GLOBALLY
    try:
        session_req = requests.get("http://localhost:10101/api/v1/live-timing/SessionInfo")
        info = session_req.json().get('SessionInfo', session_req.json()) 
        live_year = int(info.get('StartDate', '2024')[:4]) 
        live_circuit = info.get('Meeting', {}).get('Name', info.get('Meeting', {}).get('Location', 'Bahrain'))
        live_session = info.get('Name', 'Race') 
        
        session_header.subheader(f"📍 {live_year} {live_circuit} | {live_session}")
        track_x, track_y, track_dist, total_laps, turn_x, turn_y, turn_num, m_sectors = get_track_background(live_year, live_circuit, live_session)
    except Exception:
        live_year, live_circuit, live_session = 2024, 'Bahrain', 'Race'
        session_header.subheader("📍 Waiting for Session Info...")
        track_x, track_y, track_dist, total_laps, turn_x, turn_y, turn_num, m_sectors = [], [], [], None, [], [], [], []

    try:
        rcm_req = requests.get("http://localhost:10101/api/v1/live-timing/RaceControlMessages")
        messages = rcm_req.json().get('Messages', rcm_req.json().get('RaceControlMessages', {}).get('Messages', []))
    except:
        messages = []

    # 2. PROCESS RACE CONTROL (FLAGS & TOAST)
    active_yellow_sectors = set()
    latest_rcm_text = ""
    
    if messages:
        latest_rcm_text = messages[-1].get('Message', '')
        
        # Regex to mathematically parse which sectors are currently yellow
        for msg in messages:
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
                elif 'SECTOR' not in text: # Global clear
                    active_yellow_sectors.clear()

    # Handle Custom Pop-Up Toast
    if latest_rcm_text != last_notified_msg:
        last_notified_msg = latest_rcm_text
        if any(kw in latest_rcm_text.upper() for kw in toast_keywords):
            current_toast_msg = latest_rcm_text
            toast_show_until = time.time() + 2.5 # Display toast for ~2.5 seconds
            
    if time.time() < toast_show_until:
        html_toast = f"""
        <div id="f1-toast" style="position: fixed; top: 70px; right: 20px; background-color: #222222; color: white; padding: 15px 20px; border-radius: 8px; border-left: 6px solid #FFD700; font-family: sans-serif; box-shadow: 0px 4px 15px rgba(0,0,0,0.6); z-index: 999999; display: flex; align-items: center; justify-content: space-between; max-width: 400px; cursor: pointer;">
            <div><strong style="font-size: 15px;">⚠️ Race Control</strong><br><span style="font-size: 13px;">{current_toast_msg}</span></div>
            <div onclick="document.getElementById('f1-toast').style.display='none'; event.stopPropagation();" style="font-size: 22px; font-weight: bold; margin-left: 20px; color: #888;">&times;</div>
        </div>
        <script>
            document.getElementById('f1-toast').onclick = function() {{
                document.getElementById('race-control-board').scrollIntoView({{behavior: 'smooth'}});
                this.style.display = 'none';
            }};
        </script>
        """
        toast_placeholder.markdown(html_toast, unsafe_allow_html=True)
    else:
        toast_placeholder.empty()

    # 3. GAP TOWER
    with tower_placeholder.container():
        try:
            driver_req = requests.get("http://localhost:10101/api/v1/live-timing/DriverList")
            timing_req = requests.get("http://localhost:10101/api/v1/live-timing/TimingData")
            try:
                app_data = requests.get("http://localhost:10101/api/v1/live-timing/TimingAppData").json()
                timing_app_dict = app_data.get('Lines', app_data)
            except: timing_app_dict = {}
                
            drivers_dict = driver_req.json().get('Lines', driver_req.json())
            timing_data = timing_req.json()
            
            if not isinstance(drivers_dict, dict) or len(drivers_dict) < 5:
                st.warning("⏳ Waiting for F1 to broadcast driver data...")
            else:
                active_drivers_data = []
                current_lap = 0 
                
                for car_num, driver_info in drivers_dict.items():
                    if not isinstance(driver_info, dict) or 'Tla' not in driver_info: continue
                        
                    drv_name = driver_info.get('Tla', 'UNK') 
                    clean_color = f"#{driver_info.get('TeamColour', 'A9A9A9')}".replace('##', '#')
                    
                    try:
                        car_timing = timing_data.get('Lines', {}).get(car_num, {})
                        driver_lap = car_timing.get('NumberOfLaps', 0)
                        if driver_lap and str(driver_lap).isdigit(): current_lap = max(current_lap, int(driver_lap))
                            
                        position = int(car_timing.get('Position', 99))
                        raw_gap = car_timing.get('GapToLeader', '')
                        raw_interval = car_timing.get('IntervalToPositionAhead', {}).get('Value', '')
                        
                        if position == 1:
                            gap_seconds, interval_str, gap_str = 0.0, "Leader", ''
                        else:
                            gap_seconds = parse_gap(raw_gap)
                            interval_str = f"+{str(raw_interval).replace('+', '').strip() if raw_interval else str(raw_gap).replace('+', '').strip()}"
                            gap_str = f"+{str(raw_gap).replace('+', '').strip()}"
                            
                        if position == 99 or (position > 1 and gap_seconds == 0.0): continue
                            
                        tyre_color, tyre_age, pit_stops = '#ffffff', 0, 0
                        try:
                            stints = timing_app_dict.get(car_num, {}).get('Stints', [])
                            if stints and isinstance(stints, list):
                                pit_stops = max(0, len(stints) - 1) 
                                compound = str(stints[-1].get('Compound', '')).upper()
                                if compound == 'SOFT': tyre_color = '#FF0000'
                                elif compound == 'MEDIUM': tyre_color = '#FFFF00'
                                elif compound == 'HARD': tyre_color = '#FFFFFF'
                                elif compound == 'INTERMEDIATE': tyre_color = '#00FF00'
                                elif compound == 'WET': tyre_color = '#00BFFF'
                                raw_age = stints[-1].get('TotalLaps', stints[-1].get('Laps', 0))
                                tyre_age = int(raw_age) if str(raw_age).isdigit() else 0
                        except: pass

                        active_drivers_data.append({
                            "car_num": car_num, "drv_name": drv_name, "team_color": clean_color,
                            "gap_seconds": gap_seconds, "position": position, "interval_str": interval_str,
                            "gap_str": gap_str, "tyre_color": tyre_color, "tyre_age": tyre_age, "pit_stops": pit_stops
                        })
                    except: continue
                
                if len(active_drivers_data) > 1:
                    sorted_by_pace = sorted(active_drivers_data, key=lambda x: x['position'])
                    
                    running_max_gap = 0.0
                    for data in sorted_by_pace:
                        if data['position'] == 1: data['gap_seconds'] = 0.0
                        else:
                            if data['gap_seconds'] <= running_max_gap:
                                int_s = parse_gap(data['interval_str'])
                                data['gap_seconds'] = running_max_gap + (int_s if int_s > 0 else 1.0)
                        running_max_gap = data['gap_seconds']
                        
                    max_gap = max([d['gap_seconds'] for d in sorted_by_pace]) if sorted_by_pace else 1.0
                    bar_thickness = max(1.5, max_gap * 0.025)
                    box_width = 2.4 
                    
                    last_box_bottom = -999.0
                    for data in sorted_by_pace:
                        box_top = data['gap_seconds'] - (bar_thickness / 2)
                        if box_top < (last_box_bottom + (bar_thickness * 0.02)):
                            actual_y = last_box_bottom + (bar_thickness * 0.02) + (bar_thickness / 2)
                        else: actual_y = data['gap_seconds']
                        data['draw_y'] = actual_y
                        last_box_bottom = actual_y + (bar_thickness / 2)

                    fig, ax = plt.subplots(figsize=(5, 10))
                    fig.patch.set_alpha(0.0)
                    ax.patch.set_alpha(0.0)
                    
                    ax.bar(0, last_box_bottom + (max_gap * 0.05), width=0.15, color='#333333', bottom=0, zorder=1)
                    
                    text_x_start = (box_width / 2) + 0.3
                    scatter_x = -(box_width / 2) - 0.5
                    text_x_left = scatter_x - 0.4

                    for data in sorted_by_pace:
                        y = data['draw_y']  
                        ax.bar(0, bar_thickness, bottom=y - (bar_thickness/2), width=box_width, color=data['team_color'], edgecolor='#111111', zorder=2)
                        ax.text(0, y, f"{data['drv_name']} {data['car_num']}", color='white', fontweight='heavy', ha='center', va='center', fontsize=10, zorder=3)
                        
                        timing_text = "P1 (Leader)" if data['position'] == 1 else f"P{data['position']} ({data['interval_str']}) ({data['gap_str']})"
                        ax.text(text_x_start, y, timing_text, color='white', fontweight='bold', va='center', ha='left', fontsize=10, zorder=3)

                        ax.scatter(scatter_x, y, color=data['tyre_color'], edgecolor='#000000', s=80, zorder=3)
                        ax.text(text_x_left, y, f"{data['tyre_age']} ({data['pit_stops']})", color='white', fontweight='bold', ha='right', va='center', fontsize=9, zorder=3)

                    ax.invert_yaxis()
                    ax.set_axis_off() 
                    ax.set_xlim(-box_width * 2.5, box_width * 4.0)
                    
                    lap_display = f"Lap {current_lap}/{total_laps}" if total_laps else f"Lap {current_lap}"
                    ax.set_title(f"Live Race Gaps ({lap_display})", fontdict={'family': 'sans-serif', 'weight': 'bold', 'size': 14, 'color': 'white'}, loc='left')
                    st.pyplot(fig) 
                    plt.close(fig) 
        except Exception as e: st.error(f"⚠️ Error loading tower: {e}")

    # 4. DRIVER TRACKER
    with tracker_placeholder.container():
        try:
            pos_data = requests.get("http://localhost:10101/api/v1/live-timing/Position").json()
            raw_pos = pos_data.get('Position', pos_data)
            latest_update = raw_pos[-1] if isinstance(raw_pos, list) and len(raw_pos) > 0 else raw_pos
            cars_pos = latest_update.get('Entries', {}) if isinstance(latest_update, dict) else {}
                
            if not cars_pos:
                st.write("⏳ Waiting for X/Y coordinates stream...")
            else:
                coords = []
                current_timing_dict = locals().get('timing_data', {}).get('Lines', {})

                for car_num, data in cars_pos.items():
                    if 'X' in data and 'Y' in data:
                        car_str = str(car_num)
                        if car_str == '243': continue 
                            
                        car_timing = current_timing_dict.get(car_str, {})
                        if car_timing.get('Retired') == True or car_timing.get('Stopped') == True: continue 
                            
                        edge_color = '#ffffff' 
                        if car_str in ['241', '242']:
                            clean_color, drv_name, pos_str = '#FFD700', "", "SC"
                        else:
                            current_drivers_dict = locals().get('drivers_dict', {})
                            clean_color = f"#{current_drivers_dict.get(car_str, {}).get('TeamColour', 'A9A9A9')}".replace('##', '#')
                            drv_name = current_drivers_dict.get(car_str, {}).get('Tla', car_str)
                            pos = car_timing.get('Position')
                            pos_str = str(pos) if pos and str(pos).isdigit() else ""
                            
                            try:
                                stints = locals().get('timing_app_dict', {}).get(car_str, {}).get('Stints', [])
                                if stints and isinstance(stints, list):
                                    c = str(stints[-1].get('Compound', '')).upper()
                                    if c == 'SOFT': edge_color = '#FF0000'
                                    elif c == 'MEDIUM': edge_color = '#FFFF00'
                                    elif c == 'INTERMEDIATE': edge_color = '#00FF00'
                                    elif c == 'WET': edge_color = '#00BFFF'
                            except: pass
                        
                        coords.append({"drv_name": drv_name, "pos_str": pos_str, "x": data['X'], "y": data['Y'], "color": clean_color, "edge_color": edge_color})
                        
                if coords:
                    df_coords = pd.DataFrame(coords)
                    cx = (np.max(track_x) + np.min(track_x)) / 2 if len(track_x) > 0 else 0
                    cy = (np.max(track_y) + np.min(track_y)) / 2 if len(track_y) > 0 else 0
                        
                    def tilt_coords(x_arr, y_arr, rot_deg, tilt_deg):
                        x_arr, y_arr = np.array(x_arr, dtype=float), np.array(y_arr, dtype=float)
                        rot_rad, tilt_rad = np.radians(rot_deg), np.radians(tilt_deg)
                        xs, ys = x_arr - cx, y_arr - cy
                        xr = xs * np.cos(rot_rad) - ys * np.sin(rot_rad)
                        yr = xs * np.sin(rot_rad) + ys * np.cos(rot_rad)
                        return xr, yr * np.cos(tilt_rad)

                    t_x_tilt, t_y_tilt = tilt_coords(track_x, track_y, rot_val, tilt_val)
                    d_x_tilt, d_y_tilt = tilt_coords(df_coords['x'].values, df_coords['y'].values, rot_val, tilt_val)
                    
                    fig_map, ax_map = plt.subplots(figsize=(8, 4))
                    fig_map.subplots_adjust(left=0, right=1, bottom=0, top=1)
                    fig_map.patch.set_alpha(0.0)
                    ax_map.patch.set_alpha(0.0)
                    
                    if len(t_x_tilt) > 0:
                        ax_map.plot(t_x_tilt, t_y_tilt - 300, color='#000000', linewidth=16, zorder=1) 
                        ax_map.plot(t_x_tilt, t_y_tilt, color='#333333', linewidth=14, zorder=2) 
                        ax_map.plot(t_x_tilt, t_y_tilt, color='#ffffff', linewidth=1, linestyle='--', zorder=3)
                        
                        # --- NEW: MAP YELLOW SECTORS ---
                        if m_sectors and active_yellow_sectors and len(track_dist) == len(t_x_tilt):
                            # Digitize the track points to find which marshal sector they belong to
                            m_sectors = sorted(m_sectors, key=lambda x: x['Distance'])
                            sector_dists = np.array([s['Distance'] for s in m_sectors])
                            sector_nums = np.array([s['Number'] for s in m_sectors])
                            
                            indices = np.digitize(track_dist, sector_dists) - 1
                            indices[indices < 0] = 0
                            track_m_sectors = sector_nums[indices]
                            
                            for s_num in active_yellow_sectors:
                                mask = track_m_sectors == s_num
                                if np.any(mask):
                                    ax_map.plot(t_x_tilt[mask], t_y_tilt[mask], color='#FFD700', linewidth=14, zorder=2.5)

                        # Turn Numbers
                        if len(turn_x) > 0:
                            turn_x_tilt, turn_y_tilt = tilt_coords(turn_x, turn_y, rot_val, tilt_val)
                            for j in range(len(turn_x)):
                                ax_map.text(turn_x_tilt[j] + 250, turn_y_tilt[j] + 250, str(turn_num[j]), color='#aaaaaa', fontsize=9, ha='center', va='center', fontweight='bold', zorder=2.8)

                    ax_map.scatter(d_x_tilt, d_y_tilt, color=df_coords['color'], edgecolor=df_coords['edge_color'], s=300, linewidths=2.5, zorder=4)
                    
                    for i, row in df_coords.iterrows():
                        ax_map.text(d_x_tilt[i], d_y_tilt[i], str(row['pos_str']), color='white', fontsize=8, ha='center', va='center', fontweight='heavy', zorder=5)
                        if row['drv_name']: ax_map.text(d_x_tilt[i] + 250, d_y_tilt[i], str(row['drv_name']), color='white', fontsize=10, ha='left', va='center', fontweight='bold', zorder=5)
                        
                    ax_map.set_aspect('equal', adjustable='datalim') 
                    ax_map.set_axis_off()
                    st.pyplot(fig_map)
                    plt.close(fig_map) 
        except Exception as e: st.error(f"⚠️ Error loading positions: {e}")

    # 5. RACE CONTROL MESSAGES
    with rcm_placeholder.container():
        if not messages: st.write("🔇 No messages from Race Control yet...")
        else:
            for msg in reversed(messages):
                rcm_text = msg.get('Message', '...')
                raw_time = msg.get('Utc', '')
                if 'T' in raw_time:
                    time_str = raw_time.split('T')[1][:8] 
                    st.markdown(f"⚠️ **[{time_str}] FIA:** {rcm_text}")
                else: st.markdown(f"⚠️ **FIA:** {rcm_text}")               

    time.sleep(0.2)
