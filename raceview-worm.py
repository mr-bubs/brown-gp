import streamlit as st
import fastf1
import matplotlib.pyplot as plt
import matplotlib.animation as animation
import pandas as pd
import os
import re

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

animated_view = st.checkbox("Race View (MP4 Video Player)")

if animated_view:
    # Now it controls the actual frame rate of the exported video!
    fps = st.slider("Playback Speed (Laps per second)", min_value=1, max_value=5, value=2, step=1)
    button_text = "Bake & Play Video ▶️"
else:
    button_text = "Generate Static Tower"

if st.button(button_text):
    
    # Grab colors early
    session = fastf1.get_session(YEAR, TRACK, 'R')
    
    with st.spinner(f"Pulling telemetry for {TRACK} {YEAR}..."):
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
    # 🎬 MODE 1: THE VIDEO BAKER (CACHED MP4)
    # ==========================================
    if animated_view:
        # Create a safe filename with the year, track, and speed
        safe_track_name = TRACK.replace(" ", "_")
        video_filename = f"f1cache/{YEAR}_{safe_track_name}_{fps}fps.mp4"
        
        # THE CACHE CHECK: Does this video already exist?
        if os.path.exists(video_filename):
            st.success("✅ Video loaded from cache instantly!")
            st.video(video_filename)
            
        else:
            with st.spinner("Baking the MP4 Video! This takes about 1-2 minutes, but will load instantly next time..."):
                laps = session.laps
                total_laps = int(laps['LapNumber'].max())
                
                # Step A: Pre-calculate all the data frames so Matplotlib doesn't lag
                frames_data = []
                for current_lap in range(1, total_laps + 1):
                    lap_data = laps[laps['LapNumber'] == current_lap].dropna(subset=['Time'])
                    if lap_data.empty: continue
                        
                    lap_data = lap_data.sort_values(by='Time')
                    leader_time = lap_data.iloc[0]['Time'].total_seconds()
                    
                    driver_times = []
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
                        
                    frames_data.append({
                        'lap': current_lap,
                        'positions': positions,
                        'gaps': accumulated_gaps,
                        'colors': accumulated_colors,
                        'driver_pos': driver_positions
                    })

                # Step B: Set up the Matplotlib Figure
                fig, ax = plt.subplots(figsize=(5, 8))
                
                # Step C: The Animation Function that draws each frame
                def update(frame_idx):
                    ax.clear()
                    data = frames_data[frame_idx]
                    
                    max_y_center = data['gaps'][-1] if data['gaps'] else 10
                    ax.bar(x=0, bottom=0, height=max_y_center, width=0.3, color='darkgrey', zorder=1)
                    
                    for i in range(len(data['positions'])):
                        ax.bar(x=0, bottom=data['gaps'][i] - 0.3, height=0.6, width=0.3, color=data['colors'][i], edgecolor='black', zorder=2)
                        if data['driver_pos'][i] % 2 != 0:
                            ax.text(0.25, data['gaps'][i], data['positions'][i], va='center', ha='left', fontweight='bold')
                        else:
                            ax.text(-0.25, data['gaps'][i], data['positions'][i], va='center', ha='right', fontweight='bold')

                    ax.set_title(f"{TRACK} {YEAR} - Lap {data['lap']} / {total_laps}")
                    ax.set_ylabel("Seconds Ahead of Last Place")
                    ax.set_xticks([])
                    ax.set_xlim(-2.5, 2.5)
                    ax.spines['top'].set_visible(False)
                    ax.spines['right'].set_visible(False)
                    ax.spines['bottom'].set_visible(False)

                # Step D: Bake the Video!
                ani = animation.FuncAnimation(fig, update, frames=len(frames_data), blit=False)
                ani.save(video_filename, writer='ffmpeg', fps=fps, dpi=150)
                plt.close(fig)
                
                # Render the final baked video to the screen
                st.success("🏁 Video baked and cached successfully!")
                st.video(video_filename)

    # ==========================================
    # 📊 MODE 2: THE STATIC FINAL CLASSIFICATION
    # ==========================================
    else:
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
