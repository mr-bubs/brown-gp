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

# A simple check to ensure the server is awake
@app.get("/")
def read_root():
    return {"status": "Brown GP Middleman is Live!"}

# The F1 Data Fetcher (We will expand this later to pull the live feed)
@app.get("/api/f1-data")
def get_f1_data():
    return {"message": "Ready to connect to livetiming.formula1.com"}
