from pathlib import Path
from datetime import datetime
from typing import Dict, Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="Option Intelligence Engine",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# FRONTEND
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
INDEX_FILE = FRONTEND_DIR / "index.html"

if FRONTEND_DIR.exists():
    app.mount(
        "/static",
        StaticFiles(directory=str(FRONTEND_DIR)),
        name="static"
    )


# ============================================================
# BASIC HELPERS
# ============================================================

def safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def clamp(value, minimum=0, maximum=100):
    return max(minimum, min(maximum, value))


# ============================================================
# PRICE + OI CLASSIFICATION
# ============================================================

def classify_underlying_oi(price_change, oi_change):
    """
    Classifies the basic futures-style relationship.

    Price UP + OI UP   = Long Buildup
    Price DOWN + OI UP = Short Buildup
    Price UP + OI DOWN = Short Covering
    Price DOWN + OI DOWN = Long Unwinding
    """

    price_change = safe_float(price_change)
    oi_change = safe_float(oi_change)

    if price_change > 0 and oi_change > 0:
        return "LONG BUILDUP"

    if price_change < 0 and oi_change > 0:
        return "SHORT BUILDUP"

    if price_change > 0 and oi_change < 0:
        return "SHORT COVERING"

    if price_change < 0 and oi_change < 0:
        return "LONG UNWINDING"

    return "NEUTRAL"


def classify_option_position(
    premium_change,
    oi_change,
    option_type
):
    """
    Interprets CE/PE option positioning.

    CE:
      OI UP + Premium DOWN = Call Writing
      OI DOWN + Premium UP = Call Short Covering

    PE:
      OI UP + Premium DOWN = Put Writing
      OI DOWN + Premium UP = Put Short Covering
    """

    premium_change = safe_float(premium_change)
    oi_change = safe_float(oi_change)

    option_type = option_type.upper()

    if oi_change > 0 and premium_change < 0:
        if option_type == "CE":
            return "CALL WRITING"
        return "PUT WRITING"

    if oi_change < 0 and premium_change > 0:
        if option_type == "CE":
            return "CALL SHORT COVERING"
        return "PUT SHORT COVERING"

    if oi_change > 0 and premium_change > 0:
        return "LONG BUILDUP"

    if oi_change < 0 and premium_change < 0:
        return "LONG UNWINDING"

    return "NEUTRAL"


# ============================================================
# PCR
# ============================================================

def calculate_pcr(put_oi, call_oi):
    put_oi = safe_float(put_oi)
    call_oi = safe_float(call_oi)

    if call_oi <= 0:
        return 0

    return round(put_oi / call_oi, 2)


# ============================================================
# TECHNICAL SCORE
# ============================================================

def technical_score(data: Dict[str, Any]):

    score = 0
    reasons = []

    price = safe_float(data.get("price"))
    vwap = safe_float(data.get("vwap"))
    ema9 = safe_float(data.get("ema9"))
    ema21 = safe_float(data.get("ema21"))
    rsi = safe_float(data.get("rsi"))
    volume_ratio = safe_float(data.get("volume_ratio"), 1)
    structure = str(data.get("structure", "")).upper()

    # -------------------------
    # VWAP
    # -------------------------

    if price > vwap and vwap > 0:
        score += 8
        reasons.append("price is above VWAP")

    elif price < vwap and vwap > 0:
        score -= 8
        reasons.append("price is below VWAP")

    # -------------------------
    # EMA
    # -------------------------

    if ema9 > ema21 and ema21 > 0:
        score += 7
        reasons.append("EMA 9 is above EMA 21")

    elif ema9 < ema21 and ema21 > 0:
        score -= 7
        reasons.append("EMA 9 is below EMA 21")

    # -------------------------
    # RSI
    # -------------------------

    if 55 <= rsi <= 70:
        score += 6
        reasons.append("RSI supports bullish momentum")

    elif 30 <= rsi <= 45:
        score -= 6
        reasons.append("RSI supports bearish momentum")

    elif rsi > 75:
        reasons.append("RSI is overheated")

    elif rsi < 25:
        reasons.append("RSI is deeply oversold")

    # -------------------------
    # STRUCTURE
    # -------------------------

    if structure in ["HH-HL", "HH_HL", "BULLISH"]:
        score += 7
        reasons.append("higher-high higher-low structure")

    elif structure in ["LH-LL", "LH_LL", "BEARISH"]:
        score -= 7
        reasons.append("lower-high lower-low structure")

    # -------------------------
    # VOLUME
    # -------------------------

    if volume_ratio >= 1.5:
        score += 5
        reasons.append("volume expansion confirms the move")

    elif volume_ratio <= 0.7:
        reasons.append("volume is weak")

    return score, reasons


# ============================================================
# OI SCORE
# ============================================================

def oi_score(data: Dict[str, Any]):

    score = 0
    reasons = []

    ce_position = str(
        data.get("ce_position", "")
    ).upper()

    pe_position = str(
        data.get("pe_position", "")
    ).upper()

    # Bullish option positioning
    if ce_position in [
        "CALL SHORT COVERING",
        "CALL UNWINDING"
    ]:
        score += 10
        reasons.append("call resistance is being unwound")

    if pe_position == "PUT WRITING":
        score += 10
        reasons.append("put writing is providing support")

    # Bearish option positioning
    if ce_position == "CALL WRITING":
        score -= 10
        reasons.append("call writing is creating resistance")

    if pe_position == "PUT SHORT COVERING":
        score -= 10
        reasons.append("put support is being unwound")

    return score, reasons


# ============================================================
# PCR SCORE
# ============================================================

def pcr_score(pcr):

    pcr = safe_float(pcr)

    if pcr >= 1.20:
        return 8, "PCR indicates stronger put-side positioning"

    if 1.05 <= pcr < 1.20:
        return 4, "PCR is mildly bullish"

    if 0.90 <= pcr < 1.05:
        return 0, "PCR is neutral"

    if 0.75 <= pcr < 0.90:
        return -4, "PCR is mildly bearish"

    return -8, "PCR indicates stronger call-side pressure"


# ============================================================
# MARKET REGIME
# ============================================================

def determine_regime(score, data):

    price = safe_float(data.get("price"))
    vwap = safe_float(data.get("vwap"))
    ema9 = safe_float(data.get("ema9"))
    ema21 = safe_float(data.get("ema21"))

    # Hard sideways filter
    if (
        abs(score) < 15
        or (
            vwap > 0
            and abs(price - vwap) / vwap < 0.001
            and abs(ema9 - ema21) / max(price, 1) < 0.001
        )
    ):
        return "SIDEWAYS"

    if score >= 25:
        return "BULLISH"

    if score <= -25:
        return "BEARISH"

    return "SIDEWAYS"


# ============================================================
# FINAL DECISION
# ============================================================

def decision_engine(data: Dict[str, Any]):

    tech_score, tech_reasons = technical_score(data)
    oi_score_value, oi_reasons = oi_score(data)

    pcr = calculate_pcr(
        data.get("put_oi"),
        data.get("call_oi")
    )

    pcr_points, pcr_reason = pcr_score(pcr)

    # Total directional score
    raw_score = tech_score + oi_score_value + pcr_points

    # Convert -100/+100 direction into 0-100 confidence-style score
    decision_score = clamp(
        round(50 + raw_score * 1.5)
    )

    regime = determine_regime(
        raw_score,
        data
    )

    all_reasons = (
        tech_reasons
        + oi_reasons
        + [pcr_reason]
    )

    # ---------------------------------------
    # SIDEWAYS = NO TRADE
    # ---------------------------------------

    if regime == "SIDEWAYS":
        signal = "WAIT"

    elif raw_score >= 25:
        signal = "BUY CALL"

    elif raw_score <= -25:
        signal = "BUY PUT"

    else:
        signal = "WAIT"

    # ---------------------------------------
    # CONFIDENCE
    # ---------------------------------------

    confidence = clamp(
        round(abs(raw_score) * 2)
    )

    # Don't allow misleading high confidence
    # during weak/sideways conditions.
    if regime == "SIDEWAYS":
        confidence = min(confidence, 55)

    # ---------------------------------------
    # MARKET STORY
    # ---------------------------------------

    if all_reasons:
        reason_text = ", ".join(all_reasons[:5])
    else:
        reason_text = "insufficient confirmation"

    if regime == "BULLISH":

        statement = (
            "Bullish momentum is strengthening because "
            + reason_text
            + "."
        )

    elif regime == "BEARISH":

        statement = (
            "Bearish momentum is strengthening because "
            + reason_text
            + "."
        )

    else:

        statement = (
            "Market is currently sideways or conflicting. "
            "Price, technical and option positioning are "
            "not providing enough confirmation."
        )

    return {
        "market_condition": regime,
        "score": decision_score,
        "confidence": confidence,
        "signal": signal,
        "pcr": pcr,
        "statement": statement,
        "technical_score": tech_score,
        "oi_score": oi_score_value,
        "pcr_score": pcr_points,
        "reasons": all_reasons
    }


# ============================================================
# HOME
# ============================================================

@app.get("/")
def home():

    if INDEX_FILE.exists():
        return FileResponse(str(INDEX_FILE))

    return {
        "status": "online",
        "message": "Option Intelligence Engine is running"
    }


# ============================================================
# STATUS
# ============================================================

@app.get("/api/status")
def status():

    return {
        "engine": "Option Intelligence Engine",
        "status": "ready",
        "version": "2.0.0",
        "time": datetime.now().isoformat()
    }


# ============================================================
# MARKET API
# ============================================================

@app.get("/api/market")
def market(symbol: str = "NIFTY"):

    symbol = symbol.upper()

    if symbol not in ["NIFTY", "BANKNIFTY"]:
        symbol = "NIFTY"

    # --------------------------------------------------------
    # IMPORTANT:
    # These are NOT live values.
    # They are only a neutral data structure used to test
    # the intelligence engine until the free data connector
    # is connected.
    # --------------------------------------------------------

    data = {
        "symbol": symbol,

        "price": 0,
        "change": 0,

        "vwap": 0,
        "ema9": 0,
        "ema21": 0,
        "rsi": 50,
        "atr": 0,

        "volume": 0,
        "volume_ratio": 1,

        "structure": "NEUTRAL",

        "call_oi": 0,
        "put_oi": 0,

        "ce_position": "NEUTRAL",
        "pe_position": "NEUTRAL",

        "iv": 0
    }

    result = decision_engine(data)

    return {
        "symbol": symbol,

        "price": data["price"],
        "change": data["change"],

        "market_condition": result["market_condition"],
        "score": result["score"],
        "confidence": result["confidence"],
        "signal": result["signal"],

        "statement": result["statement"],

        "vwap": data["vwap"],
        "ema9": data["ema9"],
        "ema21": data["ema21"],
        "rsi": data["rsi"],
        "atr": data["atr"],
        "volume": data["volume"],

        "pcr": result["pcr"],
        "callOI": data["call_oi"],
        "putOI": data["put_oi"],
        "iv": data["iv"],

        "vwapStatus": "WAITING",
        "structure": data["structure"],

        "resistance": "--",
        "resistance2": "--",
        "support": "--",
        "support2": "--",

        "entry": "--",
        "stoploss": "--",
        "target1": "--",
        "target2": "--",

        "data_status": "ENGINE READY - LIVE CONNECTOR NOT CONFIGURED",

        "engine": {
            "technical_score": result["technical_score"],
            "oi_score": result["oi_score"],
            "pcr_score": result["pcr_score"],
            "reasons": result["reasons"]
        },

        "time": datetime.now().isoformat()
    }
