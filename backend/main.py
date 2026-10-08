from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pathlib import Path

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
import math
import threading
import time

try:
    from curl_cffi import requests
except Exception:
    import requests


# ============================================================
# APPLICATION
# ============================================================

app = FastAPI(
    title="Option Intelligence Engine",
    version="Final Free Edition"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


BASE_DIR = Path(__file__).resolve().parent
FRONTEND_FILE = BASE_DIR.parent / "frontend" / "index.html"


# ============================================================
# NSE / OPENCHART CONFIGURATION
# ============================================================

NSE = "https://www.nseindia.com"
CHART = "https://charting.nseindia.com"

OPTION_URL = NSE + "/api/option-chain-indices"
QUOTE_URL = NSE + "/api/quote-equity"

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
        "token": "26009",
        "step": 100,
    },
}


# ============================================================
# CACHE
# ============================================================

CACHE_TTL = 8.0

_cache: Dict[str, Dict[str, Any]] = {}
_cache_lock = threading.Lock()


def cache_get(symbol: str):
    with _cache_lock:
        item = _cache.get(symbol)

        if not item:
            return None

        if time.time() - item["time"] > CACHE_TTL:
            return None

        return item["data"]


def cache_set(symbol: str, data: Dict[str, Any]):
    with _cache_lock:
        _cache[symbol] = {
            "time": time.time(),
            "data": data,
        }


# ============================================================
# BASIC HELPERS
# ============================================================

def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default

        if isinstance(value, str):
            value = value.replace(",", "").strip()

        number = float(value)

        if not math.isfinite(number):
            return default

        return number

    except Exception:
        return default


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def round_price(value: float, step: float) -> float:
    if step <= 0:
        return value

    return round(value / step) * step


def clean_number(value: float):
    if abs(value - round(value)) < 0.000001:
        return int(round(value))

    return round(value, 2)


# ============================================================
# NSE SESSION
# ============================================================

def create_session():
    try:
        s = requests.Session(
            impersonate="chrome"
        )
    except Exception:
        s = requests.Session()

    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/154.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
    })

    return s


def warm_nse(session):
    try:
        session.get(
            NSE,
            timeout=12
        )
    except Exception:
        pass


def warm_chart(session):
    try:
        session.get(
            CHART,
            timeout=12
        )
    except Exception:
        pass


# ============================================================
# TECHNICAL INDICATORS
# ============================================================

def ema(values: List[float], period: int) -> float:
    if not values:
        return 0.0

    if len(values) < period:
        return sum(values) / len(values)

    multiplier = 2.0 / (period + 1.0)

    result = sum(values[:period]) / period

    for value in values[period:]:
        result = (
            (value - result) * multiplier
            + result
        )

    return result


def rsi(values: List[float], period: int = 14) -> float:
    if len(values) < period + 1:
        return 50.0

    gains = []
    losses = []

    for i in range(1, len(values)):
        change = values[i] - values[i - 1]

        if change > 0:
            gains.append(change)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(change))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (
            (avg_gain * (period - 1))
            + gains[i]
        ) / period

        avg_loss = (
            (avg_loss * (period - 1))
            + losses[i]
        ) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss

    return 100.0 - (
        100.0 / (1.0 + rs)
    )


def atr(candles: List[Dict[str, Any]], period: int = 14) -> float:
    if len(candles) < 2:
        return 0.0

    true_ranges = []

    for i in range(1, len(candles)):

        high = safe_float(candles[i]["high"])
        low = safe_float(candles[i]["low"])
        previous_close = safe_float(
            candles[i - 1]["close"]
        )

        tr = max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close)
        )

        true_ranges.append(tr)

    if not true_ranges:
        return 0.0

    if len(true_ranges) < period:
        return sum(true_ranges) / len(true_ranges)

    result = sum(
        true_ranges[:period]
    ) / period

    for value in true_ranges[period:]:
        result = (
            (result * (period - 1))
            + value
        ) / period

    return result


def calculate_vwap(candles: List[Dict[str, Any]]) -> float:

    numerator = 0.0
    denominator = 0.0

    for candle in candles:

        high = safe_float(candle["high"])
        low = safe_float(candle["low"])
        close = safe_float(candle["close"])
        volume = safe_float(candle.get("volume"))

        typical_price = (
            high + low + close
        ) / 3.0

        if volume > 0:

            numerator += (
                typical_price * volume
            )

            denominator += volume

    if denominator <= 0:
        return 0.0

    return numerator / denominator


def market_structure(candles: List[Dict[str, Any]]) -> str:

    if len(candles) < 8:
        return "INSUFFICIENT DATA"

    recent = candles[-8:]

    highs = [
        safe_float(x["high"])
        for x in recent
    ]

    lows = [
        safe_float(x["low"])
        for x in recent
    ]

    midpoint = len(recent) // 2

    first_high = max(highs[:midpoint])
    second_high = max(highs[midpoint:])

    first_low = min(lows[:midpoint])
    second_low = min(lows[midpoint:])

    higher_high = second_high > first_high
    higher_low = second_low > first_low

    lower_high = second_high < first_high
    lower_low = second_low < first_low

    if higher_high and higher_low:
        return "BULLISH HH-HL"

    if lower_high and lower_low:
        return "BEARISH LH-LL"

    return "MIXED / RANGE"


def volume_state(
    candles: List[Dict[str, Any]]
):

    if len(candles) < 21:
        return 0.0, "INSUFFICIENT DATA"

    current = safe_float(
        candles[-1].get("volume")
    )

    previous = [
        safe_float(x.get("volume"))
        for x in candles[-21:-1]
    ]

    previous = [
        x for x in previous
        if x > 0
    ]

    if not previous:
        return current, "UNAVAILABLE"

    average = sum(previous) / len(previous)

    if average <= 0:
        return current, "UNAVAILABLE"

    ratio = current / average

    if ratio >= 1.50:
        state = "HIGH"

    elif ratio >= 1.15:
        state = "ABOVE NORMAL"

    elif ratio <= 0.70:
        state = "LOW"

    else:
        state = "NORMAL"

    return current, state


# ============================================================
# INDEX CANDLES
# ============================================================

def parse_candle_response(result):

    if not isinstance(result, dict):
        return []

    raw = result.get("data", [])

    if not isinstance(raw, list):
        return []

    candles = []

    for item in raw:

        if not isinstance(item, dict):
            continue

        time_value = item.get("time")

        if time_value is None:
            time_value = item.get("timestamp")

        candle = {
            "time": time_value,
            "open": safe_float(item.get("open")),
            "high": safe_float(item.get("high")),
            "low": safe_float(item.get("low")),
            "close": safe_float(item.get("close")),
            "volume": safe_float(item.get("volume")),
        }

        if (
            candle["time"] is not None
            and candle["close"] > 0
            and candle["high"] > 0
            and candle["low"] > 0
        ):
            candles.append(candle)

    candles.sort(
        key=lambda x: safe_float(x["time"])
    )

    return candles[-500:]


def fetch_candles(symbol: str):

    config = SYMBOL_CONFIG[symbol]

    session = create_session()

    warm_nse(session)
    warm_chart(session)

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=7)

    payload = {
        "token": config["token"],
        "fromDate": int(start.timestamp()),
        "toDate": int(end.timestamp()),
        "symbol": config["chart_symbol"],
        "symbolType": "Index",
        "chartType": "I",
        "timeInterval": 5,
    }

    try:

        response = session.get(
            CHART +
            "/v1/charts/symbolHistoricalData",
            params=payload,
            timeout=20
        )

        response.raise_for_status()

        result = response.json()

        return parse_candle_response(result)

    except Exception:

        return []


# ============================================================
# FUTURES CANDLES FOR VOLUME / VWAP
# ============================================================

def fetch_futures_candles(symbol: str):

    session = create_session()

    warm_nse(session)
    warm_chart(session)

    search_symbol = (
        "NIFTY"
        if symbol == "NIFTY"
        else "BANKNIFTY"
    )

    try:

        response = session.get(
            CHART +
            "/v1/exchanges/symbolsDynamic",
            params={
                "symbol": search_symbol,
                "segment": "FO"
            },
            timeout=15
        )

        response.raise_for_status()

        result = response.json()

    except Exception:

        return []

    items = (
        result.get("data", [])
        if isinstance(result, dict)
        else []
    )

    if not isinstance(items, list):
        return []

    candidates = []

    for item in items:

        if not isinstance(item, dict):
            continue

        item_symbol = str(
            item.get("symbol", "")
        ).strip().upper()

        item_type = str(
            item.get("type", "")
        ).strip().lower()

        token = str(
            item.get("scripcode", "")
        ).strip()

        if not token or not item_symbol:
            continue

        if not item_symbol.endswith("FUT"):
            continue

        if item_type not in (
            "",
            "futures"
        ):
            continue

        candidates.append(item)

    if not candidates:
        return []

    today = datetime.now(
        timezone.utc
    ).date()

    def get_expiry(item):

        keys = (
            "expiry",
            "expiryDate",
            "expiry_date",
            "contractExpiry"
        )

        for key in keys:

            value = item.get(key)

            if not value:
                continue

            text_value = str(value).strip()

            formats = (
                "%d-%b-%Y",
                "%d-%B-%Y",
                "%Y-%m-%d",
                "%d-%b-%y",
            )

            for fmt in formats:

                try:
                    return datetime.strptime(
                        text_value,
                        fmt
                    ).date()

                except ValueError:
                    pass

        return None

    dated = [
        (get_expiry(item), item)
        for item in candidates
    ]

    future_dated = [
        pair
        for pair in dated
        if pair[0] is not None
        and pair[0] >= today
    ]

    if future_dated:

        future_dated.sort(
            key=lambda pair: pair[0]
        )

        info = future_dated[0][1]

    else:

        # Prefer the contract whose symbol
        # contains the nearest-looking month.
        info = candidates[0]

    token = str(
        info.get("scripcode", "")
    ).strip()

    chart_symbol = str(
        info.get("symbol", "")
    ).strip()

    symbol_type = str(
        info.get("type", "Futures")
    ).strip() or "Futures"

    if not token or not chart_symbol:
        return []

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=7)

    payload = {
        "token": token,
        "fromDate": int(start.timestamp()),
        "toDate": int(end.timestamp()),
        "symbol": chart_symbol,
        "symbolType": symbol_type,
        "chartType": "I",
        "timeInterval": 5,
    }

    try:

        response = session.get(
            CHART +
            "/v1/charts/symbolHistoricalData",
            params=payload,
            timeout=20
        )

        response.raise_for_status()

        result = response.json()

    except Exception:

        return []

    if (
        not isinstance(result, dict)
        or not result.get("data")
    ):
        return []

    return parse_candle_response(result)


# ============================================================
# OPTION CHAIN
# ============================================================

def fetch_option_chain(symbol: str):

    session = create_session()

    warm_nse(session)

    try:

        response = session.get(
            OPTION_URL,
            params={
                "symbol": symbol
            },
            timeout=20
        )

        response.raise_for_status()

        return response.json()

    except Exception:

        return None


def extract_option_data(raw):

    if not isinstance(raw, dict):
        return None

    records = raw.get("records")

    if not isinstance(records, dict):
        return None

    underlying = safe_float(
        records.get("underlyingValue")
    )

    expiry_dates = records.get(
        "expiryDates",
        []
    )

    rows = records.get(
        "data",
        []
    )

    if not isinstance(rows, list):
        rows = []

    return {
        "underlying": underlying,
        "expiry_dates": expiry_dates,
        "rows": rows,
    }


# ============================================================
# OPTION CLASSIFICATION
# ============================================================

def classify_option(
    oi_change: float,
    premium_change: float
):

    # OI rising + premium falling
    # = writing
    if oi_change > 0 and premium_change < 0:
        return "WRITING"

    # OI rising + premium rising
    # = long buildup
    if oi_change > 0 and premium_change > 0:
        return "LONG BUILDUP"

    # OI falling + premium rising
    # = short covering
    if oi_change < 0 and premium_change > 0:
        return "SHORT COVERING"

    # OI falling + premium falling
    # = long unwinding
    if oi_change < 0 and premium_change < 0:
        return "LONG UNWINDING"

    return "NEUTRAL"


def option_side_intelligence(
    rows: List[Dict[str, Any]],
    side: str
):

    stats = {
        "WRITING": 0.0,
        "LONG BUILDUP": 0.0,
        "SHORT COVERING": 0.0,
        "LONG UNWINDING": 0.0,
        "NEUTRAL": 0.0,
    }

    for row in rows:

        option = row.get(side)

        if not isinstance(option, dict):
            continue

        oi_change = safe_float(
            option.get(
                "changeinOpenInterest"
            )
        )

        premium_change = safe_float(
            option.get("change")
        )

        weight = abs(oi_change)

        classification = classify_option(
            oi_change,
            premium_change
        )

        stats[classification] += weight

    if not stats:
        return "NEUTRAL", stats

    dominant = max(
        stats,
        key=stats.get
    )

    if stats[dominant] <= 0:
        dominant = "NEUTRAL"

    return dominant, stats


# ============================================================
# OPTION ANALYSIS
# ============================================================

def analyse_options(
    symbol: str,
    option_data: Dict[str, Any],
    price: float
):

    config = SYMBOL_CONFIG[symbol]
    step = config["step"]

    rows = option_data["rows"]

    expiry_dates = option_data[
        "expiry_dates"
    ]

    if not expiry_dates:
        expiry = ""

    else:
        expiry = str(
            expiry_dates[0]
        )

    clean_rows = []

    for row in rows:

        if not isinstance(row, dict):
            continue

        strike = safe_float(
            row.get("strikePrice")
        )

        if strike <= 0:
            continue

        row_expiry = str(
            row.get("expiryDate", "")
        )

        if expiry and row_expiry != expiry:
            continue

        clean_rows.append(row)

    if not clean_rows:

        return {
            "pcr": 0.0,
            "callOI": 0.0,
            "putOI": 0.0,
            "callChangeOI": 0.0,
            "putChangeOI": 0.0,
            "iv": 0.0,
            "resistance": 0.0,
            "resistance2": 0.0,
            "support": 0.0,
            "support2": 0.0,
            "expiry": expiry,
            "max_pain": 0.0,
            "call_positioning": "N/A",
            "put_positioning": "N/A",
            "call_stats": {},
            "put_stats": {},
            "suggested_strike": 0.0,
            "rows_used": 0,
        }

    atm = round_price(
        price,
        step
    )

    # ATM +/- 5 strikes
    lower = atm - (5 * step)
    upper = atm + (5 * step)

    nearby = []

    for row in clean_rows:

        strike = safe_float(
            row.get("strikePrice")
        )

        if lower <= strike <= upper:
            nearby.append(row)

    if not nearby:
        nearby = clean_rows

    call_oi = 0.0
    put_oi = 0.0

    call_change_oi = 0.0
    put_change_oi = 0.0

    iv_values = []

    for row in nearby:

        ce = row.get("CE", {})
        pe = row.get("PE", {})

        if isinstance(ce, dict):

            call_oi += safe_float(
                ce.get("openInterest")
            )

            call_change_oi += safe_float(
                ce.get("changeinOpenInterest")
            )

            iv = safe_float(
                ce.get("impliedVolatility")
            )

            if iv > 0:
                iv_values.append(iv)

        if isinstance(pe, dict):

            put_oi += safe_float(
                pe.get("openInterest")
            )

            put_change_oi += safe_float(
                pe.get("changeinOpenInterest")
            )

            iv = safe_float(
                pe.get("impliedVolatility")
            )

            if iv > 0:
                iv_values.append(iv)

    pcr = (
        put_oi / call_oi
        if call_oi > 0
        else 0.0
    )

    iv = (
        sum(iv_values) / len(iv_values)
        if iv_values
        else 0.0
    )

    # --------------------------------------------------------
    # SUPPORT / RESISTANCE
    # --------------------------------------------------------

    call_levels = []
    put_levels = []

    for row in clean_rows:

        strike = safe_float(
            row.get("strikePrice")
        )

        ce = row.get("CE", {})
        pe = row.get("PE", {})

        if strike > price and isinstance(ce, dict):

            call_levels.append(
                (
                    safe_float(
                        ce.get("openInterest")
                    ),
                    strike
                )
            )

        if strike < price and isinstance(pe, dict):

            put_levels.append(
                (
                    safe_float(
                        pe.get("openInterest")
                    ),
                    strike
                )
            )

    call_levels.sort(
        reverse=True
    )

    put_levels.sort(
        reverse=True
    )

    resistance = 0.0
    resistance2 = 0.0

    if call_levels:

        call_levels_sorted = sorted(
            call_levels,
            reverse=True
        )

        resistance = call_levels_sorted[0][1]

        if len(call_levels_sorted) > 1:
            resistance2 = (
                call_levels_sorted[1][1]
            )

    support = 0.0
    support2 = 0.0

    if put_levels:

        put_levels_sorted = sorted(
            put_levels,
            reverse=True
        )

        support = put_levels_sorted[0][1]

        if len(put_levels_sorted) > 1:
            support2 = (
                put_levels_sorted[1][1]
            )

    # --------------------------------------------------------
    # OI CLASSIFICATION
    # --------------------------------------------------------

    call_positioning, call_stats = (
        option_side_intelligence(
            nearby,
            "CE"
        )
    )

    put_positioning, put_stats = (
        option_side_intelligence(
            nearby,
            "PE"
        )
    )

    # --------------------------------------------------------
    # MAX PAIN
    # --------------------------------------------------------

    strikes = []

    for row in clean_rows:

        strike = safe_float(
            row.get("strikePrice")
        )

        if strike > 0:
            strikes.append(
                strike
            )

    strikes = sorted(
        set(strikes)
    )

    max_pain = 0.0
    minimum_loss = None

    for candidate in strikes:

        total_loss = 0.0

        for row in clean_rows:

            strike = safe_float(
                row.get("strikePrice")
            )

            ce = row.get("CE", {})
            pe = row.get("PE", {})

            ce_oi = (
                safe_float(
                    ce.get("openInterest")
                )
                if isinstance(ce, dict)
                else 0.0
            )

            pe_oi = (
                safe_float(
                    pe.get("openInterest")
                )
                if isinstance(pe, dict)
                else 0.0
            )

            if candidate > strike:
                total_loss += (
                    candidate - strike
                ) * ce_oi

            if candidate < strike:
                total_loss += (
                    strike - candidate
                ) * pe_oi

        if (
            minimum_loss is None
            or total_loss < minimum_loss
        ):

            minimum_loss = total_loss
            max_pain = candidate

    # --------------------------------------------------------
    # SUGGESTED STRIKE
    # --------------------------------------------------------

    suggested_call = atm + step
    suggested_put = atm - step

    return {
        "pcr": round(pcr, 2),
        "callOI": round(call_oi, 2),
        "putOI": round(put_oi, 2),
        "callChangeOI": round(
            call_change_oi,
            2
        ),
        "putChangeOI": round(
            put_change_oi,
            2
        ),
        "iv": round(iv, 2),
        "resistance": clean_number(
            resistance
        ),
        "resistance2": clean_number(
            resistance2
        ),
        "support": clean_number(
            support
        ),
        "support2": clean_number(
            support2
        ),
        "expiry": expiry,
        "max_pain": clean_number(
            max_pain
        ),
        "call_positioning": call_positioning,
        "put_positioning": put_positioning,
        "call_stats": call_stats,
        "put_stats": put_stats,
        "suggested_call": clean_number(
            suggested_call
        ),
        "suggested_put": clean_number(
            suggested_put
        ),
        "rows_used": len(nearby),
    }


# ============================================================
# DECISION ENGINE
# ============================================================

def decision_engine(
    symbol: str,
    price: float,
    vwap: float,
    ema9_value: float,
    ema21_value: float,
    rsi_value: float,
    atr_value: float,
    structure: str,
    volume_state_value: str,
    option: Dict[str, Any],
):

    bull = 0.0
    bear = 0.0

    bull_reasons = []
    bear_reasons = []

    # ========================================================
    # 1. PRICE / VWAP — 20 POINTS
    # ========================================================

    if vwap > 0:

        distance = (
            price - vwap
        ) / vwap * 100

        if distance > 0.10:

            bull += 20

            bull_reasons.append(
                "price is above VWAP"
            )

        elif distance < -0.10:

            bear += 20

            bear_reasons.append(
                "price is below VWAP"
            )

        else:

            bull += 5
            bear += 5

    # ========================================================
    # 2. EMA 9/21 — 15 POINTS
    # ========================================================

    if ema9_value > ema21_value:

        bull += 15

        bull_reasons.append(
            "EMA9 is above EMA21"
        )

    elif ema9_value < ema21_value:

        bear += 15

        bear_reasons.append(
            "EMA9 is below EMA21"
        )

    # ========================================================
    # 3. RSI — 10 POINTS
    # ========================================================

    if rsi_value >= 60:

        bull += 10

        bull_reasons.append(
            "RSI shows bullish momentum"
        )

    elif rsi_value <= 40:

        bear += 10

        bear_reasons.append(
            "RSI shows bearish momentum"
        )

    elif rsi_value >= 52:

        bull += 5

    elif rsi_value <= 48:

        bear += 5

    # ========================================================
    # 4. MARKET STRUCTURE — 15 POINTS
    # ========================================================

    if structure == "BULLISH HH-HL":

        bull += 15

        bull_reasons.append(
            "bullish HH-HL structure is present"
        )

    elif structure == "BEARISH LH-LL":

        bear += 15

        bear_reasons.append(
            "bearish LH-LL structure is present"
        )

    # ========================================================
    # 5. VOLUME — 10 POINTS
    # ========================================================

    if volume_state_value in (
        "HIGH",
        "ABOVE NORMAL"
    ):

        # Volume confirms whichever side
        # already has stronger technical evidence.
        if bull > bear:

            bull += 10

            bull_reasons.append(
                "volume is confirming the move"
            )

        elif bear > bull:

            bear += 10

            bear_reasons.append(
                "volume is confirming the move"
            )

    # ========================================================
    # 6. CALL OI POSITIONING — 15 POINTS
    # ========================================================

    call_position = option.get(
        "call_positioning",
        "NEUTRAL"
    )

    put_position = option.get(
        "put_positioning",
        "NEUTRAL"
    )

    if call_position == "WRITING":

        bear += 15

        bear_reasons.append(
            "call-side OI is building, adding resistance"
        )

    elif call_position == "SHORT COVERING":

        bull += 15

        bull_reasons.append(
            "call short covering is supporting the upside"
        )

    elif call_position == "LONG UNWINDING":

        bear += 7

        bear_reasons.append(
            "call-side long unwinding is bearish"
        )

    elif call_position == "LONG BUILDUP":

        bull += 7

        bull_reasons.append(
            "call-side long buildup is bullish"
        )

    # ========================================================
    # 7. PUT OI POSITIONING — 15 POINTS
    # ========================================================

    if put_position == "WRITING":

        bull += 15

        bull_reasons.append(
            "put writing is creating support"
        )

    elif put_position == "SHORT COVERING":

        bear += 15

        bear_reasons.append(
            "put short covering is bearish"
        )

    elif put_position == "LONG UNWINDING":

        bear += 12

        bear_reasons.append(
            "put-side OI is unwinding, weakening support"
        )

    elif put_position == "LONG BUILDUP":

        bear += 7

        bear_reasons.append(
            "put-side long buildup is bearish"
        )

    # ========================================================
    # 8. PCR — 10 POINTS
    # ========================================================

    pcr = safe_float(
        option.get("pcr")
    )

    if pcr >= 1.10:

        bull += 10

        bull_reasons.append(
            "PCR is supportive of bullish positioning"
        )

    elif pcr <= 0.80:

        bear += 10

        bear_reasons.append(
            "PCR is bearish"
        )

    elif pcr >= 0.95:

        bull += 5

    elif pcr <= 0.90:

        bear += 5

    # ========================================================
    # NORMALISE
    # ========================================================

    bull = clamp(
        bull,
        0,
        100
    )

    bear = clamp(
        bear,
        0,
        100
    )

    directional_difference = (
        bull - bear
    )

    if bull > bear:

        direction = "BULLISH"
        raw_score = bull
        reasons = bull_reasons

    elif bear > bull:

        direction = "BEARISH"
        raw_score = bear
        reasons = bear_reasons

    else:

        direction = "SIDEWAYS"
        raw_score = 0
        reasons = []

    # ========================================================
    # CONFLICT FILTER
    # ========================================================

    conflict = (
        abs(bull - bear) < 12
    )

    sideways_structure = (
        structure == "MIXED / RANGE"
    )

    near_vwap = False

    if vwap > 0:

        near_vwap = (
            abs(price - vwap)
            / vwap
            < 0.0015
        )

    # ========================================================
    # BUYER CONFIRMATION
    # ========================================================

    buyer_confirmation = True

    confirmation_reasons = []

    if near_vwap:

        buyer_confirmation = False

        confirmation_reasons.append(
            "price is too close to VWAP"
        )

    if sideways_structure:

        buyer_confirmation = False

        confirmation_reasons.append(
            "price structure is mixed/range"
        )

    if volume_state_value == "LOW":

        buyer_confirmation = False

        confirmation_reasons.append(
            "volume is weak"
        )

    # Strong conflict overrides everything.
    if conflict:

        buyer_confirmation = False

        confirmation_reasons.append(
            "bullish and bearish evidence are too close"
        )

    # ========================================================
    # FINAL SCORE
    # ========================================================

    score = int(
        round(
            clamp(
                raw_score,
                0,
                100
            )
        )
    )

    # ========================================================
    # FINAL SIGNAL
    # ========================================================

    signal = "WAIT"

    if (
        direction == "BULLISH"
        and score >= 70
        and buyer_confirmation
    ):

        signal = "BUY CALL"

    elif (
        direction == "BEARISH"
        and score >= 70
        and buyer_confirmation
    ):

        signal = "BUY PUT"

    else:

        signal = "WAIT"

    # ========================================================
    # CONFIDENCE
    # ========================================================

    # Confidence deliberately follows the same score.
    # This prevents:
    # score = 0 + confidence = 96%
    confidence = score

    # If signal is WAIT because of a hard filter,
    # reduce confidence to reflect the uncertainty.
    if signal == "WAIT":

        confidence = min(
            confidence,
            69
        )

    # ========================================================
    # MARKET CONDITION
    # ========================================================

    if signal == "BUY CALL":

        market_condition = "BULLISH"

    elif signal == "BUY PUT":

        market_condition = "BEARISH"

    elif (
        direction == "BULLISH"
        and score >= 55
        and not conflict
    ):

        market_condition = "BULLISH"

    elif (
        direction == "BEARISH"
        and score >= 55
        and not conflict
    ):

        market_condition = "BEARISH"

    else:

        market_condition = "SIDEWAYS"

    # ========================================================
    # TRADE LEVELS
    # ========================================================

    entry = 0.0
    stoploss = 0.0
    target1 = 0.0
    target2 = 0.0

    if (
        signal == "BUY CALL"
        and atr_value > 0
    ):

        entry = price

        stoploss = (
            entry - 1.20 * atr_value
        )

        target1 = (
            entry + 1.50 * atr_value
        )

        target2 = (
            entry + 2.50 * atr_value
        )

    elif (
        signal == "BUY PUT"
        and atr_value > 0
    ):

        entry = price

        stoploss = (
            entry + 1.20 * atr_value
        )

        target1 = (
            entry - 1.50 * atr_value
        )

        target2 = (
            entry - 2.50 * atr_value
        )

    # ========================================================
    # MARKET STORY
    # ========================================================

    if signal == "BUY CALL":

        headline = (
            "Bullish momentum is confirmed"
        )

        if bull_reasons:

            body = "; ".join(
                bull_reasons[:5]
            )

        else:

            body = (
                "multiple bullish factors are aligned"
            )

        statement = (
            headline
            + " because "
            + body
            + ". CALL buying conditions are confirmed."
        )

    elif signal == "BUY PUT":

        headline = (
            "Bearish momentum is confirmed"
        )

        if bear_reasons:

            body = "; ".join(
                bear_reasons[:5]
            )

        else:

            body = (
                "multiple bearish factors are aligned"
            )

        statement = (
            headline
            + " because "
            + body
            + ". PUT buying conditions are confirmed."
        )

    else:

        if direction == "BULLISH":

            if bull_reasons:

                body = "; ".join(
                    bull_reasons[:4]
                )

            else:

                body = (
                    "some bullish evidence is present"
                )

            statement = (
                "Bullish bias is developing because "
                + body
                + ". However, confirmation is insufficient for an option-buying entry. WAIT."
            )

        elif direction == "BEARISH":

            if bear_reasons:

                body = "; ".join(
                    bear_reasons[:4]
                )

            else:

                body = (
                    "some bearish evidence is present"
                )

            statement = (
                "Bearish bias is developing because "
                + body
                + ". However, confirmation is insufficient for an option-buying entry. WAIT."
            )

        else:

            statement = (
                "Market conditions are mixed. "
                "Bullish and bearish evidence are not sufficiently separated. WAIT."
            )

    if confirmation_reasons and signal == "WAIT":

        statement += (
            " Filter: "
            + "; ".join(
                confirmation_reasons[:3]
            )
            + "."
        )

    return {
        "market_condition": market_condition,
        "score": score,
        "confidence": confidence,
        "directional_score": int(
            round(
                clamp(
                    directional_difference,
                    -100,
                    100
                )
            )
        ),
        "bullish_score": int(
            round(bull)
        ),
        "bearish_score": int(
            round(bear)
        ),
        "signal": signal,
        "statement": statement,
        "entry": clean_number(entry),
        "stoploss": clean_number(stoploss),
        "target1": clean_number(target1),
        "target2": clean_number(target2),
        "bullish_reasons": bull_reasons,
        "bearish_reasons": bear_reasons,
        "filter_reasons": confirmation_reasons,
    }


# ============================================================
# COMPLETE MARKET ANALYSIS
# ============================================================

def build_market(symbol: str):

    cached = cache_get(symbol)

    if cached is not None:
        return cached

    config = SYMBOL_CONFIG[symbol]

    # --------------------------------------------------------
    # FETCH INDEX CANDLES
    # --------------------------------------------------------

    candles = fetch_candles(symbol)

    if not candles:

        raise RuntimeError(
            "5-minute candle data unavailable"
        )

    closes = [
        safe_float(x["close"])
        for x in candles
        if safe_float(x["close"]) > 0
    ]

    if not closes:

        raise RuntimeError(
            "No valid candle prices"
        )

    price = closes[-1]

    # --------------------------------------------------------
    # INDEX TECHNICALS
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # FUTURES DATA
    # --------------------------------------------------------

    futures_candles = (
        fetch_futures_candles(symbol)
    )

    if futures_candles:

        vwap = calculate_vwap(
            futures_candles
        )

        volume_value, volume_state_value = (
            volume_state(
                futures_candles
            )
        )

        futures_status = (
            "LIVE FUTURES VOLUME"
        )

    else:

        # Fall back to index candle VWAP.
        vwap = calculate_vwap(
            candles
        )

        volume_value, volume_state_value = (
            volume_state(
                candles
            )
        )

        futures_status = (
            "INDEX VOLUME FALLBACK"
        )

    if vwap <= 0:

        vwap = calculate_vwap(
            candles
        )

    # --------------------------------------------------------
    # OPTION CHAIN
    # --------------------------------------------------------

    raw_options = fetch_option_chain(
        config["option_symbol"]
    )

    option_data = extract_option_data(
        raw_options
    )

    if option_data is None:

        raise RuntimeError(
            "NSE option-chain data unavailable"
        )

    # Prefer NSE's underlying value if valid.
    nse_underlying = safe_float(
        option_data.get("underlying")
    )

    if nse_underlying > 0:

        price = nse_underlying

    option = analyse_options(
        symbol,
        option_data,
        price
    )

    # --------------------------------------------------------
    # VWAP STATUS
    # --------------------------------------------------------

    if vwap > 0:

        if price > vwap * 1.001:

            vwap_status = "ABOVE VWAP"

        elif price < vwap * 0.999:

            vwap_status = "BELOW VWAP"

        else:

            vwap_status = "AT VWAP"

    else:

        vwap_status = "VWAP UNAVAILABLE"

    # --------------------------------------------------------
    # DECISION ENGINE
    # --------------------------------------------------------

    decision = decision_engine(
        symbol=symbol,
        price=price,
        vwap=vwap,
        ema9_value=ema9_value,
        ema21_value=ema21_value,
        rsi_value=rsi_value,
        atr_value=atr_value,
        structure=structure,
        volume_state_value=volume_state_value,
        option=option,
    )

    # --------------------------------------------------------
    # SUGGESTED STRIKE
    # --------------------------------------------------------

    suggested_strike = 0

    if decision["signal"] == "BUY CALL":

        suggested_strike = option.get(
            "suggested_call",
            0
        )

        strike_type = "CALL"

    elif decision["signal"] == "BUY PUT":

        suggested_strike = option.get(
            "suggested_put",
            0
        )

        strike_type = "PUT"

    else:

        suggested_strike = 0
        strike_type = "WAIT"

    # --------------------------------------------------------
    # DATA STATUS
    # --------------------------------------------------------

    data_status = (
        "LIVE NSE OPTION CHAIN + "
        "LIVE 5-MIN CANDLES + "
        + futures_status
    )

    # --------------------------------------------------------
    # FINAL RESPONSE
    # --------------------------------------------------------

    result = {
        "ok": True,
        "symbol": symbol,

        "price": round(
            price,
            2
        ),

        "change": 0,

        "market_condition":
            decision["market_condition"],

        "score":
            decision["score"],

        "directional_score":
            decision["directional_score"],

        "bullish_score":
            decision["bullish_score"],

        "bearish_score":
            decision["bearish_score"],

        "confidence":
            decision["confidence"],

        "signal":
            decision["signal"],

        "statement":
            decision["statement"],

        # ----------------------------------------------------
        # TECHNICAL
        # ----------------------------------------------------

        "vwap": round(
            vwap,
            2
        ),

        "vwapStatus":
            vwap_status,

        "ema9": round(
            ema9_value,
            2
        ),

        "ema21": round(
            ema21_value,
            2
        ),

        "rsi": round(
            rsi_value,
            2
        ),

        "atr": round(
            atr_value,
            2
        ),

        "volume":
            round(
                volume_value,
                2
            ),

        "volumeState":
            volume_state_value,

        "structure":
            structure,

        # ----------------------------------------------------
        # OPTION CHAIN
        # ----------------------------------------------------

        "pcr":
            option["pcr"],

        "callOI":
            option["callOI"],

        "putOI":
            option["putOI"],

        "iv":
            option["iv"],

        "oi_change": {
            "call":
                option["callChangeOI"],
            "put":
                option["putChangeOI"],
        },

        "oi_positioning": {
            "call":
                option["call_positioning"],
            "put":
                option["put_positioning"],
        },

        # Detailed OI intelligence
        "oi_detail": {
            "call": option[
                "call_stats"
            ],
            "put": option[
                "put_stats"
            ],
        },

        # ----------------------------------------------------
        # SUPPORT / RESISTANCE
        # ----------------------------------------------------

        "resistance":
            option["resistance"],

        "resistance2":
            option["resistance2"],

        "support":
            option["support"],

        "support2":
            option["support2"],

        # ----------------------------------------------------
        # TRADE
        # ----------------------------------------------------

        "entry":
            decision["entry"],

        "stoploss":
            decision["stoploss"],

        "target1":
            decision["target1"],

        "target2":
            decision["target2"],

        "suggested_strike":
            suggested_strike,

        "strike_type":
            strike_type,

        # ----------------------------------------------------
        # EXPIRY / MAX PAIN
        # ----------------------------------------------------

        "expiry":
            option["expiry"],

        "max_pain":
            option["max_pain"],

        # ----------------------------------------------------
        # ENGINE DETAILS
        # ----------------------------------------------------

        "engine": {

            "technical_score": int(
                round(
                    decision["bullish_score"]
                    - decision["bearish_score"]
                )
            ),

            "bullish_score":
                decision["bullish_score"],

            "bearish_score":
                decision["bearish_score"],

            "reasons":
                (
                    decision["bullish_reasons"]
                    if decision["market_condition"]
                    == "BULLISH"
                    else decision[
                        "bearish_reasons"
                    ]
                ),

            "filter_reasons":
                decision[
                    "filter_reasons"
                ],

        },

        "data_status":
            data_status,

        "timestamp":
            datetime.now(
                timezone.utc
            ).isoformat(),

    }

    cache_set(
        symbol,
        result
    )

    return result


# ============================================================
# API ENDPOINTS
# ============================================================

@app.get("/api/health")
def health():

    return {
        "ok": True,
        "service":
            "Option Intelligence Engine",
        "status":
            "online",
        "timestamp":
            datetime.now(
                timezone.utc
            ).isoformat()
    }


@app.get("/api/market")
def market(
    symbol: str = Query(
        "NIFTY"
    )
):

    symbol = symbol.upper().strip()

    if symbol not in SYMBOL_CONFIG:

        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "error":
                    "Invalid symbol. Use NIFTY or BANKNIFTY."
            }
        )

    try:

        result = build_market(
            symbol
        )

        return result

    except Exception as error:

        # Return a clean API response instead
        # of allowing the frontend to break.
        return JSONResponse(
            status_code=503,
            content={
                "ok": False,
                "error":
                    "Live market data temporarily unavailable.",
                "detail":
                    str(error),
                "symbol":
                    symbol,
                "timestamp":
                    datetime.now(
                        timezone.utc
                    ).isoformat()
            }
        )


# ============================================================
# FRONTEND
# ============================================================

@app.get("/")
def home():

    if FRONTEND_FILE.exists():

        return FileResponse(
            FRONTEND_FILE
        )

    return JSONResponse(
        status_code=404,
        content={
            "ok": False,
            "error":
                "Frontend file not found."
        }
    )


# ============================================================
# OPTIONAL FAVICON HANDLER
# ============================================================

@app.get("/favicon.ico")
def favicon():

    return JSONResponse(
        status_code=204,
        content=None
    )
