from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime

app = FastAPI(
    title="Option Intelligence Engine",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def home():
    return {
        "status": "online",
        "message": "Option Intelligence Engine is running"
    }


@app.get("/api/status")
def status():
    return {
        "engine": "Option Intelligence Engine",
        "status": "ready",
        "time": datetime.now().isoformat()
    }


@app.get("/api/market")
def market():
    return {
        "symbol": "NIFTY",
        "market_condition": "WAIT",
        "score": 50,
        "confidence": 50,
        "signal": "WAIT",
        "statement": "Waiting for live market data.",
        "data_status": "DATA CONNECTOR NOT CONFIGURED"
    }
