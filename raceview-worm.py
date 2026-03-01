import streamlit as st
import fastf1
import matplotlib.pyplot as plt
import pandas as pd
import os
import time

# --- 1. SETUP & CACHE ---
if not os.path.exists('f1cache'):
    os.makedirs('f1cache')
fastf1.Cache.enable_cache('f1cache')

# --- 2. STREAMLIT UI SETUP ---
st.title("Brown GP | Timing Worm 🏎️")
st.write("Select a race to generate the official gap chart.")

col1, col2 = st.columns(2)

with col1:
    YEAR = st.selectbox("Select Year", [2025, 2024, 2023, 2022, 2021, 2020, 2019, 2018])

with col2:
    schedule = fastf1.get_event_schedule(YEAR)
    race_events = schedule[schedule['EventFormat'] != 'testing']['EventName'].tolist()
    TRACK = st.selectbox("Select Track", race_events)

# NEW: The Animation Checkbox & Slider!
animated_view = st.checkbox("Race View (Animated Lap-by-Lap)")

# Dynamic UI based on the checkbox
if animated_view:
    # The slider that fixes the lap skipping!
    frame_delay = st.slider("Animation Speed (seconds per frame)", min_value=0.1, max_value=2.0, value=0.5, step=0.1)
    button_text = "Play Animation ▶️"
else:
    button_text = "Generate Tower"

# The button text dynamically changes
if st.button(button_text):
    
    with st.spinner(f"Pulling telemetry for {TRACK} {YEAR}..."):
        session = fastf1.get_session(YEAR, TRACK, 'R')
        session.load()
        
        drivers = session.results['Abbreviation'].tolist()
        colors = {}
        for drv in drivers:
            try:
                raw_color = session.results.loc[session.results['Abbreviation'] == drv, 'TeamColor'].values[0]
                colors[drv] = f"#{raw_color}" if pd.notna(raw_color) and str(raw_color).strip() != "" else 'mediumpurple'
            except:
                colors[drv] = 'mediumpurple'

        # ==========================================
        # 🎬 MODE 1: THE ANIMATED RACE VIEW
        # ==========================================
        if animated_view:
            laps = session.laps
            total_laps = int(laps['LapNumber'].max())
            
            animation_frame = st.empty()
            
            for current_lap in range(1, total_laps + 1):
                lap_data = laps[laps['LapNumber'] == current_lap].dropna(subset=['Time'])
                
                if lap_data.empty:
                    continue
                    
                lap_data = lap_data.sort_values(by='Time')
                leader_time = lap_data.iloc[0]['Time'].total_seconds()
                
                driver_times = []
                last_time = 0.0
                
                for index, row in lap_data.iterrows():
                    name = row['Driver']
                    time_behind = row['Time'].total_seconds() - leader_time
                    
                    driver_times.append({
                        "name": f"{name} (+{time_behind:.1f}s)" if time_behind > 0 else name,
                        "time_behind_leader": time_behind,
                        "color": colors.get(name, 'mediumpurple'),
                        "position": len(driver_times) + 1
                    })
                
                if not driver_times: continue
                p_last_time = driver_times[-1]["time_behind_leader"]
                
                positions, accumulated_gaps, accumulated_colors, driver_positions = [], [], [], []
                
                for driver in reversed(driver_times):
                    positions.append(driver["name"])
                    accumulated_gaps.append(p_last_time - driver["time_behind_leader"])
                    accumulated_colors.append(driver["color"])
                    driver_positions.append(driver["position"])

                fig = plt.figure(figsize=(5, 8)) 
                max_y_center = accumulated_gaps[-1] if accumulated_gaps else 10
                plt.bar(x=0, bottom=0, height=max_y_center, width=0.3, color='darkgrey', zorder=1)

                for i in range(len(positions)):
                    plt.bar(x=0, bottom=accumulated_gaps[i] - 0.3, height=0.6, width=0.3, color=accumulated_colors[i], edgecolor='black', zorder=2)
                    if driver_positions[i] % 2 != 0:
                        plt.text(0.25, accumulated_gaps[i], positions[i], va='center', ha='left', fontweight='bold')
                    else:
                        plt.text(-0.25, accumulated_gaps[i], positions[i], va='center', ha='right', fontweight='bold')

                plt.title(f"{TRACK} {YEAR} - Lap {current_lap} / {total_laps}")
                plt.ylabel("Seconds Ahead of Last Place")
                plt.xticks([]) 
                plt.xlim(-2.5, 2.5) 
                plt.gca().spines['top'].set_visible(False)
                plt.gca().spines['right'].set_visible(False)
                plt.gca().spines['bottom'].set_visible(False)
                
                animation_frame.pyplot(fig)
                plt.close(fig) 
                
                # --- NEW: THE DRAMATIC PAUSE & SPEED CONTROL ---
                if current_lap == 1:
                    # Hold the Lap 1 starting grid on screen so the user can get ready!
                    time.sleep(1.5)
                else:
                    # Use the slider value for the rest of the race
                    time.sleep(frame_delay) 
                
            st.success("🏁 Checkered Flag!")

        # ==========================================
        # 📊 MODE 2: THE STATIC FINAL CLASSIFICATION
        # ==========================================
        else:
            import re 
            results = session.results
            finished_drivers = results[results['Status'].str.contains('Finished|Lap', case=False, na=False)].copy()
            sorted_drivers = finished_drivers.sort_values(by='Position', ascending=True)
            median_lap = session.laps.pick_quicklaps()['LapTime'].median().total_seconds()

            driver_times, last_time = [], 0.0

            for index, driver in sorted_drivers.iterrows():
                position, status, name = driver['Position'], str(driver['Status']), driver['Abbreviation']
                team_color = colors.get(name, 'mediumpurple')
                is_lapped = 'Lap' in status
                
                if position == 1.0:
                    time_behind, display_name = 0.0, name
                elif not is_lapped and pd.notna(driver['Time']):
                    time_behind = driver['Time'].total_seconds()
                    display_name = f"{name} (+{time_behind:.2f}s)"
                else:
                    match = re.search(r'\d+', status)
                    laps_down = int(match.group()) if match else 1
                    time_behind = max(last_time + 3.0, laps_down * median_lap)
                    display_name = f"{name} (+{laps_down} {'Lap' if laps_down == 1 else 'Laps'})"
                        
                last_time = time_behind
                driver_times.append({"name": display_name, "time_behind_leader": time_behind, "color": team_color, "position": position})

            p20_time = driver_times[-1]["time_behind_leader"]
            positions, accumulated_gaps, accumulated_colors, driver_positions = [], [], [], []

            for driver in reversed(driver_times):
                positions.append(driver["name"])
                accumulated_gaps.append(p20_time - driver["time_behind_leader"])
                accumulated_colors.append(driver["color"])
                driver_positions.append(driver["position"])

            fig = plt.figure(figsize=(5, 8)) 
            max_y_center = accumulated_gaps[-1] 
            plt.bar(x=0, bottom=0, height=max_y_center, width=0.3, color='darkgrey', zorder=1)

            for i in range(len(positions)):
                plt.bar(x=0, bottom=accumulated_gaps[i] - 0.3, height=0.6, width=0.3, color=accumulated_colors[i], edgecolor='black', zorder=2)
                if driver_positions[i] % 2 != 0:
                    plt.text(0.25, accumulated_gaps[i], positions[i], va='center', ha='left', fontweight='bold')
                else:
                    plt.text(-0.25, accumulated_gaps[i], positions[i], va='center', ha='right', fontweight='bold')

            plt.title(f"{TRACK} {YEAR} Final Classification")
            plt.ylabel("Seconds Ahead of Last Place")
            plt.xticks([]) 
            plt.xlim(-2.5, 2.5) 
            plt.gca().spines['top'].set_visible(False)
            plt.gca().spines['right'].set_visible(False)
            plt.gca().spines['bottom'].set_visible(False)
            
            st.pyplot(fig)
            st.success("Tower generated successfully!")
