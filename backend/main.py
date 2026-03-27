from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import requests

app = FastAPI()

# This tells the server to ONLY accept requests from your domain
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://browngp.xyz", "https://www.browngp.xyz"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Disguise our server as a regular web browser so F1's bouncers let us in
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

@app.get("/")
def read_root():
    return {"status": "Brown GP Middleman is Live!"}

# Endpoint 1: Fetch the Live Timing Tower Data
@app.get("/api/timing")
def get_timing():
    try:
        url = "https://livetiming.formula1.com/static/TimingData.json"
        response = requests.get(url, headers=HEADERS)
        return response.json()
    except Exception as e:
        return {"error": "Failed to fetch timing data"}

# Endpoint 2: Fetch the Session Info (Track, Name)
@app.get("/api/session")
def get_session():
    try:
        url = "https://livetiming.formula1.com/static/SessionInfo.json"
        response = requests.get(url, headers=HEADERS)
        return response.json()
    except Exception as e:
        return {"error": "Failed to fetch session info"}
