import asyncio
import json
import base64
import zlib
from urllib.parse import quote
import aiohttp
import websockets
import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

connected_clients = set()

@app.get("/radio")
def read_root():
    with open("radio.html", "r") as f:
        return HTMLResponse(content=f.read())

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_clients.add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except:
        connected_clients.discard(websocket)

async def f1_signalr_radio_client():
    url = 'https://livetiming.formula1.com/signalr'
    while True:
        try:
            print("[RADIO] Negotiating with F1...")
            ws_headers = {"User-Agent": "BestHTTP"}
            async with aiohttp.ClientSession(headers=ws_headers) as session:
                async with session.get(f"{url}/negotiate?clientProtocol=1.5&connectionData=[%7B%22name%22:%22Streaming%22%7D]") as resp:
                    data = await resp.json()
                    token = quote(data['ConnectionToken'])
                    cookie_header = "; ".join([f"{key}={val.value}" for key, val in resp.cookies.items()])

                ws_url = f"wss://livetiming.formula1.com/signalr/connect?clientProtocol=1.5&transport=webSockets&connectionToken={token}&connectionData=[%7B%22name%22:%22Streaming%22%7D]"
                
                connect_headers = {"User-Agent": "BestHTTP"}
                if cookie_header: connect_headers["Cookie"] = cookie_header
                    
                async with websockets.connect(ws_url, additional_headers=connect_headers) as ws:
                    print("[RADIO] Connected. Subscribing...")
                    sub_msg = {
                        "H": "Streaming", 
                        "M": "Subscribe", 
                        "A": [["Heartbeat", "RaceControlMessages", "TeamRadio"]], 
                        "I": 1
                    }
                    await ws.send(json.dumps(sub_msg))
                    
                    while True:
                        msg = await ws.recv()
                        data = json.loads(msg)
                        
                        if 'M' in data:
                            for m in data['M']:
                                if m['A']:
                                    feed, content = m['A'][0], m['A'][1]
                                    
                                    if type(content) == str and len(content) > 50 and feed.endswith('.z'):
                                        try:
                                            content = json.loads(zlib.decompress(base64.b64decode(content), -zlib.MAX_WBITS))
                                            feed = feed.replace('.z', '')
                                        except: pass
                                    
                                    out_msg = json.dumps({"type": "live_update", feed: content})
                                    for client in list(connected_clients):
                                        try: await client.send_text(out_msg)
                                        except: pass
        except Exception as e:
            print(f"[RADIO] Error: {e}. Retrying...")
            await asyncio.sleep(5)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(f1_signalr_radio_client())

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
