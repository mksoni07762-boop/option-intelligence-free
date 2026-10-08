from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pathlib import Path

import math
import statistics
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

try:
    from curl_cffi import requests as cffi_requests
    CURL_CFFI_AVAILABLE = True
except Exception:
    import requests as cffi_requests
    CURL_CFFI_AVAILABLE = False


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="Option Intelligence Free",
    version="Final NSE V3"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# PATHS
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_FILE = BASE_DIR.parent / "frontend" / "index.html"


# ============================================================
# NSE CONFIG
# ============================================================

NSE_HOME = "https://www.nseindia.com"
NSE_OPTION_PAGE = f"{NSE_HOME}/option-chain"

NSE_CONTRACT_INFO = (
    f"{NSE_HOME}/api/option-chain-contract-info"
)

NSE_OPTION_CHAIN_V3 = (
    f"{NSE_HOME}/api/option-chain-v3"
)

NSE_ALL_INDICES = (
    f"{NSE_HOME}/api/allIndices"
)

CHART_URL = (
    "https://charting.nseindia.com/v1/charts/"
    "symbolHistoricalData"
)

SYMBOLS_DYNAMIC_URL = (
    "https://charting.nseindia.com/v1/exchanges/"
    "symbolsDynamic"
)


# NSE OpenChart tokens
INDEX_TOKENS = {
    "NIFTY": "26000",
    "BANKNIFTY": "26004",
}


INDEX_NAMES = {
    "NIFTY": "NIFTY 50",
    "BANKNIFTY": "NIFTY BANK",
}


# ============================================================
# SESSION
# ============================================================

session = None
last_warm_time = 0

CACHE = {}

CACHE_TTL = 8


def create_session():
    global session

    if CURL_CFFI_AVAILABLE:
        session = cffi_requests.Session(
            impersonate="chrome"
        )
    else:
        session = cffi_requests.Session()

    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/140.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,image/avif,image/webp,"
            "image/apng,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
        "Referer": NSE_OPTION_PAGE,
        "Origin": NSE_HOME,
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    })

    return session


create_session()


# ============================================================
# BASIC HELPERS
# ============================================================

def now_ms():
    return int(time.time() * 1000)


def safe_float(value, default=0.0):
    try:
        if value is None:
            return default

        if isinstance(value, str):
            value = (
                value.replace(",", "")
                .replace("%", "")
                .strip()
            )

            if value in ("", "-", "--", "NA", "N/A"):
                return default

        return float(value)

    except Exception:
        return default


def safe_int(value, default=0):
    try:
        if value is None:
            return default

        if isinstance(value, str):
            value = (
                value.replace(",", "")
                .replace("%", "")
                .strip()
            )

            if value in ("", "-", "--", "NA", "N/A"):
                return default

        return int(float(value))

    except Exception:
        return default


def first_value(d, keys, default=None):
    if not isinstance(d, dict):
        return default

    for key in keys:
        if key in d and d[key] is not None:
            return d[key]

    return default


def clamp(value, low, high):
    return max(low, min(high, value))


def round_or_zero(value, digits=2):
    try:
        return round(float(value), digits)
    except Exception:
        return 0


# ============================================================
# NSE SESSION / REQUEST
# ============================================================

def warm_nse(force=False):
    global last_warm_time

    if not force and time.time() - last_warm_time < 60:
        return

    try:
        session.get(
            NSE_HOME,
            timeout=12
        )

        session.get(
            NSE_OPTION_PAGE,
            timeout=12
        )

        last_warm_time = time.time()

    except Exception:
        pass


def nse_get(url, params=None, retries=2):
    global session

    if session is None:
        create_session()

    last_error = None

    for attempt in range(retries + 1):

        try:

            if attempt > 0:
                warm_nse(force=True)

            response = session.get(
                url,
                params=params,
                timeout=20
            )

            if response.status_code in (401, 403):
                warm_nse(force=True)
                continue

            if response.status_code != 200:
                raise RuntimeError(
                    f"HTTP {response.status_code} from {url}"
                )

            try:
                return response.json()
            except Exception:
                raise RuntimeError(
                    f"Invalid JSON from {url}"
                )

        except Exception as exc:
            last_error = exc

            if attempt < retries:
                time.sleep(1)

    raise RuntimeError(str(last_error))


# ============================================================
# NSE OPTION CHAIN V3
# ============================================================

def get_expiry_dates(symbol):
    """
    Current NSE flow:

    /api/option-chain-contract-info?symbol=NIFTY

    returns available expiries.
    """

    warm_nse()

    data = nse_get(
        NSE_CONTRACT_INFO,
        params={
            "symbol": symbol
        }
    )

    expiry_dates = []

    if isinstance(data, dict):

        # Current format
        expiry_dates = data.get("expiryDates", [])

        # Some responses may wrap it
        if not expiry_dates:

            records = data.get("records", {})

            if isinstance(records, dict):
                expiry_dates = records.get(
                    "expiryDates",
                    []
                )

    if not isinstance(expiry_dates, list):
        expiry_dates = []

    expiry_dates = [
        str(x).strip()
        for x in expiry_dates
        if x
    ]

    return expiry_dates


def fetch_option_chain_v3(symbol):
    """
    Fetch nearest-expiry index option chain
    using the current NSE v3 API.
    """

    expiry_dates = get_expiry_dates(symbol)

    if not expiry_dates:
        raise RuntimeError(
            f"No NSE expiry dates returned for {symbol}"
        )

    expiry = expiry_dates[0]

    data = nse_get(
        NSE_OPTION_CHAIN_V3,
        params={
            "type": "Indices",
            "symbol": symbol,
            "expiry": expiry
        }
    )

    if not isinstance(data, dict):
        raise RuntimeError(
            f"Invalid option-chain response for {symbol}"
        )

    records = data.get("records", {})

    if not isinstance(records, dict):
        records = {}

    rows = records.get("data", [])

    if not isinstance(rows, list):
        rows = []

    # Some NSE responses may expose data elsewhere
    if not rows:
        rows = data.get("data", [])

    if not isinstance(rows, list):
        rows = []

    # Underlying value
    underlying = safe_float(
        first_value(
            records,
            [
                "underlyingValue",
                "underlying_value",
                "underlying"
            ],
            0
        )
    )

    if underlying <= 0:
        underlying = safe_float(
            first_value(
                data,
                [
                    "underlyingValue",
                    "underlying_value"
                ],
                0
            )
        )

    return {
        "symbol": symbol,
        "expiry": expiry,
        "expiryDates": expiry_dates,
        "underlyingValue": underlying,
        "rows": rows,
        "raw": data
    }


# ============================================================
# INDEX SPOT FALLBACK
# ============================================================

def fetch_index_spot(symbol):
    """
    Fallback spot source from NSE allIndices.
    """

    try:

        data = nse_get(
            NSE_ALL_INDICES
        )

        candidates = []

        if isinstance(data, dict):

            if isinstance(data.get("data"), list):
                candidates.extend(
                    data["data"]
                )

            if isinstance(
                data.get("allIndices"),
                list
            ):
                candidates.extend(
                    data["allIndices"]
                )

        for item in candidates:

            if not isinstance(item, dict):
                continue

            name = str(
                first_value(
                    item,
                    [
                        "index",
                        "indexSymbol",
                        "symbol",
                        "name"
                    ],
                    ""
                )
            ).upper()

            if symbol == "NIFTY" and (
                "NIFTY 50" in name
                or name == "NIFTY"
            ):
                return safe_float(
                    first_value(
                        item,
                        [
                            "last",
                            "lastPrice",
                            "ltp",
                            "lastPriceValue"
                        ],
                        0
                    )
                )

            if symbol == "BANKNIFTY" and (
                "NIFTY BANK" in name
                or "BANK NIFTY" in name
                or name == "BANKNIFTY"
            ):
                return safe_float(
                    first_value(
                        item,
                        [
                            "last",
                            "lastPrice",
                            "ltp",
                            "lastPriceValue"
                        ],
                        0
                    )
                )

    except Exception:
        pass

    return 0.0


# ============================================================
# OPTION SIDE NORMALISATION
# ============================================================

def extract_option_side(row, side):
    """
    Normalises NSE CE / PE fields.

    Handles possible:
      CE / PE
      ce / pe
      call / put
      CALL / PUT
    """

    if not isinstance(row, dict):
        return {}

    if side == "CE":
        keys = [
            "CE",
            "ce",
            "CALL",
            "call",
            "calls",
            "CALLS"
        ]
    else:
        keys = [
            "PE",
            "pe",
            "PUT",
            "put",
            "puts",
            "PUTS"
        ]

    side_data = {}

    for key in keys:

        value = row.get(key)

        if isinstance(value, dict):
            side_data = value
            break

    return side_data


def option_value(side_data, field, default=0):
    """
    Handles camelCase and snake_case fields.
    """

    aliases = {

        "oi": [
            "openInterest",
            "open_interest",
            "OI",
            "oi"
        ],

        "change_oi": [
            "changeinOpenInterest",
            "changeInOpenInterest",
            "change_in_open_interest",
            "changeOI",
            "change_oi",
            "chngInOI"
        ],

        "ltp": [
            "lastPrice",
            "last_price",
            "LTP",
            "ltp"
        ],

        "change": [
            "change",
            "priceChange",
            "price_change"
        ],

        "volume": [
            "totalTradedVolume",
            "total_traded_volume",
            "tradedVolume",
            "volume",
            "Volume"
        ],

        "iv": [
            "impliedVolatility",
            "implied_volatility",
            "IV",
            "iv"
        ],

        "bid": [
            "bidprice",
            "bidPrice",
            "bid",
            "Bid"
        ],

        "ask": [
            "askPrice",
            "askprice",
            "ask",
            "Ask"
        ]
    }

    return safe_float(
        first_value(
            side_data,
            aliases.get(field, [field]),
            default
        ),
        default
    )


# ============================================================
# OPTION CHAIN ANALYSIS
# ============================================================

def analyse_options(chain, symbol, spot):
    rows = chain.get("rows", [])

    if not rows:
        return {
            "callOI": 0,
            "putOI": 0,
            "callChangeOI": 0,
            "putChangeOI": 0,
            "callVolume": 0,
            "putVolume": 0,
            "pcr": 0,
            "iv": 0,
            "support": 0,
            "support2": 0,
            "resistance": 0,
            "resistance2": 0,
            "callPositioning": "UNKNOWN",
            "putPositioning": "UNKNOWN",
            "rows": [],
            "maxPain": 0,
            "atmStrike": 0
        }

    parsed = []

    for row in rows:

        if not isinstance(row, dict):
            continue

        strike = safe_float(
            first_value(
                row,
                [
                    "strikePrice",
                    "strike_price",
                    "strike",
                    "StrikePrice"
                ],
                0
            )
        )

        if strike <= 0:
            continue

        ce = extract_option_side(row, "CE")
        pe = extract_option_side(row, "PE")

        parsed.append({
            "strike": strike,

            "ce": {
                "oi": option_value(ce, "oi"),
                "change_oi": option_value(
                    ce,
                    "change_oi"
                ),
                "ltp": option_value(
                    ce,
                    "ltp"
                ),
                "change": option_value(
                    ce,
                    "change"
                ),
                "volume": option_value(
                    ce,
                    "volume"
                ),
                "iv": option_value(
                    ce,
                    "iv"
                )
            },

            "pe": {
                "oi": option_value(pe, "oi"),
                "change_oi": option_value(
                    pe,
                    "change_oi"
                ),
                "ltp": option_value(
                    pe,
                    "ltp"
                ),
                "change": option_value(
                    pe,
                    "change"
                ),
                "volume": option_value(
                    pe,
                    "volume"
                ),
                "iv": option_value(
                    pe,
                    "iv"
                )
            }
        })

    if not parsed:

        return {
            "callOI": 0,
            "putOI": 0,
            "callChangeOI": 0,
            "putChangeOI": 0,
            "callVolume": 0,
            "putVolume": 0,
            "pcr": 0,
            "iv": 0,
            "support": 0,
            "support2": 0,
            "resistance": 0,
            "resistance2": 0,
            "callPositioning": "UNKNOWN",
            "putPositioning": "UNKNOWN",
            "rows": [],
            "maxPain": 0,
            "atmStrike": 0
        }

    # --------------------------------------------------------
    # ATM
    # --------------------------------------------------------

    atm_row = min(
        parsed,
        key=lambda x: abs(
            x["strike"] - spot
        )
    )

    atm = atm_row["strike"]

    # --------------------------------------------------------
    # Restrict detailed analysis around ATM
    # --------------------------------------------------------

    nearby = sorted(
        parsed,
        key=lambda x: abs(
            x["strike"] - atm
        )
    )[:15]

    # --------------------------------------------------------
    # Total OI
    # --------------------------------------------------------

    call_oi = sum(
        x["ce"]["oi"]
        for x in parsed
    )

    put_oi = sum(
        x["pe"]["oi"]
        for x in parsed
    )

    call_change = sum(
        x["ce"]["change_oi"]
        for x in parsed
    )

    put_change = sum(
        x["pe"]["change_oi"]
        for x in parsed
    )

    call_volume = sum(
        x["ce"]["volume"]
        for x in parsed
    )

    put_volume = sum(
        x["pe"]["volume"]
        for x in parsed
    )

    pcr = (
        put_oi / call_oi
        if call_oi > 0
        else 0
    )

    # --------------------------------------------------------
    # ATM IV
    # --------------------------------------------------------

    iv_values = []

    for row in nearby:

        if row["ce"]["iv"] > 0:
            iv_values.append(
                row["ce"]["iv"]
            )

        if row["pe"]["iv"] > 0:
            iv_values.append(
                row["pe"]["iv"]
            )

    iv = (
        statistics.mean(iv_values)
        if iv_values
        else 0
    )

    # --------------------------------------------------------
    # Resistance based on CE OI
    # --------------------------------------------------------

    resistance_candidates = sorted(
        [
            x for x in parsed
            if x["strike"] >= atm
        ],
        key=lambda x: x["ce"]["oi"],
        reverse=True
    )

    resistance_levels = []

    for x in resistance_candidates:

        if x["ce"]["oi"] <= 0:
            continue

        resistance_levels.append(
            x["strike"]
        )

        if len(resistance_levels) >= 2:
            break

    # --------------------------------------------------------
    # Support based on PE OI
    # --------------------------------------------------------

    support_candidates = sorted(
        [
            x for x in parsed
            if x["strike"] <= atm
        ],
        key=lambda x: x["pe"]["oi"],
        reverse=True
    )

    support_levels = []

    for x in support_candidates:

        if x["pe"]["oi"] <= 0:
            continue

        support_levels.append(
            x["strike"]
        )

        if len(support_levels) >= 2:
            break

    resistance = (
        resistance_levels[0]
        if resistance_levels
        else 0
    )

    resistance2 = (
        resistance_levels[1]
        if len(resistance_levels) > 1
        else 0
    )

    support = (
        support_levels[0]
        if support_levels
        else 0
    )

    support2 = (
        support_levels[1]
        if len(support_levels) > 1
        else 0
    )

    # --------------------------------------------------------
    # Positioning
    # --------------------------------------------------------

    atm_ce = atm_row["ce"]
    atm_pe = atm_row["pe"]

    call_positioning = classify_option_position(
        atm_ce["change_oi"],
        atm_ce["change"],
        atm_ce["ltp"]
    )

    put_positioning = classify_option_position(
        atm_pe["change_oi"],
        atm_pe["change"],
        atm_pe["ltp"]
    )

    # --------------------------------------------------------
    # Max pain
    # --------------------------------------------------------

    max_pain = calculate_max_pain(parsed)

    return {
        "callOI": call_oi,
        "putOI": put_oi,
        "callChangeOI": call_change,
        "putChangeOI": put_change,
        "callVolume": call_volume,
        "putVolume": put_volume,

        "pcr": pcr,
        "iv": iv,

        "support": support,
        "support2": support2,

        "resistance": resistance,
        "resistance2": resistance2,

        "callPositioning": call_positioning,
        "putPositioning": put_positioning,

        "rows": parsed,

        "maxPain": max_pain,
        "atmStrike": atm,

        "atmCE": atm_ce,
        "atmPE": atm_pe
    }


def classify_option_position(change_oi, price_change, ltp):
    """
    Option premium + OI interpretation.

    OI ↑ + premium ↓ = writing
    OI ↓ + premium ↑ = short covering
    OI ↑ + premium ↑ = long buildup
    OI ↓ + premium ↓ = long unwinding
    """

    if change_oi > 0 and price_change < 0:
        return "WRITING"

    if change_oi < 0 and price_change > 0:
        return "SHORT COVERING"

    if change_oi > 0 and price_change > 0:
        return "LONG BUILDUP"

    if change_oi < 0 and price_change < 0:
        return "LONG UNWINDING"

    return "NEUTRAL"


def calculate_max_pain(rows):
    if not rows:
        return 0

    strikes = [
        x["strike"]
        for x in rows
        if x["strike"] > 0
    ]

    if not strikes:
        return 0

    # Limit calculation for performance
    strikes = sorted(set(strikes))

    if len(strikes) > 120:
        middle = len(strikes) // 2
        strikes = strikes[
            max(0, middle - 60):
            middle + 60
        ]

    best_strike = strikes[0]
    lowest_loss = float("inf")

    for expiry_price in strikes:

        loss = 0.0

        for row in rows:

            strike = row["strike"]

            call_oi = row["ce"]["oi"]
            put_oi = row["pe"]["oi"]

            if expiry_price > strike:
                loss += (
                    expiry_price - strike
                ) * call_oi

            if expiry_price < strike:
                loss += (
                    strike - expiry_price
                ) * put_oi

        if loss < lowest_loss:
            lowest_loss = loss
            best_strike = expiry_price

    return best_strike


# ============================================================
# CHARTING NSE
# ============================================================

def warm_chart():
    try:

        session.get(
            "https://charting.nseindia.com/",
            timeout=10
        )

    except Exception:
        pass


def fetch_chart(symbol, interval=5, days=3):
    """
    Fetch index 5-minute candles.
    """

    token = INDEX_TOKENS.get(symbol)

    if not token:
        return []

    warm_chart()

    end_time = int(
        time.time()
    )

    start_time = int(
        time.time() -
        days * 86400
    )

    payload = {
        "exchange": "NSE",
        "symbol": token,
        "symbolType": "Index",
        "startTime": start_time,
        "endTime": end_time,
        "timeInterval": interval,
        "chartType": "I"
    }

    try:

        response = session.post(
            CHART_URL,
            json=payload,
            timeout=20
        )

        if response.status_code != 200:
            return []

        data = response.json()

    except Exception:
        return []

    return parse_chart_response(data)


def parse_chart_response(data):
    """
    Handles several OpenChart response layouts.
    """

    if not isinstance(data, dict):
        return []

    candidates = []

    for key in [
        "data",
        "grapthData",
        "graphData",
        "candles",
        "result",
        "results"
    ]:

        value = data.get(key)

        if isinstance(value, list):
            candidates = value
            break

        if isinstance(value, dict):

            for subkey in [
                "data",
                "candles",
                "result"
            ]:

                sub = value.get(subkey)

                if isinstance(sub, list):
                    candidates = sub
                    break

            if candidates:
                break

    parsed = []

    for item in candidates:

        # Dictionary format
        if isinstance(item, dict):

            ts = first_value(
                item,
                [
                    "time",
                    "timestamp",
                    "date",
                    "datetime"
                ],
                None
            )

            open_price = safe_float(
                first_value(
                    item,
                    ["open", "Open", "o"],
                    0
                )
            )

            high = safe_float(
                first_value(
                    item,
                    ["high", "High", "h"],
                    0
                )
            )

            low = safe_float(
                first_value(
                    item,
                    ["low", "Low", "l"],
                    0
                )
            )

            close = safe_float(
                first_value(
                    item,
                    ["close", "Close", "c"],
                    0
                )
            )

            volume = safe_float(
                first_value(
                    item,
                    [
                        "volume",
                        "Volume",
                        "v"
                    ],
                    0
                )
            )

        # Array format
        elif isinstance(item, list):

            if len(item) < 5:
                continue

            ts = item[0]
            open_price = safe_float(item[1])
            high = safe_float(item[2])
            low = safe_float(item[3])
            close = safe_float(item[4])

            volume = (
                safe_float(item[5])
                if len(item) > 5
                else 0
            )

        else:
            continue

        if close <= 0:
            continue

        parsed.append({
            "time": ts,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume
        })

    parsed.sort(
        key=lambda x: str(x["time"])
    )

    return parsed


# ============================================================
# TECHNICAL INDICATORS
# ============================================================

def ema(values, period):
    if not values:
        return 0

    if len(values) < period:
        return sum(values) / len(values)

    multiplier = 2 / (period + 1)

    result = sum(
        values[:period]
    ) / period

    for price in values[period:]:
        result = (
            (price - result)
            * multiplier
            + result
        )

    return result


def rsi(values, period=14):
    if len(values) < 2:
        return 50

    changes = [
        values[i] - values[i - 1]
        for i in range(1, len(values))
    ]

    if len(changes) < period:
        period = len(changes)

    gains = [
        max(x, 0)
        for x in changes[-period:]
    ]

    losses = [
        abs(min(x, 0))
        for x in changes[-period:]
    ]

    avg_gain = (
        sum(gains) / period
        if period
        else 0
    )

    avg_loss = (
        sum(losses) / period
        if period
        else 0
    )

    if avg_loss == 0:
        return 70 if avg_gain > 0 else 50

    rs = avg_gain / avg_loss

    return 100 - (
        100 / (1 + rs)
    )


def atr(candles, period=14):
    if len(candles) < 2:
        return 0

    trs = []

    for i in range(1, len(candles)):

        current = candles[i]
        previous = candles[i - 1]

        tr = max(
            current["high"] -
            current["low"],

            abs(
                current["high"] -
                previous["close"]
            ),

            abs(
                current["low"] -
                previous["close"]
            )
        )

        trs.append(tr)

    if not trs:
        return 0

    return (
        sum(trs[-period:]) /
        min(period, len(trs))
    )


def calculate_vwap(candles):
    if not candles:
        return 0

    total_pv = 0
    total_volume = 0

    for candle in candles:

        typical = (
            candle["high"] +
            candle["low"] +
            candle["close"]
        ) / 3

        volume = candle["volume"]

        if volume <= 0:
            continue

        total_pv += (
            typical * volume
        )

        total_volume += volume

    if total_volume <= 0:
        return 0

    return (
        total_pv /
        total_volume
    )


def structure_analysis(candles):
    if len(candles) < 6:
        return "MIXED / RANGE"

    recent = candles[-6:]

    highs = [
        x["high"]
        for x in recent
    ]

    lows = [
        x["low"]
        for x in recent
    ]

    higher_highs = (
        highs[-1] > highs[-3]
        and highs[-3] > highs[0]
    )

    higher_lows = (
        lows[-1] > lows[-3]
        and lows[-3] > lows[0]
    )

    lower_highs = (
        highs[-1] < highs[-3]
        and highs[-3] < highs[0]
    )

    lower_lows = (
        lows[-1] < lows[-3]
        and lows[-3] < lows[0]
    )

    if higher_highs and higher_lows:
        return "HH-HL / UPTREND"

    if lower_highs and lower_lows:
        return "LH-LL / DOWNTREND"

    return "MIXED / RANGE"


def volume_state(candles):
    if len(candles) < 20:
        return "NORMAL"

    volumes = [
        x["volume"]
        for x in candles[-20:]
        if x["volume"] > 0
    ]

    if not volumes:
        return "UNAVAILABLE"

    avg = (
        sum(volumes) /
        len(volumes)
    )

    current = candles[-1]["volume"]

    if avg <= 0:
        return "NORMAL"

    ratio = current / avg

    if ratio >= 1.5:
        return "HIGH"

    if ratio <= 0.7:
        return "LOW"

    return "NORMAL"


# ============================================================
# OI INTELLIGENCE
# ============================================================

def oi_score(options, spot):
    score = 0
    reasons = []

    call_pos = options["callPositioning"]
    put_pos = options["putPositioning"]

    # CE
    if call_pos == "WRITING":
        score -= 8
        reasons.append(
            "call writing is adding resistance"
        )

    elif call_pos == "SHORT COVERING":
        score += 8
        reasons.append(
            "call short covering is reducing resistance"
        )

    elif call_pos == "LONG BUILDUP":
        score += 3

    elif call_pos == "LONG UNWINDING":
        score -= 2

    # PE
    if put_pos == "WRITING":
        score += 8
        reasons.append(
            "put writing is supporting price"
        )

    elif put_pos == "SHORT COVERING":
        score -= 8
        reasons.append(
            "put short covering is weakening support"
        )

    elif put_pos == "LONG BUILDUP":
        score -= 3

    elif put_pos == "LONG UNWINDING":
        score += 2

    # PCR
    pcr = options["pcr"]

    if pcr >= 1.20:
        score += 5
        reasons.append(
            "PCR is strongly supportive"
        )

    elif pcr >= 1.00:
        score += 3
        reasons.append(
            "PCR is supportive"
        )

    elif pcr <= 0.70:
        score -= 5
        reasons.append(
            "PCR is bearish"
        )

    elif pcr <= 0.85:
        score -= 3
        reasons.append(
            "PCR is mildly bearish"
        )

    return clamp(score, -25, 25), reasons


# ============================================================
# TECHNICAL ENGINE
# ============================================================

def technical_score(
    price,
    vwap,
    ema9_value,
    ema21_value,
    rsi_value,
    structure,
    volume_state_value
):

    score = 0
    reasons = []

    # VWAP
    if vwap > 0:

        if price > vwap:
            score += 8
            reasons.append(
                "price is above VWAP"
            )

        elif price < vwap:
            score -= 8
            reasons.append(
                "price is below VWAP"
            )

    # EMA
    if ema9_value > ema21_value:
        score += 6
        reasons.append(
            "EMA9 is above EMA21"
        )

    elif ema9_value < ema21_value:
        score -= 6
        reasons.append(
            "EMA9 is below EMA21"
        )

    # RSI
    if rsi_value >= 58:
        score += 5
        reasons.append(
            "RSI supports bullish momentum"
        )

    elif rsi_value <= 42:
        score -= 5
        reasons.append(
            "RSI supports bearish momentum"
        )

    # Structure
    if structure == "HH-HL / UPTREND":
        score += 6
        reasons.append(
            "price structure is HH-HL"
        )

    elif structure == "LH-LL / DOWNTREND":
        score -= 6
        reasons.append(
            "price structure is LH-LL"
        )

    # Volume
    if volume_state_value == "HIGH":
        if score > 0:
            score += 3
            reasons.append(
                "high volume confirms momentum"
            )
        elif score < 0:
            score -= 3
            reasons.append(
                "high volume confirms selling pressure"
            )

    return clamp(score, -25, 25), reasons


# ============================================================
# MARKET ENGINE
# ============================================================

def build_engine(
    symbol,
    price,
    vwap,
    ema9_value,
    ema21_value,
    rsi_value,
    atr_value,
    structure,
    volume_state_value,
    options
):

    tech, tech_reasons = technical_score(
        price,
        vwap,
        ema9_value,
        ema21_value,
        rsi_value,
        structure,
        volume_state_value
    )

    oi, oi_reasons = oi_score(
        options,
        price
    )

    pcr = options["pcr"]

    pcr_score = 0

    if pcr >= 1.20:
        pcr_score = 10

    elif pcr >= 1.00:
        pcr_score = 5

    elif pcr <= 0.70:
        pcr_score = -10

    elif pcr <= 0.85:
        pcr_score = -5

    # --------------------------------------------------------
    # Directional score
    # --------------------------------------------------------

    directional = (
        tech +
        oi +
        pcr_score
    )

    directional = clamp(
        directional,
        -60,
        60
    )

    # --------------------------------------------------------
    # Market regime
    # --------------------------------------------------------

    bullish = directional >= 20
    bearish = directional <= -20

    conflict = (
        abs(directional) < 20
    )

    if bullish:
        condition = "BULLISH"

    elif bearish:
        condition = "BEARISH"

    else:
        condition = "SIDEWAYS"

    # --------------------------------------------------------
    # Buyer confirmation
    # --------------------------------------------------------

    bullish_confirmation = 0
    bearish_confirmation = 0

    if price > vwap > 0:
        bullish_confirmation += 1

    if price < vwap and vwap > 0:
        bearish_confirmation += 1

    if ema9_value > ema21_value:
        bullish_confirmation += 1

    if ema9_value < ema21_value:
        bearish_confirmation += 1

    if rsi_value >= 55:
        bullish_confirmation += 1

    if rsi_value <= 45:
        bearish_confirmation += 1

    if structure == "HH-HL / UPTREND":
        bullish_confirmation += 1

    if structure == "LH-LL / DOWNTREND":
        bearish_confirmation += 1

    if options["callPositioning"] in (
        "SHORT COVERING",
        "LONG UNWINDING"
    ):
        bullish_confirmation += 1

    if options["putPositioning"] == "WRITING":
        bullish_confirmation += 1

    if options["putPositioning"] in (
        "SHORT COVERING",
        "LONG UNWINDING"
    ):
        bearish_confirmation += 1

    if options["callPositioning"] == "WRITING":
        bearish_confirmation += 1

    # --------------------------------------------------------
    # Score
    #
    # Base 50 + directional contribution.
    # Maximum 100, minimum 0.
    # --------------------------------------------------------

    score = 50 + directional

    # Stronger buyer confirmation
    if condition == "BULLISH":
        score += (
            bullish_confirmation * 3
        )

    elif condition == "BEARISH":
        score += (
            bearish_confirmation * 3
        )

    # Sideways penalty
    if condition == "SIDEWAYS":
        score = min(score, 58)

    score = int(
        clamp(score, 0, 100)
    )

    # --------------------------------------------------------
    # Confidence
    # --------------------------------------------------------

    confirmation_count = max(
        bullish_confirmation,
        bearish_confirmation
    )

    confidence = (
        55 +
        abs(directional) * 0.55 +
        confirmation_count * 4
    )

    confidence = int(
        clamp(
            confidence,
            50,
            96
        )
    )

    # --------------------------------------------------------
    # Signal
    # --------------------------------------------------------

    signal = "WAIT"

    if (
        condition == "BULLISH"
        and bullish_confirmation >= 4
        and score >= 70
    ):
        signal = "BUY CALL"

    elif (
        condition == "BEARISH"
        and bearish_confirmation >= 4
        and score >= 70
    ):
        signal = "BUY PUT"

    # Conflict protection
    if (
        bullish_confirmation >= 3
        and bearish_confirmation >= 3
    ):
        signal = "WAIT"
        condition = "SIDEWAYS"

    # --------------------------------------------------------
    # Reasons
    # --------------------------------------------------------

    reasons = (
        tech_reasons +
        oi_reasons
    )

    if pcr_score > 0:
        reasons.append(
            "PCR supports bullish positioning"
        )

    elif pcr_score < 0:
        reasons.append(
            "PCR is bearish"
        )

    # --------------------------------------------------------
    # Natural language story
    # --------------------------------------------------------

    if signal == "BUY CALL":

        story = (
            "Bullish momentum is confirmed because "
            + "; ".join(reasons[:5])
            + ". CALL buying conditions are confirmed."
        )

    elif signal == "BUY PUT":

        story = (
            "Bearish momentum is confirmed because "
            + "; ".join(reasons[:5])
            + ". PUT buying conditions are confirmed."
        )

    elif condition == "BULLISH":

        story = (
            "Bullish momentum is developing because "
            + "; ".join(reasons[:5])
            + ". Waiting for stronger CALL confirmation."
        )

    elif condition == "BEARISH":

        story = (
            "Bearish momentum is developing because "
            + "; ".join(reasons[:5])
            + ". Waiting for stronger PUT confirmation."
        )

    else:

        story = (
            "Market is sideways or conflicting because "
            + "; ".join(reasons[:5])
            + ". Avoiding a low-quality option-buying entry."
        )

    return {
        "condition": condition,
        "score": score,
        "directional_score": directional,
        "confidence": confidence,
        "signal": signal,
        "story": story,
        "technical_score": tech,
        "oi_score": oi,
        "pcr_score": pcr_score,
        "reasons": reasons
    }


# ============================================================
# TRADE LEVELS
# ============================================================

def trade_levels(
    signal,
    price,
    atr_value,
    options
):

    if signal == "WAIT" or price <= 0:
        return {
            "entry": 0,
            "stoploss": 0,
            "target1": 0,
            "target2": 0
        }

    if atr_value <= 0:
        atr_value = price * 0.001

    # ATR-based risk
    risk = atr_value * 1.25

    if signal == "BUY CALL":

        entry = price

        stop = (
            price - risk
        )

        target1 = (
            price + risk * 1.4
        )

        target2 = (
            price + risk * 2.2
        )

    else:

        entry = price

        stop = (
            price + risk
        )

        target1 = (
            price - risk * 1.4
        )

        target2 = (
            price - risk * 2.2
        )

    return {
        "entry": round_or_zero(
            entry
        ),
        "stoploss": round_or_zero(
            stop
        ),
        "target1": round_or_zero(
            target1
        ),
        "target2": round_or_zero(
            target2
        )
    }


# ============================================================
# SUGGESTED STRIKE
# ============================================================

def suggested_strike(
    signal,
    options
):

    atm = options.get(
        "atmStrike",
        0
    )

    if atm <= 0:
        return 0

    rows = options.get(
        "rows",
        []
    )

    if signal == "BUY CALL":

        candidates = [
            x for x in rows
            if x["strike"] >= atm
        ]

        if not candidates:
            return atm

        return min(
            candidates,
            key=lambda x:
            abs(x["strike"] - atm)
        )["strike"]

    if signal == "BUY PUT":

        candidates = [
            x for x in rows
            if x["strike"] <= atm
        ]

        if not candidates:
            return atm

        return min(
            candidates,
            key=lambda x:
            abs(x["strike"] - atm)
        )["strike"]

    return atm


# ============================================================
# MAIN MARKET DATA
# ============================================================

def generate_market_data(symbol):
    symbol = symbol.upper().strip()

    if symbol not in (
        "NIFTY",
        "BANKNIFTY"
    ):
        raise ValueError(
            "Unsupported symbol"
        )

    cache_key = symbol

    # Short cache prevents NSE request flooding
    cached = CACHE.get(cache_key)

    if cached:

        if (
            time.time() -
            cached["time"]
            < CACHE_TTL
        ):
            return cached["data"]

    # --------------------------------------------------------
    # OPTION CHAIN
    # --------------------------------------------------------

    chain = fetch_option_chain_v3(
        symbol
    )

    # --------------------------------------------------------
    # SPOT
    # --------------------------------------------------------

    price = safe_float(
        chain.get(
            "underlyingValue",
            0
        )
    )

    if price <= 0:
        price = fetch_index_spot(
            symbol
        )

    # --------------------------------------------------------
    # INDEX CANDLES
    # --------------------------------------------------------

    candles = fetch_chart(
        symbol,
        interval=5,
        days=3
    )

    # If candle endpoint temporarily fails,
    # keep the option-chain engine alive.
    if candles:

        closes = [
            x["close"]
            for x in candles
            if x["close"] > 0
        ]

        if closes:

            candle_price = closes[-1]

            # Prefer current NSE option-chain
            # underlying value if available.
            if price <= 0:
                price = candle_price

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

            vwap_value = calculate_vwap(
                candles
            )

            structure = structure_analysis(
                candles
            )

            volume_state_value = (
                volume_state(candles)
            )

            volume = (
                candles[-1]["volume"]
                if candles
                else 0
            )

        else:

            ema9_value = price
            ema21_value = price
            rsi_value = 50
            atr_value = 0
            vwap_value = 0
            structure = "MIXED / RANGE"
            volume_state_value = "UNAVAILABLE"
            volume = 0

    else:

        ema9_value = price
        ema21_value = price
        rsi_value = 50
        atr_value = 0
        vwap_value = 0
        structure = "MIXED / RANGE"
        volume_state_value = "UNAVAILABLE"
        volume = 0

    # --------------------------------------------------------
    # OPTIONS
    # --------------------------------------------------------

    options = analyse_options(
        chain,
        symbol,
        price
    )

    # If VWAP couldn't be calculated from index candles,
    # try a futures source later. For now keep 0 rather
    # than inventing a value.
    vwap_status = (
        "LIVE 5-MIN VWAP"
        if vwap_value > 0
        else "VWAP UNAVAILABLE"
    )

    # --------------------------------------------------------
    # ENGINE
    # --------------------------------------------------------

    engine = build_engine(
        symbol=symbol,
        price=price,
        vwap=vwap_value,
        ema9_value=ema9_value,
        ema21_value=ema21_value,
        rsi_value=rsi_value,
        atr_value=atr_value,
        structure=structure,
        volume_state_value=volume_state_value,
        options=options
    )

    # --------------------------------------------------------
    # TRADE LEVELS
    # --------------------------------------------------------

    levels = trade_levels(
        engine["signal"],
        price,
        atr_value,
        options
    )

    # --------------------------------------------------------
    # STRIKE
    # --------------------------------------------------------

    strike = suggested_strike(
        engine["signal"],
        options
    )

    # --------------------------------------------------------
    # VWAP STATUS
    # --------------------------------------------------------

    if vwap_value <= 0:
        vwap_position = (
            "WAITING FOR CANDLE DATA"
        )

    elif price > vwap_value:
        vwap_position = "ABOVE VWAP"

    elif price < vwap_value:
        vwap_position = "BELOW VWAP"

    else:
        vwap_position = "AT VWAP"

    # --------------------------------------------------------
    # DATA STATUS
    # --------------------------------------------------------

    data_status = (
        "LIVE NSE V3 OPTION CHAIN "
        "+ LIVE 5-MIN CANDLES"
    )

    # --------------------------------------------------------
    # FINAL API RESPONSE
    # --------------------------------------------------------

    result = {
        "ok": True,

        "symbol": symbol,

        "price": round_or_zero(
            price
        ),

        "change": 0,

        "market_condition":
            engine["condition"],

        "score":
            engine["score"],

        "directional_score":
            engine["directional_score"],

        "confidence":
            engine["confidence"],

        "signal":
            engine["signal"],

        "statement":
            engine["story"],

        "story":
            engine["story"],

        "vwap":
            round_or_zero(
                vwap_value
            ),

        "vwapStatus":
            vwap_status,

        "vwapPosition":
            vwap_position,

        "ema9":
            round_or_zero(
                ema9_value
            ),

        "ema21":
            round_or_zero(
                ema21_value
            ),

        "rsi":
            round_or_zero(
                rsi_value
            ),

        "atr":
            round_or_zero(
                atr_value
            ),

        "structure":
            structure,

        "volume":
            round_or_zero(
                volume
            ),

        "volumeState":
            volume_state_value,

        "pcr":
            round_or_zero(
                options["pcr"],
                2
            ),

        "iv":
            round_or_zero(
                options["iv"],
                2
            ),

        "callOI":
            options["callOI"],

        "putOI":
            options["putOI"],

        "oi_change": {
            "call":
                options["callChangeOI"],
            "put":
                options["putChangeOI"]
        },

        "oi_positioning": {
            "call":
                "CALL " +
                options["callPositioning"],

            "put":
                "PUT " +
                options["putPositioning"]
        },

        "callVolume":
            options["callVolume"],

        "putVolume":
            options["putVolume"],

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

        "suggestedStrike":
            strike,

        "atmStrike":
            options["atmStrike"],

        "max_pain":
            options["maxPain"],

        "expiry":
            chain["expiry"],

        "expiryDates":
            chain["expiryDates"][:5],

        "data_status":
            data_status,

        "engine": {
            "technical_score":
                engine["technical_score"],

            "oi_score":
                engine["oi_score"],

            "pcr_score":
                engine["pcr_score"],

            "reasons":
                engine["reasons"]
        },

        "timestamp":
            datetime.now(
                timezone.utc
            ).isoformat()
    }

    CACHE[cache_key] = {
        "time": time.time(),
        "data": result
    }

    return result


# ============================================================
# API ROUTES
# ============================================================

@app.get("/")
def home():

    if FRONTEND_FILE.exists():

        return FileResponse(
            FRONTEND_FILE
        )

    return JSONResponse({
        "service":
            "Option Intelligence Free",
        "status":
            "running",
        "message":
            "Frontend file not found"
    })


@app.get("/api/health")
def health():

    return {
        "ok": True,
        "service":
            "Option Intelligence Free",
        "nse_v3":
            True,
        "curl_cffi":
            CURL_CFFI_AVAILABLE
    }


@app.get("/api/market")
def market(
    symbol: str = Query(
        "NIFTY"
    )
):

    symbol = symbol.upper().strip()

    try:

        return generate_market_data(
            symbol
        )

    except Exception as exc:

        return JSONResponse(
            status_code=503,
            content={
                "ok": False,
                "symbol": symbol,
                "error": str(exc),
                "message":
                    "Live NSE market data is temporarily unavailable."
            }
        )


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
def startup():

    try:
        warm_nse(force=True)
    except Exception:
        pass

    try:
        warm_chart()
    except Exception:
        pass
