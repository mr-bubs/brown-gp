import fastf1
import pandas as pd
import json
import os
import warnings

# Suppress the annoying pink FastF1 deprecation warnings
warnings.simplefilter(action='ignore', category=FutureWarning)

# Ensure cache directory exists
if not os.path.exists('f1cache'): 
    os.makedirs('f1cache')
fastf1.Cache.enable_cache('f1cache')

def _fmt_time(td):
    if pd.isna(td): 
        return "-"
    return f"{int(td.total_seconds()//60)}:{td.total_seconds()%60:06.3f}"

def test_sprint_quali_segmented(year, event):
    print(f"\n[SANDBOX] Initiating segmented extraction for {year} {event} Sprint Qualifying...\n")
    
    try:
        # Force the 'SQ' identifier
        s = fastf1.get_session(year, event, 'SQ')
        # CRITICAL FIX: messages=True allows FastF1 to read the segment boundaries and calculate SQ1/SQ2/SQ3
        s.load(telemetry=False, laps=True, weather=False, messages=True)
    except Exception as e:
        print(f"\n[!] ERROR: Could not load session: {e}")
        return

    res = []
    
    for index, (_, row) in enumerate(s.results.iterrows()):
        
        # 1. POSITION FALLBACK LOGIC
        pos = row.get('Position')
        if pd.isna(pos): pos = row.get('ClassifiedPosition')
        try: pos = int(float(pos))
        except: pos = index + 1 
            
        # 2. DSQ (DISQUALIFIED) LOGIC
        status = str(row.get('Status', ''))
        is_dsq = (status == 'Disqualified')
        
        # 3. SEGMENTED COLUMNS (Now properly calculated by FastF1)
        cols = s.results.columns
        if 'Q1' in cols:
            q1, q2, q3 = _fmt_time(row.get('Q1')), _fmt_time(row.get('Q2')), _fmt_time(row.get('Q3'))
        elif 'SQ1' in cols:
            q1, q2, q3 = _fmt_time(row.get('SQ1')), _fmt_time(row.get('SQ2')), _fmt_time(row.get('SQ3'))
        else:
            q1, q2, q3 = "-", "-", "-"
            
        res.append({
            "pos": pos,
            "driver": str(row.get('Abbreviation', 'UNK')),
            "color": "#" + str(row.get('TeamColor', 'ffffff')).replace('#', ''),
            "q1": q1,
            "q2": q2,
            "q3": q3,
            "is_dsq": is_dsq,
            "raw_status": status
        })
        
    print(json.dumps(res, indent=2))
    print(f"\n[SANDBOX] Extraction Complete. Total Drivers Parsed: {len(res)}\n")

if __name__ == "__main__":
    test_sprint_quali_segmented(2026, 'Miami')
