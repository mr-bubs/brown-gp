import cv2
import numpy as np
import fastf1
import json
import os
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

app = FastAPI()

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

fastf1.Cache.enable_cache('f1cache')

if not os.path.exists('pitlanes.json'):
    with open('pitlanes.json', 'w') as f: json.dump({}, f)

# 🚨 NEW ENDPOINT: Reads the JSON to see what tracks are finished
@app.get("/api/mapped-tracks")
def get_mapped_tracks():
    try:
        with open('pitlanes.json', 'r') as f:
            data = json.load(f)
        circuits = set()
        for key in data.keys():
            parts = key.split('_')
            if len(parts) > 1:
                circuits.add(parts[1])
        return JSONResponse({"mapped": sorted(list(circuits))})
    except:
        return JSONResponse({"mapped": []})

@app.post("/api/align-pitlane")
async def align_pitlane(year: str = Form(...), circuit: str = Form(...), file: UploadFile = File(...)):
    try:
        contents = await file.read()
        np_img = np.frombuffer(contents, np.uint8)
        img = cv2.imdecode(np_img, cv2.IMREAD_COLOR)
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

        mask_red1 = cv2.inRange(hsv, np.array([0, 100, 100]), np.array([10, 255, 255]))
        mask_red2 = cv2.inRange(hsv, np.array([160, 100, 100]), np.array([180, 255, 255]))
        mask_red = mask_red1 + mask_red2
        mask_yellow = cv2.inRange(hsv, np.array([20, 100, 100]), np.array([40, 255, 255]))

        contours_red, _ = cv2.findContours(mask_red, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        contours_yellow, _ = cv2.findContours(mask_yellow, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)

        if not contours_red or not contours_yellow:
            return JSONResponse({"error": "Could not detect red track or yellow pitlane."})

        largest_red = max(contours_red, key=cv2.contourArea)
        largest_yellow = max(contours_yellow, key=cv2.contourArea)

        yellow_pts_raw = largest_yellow.squeeze()
        if yellow_pts_raw.ndim == 1:
            yellow_pts_raw = yellow_pts_raw.reshape(-1, 2)
        half_len = len(yellow_pts_raw) // 2
        yellow_pts = yellow_pts_raw[:half_len].tolist()

        session = fastf1.get_session(int(year), circuit, 'R')
        session.load(telemetry=True, laps=True, weather=False, messages=False)
        fastest_lap = session.laps.pick_fastest()
        tel = fastest_lap.get_telemetry()
        real_x = tel['X'].values.tolist()
        real_y = tel['Y'].values.tolist()

        rx_img = [p[0][0] for p in largest_red]; ry_img = [p[0][1] for p in largest_red]
        min_ix, max_ix, min_iy, max_iy = min(rx_img), max(rx_img), min(ry_img), max(ry_img)
        min_rx, max_rx, min_ry, max_ry = min(real_x), max(real_x), min(real_y), max(real_y)

        scale_x = (max_rx - min_rx) / max(1, max_ix - min_ix)
        scale_y = (max_ry - min_ry) / max(1, max_iy - min_iy)
        scale = max(scale_x, scale_y)

        gps_yellow_x = []
        gps_yellow_y = []
        for (px, py) in yellow_pts:
            gx = min_rx + (px - min_ix) * scale
            gy = min_ry + (py - min_iy) * scale
            gps_yellow_x.append(gx)
            gps_yellow_y.append(gy)

        stride = max(1, len(gps_yellow_x) // 60)
        final_px = gps_yellow_x[::stride]
        final_py = gps_yellow_y[::stride]

        return JSONResponse({
            "track_x": real_x, "track_y": real_y,
            "pit_x": final_px, "pit_y": final_py
        })

    except Exception as e:
        return JSONResponse({"error": str(e)})

@app.post("/api/save-pitlane")
async def save_pitlane(payload: dict):
    year = payload.get("year")
    circuit = payload.get("circuit")
    px = payload.get("pit_x", [])
    py = payload.get("pit_y", [])

    try:
        with open('pitlanes.json', 'r') as f: data = json.load(f)
    except: data = {}

    key = f"{year}_{circuit}"
    data[key] = {"x": px, "y": py}

    with open('pitlanes.json', 'w') as f: json.dump(data, f)
    
    def cache_key(y, c): return f"{y}_{c.replace(' ', '_').replace('/', '-')}"
    try:
        os.remove(os.path.join("replay_cache", f"{cache_key(str(year), circuit)}.jsonl"))
        os.remove(os.path.join("replay_cache", f"{cache_key(str(year), circuit)}.meta.json"))
    except: pass

    return JSONResponse({"status": "success"})

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=10001)
