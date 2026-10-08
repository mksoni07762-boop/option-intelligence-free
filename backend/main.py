from pathlib import Path
from datetime import datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


# --------------------------------------------------
# APP
# --------------------------------------------------

app = FastAPI(
    title="Option Intelligence Engine",
    version="1.0.0"
)


# --------------------------------------------------
# CORS
# --------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------
# PATHS
# --------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
INDEX_FILE = FRONTEND_DIR / "index.html"


# --------------------------------------------------
# FRONTEND
# --------------------------------------------------

if FRONTEND_DIR.exists():
    app.mount(
        "/static",
        StaticFiles(directory=str(FRONTEND_DIR)),
        name="static"
    )


@app.get("/")
def home():
    if INDEX_FILE.exists():
        return FileResponse(str(INDEX_FILE))

    return {
        "status": "online",
        "message": "Option Intelligence Engine is running"
    }


# --------------------------------------------------
# API STATUS
# --------------------------------------------------

@app.get("/api/status")
def status():
    return {
        "engine": "Option Intelligence Engine",
        "status": "ready",
        "time": datetime.now().isoformat()
    }


# --------------------------------------------------
# MARKET API
# --------------------------------------------------

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
