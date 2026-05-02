# Brown GP 🏎️
### F1 Live Timing & Race Replay Platform

A self-hosted Formula 1 web platform built on FastAPI and FastF1. It streams live telemetry directly from F1's SignalR servers during active sessions, and lets you replay any race from 2024–2026 with frame-by-frame car position and gap data.

---

## Architecture Overview

The backend is a single **FastAPI server** (`main.py`) that handles everything: live data ingestion, race data pre-computation, WebSocket streaming to connected frontends, and serving the HTML pages. Three frontend pages are served directly from the same server.

```
brown-gp/backend/
├── main.py          # FastAPI server — live feed, replay engine, REST API
├── landing.html     # Home page with scroll-animated background
├── index.html       # Live session timing page
└── replay.html      # Race replay "Time Machine"
```

---

## Pages

### `/` — Landing Page (`landing.html`)
The home page. Features a scroll-driven canvas animation that plays through 240 pre-rendered F1 frames as the user scrolls. Includes a system data modal showing the replay cache status (which races are precomputed, file sizes, compute durations).

### `/session-timing` — Live Timing (`index.html`)
The live pit wall timing screen. Connects via WebSocket to the backend and displays real-time data during an active F1 session:
- Driver positions, gaps, intervals, sector times, and stint tyre data
- Colour-coded lap time pills (purple = fastest, green = personal best, yellow, white)
- Grid position delta arrows (▲/▼) per driver
- Expandable driver rows showing per-driver strategy (tyre stints and stop count) or full qualifying sector breakdowns
- An **off-week dashboard** mode that automatically activates between race weekends, showing the full race classification, qualifying results, and FP1/FP2/FP3 top-3 results from the most recently completed event
- Offline banner if the WebSocket connection drops

### `/replay` — Time Machine (`replay.html`)
A full race replay tool. Select any precomputed race (2024–2026) to scrub through the entire race at 4 frames per second:
- **Gap Tower (Worm)** — a vertical zipper-style chart showing every car's position and gap to the leader, animated smoothly as the race progresses
- **Track Map** — a 2D canvas rendering of the circuit with all car positions as coloured dots, live car labels, a checkered finish line, and pit lane routing. Supports pinch-to-zoom and drag-to-pan, plus a virtual joystick for mobile
- **Race Control feed** — all FIA messages (flags, VSCs, safety cars, retirements) displayed in chronological order, colour-coded by flag type
- A scrub bar and playback controls (play/pause, speed, lap counter)
- A precomputed races modal listing which races are ready to load instantly

---

## Backend (`main.py`)

### Live Data Ingestion
On startup, the server connects to `livetiming.formula1.com` via SignalR WebSocket and subscribes to all live feeds: timing data, car positions, driver list, tyre app data, race control messages, session info, and more. Compressed `CarData.z` and `Position.z` feeds are decompressed on the fly with zlib. All incoming data is merged into a global `LIVE_DATA` store and immediately broadcast to all connected frontend clients via the `/ws` WebSocket endpoint. If the connection drops, it automatically reconnects every 5 seconds.

An offline fallback roster is built from cached FastF1 data at startup so driver names and team colours are available even before the live feed connects.

### Replay Engine
Races are pre-processed from FastF1 telemetry into a frame cache at 4 frames per second (0.25s resolution). Each frame contains:
- X/Y/Z coordinates for every car on track
- Full gap tower data (position, gap to leader, tyre compound, tyre age, pit stops)
- Pit lane detection with custom pitlane routing support via `pitlanes.json`
- DNF/retirement detection

Frames are stored as a line-delimited JSON file (`.jsonl`) with a byte-offset index (`.index.json`) for fast random-access seeking, plus a metadata file (`.meta.json`) with track shape, race control messages, and compute info. Chunk reads are used so the server never loads a full race into memory at once.

The `/ws/replay` WebSocket handles the full replay session lifecycle: `init` (load or trigger compute), `get_chunk` (stream a range of frames). If a race isn't cached yet, it's computed on-demand with live status updates streamed back to the client.

### Background Precompute
A background task runs continuously and precomputes uncached races from the `ALL_RACES` list (2024–2026 seasons). It processes up to 4 races per day with a 6-hour interval between batches to avoid hammering the FastF1 API.

### Recap Data
Every 30 minutes, a background task checks the F1 schedule and builds a `recap_cache.json` for the most recently completed race weekend. The recap includes race classification with tyre strategies, qualifying results with sector times, and FP top-3 results. This powers the off-week dashboard in `index.html`.

### REST API

| Endpoint | Description |
|---|---|
| `GET /api/session` | Current session info with live/offline status and recap data |
| `GET /api/timing` | Raw live timing data |
| `GET /api/timingapp` | Tyre and stint data |
| `GET /api/messages` | Race control messages |
| `GET /api/drivers` | Driver list with team colours |
| `GET /api/cache-status` | Replay cache status for all races |
| `WS /ws` | Live data stream for connected clients |
| `WS /ws/replay` | Replay session stream |

---

## Setup

### Requirements
- Python 3.10+
- An `F1_Frames/` directory containing 240 numbered PNG frames (`ezgif-frame-001.png` ... `ezgif-frame-240.png`) for the landing page scroll animation

### Install & Run

```bash
pip install fastapi uvicorn fastf1 pandas numpy websockets aiohttp
```

```bash
python main.py
```

The server starts on `http://0.0.0.0:10000`. On first launch it will:
1. Build a fallback driver roster from the FastF1 cache
2. Connect to the F1 live timing feed
3. Begin background precomputing uncached races

### Optional: Custom Pit Lane Routing
Create a `pitlanes.json` file to define custom pit lane X/Y coordinate paths per circuit, used to animate cars smoothly through the pit lane during replay:

```json
{
  "2025_Monaco": {
    "x": [1000, 1100, 1200],
    "y": [500, 520, 540]
  }
}
```

---

## Data Coverage
Replay is supported for all races in the `ALL_RACES` list, spanning the **2024, 2025, and 2026** seasons. Live timing works for any active F1 session when the server is running.
