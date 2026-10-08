from pathlib import Path
from datetime import datetime
from typing import Dict, Any
from .data_provider import get_market_data
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
def calculate_candle_indicators(candles):
    if not candles or len(candles) < 2:
        return {
            "vwap": 0,
            "ema9": 0,
            "ema21": 0,
            "rsi": 50,
            "atr": 0,
            "volume": 0,
            "structure": "WAITING FOR CANDLE DATA"
        }

    closes = [float(c.get("close", 0)) for c in candles]
    highs = [float(c.get("high", 0)) for c in candles]
    lows = [float(c.get("low", 0)) for c in candles]
    volumes = [float(c.get("volume", 0)) for c in candles]

    closes = [x for x in closes if x > 0]
    highs = [x for x in highs if x > 0]
    lows = [x for x in lows if x > 0]

    if not closes:
        return {
            "vwap": 0,
            "ema9": 0,
            "ema21": 0,
            "rsi": 50,
            "atr": 0,
            "volume": 0,
            "structure": "WAITING FOR CANDLE DATA"
        }

    # -------------------------
    # VWAP
    # -------------------------
    cumulative_pv = 0
    cumulative_volume = 0

    for candle in candles:
        high = float(candle.get("high", 0))
        low = float(candle.get("low", 0))
        close = float(candle.get("close", 0))
        volume = float(candle.get("volume", 0))

        if close > 0:
            typical_price = (high + low + close) / 3
            cumulative_pv += typical_price * volume
            cumulative_volume += volume

    if cumulative_volume > 0:
        vwap = cumulative_pv / cumulative_volume
    else:
        vwap = closes[-1]

    # -------------------------
    # EMA helper
    # -------------------------
    def ema(values, period):
        if not values:
            return 0

        alpha = 2 / (period + 1)
        result = values[0]

        for value in values[1:]:
            result = (value * alpha) + (result * (1 - alpha))

        return result

    ema9 = ema(closes, 9)
    ema21 = ema(closes, 21)

    # -------------------------
    # RSI 14
    # -------------------------
    if len(closes) < 15:
        rsi = 50
    else:
        gains = []
        losses = []

        for i in range(1, len(closes)):
            change = closes[i] - closes[i - 1]

            if change > 0:
                gains.append(change)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(abs(change))

        period = min(14, len(gains))

        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        if avg_loss == 0:
            rsi = 100
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))

    # -------------------------
    # ATR 14
    # -------------------------
    true_ranges = []

    for i in range(1, len(candles)):
        high = float(candles[i].get("high", 0))
        low = float(candles[i].get("low", 0))
        previous_close = float(
            candles[i - 1].get("close", 0)
        )

        if high > 0 and low > 0 and previous_close > 0:
            tr = max(
                high - low,
                abs(high - previous_close),
                abs(low - previous_close)
            )

            true_ranges.append(tr)

    if true_ranges:
        atr_period = min(14, len(true_ranges))
        atr = sum(true_ranges[-atr_period:]) / atr_period
    else:
        atr = 0

    # -------------------------
    # Volume
    # -------------------------
    valid_volumes = [v for v in volumes if v >= 0]

    if valid_volumes:
        current_volume = valid_volumes[-1]
    else:
        current_volume = 0

    # -------------------------
    # Market structure
    # -------------------------
    structure = "SIDEWAYS"

    if len(closes) >= 6:
        recent_highs = highs[-6:]
        recent_lows = lows[-6:]

        previous_high = max(recent_highs[:-3])
        latest_high = max(recent_highs[-3:])

        previous_low = min(recent_lows[:-3])
        latest_low = min(recent_lows[-3:])

        if latest_high > previous_high and latest_low > previous_low:
            structure = "HH-HL"
        elif latest_high < previous_high and latest_low < previous_low:
            structure = "LH-LL"

    return {
        "vwap": round(vwap, 2),
        "ema9": round(ema9, 2),
        "ema21": round(ema21, 2),
        "rsi": round(rsi, 2),
        "atr": round(atr, 2),
        "volume": round(current_volume, 2),
        "structure": structure
    }
@app.get("/api/market")
def market(symbol: str = "NIFTY"):

    symbol = symbol.upper()

    if symbol not in ["NIFTY", "BANKNIFTY"]:
        symbol = "NIFTY"

    # ========================================================
    # LIVE FREE OPTION-CHAIN DATA
    # ========================================================

    live = get_market_data(symbol)

    # --------------------------------------------------------
    # If the free provider is unavailable
    # --------------------------------------------------------

    if not live.get("ok"):

        return {
            "symbol": symbol,

            "price": 0,
            "change": 0,

            "market_condition": "WAIT",
            "score": 0,
            "confidence": 0,
            "signal": "WAIT",

            "statement": (
                "Live option-chain data is currently unavailable. "
                "No trading signal is generated."
            ),

            "vwap": 0,
            "ema9": 0,
            "ema21": 0,
            "rsi": 50,
            "atr": 0,
            "volume": 0,

            "pcr": 0,
            "callOI": 0,
            "putOI": 0,
            "iv": 0,

            "vwapStatus": "WAITING",
            "structure": "WAITING",

            "resistance": "--",
            "resistance2": "--",
            "support": "--",
            "support2": "--",

            "entry": "--",
            "stoploss": "--",
            "target1": "--",
            "target2": "--",

            "data_status": (
                "FREE OPTION CHAIN CONNECTOR ERROR: "
                + str(live.get("error", "Unknown error"))
            ),

            "engine": {
                "technical_score": 0,
                "oi_score": 0,
                "pcr_score": 0,
                "reasons": []
            },

            "time": datetime.now().isoformat()
        }

    # ========================================================
    # EXTRACT LIVE CHAIN
    # ========================================================
    
    rows = live.get("rows", [])

    spot = safe_float(live.get("spot"))

    candles = live.get("candles", [])

    technical = calculate_candle_indicators(candles)

    total_call_oi = 0
    total_put_oi = 0

    total_call_change_oi = 0
    total_put_change_oi = 0

    iv_values = []
for row in rows:
        ce = row.get("ce", {})
        pe = row.get("pe", {})

        total_call_oi += int(
            safe_float(ce.get("oi"))
        )

        total_put_oi += int(
            safe_float(pe.get("oi"))
        )

        total_call_change_oi += int(
            safe_float(ce.get("change_oi"))
        )

        total_put_change_oi += int(
            safe_float(pe.get("change_oi"))
        )

        ce_iv = safe_float(ce.get("iv"))
        pe_iv = safe_float(pe.get("iv"))

        if ce_iv > 0:
            iv_values.append(ce_iv)

        if pe_iv > 0:
            iv_values.append(pe_iv)

    # ========================================================
    # PCR
    # ========================================================

        pcr = calculate_pcr(
        total_put_oi,
        total_call_oi
    )
    # ========================================================
    # BASIC OI POSITIONING
    # ========================================================

    if total_call_change_oi > 0:
        ce_position = "CALL WRITING"
    elif total_call_change_oi < 0:
        ce_position = "CALL UNWINDING"
    else:
        ce_position = "NEUTRAL"

    if total_put_change_oi > 0:
        pe_position = "PUT WRITING"
    elif total_put_change_oi < 0:
        pe_position = "PUT UNWINDING"
    else:
        pe_position = "NEUTRAL"

    # ========================================================
    # AVERAGE IV
    # ========================================================

    if iv_values:
        average_iv = round(
            sum(iv_values) / len(iv_values),
            2
        )
    else:
        average_iv = 0

    # ========================================================
    # TEMPORARY TECHNICAL VALUES
    #
    # These will be connected to actual candle data next.
    # We deliberately DO NOT create fake VWAP/EMA/RSI values.
    # ========================================================

    data = {

        "symbol": symbol,

        "price": spot,
        "change": 0,

        "vwap": 0,
        "ema9": 0,
        "ema21": 0,
        "rsi": 50,
        "atr": 0,

        "volume": 0,
        "volume_ratio": 1,

        "structure": "WAITING",

        "call_oi": total_call_oi,
        "put_oi": total_put_oi,

        "ce_position": ce_position,
        "pe_position": pe_position,

        "iv": average_iv
    }

    # ========================================================
    # INTELLIGENCE ENGINE
    # ========================================================

    result = decision_engine(data)

    # ========================================================
    # IMPORTANT SAFETY FILTER
    #
    # Until candle/VWAP/EMA data is connected, do not allow
    # the option-chain alone to generate an aggressive trade.
    # ========================================================

    signal = "WAIT"

    confidence = min(
        result["confidence"],
        55
    )

    condition = result["market_condition"]

    statement = (
        "Live option-chain data received. "
        "Technical candle confirmation is still being connected. "
        "Therefore the engine is observing OI/PCR conditions "
        "but will not generate a CALL/PUT entry yet."
    )

    # ========================================================
    # RESPONSE
    # ========================================================

    return {

        "symbol": symbol,

        "price": spot,
        "change": 0,

        "market_condition": condition,
        "score": result["score"],
        "confidence": confidence,
        "signal": signal,

        "statement": statement,

               "vwap": technical["vwap"],
        "ema9": technical["ema9"],
        "ema21": technical["ema21"],
        "rsi": technical["rsi"],
        "atr": technical["atr"],
        "volume": technical["volume"],
        "pcr": pcr,
        "callOI": total_call_oi,
        "putOI": total_put_oi,
        "iv": average_iv,

        "vwapStatus": (
    "ABOVE VWAP"
    if spot > technical["vwap"]
    else "BELOW VWAP"
    if spot < technical["vwap"]
    else "AT VWAP"
),
       "structure": technical["structure"],
        "resistance": "--",
        "resistance2": "--",
        "support": "--",
        "support2": "--",

        "entry": "--",
        "stoploss": "--",
        "target1": "--",
        "target2": "--",

        "data_status": (
            "LIVE FREE OPTION CHAIN CONNECTED "
            "(INDICATIVE SOURCE)"
        ),

        "expiry": live.get("expiry"),
        "max_pain": live.get("max_pain"),

        "oi_change": {
            "call": total_call_change_oi,
            "put": total_put_change_oi
        },

        "oi_positioning": {
            "call": ce_position,
            "put": pe_position
        },

        "engine": {
            "technical_score": result["technical_score"],
            "oi_score": result["oi_score"],
            "pcr_score": result["pcr_score"],
            "reasons": result["reasons"]
        },

        "time": datetime.now().isoformat()
    }
