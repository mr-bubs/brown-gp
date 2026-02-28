import streamlit as st
import fastf1
import matplotlib.pyplot as plt
import pandas as pd
import re
import os

# --- 1. STREAMLIT UI SETUP ---
st.title("F1 Timing Tower Builder 🏎️")
st.write("Select a race to generate the official gap chart.")

# Create two columns for our dropdowns
col1, col2 = st.columns(2)

with col1:
    YEAR = st.selectbox("Select Year", [2023, 2022, 2021])

with col2:
    TRACK = st.selectbox("Select Track", [
        'Bahrain', 'Saudi Arabia', 'Australia', 'Miami', 'Monaco', 
        'Silverstone', 'Spa', 'Monza', 'Singapore', 'Austin', 'Las Vegas', 'Abu Dhabi'
    ])

# Add a button so it only runs when the user is ready
if st.button("Generate Tower"):
    
    # --- 2. DATA PULL & LOGIC ---
    # Put a little loading spinner on the screen while FastF1 downloads the data
    with st.spinner(f"Pulling telemetry for {TRACK} {YEAR}..."):
        
        if not os.path.exists('f1cache'):
            os.makedirs('f1cache')
        fastf1.Cache.enable_cache('f1cache')

        session = fastf1.get_session(YEAR, TRACK, 'R')
        session.load()

        results = session.results
        finished_drivers = results[results['Status'].str.contains('Finished|Lap', case=False, na=False)].copy()
        sorted_drivers = finished_drivers.sort_values(by='Position', ascending=True)

        median_lap = session.laps.pick_quicklaps()['LapTime'].median().total_seconds()

        driver_times = []
        last_time = 0.0

        for index, driver in sorted_drivers.iterrows():
            position = driver['Position']
            status = str(driver['Status'])
            name = driver['Abbreviation']
            
            raw_color = driver.get('TeamColor', 'mediumpurple')
            team_color = f"#{raw_color}" if pd.notna(raw_color) and str(raw_color).strip() != "" else 'mediumpurple'
            
            is_lapped = 'Lap' in status
            
            if position == 1.0:
                time_behind = 0.0
                display_name = name
            elif not is_lapped and pd.notna(driver['Time']):
                time_behind = driver['Time'].total_seconds()
                display_name = f"{name} (+{time_behind:.2f}s)"
            else:
                match = re.search(r'\d+', status)
                laps_down = int(match.group()) if match else 1
                base_penalty = laps_down * median_lap
                time_behind = max(last_time + 3.0, base_penalty)
                
                lap_text = "Lap" if laps_down == 1 else "Laps"
                display_name = f"{name} (+{laps_down} {lap_text})"
                    
            last_time = time_behind
            
            driver_times.append({
                "name": display_name, 
                "time_behind_leader": time_behind,
                "color": team_color,
                "position": position
            })

        p20_time = driver_times[-1]["time_behind_leader"]

        positions = []
        accumulated_gaps = []
        accumulated_colors = []
        driver_positions = []

        for driver in reversed(driver_times):
            positions.append(driver["name"])
            gap_ahead_of_p20 = p20_time - driver["time_behind_leader"]
            accumulated_gaps.append(gap_ahead_of_p20)
            accumulated_colors.append(driver["color"])
            driver_positions.append(driver["position"])

        # --- 3. DRAWING THE GRAPH ---
        # We assign the plot to 'fig' so Streamlit can render it
        fig = plt.figure(figsize=(5, 8)) 

        max_y_center = accumulated_gaps[-1] 

        plt.bar(x=0, bottom=0, height=max_y_center, width=0.3, color='darkgrey', zorder=1)

        for i in range(len(positions)):
            display_text = positions[i]
            current_y = accumulated_gaps[i]
            current_color = accumulated_colors[i]
            pos = driver_positions[i]
            
            plt.bar(x=0, bottom=current_y - 0.3, height=0.6, width=0.3, color=current_color, edgecolor='black', zorder=2)
            
            if pos % 2 != 0:
                plt.text(0.25, current_y, display_text, va='center', ha='left', fontweight='bold')
            else:
                plt.text(-0.25, current_y, display_text, va='center', ha='right', fontweight='bold')

        plt.title(f"{TRACK} {YEAR} Final Classification")
        plt.ylabel("Seconds Ahead of Last Place")
        plt.xticks([]) 
        plt.xlim(-2.5, 2.5) 

        plt.gca().spines['top'].set_visible(False)
        plt.gca().spines['right'].set_visible(False)
        plt.gca().spines['bottom'].set_visible(False)
        plt.grid(False) 

        # --- 4. STREAMLIT RENDER ---
        # Tell Streamlit to display the figure we just built
        st.pyplot(fig)
        
        # Add a nice little success message at the bottom
        st.success("Tower generated successfully!")
