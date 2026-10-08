from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import requests
import math
import statistics
import time
from datetime import datetime, timedelta, timezone

# ============================================================
# OPTION INTELLIGENCE FREE
# NIFTY / BANK NIFTY
# NSE OPTION CHAIN + 5 MIN CANDLES
# BUYER-FOCUSED DECISION ENGINE
# ============================================================

app = FastAPI(title="Option Intelligence Free")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# CONFIGURATION
# ============================================================

NSE = "https://www.nseindia.com"
CHART = "https://charting.nseindia.com"

OPTION_CHAIN_URL = NSE + "/api/option-chain-indices"
CHART_SYMBOLS_URL = CHART + "/v1/exchanges/symbolsDynamic"
CHART_HISTORY_URL = CHART + "/v1/charts/symbolHistoricalData"

SYMBOL_CONFIG = {
    "NIFTY": {
        "option_symbol": "NIFTY",
        "chart_symbol": "NIFTY 50",
        "token": "26000",
        "step": 50,
    },
    "BANKNIFTY": {
        "option_symbol": "BANKNIFTY",
        "chart_symbol": "NIFTY BANK",
        "token": "26004",
        "step": 100,
    },
}

CACHE_SECONDS = 8

_market_cache = {}


# ============================================================
# SESSION / NSE CONNECTION
# ============================================================

def create_session():
    """
    Create a browser-like NSE session.

    NSE frequently rejects direct requests unless the session
    first visits the main site and carries browser headers.
    """

    s = requests.Session()

    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/154.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;"
            "q=0.9,image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Referer": "https://www.nseindia.com/",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    })

    return s


def warm_nse(session):
    """
    Establish NSE cookies.
    """

    try:
        r = session.get(
            NSE,
            timeout=15,
            allow_redirects=True
        )

        return r.status_code < 500

    except Exception:
        return False


def get_option_chain(symbol):
    """
    Fetch NSE option chain with retries.
    """

    symbol = symbol.upper()

    if symbol not in SYMBOL_CONFIG:
        raise ValueError("Unsupported symbol")

    last_error = None

    for attempt in range(3):

        try:
            session = create_session()

            warm_nse(session)

            time.sleep(0.4)

            response = session.get(
                OPTION_CHAIN_URL,
                params={
                    "symbol": SYMBOL_CONFIG[symbol]["option_symbol"]
                },
                timeout=20,
                allow_redirects=True,
            )

            if response.status_code == 200:

                data = response.json()

                if isinstance(data, dict) and data.get("records"):
                    return data

                last_error = "NSE returned empty option-chain data"

            else:
                last_error = (
                    f"NSE option-chain HTTP {response.status_code}"
                )

        except Exception as e:
            last_error = str(e)

        time.sleep(1.0 + attempt)

    raise RuntimeError(
        f"Option chain unavailable: {last_error}"
    )


# ============================================================
# SAFE NUMERIC HELPERS
# ============================================================

def num(value, default=0.0):

    try:

        if value is None:
            return default

        if isinstance(value, str):
            value = value.replace(",", "").strip()

        return float(value)

    except Exception:
        return default


def integer(value, default=0):

    try:
        return int(float(value))
    except Exception:
        return default


def safe_div(a, b, default=0.0):

    if abs(b) < 1e-12:
        return default

    return a / b


def clamp(value, low, high):
    return max(low, min(high, value))


def round_price(value):
    if value == 0:
        return 0
    return round(value, 2)


# ============================================================
# NSE OPTION DATA
# ============================================================

def get_records(option_data):

    records = option_data.get("records", {})

    data = records.get("data", [])

    if not isinstance(data, list):
        return []

    return data


def get_spot(option_data):

    records = option_data.get("records", {})

    underlying = (
        records.get("underlyingValue")
        or option_data.get("underlyingValue")
    )

    return num(underlying)


def get_expiry(option_data):

    records = option_data.get("records", {})

    expiries = records.get("expiryDates", [])

    if expiries:
        return expiries[0]

    return ""


def get_max_pain(option_data):

    records = get_records(option_data)

    strikes = []

    for row in records:

        strike = num(row.get("strikePrice"))

        if strike > 0:
            strikes.append(strike)

    if not strikes:
        return 0

    strikes = sorted(set(strikes))

    best_strike = strikes[0]
    lowest_pain = float("inf")

    for strike in strikes:

        pain = 0

        for row in records:

            s = num(row.get("strikePrice"))

            ce = row.get("CE") or {}
            pe = row.get("PE") or {}

            ce_oi = num(ce.get("openInterest"))
            pe_oi = num(pe.get("openInterest"))

            # Calls lose value below strike
            if strike > s:
                pain += (strike - s) * ce_oi

            # Puts lose value above strike
            if strike < s:
                pain += (s - strike) * pe_oi

        if pain < lowest_pain:
            lowest_pain = pain
            best_strike = strike

    return best_strike


# ============================================================
# OPTION CHAIN ANALYSIS
# ============================================================

def analyse_options(option_data, symbol):

    records = get_records(option_data)

    spot = get_spot(option_data)

    step = SYMBOL_CONFIG[symbol]["step"]

    if not records or spot <= 0:
        return {
            "callOI": 0,
            "putOI": 0,
            "callChangeOI": 0,
            "putChangeOI": 0,
            "callVolume": 0,
            "putVolume": 0,
            "pcr": 0,
            "iv": 0,
            "callPosition": "UNAVAILABLE",
            "putPosition": "UNAVAILABLE",
            "callInterpretation": "UNAVAILABLE",
            "putInterpretation": "UNAVAILABLE",
            "resistance": 0,
            "resistance2": 0,
            "support": 0,
            "support2": 0,
            "maxPain": 0,
            "suggestedStrike": 0,
        }

    # --------------------------------------------------------
    # Total OI
    # --------------------------------------------------------

    total_call_oi = 0
    total_put_oi = 0

    total_call_change = 0
    total_put_change = 0

    total_call_volume = 0
    total_put_volume = 0

    iv_values = []

    strikes_data = []

    for row in records:

        strike = num(row.get("strikePrice"))

        ce = row.get("CE") or {}
        pe = row.get("PE") or {}

        ce_oi = integer(ce.get("openInterest"))
        pe_oi = integer(pe.get("openInterest"))

        ce_change = integer(
            ce.get("changeinOpenInterest")
        )

        pe_change = integer(
            pe.get("changeinOpenInterest")
        )

        ce_volume = integer(ce.get("totalTradedVolume"))
        pe_volume = integer(pe.get("totalTradedVolume"))

        total_call_oi += ce_oi
        total_put_oi += pe_oi

        total_call_change += ce_change
        total_put_change += pe_change

        total_call_volume += ce_volume
        total_put_volume += pe_volume

        ce_iv = num(ce.get("impliedVolatility"))
        pe_iv = num(pe.get("impliedVolatility"))

        if ce_iv > 0:
            iv_values.append(ce_iv)

        if pe_iv > 0:
            iv_values.append(pe_iv)

        strikes_data.append({
            "strike": strike,
            "ce_oi": ce_oi,
            "pe_oi": pe_oi,
            "ce_change": ce_change,
            "pe_change": pe_change,
            "ce_volume": ce_volume,
            "pe_volume": pe_volume,
            "ce_ltp": num(ce.get("lastPrice")),
            "pe_ltp": num(pe.get("lastPrice")),
            "ce_change_price": num(ce.get("change")),
            "pe_change_price": num(pe.get("change")),
        })

    # --------------------------------------------------------
    # PCR
    # --------------------------------------------------------

    pcr = safe_div(
        total_put_oi,
        total_call_oi,
        0
    )

    # --------------------------------------------------------
    # Average IV
    # --------------------------------------------------------

    iv = (
        statistics.mean(iv_values)
        if iv_values
        else 0
    )

    # --------------------------------------------------------
    # OI POSITIONING
    # --------------------------------------------------------

    call_position = classify_call(
        total_call_change,
        strikes_data,
        spot
    )

    put_position = classify_put(
        total_put_change,
        strikes_data,
        spot
    )

    # --------------------------------------------------------
    # OI RESISTANCE
    # --------------------------------------------------------

    resistance_levels = sorted(
        [
            x["strike"]
            for x in strikes_data
            if x["strike"] > spot
            and x["ce_oi"] > 0
        ],
        key=lambda x: abs(x - spot)
    )

    support_levels = sorted(
        [
            x["strike"]
            for x in strikes_data
            if x["strike"] < spot
            and x["pe_oi"] > 0
        ],
        key=lambda x: abs(x - spot)
    )

    resistance = (
        resistance_levels[0]
        if resistance_levels
        else round(spot / step) * step + step
    )

    resistance2 = (
        resistance_levels[1]
        if len(resistance_levels) > 1
        else resistance + step
    )

    support = (
        support_levels[0]
        if support_levels
        else round(spot / step) * step - step
    )

    support2 = (
        support_levels[1]
        if len(support_levels) > 1
        else support - step
    )

    # --------------------------------------------------------
    # STRIKE SELECTION
    # --------------------------------------------------------

    atm = round(spot / step) * step

    suggested_strike = atm

    # Option buyer preference:
    # Slightly ITM/ATM instead of far OTM.
    suggested_strike = atm

    return {
        "callOI": total_call_oi,
        "putOI": total_put_oi,

        "callChangeOI": total_call_change,
        "putChangeOI": total_put_change,

        "callVolume": total_call_volume,
        "putVolume": total_put_volume,

        "pcr": round(pcr, 2),

        "iv": round(iv, 2),

        "callPosition": call_position["position"],
        "putPosition": put_position["position"],

        "callInterpretation": call_position["interpretation"],
        "putInterpretation": put_position["interpretation"],

        "resistance": resistance,
        "resistance2": resistance2,

        "support": support,
        "support2": support2,

        "maxPain": get_max_pain(option_data),

        "suggestedStrike": suggested_strike,
    }


# ============================================================
# OI CLASSIFICATION
# ============================================================

def classify_call(total_change, rows, spot):

    nearby = [
        x for x in rows
        if abs(x["strike"] - spot) <= 5 * 100
    ]

    if total_change > 0:
        return {
            "position": "CALL WRITING",
            "interpretation": (
                "Call-side OI is building, "
                "adding resistance"
            )
        }

    if total_change < 0:
        return {
            "position": "CALL UNWINDING",
            "interpretation": (
                "Call-side OI is falling, "
                "reducing resistance"
            )
        }

    return {
        "position": "CALL NEUTRAL",
        "interpretation": "Call-side OI is neutral"
    }


def classify_put(total_change, rows, spot):

    if total_change > 0:
        return {
            "position": "PUT WRITING",
            "interpretation": (
                "Put-side OI is building, "
                "indicating support"
            )
        }

    if total_change < 0:
        return {
            "position": "PUT UNWINDING",
            "interpretation": (
                "Put-side OI is falling, "
                "weakening support"
            )
        }

    return {
        "position": "PUT NEUTRAL",
        "interpretation": "Put-side OI is neutral"
    }


# ============================================================
# CANDLE DATA
# ============================================================

def fetch_candles(symbol):

    config = SYMBOL_CONFIG[symbol]

    token = config["token"]
    chart_symbol = config["chart_symbol"]

    now = datetime.now(timezone.utc)

    start = now - timedelta(days=5)

    payload = {
        "token": token,
        "fromDate": int(start.timestamp()),
        "toDate": int(now.timestamp()),
        "symbol": chart_symbol,
        "symbolType": "Index",
        "chartType": "I",
        "timeInterval": 5,
    }

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/154 Safari/537.36"
        ),
        "Accept": "application/json,text/plain,*/*",
        "Referer": "https://www.nseindia.com/",
        "Origin": "https://www.nseindia.com",
    }

    last_error = None

    for attempt in range(3):

        try:

            response = requests.get(
                CHART_HISTORY_URL,
                params=payload,
                headers=headers,
                timeout=20,
            )

            if response.status_code == 200:

                data = response.json()

                candles = extract_candles(data)

                if candles:
                    return candles

                last_error = "NSE chart returned no candles"

            else:

                last_error = (
                    f"NSE chart HTTP "
                    f"{response.status_code}"
                )

        except Exception as e:
            last_error = str(e)

        time.sleep(1 + attempt)

    raise RuntimeError(
        f"Candle data unavailable: {last_error}"
    )


def extract_candles(data):

    if isinstance(data, list):
        raw = data

    elif isinstance(data, dict):

        raw = (
            data.get("data")
            or data.get("candles")
            or data.get("results")
            or []
        )

        if isinstance(raw, dict):

            raw = (
                raw.get("data")
                or raw.get("candles")
                or []
            )

    else:
        raw = []

    candles = []

    for row in raw:

        try:

            if isinstance(row, dict):

                timestamp = (
                    row.get("timestamp")
                    or row.get("time")
                    or row.get("date")
                )

                o = num(
                    row.get("open")
                    or row.get("Open")
                )

                h = num(
                    row.get("high")
                    or row.get("High")
                )

                l = num(
                    row.get("low")
                    or row.get("Low")
                )

                c = num(
                    row.get("close")
                    or row.get("Close")
                )

                v = num(
                    row.get("volume")
                    or row.get("Volume")
                )

            elif isinstance(row, list) and len(row) >= 5:

                timestamp = row[0]
                o = num(row[1])
                h = num(row[2])
                l = num(row[3])
                c = num(row[4])
                v = num(row[5]) if len(row) > 5 else 0

            else:
                continue

            if c <= 0:
                continue

            candles.append({
                "time": timestamp,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": v,
            })

        except Exception:
            continue

    candles.sort(
        key=lambda x: str(x["time"])
    )

    return candles[-250:]


# ============================================================
# TECHNICAL INDICATORS
# ============================================================

def ema(values, period):

    if not values:
        return 0

    if len(values) < period:
        return sum(values) / len(values)

    multiplier = 2 / (period + 1)

    result = sum(values[:period]) / period

    for price in values[period:]:
        result = (
            price - result
        ) * multiplier + result

    return result


def rsi(values, period=14):

    if len(values) < period + 1:
        return 50

    gains = []
    losses = []

    for i in range(1, len(values)):

        difference = values[i] - values[i - 1]

        if difference >= 0:
            gains.append(difference)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(difference))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):

        avg_gain = (
            avg_gain * (period - 1)
            + gains[i]
        ) / period

        avg_loss = (
            avg_loss * (period - 1)
            + losses[i]
        ) / period

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss

    return 100 - (100 / (1 + rs))


def atr(candles, period=14):

    if len(candles) < period + 1:
        return 0

    true_ranges = []

    for i in range(1, len(candles)):

        current = candles[i]
        previous = candles[i - 1]

        tr = max(
            current["high"] - current["low"],
            abs(
                current["high"]
                - previous["close"]
            ),
            abs(
                current["low"]
                - previous["close"]
            ),
        )

        true_ranges.append(tr)

    if len(true_ranges) < period:
        return 0

    return sum(true_ranges[-period:]) / period


def vwap(candles):

    if not candles:
        return 0

    total_pv = 0
    total_volume = 0

    for candle in candles:

        typical = (
            candle["high"]
            + candle["low"]
            + candle["close"]
        ) / 3

        volume = candle["volume"]

        if volume > 0:

            total_pv += typical * volume
            total_volume += volume

    if total_volume <= 0:
        return 0

    return total_pv / total_volume


def market_structure(candles):

    if len(candles) < 10:
        return "MIXED / RANGE"

    recent = candles[-10:]

    highs = [x["high"] for x in recent]
    lows = [x["low"] for x in recent]

    higher_highs = 0
    higher_lows = 0

    lower_highs = 0
    lower_lows = 0

    for i in range(1, len(recent)):

        if highs[i] > highs[i - 1]:
            higher_highs += 1

        if lows[i] > lows[i - 1]:
            higher_lows += 1

        if highs[i] < highs[i - 1]:
            lower_highs += 1

        if lows[i] < lows[i - 1]:
            lower_lows += 1

    if higher_highs >= 5 and higher_lows >= 5:
        return "HH-HL / UPTREND"

    if lower_highs >= 5 and lower_lows >= 5:
        return "LH-LL / DOWNTREND"

    return "MIXED / RANGE"


def volume_state(candles):

    if len(candles) < 20:
        return "NORMAL"

    volumes = [
        x["volume"]
        for x in candles[-21:-1]
        if x["volume"] > 0
    ]

    if not volumes:
        return "UNAVAILABLE"

    average = sum(volumes) / len(volumes)

    current = candles[-1]["volume"]

    if average <= 0:
        return "NORMAL"

    ratio = current / average

    if ratio >= 1.5:
        return "HIGH"

    if ratio <= 0.65:
        return "LOW"

    return "NORMAL"


# ============================================================
# DECISION ENGINE
# ============================================================

def build_decision(
    price,
    vw,
    ema9_value,
    ema21_value,
    rsi_value,
    atr_value,
    structure,
    volume_state_value,
    options,
):

    bullish = 0
    bearish = 0

    bullish_reasons = []
    bearish_reasons = []

    # --------------------------------------------------------
    # PRICE / VWAP
    # --------------------------------------------------------

    if vw > 0:

        if price > vw:

            bullish += 20
            bullish_reasons.append(
                "price is above VWAP"
            )

        elif price < vw:

            bearish += 20
            bearish_reasons.append(
                "price is below VWAP"
            )

    # --------------------------------------------------------
    # EMA
    # --------------------------------------------------------

    if ema9_value > ema21_value:

        bullish += 15
        bullish_reasons.append(
            "EMA9 is above EMA21"
        )

    elif ema9_value < ema21_value:

        bearish += 15
        bearish_reasons.append(
            "EMA9 is below EMA21"
        )

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    if rsi_value >= 55:

        bullish += 10
        bullish_reasons.append(
            "RSI has bullish momentum"
        )

    elif rsi_value <= 45:

        bearish += 10
        bearish_reasons.append(
            "RSI has bearish momentum"
        )

    # --------------------------------------------------------
    # MARKET STRUCTURE
    # --------------------------------------------------------

    if structure == "HH-HL / UPTREND":

        bullish += 15
        bullish_reasons.append(
            "higher-high higher-low structure"
        )

    elif structure == "LH-LL / DOWNTREND":

        bearish += 15
        bearish_reasons.append(
            "lower-high lower-low structure"
        )

    # --------------------------------------------------------
    # VOLUME
    # --------------------------------------------------------

    if volume_state_value == "HIGH":

        if bullish > bearish:
            bullish += 10
            bullish_reasons.append(
                "volume is confirming momentum"
            )

        elif bearish > bullish:
            bearish += 10
            bearish_reasons.append(
                "volume is confirming downside momentum"
            )

    # --------------------------------------------------------
    # OPTION OI
    # --------------------------------------------------------

    call_position = options["callPosition"]
    put_position = options["putPosition"]

    if call_position == "CALL WRITING":

        bearish += 10
        bearish_reasons.append(
            "call-side OI is building, adding resistance"
        )

    elif call_position == "CALL UNWINDING":

        bullish += 15
        bullish_reasons.append(
            "call-side OI is unwinding, reducing resistance"
        )

    if put_position == "PUT WRITING":

        bullish += 10
        bullish_reasons.append(
            "put-side OI is building, indicating support"
        )

    elif put_position == "PUT UNWINDING":

        bearish += 15
        bearish_reasons.append(
            "put-side OI is unwinding, weakening support"
        )

    # --------------------------------------------------------
    # PCR
    # --------------------------------------------------------

    pcr = options["pcr"]

    if pcr >= 1.05:

        bullish += 10
        bullish_reasons.append(
            "PCR is supportive of bullish positioning"
        )

    elif pcr <= 0.80:

        bearish += 10
        bearish_reasons.append(
            "PCR is bearish"
        )

    # --------------------------------------------------------
    # RAW DIRECTIONAL SCORE
    # --------------------------------------------------------

    directional_score = bullish - bearish

    # --------------------------------------------------------
    # MARKET CONDITION
    # --------------------------------------------------------

    if (
        bullish >= 55
        and directional_score >= 15
    ):

        condition = "BULLISH"

    elif (
        bearish >= 55
        and directional_score <= -15
    ):

        condition = "BEARISH"

    else:

        condition = "SIDEWAYS"

    # --------------------------------------------------------
    # BUYER CONFIRMATION
    # --------------------------------------------------------

    buyer_confirmed = True

    if condition == "SIDEWAYS":
        buyer_confirmed = False

    if volume_state_value == "LOW":
        buyer_confirmed = False

    if (
        abs(directional_score) < 20
    ):
        buyer_confirmed = False

    # Avoid buying directly against VWAP
    if condition == "BULLISH" and price < vw:
        buyer_confirmed = False

    if condition == "BEARISH" and price > vw:
        buyer_confirmed = False

    # --------------------------------------------------------
    # FINAL SIGNAL
    # --------------------------------------------------------

    if (
        condition == "BULLISH"
        and bullish >= 70
        and buyer_confirmed
    ):

        signal = "BUY CALL"

    elif (
        condition == "BEARISH"
        and bearish >= 70
        and buyer_confirmed
    ):

        signal = "BUY PUT"

    else:

        signal = "WAIT"

    # --------------------------------------------------------
    # SCORE
    # --------------------------------------------------------

    if condition == "BULLISH":
        score = bullish

    elif condition == "BEARISH":
        score = bearish

    else:
        score = max(bullish, bearish)

    score = int(clamp(score, 0, 100))

    # --------------------------------------------------------
    # CONFIDENCE
    # --------------------------------------------------------

    difference = abs(directional_score)

    confidence = int(
        clamp(
            50 + difference * 1.5,
            50,
            96
        )
    )

    if signal == "WAIT":
        confidence = min(confidence, 69)

    # --------------------------------------------------------
    # STORY
    # --------------------------------------------------------

    if condition == "BULLISH":

        reasons = bullish_reasons[:4]

        if signal == "BUY CALL":

            story = (
                "Bullish momentum is strengthening because "
                + "; ".join(reasons)
                + ". CALL buying conditions are confirmed."
            )

        else:

            story = (
                "Bullish bias is developing because "
                + "; ".join(reasons)
                + ". Waiting for stronger confirmation."
            )

    elif condition == "BEARISH":

        reasons = bearish_reasons[:4]

        if signal == "BUY PUT":

            story = (
                "Bearish momentum is strengthening because "
                + "; ".join(reasons)
                + ". PUT buying conditions are confirmed."
            )

        else:

            story = (
                "Bearish bias is developing because "
                + "; ".join(reasons)
                + ". Waiting for stronger confirmation."
            )

    else:

        story = (
            "Market conditions are mixed because "
            "directional indicators are not sufficiently aligned. "
            "Avoiding option buying until momentum and OI confirm."
        )

    return {
        "market_condition": condition,
        "bullish_score": bullish,
        "bearish_score": bearish,
        "directional_score": directional_score,
        "score": score,
        "confidence": confidence,
        "signal": signal,
        "story": story,
        "buyer_confirmed": buyer_confirmed,
        "bullish_reasons": bullish_reasons,
        "bearish_reasons": bearish_reasons,
    }


# ============================================================
# TRADE LEVELS
# ============================================================

def trade_levels(
    price,
    atr_value,
    signal,
    support,
    resistance,
):

    if price <= 0 or atr_value <= 0:

        return {
            "entry": 0,
            "stoploss": 0,
            "target1": 0,
            "target2": 0,
        }

    # CALL BUY

    if signal == "BUY CALL":

        entry = price

        sl = price - (1.20 * atr_value)

        t1 = price + (1.50 * atr_value)

        t2 = price + (2.50 * atr_value)

        # Don't put target below resistance
        if resistance > price:
            t1 = min(t1, resistance)

        return {
            "entry": round_price(entry),
            "stoploss": round_price(sl),
            "target1": round_price(t1),
            "target2": round_price(t2),
        }

    # PUT BUY

    if signal == "BUY PUT":

        entry = price

        sl = price + (1.20 * atr_value)

        t1 = price - (1.50 * atr_value)

        t2 = price - (2.50 * atr_value)

        if support > 0 and support < price:
            t1 = max(t1, support)

        return {
            "entry": round_price(entry),
            "stoploss": round_price(sl),
            "target1": round_price(t1),
            "target2": round_price(t2),
        }

    return {
        "entry": 0,
        "stoploss": 0,
        "target1": 0,
        "target2": 0,
    }


# ============================================================
# MAIN MARKET BUILDER
# ============================================================

def build_market(symbol):

    symbol = symbol.upper()

    if symbol not in SYMBOL_CONFIG:
        raise ValueError(
            "Use NIFTY or BANKNIFTY"
        )

    # --------------------------------------------------------
    # CACHE
    # --------------------------------------------------------

    now = time.time()

    cached = _market_cache.get(symbol)

    if cached:

        age = now - cached["time"]

        if age < CACHE_SECONDS:
            return cached["data"]

    # --------------------------------------------------------
    # FETCH DATA
    # --------------------------------------------------------

    option_data = get_option_chain(symbol)

    candles = fetch_candles(symbol)

    if not candles:
        raise RuntimeError(
            "No 5-minute candle data received"
        )

    options = analyse_options(
        option_data,
        symbol
    )

    # --------------------------------------------------------
    # TECHNICAL DATA
    # --------------------------------------------------------

    closes = [
        x["close"]
        for x in candles
    ]

    price = closes[-1]

    vw = vwap(candles)

    ema9_value = ema(
        closes,
        9
    )

    ema21_value = ema(
        closes,
        21
    )

    rsi_value = rsi(
        closes,
        14
    )

    atr_value = atr(
        candles,
        14
    )

    structure = market_structure(
        candles
    )

    vol_state = volume_state(
        candles
    )

    current_volume = candles[-1]["volume"]

    # --------------------------------------------------------
    # DECISION
    # --------------------------------------------------------

    decision = build_decision(
        price,
        vw,
        ema9_value,
        ema21_value,
        rsi_value,
        atr_value,
        structure,
        vol_state,
        options,
    )

    # --------------------------------------------------------
    # TRADE LEVELS
    # --------------------------------------------------------

    levels = trade_levels(
        price,
        atr_value,
        decision["signal"],
        options["support"],
        options["resistance"],
    )

    # --------------------------------------------------------
    # VWAP STATUS
    # --------------------------------------------------------

    if vw <= 0:

        vwap_status = "UNAVAILABLE"

    elif price > vw:

        vwap_status = "ABOVE VWAP"

    elif price < vw:

        vwap_status = "BELOW VWAP"

    else:

        vwap_status = "AT VWAP"

    # --------------------------------------------------------
    # PRICE CHANGE
    # --------------------------------------------------------

    if len(closes) >= 2:

        previous = closes[-2]

        change = price - previous

        change_percent = safe_div(
            change * 100,
            previous,
            0
        )

    else:

        change = 0
        change_percent = 0

    # --------------------------------------------------------
    # RESPONSE
    # --------------------------------------------------------

    result = {

        "ok": True,

        "symbol": symbol,

        "price": round_price(price),

        "change": round_price(change),

        "changePercent": round(
            change_percent,
            2
        ),

        "market_condition":
            decision["market_condition"],

        "score":
            decision["score"],

        "bullish_score":
            decision["bullish_score"],

        "bearish_score":
            decision["bearish_score"],

        "directional_score":
            decision["directional_score"],

        "confidence":
            decision["confidence"],

        "signal":
            decision["signal"],

        "statement":
            decision["story"],

        "story":
            decision["story"],

        "vwap":
            round_price(vw),

        "vwapStatus":
            vwap_status,

        "ema9":
            round_price(ema9_value),

        "ema21":
            round_price(ema21_value),

        "rsi":
            round(rsi_value, 2),

        "atr":
            round(atr_value, 2),

        "structure":
            structure,

        "volume":
            round_price(current_volume),

        "volumeState":
            vol_state,

        "pcr":
            options["pcr"],

        "iv":
            options["iv"],

        "callOI":
            options["callOI"],

        "putOI":
            options["putOI"],

        "oi_change": {
            "call":
                options["callChangeOI"],
            "put":
                options["putChangeOI"],
        },

        "oi_positioning": {
            "call":
                options["callPosition"],
            "put":
                options["putPosition"],
        },

        "oi_interpretation": {
            "call":
                options["callInterpretation"],
            "put":
                options["putInterpretation"],
        },

        "resistance":
            options["resistance"],

        "resistance2":
            options["resistance2"],

        "support":
            options["support"],

        "support2":
            options["support2"],

        "entry":
            levels["entry"],

        "stoploss":
            levels["stoploss"],

        "target1":
            levels["target1"],

        "target2":
            levels["target2"],

        "suggested_strike":
            options["suggestedStrike"],

        "strike_type":
            "ATM",

        "expiry":
            get_expiry(option_data),

        "max_pain":
            options["maxPain"],

        "data_status":
            "LIVE NSE OPTION CHAIN + LIVE NSE 5-MIN CANDLES",

        "engine": {

            "technical_score":
                decision["bullish_score"]
                - decision["bearish_score"],

            "oi_score":
                (
                    (
                        10
                        if options["putPosition"]
                        == "PUT WRITING"
                        else 0
                    )
                    +
                    (
                        15
                        if options["callPosition"]
                        == "CALL UNWINDING"
                        else 0
                    )
                    -
                    (
                        10
                        if options["callPosition"]
                        == "CALL WRITING"
                        else 0
                    )
                    -
                    (
                        15
                        if options["putPosition"]
                        == "PUT UNWINDING"
                        else 0
                    )
                ),

            "pcr_score":
                (
                    10
                    if options["pcr"] >= 1.05
                    else -10
                    if options["pcr"] <= 0.80
                    else 0
                ),

            "reasons":
                (
                    decision["bullish_reasons"]
                    if decision["market_condition"]
                    == "BULLISH"
                    else decision["bearish_reasons"]
                    if decision["market_condition"]
                    == "BEARISH"
                    else []
                ),
        },
    }

    # --------------------------------------------------------
    # CACHE
    # --------------------------------------------------------

    _market_cache[symbol] = {
        "time": now,
        "data": result,
    }

    return result


# ============================================================
# API ROUTES
# ============================================================

@app.get("/")
def home():

    return HTMLResponse(
        """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Option Intelligence Free</title>
            <meta name="viewport"
                  content="width=device-width,initial-scale=1">
        </head>
        <body style="
            font-family:Arial;
            text-align:center;
            padding:40px;
        ">
            <h1>Option Intelligence Free</h1>
            <p>NSE option-chain intelligence engine is running.</p>
            <p>
                Use the frontend dashboard for live analysis.
            </p>
        </body>
        </html>
        """
    )


@app.get("/favicon.ico")
def favicon():
    return {"ok": True}


@app.get("/api/health")
def health():

    return {
        "ok": True,
        "service": "option-intelligence-free",
        "status": "LIVE",
        "symbols": [
            "NIFTY",
            "BANKNIFTY"
        ],
    }


@app.get("/api/market")
def market(symbol: str = "NIFTY"):

    symbol = symbol.upper()

    try:

        return build_market(symbol)

    except Exception as e:

        # Return useful error information instead
        # of hiding the real failure.

        print(
            f"[MARKET ERROR] {symbol}: "
            f"{type(e).__name__}: {e}"
        )

        raise HTTPException(
            status_code=503,
            detail=(
                f"Market data temporarily unavailable "
                f"for {symbol}: "
                f"{type(e).__name__}: {str(e)}"
            ),
        )


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
def startup():

    print(
        "Option Intelligence Free started."
    )

    print(
        "Supported symbols: NIFTY, BANKNIFTY"
    )

    print(
        "BANKNIFTY chart token: 26004"
    )
