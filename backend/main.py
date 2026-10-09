from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pathlib import Path

import time
from datetime import datetime, timezone

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
# NSE CONFIGURATION
# ============================================================

NSE_HOME = "https://www.nseindia.com"

NSE_OPTION_PAGE = (
    "https://www.nseindia.com/option-chain"
)

NSE_CONTRACT_INFO = (
    "https://www.nseindia.com/api/"
    "option-chain-contract-info"
)

NSE_OPTION_CHAIN_V3 = (
    "https://www.nseindia.com/api/"
    "option-chain-v3"
)

NSE_ALL_INDICES = (
    "https://www.nseindia.com/api/allIndices"
)

# NSE OpenChart
CHART_URL = (
    "https://charting.nseindia.com/v1/charts/"
    "symbolHistoricalData"
)

SYMBOLS_DYNAMIC_URL = (
    "https://charting.nseindia.com/v1/exchanges/"
    "symbolsDynamic"
)


# ============================================================
# KNOWN NSE INDEX TOKENS
# ============================================================

INDEX_TOKENS = {
    "NIFTY": "26000",
    "BANKNIFTY": "26004",
}

INDEX_NAMES = {
    "NIFTY": "NIFTY 50",
    "BANKNIFTY": "NIFTY BANK",
}


# ============================================================
# SESSION / CACHE
# ============================================================

session = None

last_nse_warm = 0

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
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
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

def safe_float(value, default=0.0):
    try:
        if value is None:
            return default

        if isinstance(value, str):
            value = (
                value
                .replace(",", "")
                .replace("%", "")
                .strip()
            )

            if value in (
                "",
                "-",
                "--",
                "NA",
                "N/A"
            ):
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
                value
                .replace(",", "")
                .replace("%", "")
                .strip()
            )

            if value in (
                "",
                "-",
                "--",
                "NA",
                "N/A"
            ):
                return default

        return int(float(value))

    except Exception:
        return default


def first_value(data, keys, default=None):

    if not isinstance(data, dict):
        return default

    for key in keys:

        if (
            key in data
            and data[key] is not None
        ):
            return data[key]

    return default


def clamp(value, low, high):
    return max(
        low,
        min(high, value)
    )


def rounded(value, digits=2):

    try:
        return round(
            float(value),
            digits
        )
    except Exception:
        return 0


# ============================================================
# NSE SESSION
# ============================================================

def warm_nse(force=False):

    global last_nse_warm

    if (
        not force
        and time.time() - last_nse_warm < 60
    ):
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

        last_nse_warm = time.time()

    except Exception:
        pass


def nse_get(
    url,
    params=None,
    retries=2
):

    global session

    if session is None:
        create_session()

    last_error = None

    for attempt in range(
        retries + 1
    ):

        try:

            if attempt > 0:
                warm_nse(force=True)

            response = session.get(
                url,
                params=params,
                timeout=20
            )

            if response.status_code in (
                401,
                403
            ):

                warm_nse(
                    force=True
                )

                continue

            if response.status_code != 200:

                raise RuntimeError(
                    f"HTTP {response.status_code} "
                    f"from {url}"
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

    raise RuntimeError(
        str(last_error)
    )


# ============================================================
# NSE OPTION CHAIN V3
# ============================================================

def get_expiry_dates(symbol):

    warm_nse()

    data = nse_get(
        NSE_CONTRACT_INFO,
        params={
            "symbol": symbol
        }
    )

    expiry_dates = []

    if isinstance(data, dict):

        expiry_dates = data.get(
            "expiryDates",
            []
        )

        if not expiry_dates:

            records = data.get(
                "records",
                {}
            )

            if isinstance(
                records,
                dict
            ):

                expiry_dates = (
                    records.get(
                        "expiryDates",
                        []
                    )
                )

    if not isinstance(
        expiry_dates,
        list
    ):
        expiry_dates = []

    return [
        str(x).strip()
        for x in expiry_dates
        if x
    ]


def fetch_option_chain_v3(symbol):

    expiry_dates = get_expiry_dates(
        symbol
    )

    if not expiry_dates:

        raise RuntimeError(
            f"No NSE expiry dates returned "
            f"for {symbol}"
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

    if not isinstance(
        data,
        dict
    ):

        raise RuntimeError(
            f"Invalid option-chain response "
            f"for {symbol}"
        )

    records = data.get(
        "records",
        {}
    )

    if not isinstance(
        records,
        dict
    ):
        records = {}

    rows = records.get(
        "data",
        []
    )

    if not isinstance(
        rows,
        list
    ):
        rows = []

    if not rows:

        rows = data.get(
            "data",
            []
        )

    if not isinstance(
        rows,
        list
    ):
        rows = []

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

def fetch_index_quote(symbol):

    """Spot, day change, % change and previous close from allIndices."""

    result = {
        "last": 0,
        "change": 0,
        "percent": 0,
        "prev": 0
    }

    names = {
        "NIFTY": ("NIFTY 50", "NIFTY"),
        "BANKNIFTY": ("NIFTY BANK", "BANK NIFTY", "BANKNIFTY"),
    }.get(symbol, ())

    try:

        data = nse_get(NSE_ALL_INDICES)

        candidates = []

        if isinstance(data, dict):

            for key in ("data", "allIndices"):

                if isinstance(data.get(key), list):
                    candidates.extend(data[key])

        for item in candidates:

            if not isinstance(item, dict):
                continue

            name = str(
                first_value(
                    item,
                    ["index", "indexSymbol", "symbol", "name"],
                    ""
                )
            ).upper().strip()

            # exact match: "NIFTY 50" must not match "NIFTY 500"
            if name not in names:
                continue

            result["last"] = safe_float(
                first_value(
                    item,
                    ["last", "lastPrice", "ltp", "lastPriceValue"],
                    0
                )
            )

            result["change"] = safe_float(
                first_value(
                    item,
                    ["variation", "change", "pointChange"],
                    0
                )
            )

            result["percent"] = safe_float(
                first_value(
                    item,
                    ["percentChange", "pChange"],
                    0
                )
            )

            result["prev"] = safe_float(
                first_value(
                    item,
                    ["previousClose", "prevClose"],
                    0
                )
            )

            break

    except Exception:
        pass

    return result


def fetch_index_spot(symbol):

    return fetch_index_quote(symbol)["last"]


# ============================================================
# OPTION DATA
# ============================================================

def extract_option_side(
    row,
    side
):

    if not isinstance(
        row,
        dict
    ):
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

    for key in keys:

        value = row.get(key)

        if isinstance(
            value,
            dict
        ):
            return value

    return {}


def option_value(
    side,
    field,
    default=0
):

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
        ]
    }

    return safe_float(
        first_value(
            side,
            aliases.get(
                field,
                [field]
            ),
            default
        ),
        default
    )


def classify_option_position(
    oi_change,
    ltp_change,
    side
):

    # OI increasing
    if oi_change > 0:

        if ltp_change > 0:

            return (
                "LONG BUILDUP"
            )

        if ltp_change < 0:

            return (
                "WRITING"
            )

    # OI decreasing
    elif oi_change < 0:

        if ltp_change > 0:

            return (
                "SHORT COVERING"
            )

        if ltp_change < 0:

            return (
                "LONG UNWINDING"
            )

    return "NEUTRAL"


def calculate_max_pain(
    rows
):

    if not rows:
        return 0

    strikes = [
        x["strike"]
        for x in rows
        if x["strike"] > 0
    ]

    if not strikes:
        return 0

    best_strike = 0
    lowest_pain = None

    for expiry_strike in strikes:

        pain = 0

        for row in rows:

            strike = row["strike"]

            ce_oi = row["ce"]["oi"]
            pe_oi = row["pe"]["oi"]

            if expiry_strike > strike:

                pain += (
                    expiry_strike
                    - strike
                ) * ce_oi

            if expiry_strike < strike:

                pain += (
                    strike
                    - expiry_strike
                ) * pe_oi

        if (
            lowest_pain is None
            or pain < lowest_pain
        ):

            lowest_pain = pain
            best_strike = expiry_strike

    return best_strike


def analyse_options(
    chain,
    spot
):

    raw_rows = chain.get(
        "rows",
        []
    )

    parsed = []

    for row in raw_rows:

        if not isinstance(
            row,
            dict
        ):
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

        ce = extract_option_side(
            row,
            "CE"
        )

        pe = extract_option_side(
            row,
            "PE"
        )

        ce_ltp = option_value(
            ce,
            "ltp"
        )

        pe_ltp = option_value(
            pe,
            "ltp"
        )

        ce_change = option_value(
            ce,
            "change"
        )

        pe_change = option_value(
            pe,
            "change"
        )

        parsed.append({

            "strike": strike,

            "ce": {
                "oi": option_value(
                    ce,
                    "oi"
                ),

                "change_oi":
                    option_value(
                        ce,
                        "change_oi"
                    ),

                "ltp": ce_ltp,

                "change": ce_change,

                "volume":
                    option_value(
                        ce,
                        "volume"
                    ),

                "iv":
                    option_value(
                        ce,
                        "iv"
                    ),

                "position":
                    classify_option_position(
                        option_value(
                            ce,
                            "change_oi"
                        ),
                        ce_change,
                        "CE"
                    )
            },

            "pe": {
                "oi": option_value(
                    pe,
                    "oi"
                ),

                "change_oi":
                    option_value(
                        pe,
                        "change_oi"
                    ),

                "ltp": pe_ltp,

                "change": pe_change,

                "volume":
                    option_value(
                        pe,
                        "volume"
                    ),

                "iv":
                    option_value(
                        pe,
                        "iv"
                    ),

                "position":
                    classify_option_position(
                        option_value(
                            pe,
                            "change_oi"
                        ),
                        pe_change,
                        "PE"
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

    atm_row = min(
        parsed,
        key=lambda x:
        abs(
            x["strike"] - spot
        )
    )

    atm = atm_row["strike"]

    # Total chain OI
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

    # Nearby strikes
    nearby = sorted(
        parsed,
        key=lambda x:
        abs(
            x["strike"] - atm
        )
    )[:15]

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
        sum(iv_values)
        / len(iv_values)
        if iv_values
        else 0
    )

    # --------------------------------------------------------
    # Support / resistance by OI
    # --------------------------------------------------------

    below = [
        x for x in parsed
        if x["strike"] < atm
    ]

    above = [
        x for x in parsed
        if x["strike"] > atm
    ]

    below.sort(
        key=lambda x:
        x["pe"]["oi"],
        reverse=True
    )

    above.sort(
        key=lambda x:
        x["ce"]["oi"],
        reverse=True
    )

    support = (
        below[0]["strike"]
        if below
        else 0
    )

    support2 = (
        below[1]["strike"]
        if len(below) > 1
        else 0
    )

    resistance = (
        above[0]["strike"]
        if above
        else 0
    )

    resistance2 = (
        above[1]["strike"]
        if len(above) > 1
        else 0
    )

    # --------------------------------------------------------
    # ATM positioning
    # --------------------------------------------------------

    call_positioning = (
        atm_row["ce"]["position"]
    )

    put_positioning = (
        atm_row["pe"]["position"]
    )

    return {

        "callOI": safe_int(
            call_oi
        ),

        "putOI": safe_int(
            put_oi
        ),

        "callChangeOI":
            safe_int(
                call_change
            ),

        "putChangeOI":
            safe_int(
                put_change
            ),

        "callVolume":
            safe_int(
                call_volume
            ),

        "putVolume":
            safe_int(
                put_volume
            ),

        "pcr":
            pcr,

        "iv":
            iv,

        "support":
            support,

        "support2":
            support2,

        "resistance":
            resistance,

        "resistance2":
            resistance2,

        "callPositioning":
            call_positioning,

        "putPositioning":
            put_positioning,

        "rows":
            parsed,

        "maxPain":
            calculate_max_pain(
                parsed
            ),

        "atmStrike":
            atm
    }


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


# ------------------------------------------------------------
# Chart settings
# ------------------------------------------------------------

NSE_CHART_LEGACY_URL = (
    "https://charting.nseindia.com//Charts/ChartData/"
)

YAHOO_SYMBOLS = {
    "NIFTY": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
}

YAHOO_CHART_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
)

CHART_CACHE = {}

CHART_CACHE_TTL = 45


def to_epoch_seconds(value):

    """
    Accepts seconds, milliseconds or a datetime string
    and returns epoch seconds (0 if unknown).
    """

    ts = safe_float(value, 0)

    if ts <= 0 and isinstance(value, str):

        try:
            ts = datetime.fromisoformat(
                value.strip().replace("Z", "+00:00")
            ).timestamp()
        except Exception:
            ts = 0

    if ts > 1e12:
        ts = ts / 1000.0

    return ts


def columnar_to_rows(block):

    """
    Converts {"t":[], "o":[], "h":[], "l":[], "c":[], "v":[]}
    (or long-named keys) into a list of candle dicts.
    """

    if not isinstance(block, dict):
        return None

    def pick(*names):

        for name in names:

            value = block.get(name)

            if isinstance(value, list):
                return value

        return None

    t = pick("t", "time", "timestamp", "timestamps")
    c = pick("c", "close", "Close")

    if not t or not c:
        return None

    o = pick("o", "open", "Open")
    h = pick("h", "high", "High")
    l = pick("l", "low", "Low")
    v = pick("v", "volume", "Volume")

    def at(arr, i, fallback):

        if arr and i < len(arr) and arr[i] is not None:
            return arr[i]

        return fallback

    rows = []

    for i in range(min(len(t), len(c))):

        close = c[i]

        rows.append({
            "time": t[i],
            "open": at(o, i, close),
            "high": at(h, i, close),
            "low": at(l, i, close),
            "close": close,
            "volume": at(v, i, 0),
        })

    return rows


def parse_chart_response(data):

    """
    Supports:
      1. columnar  : {"s":"Ok","t":[..],"o":[..],"h":[..],"l":[..],"c":[..],"v":[..]}
      2. dict list : {"data":[{"time":..,"open":..,...}]}
      3. array list: {"data":[[time,o,h,l,c,v], ...]}
    """

    candidates = None

    if isinstance(data, list):
        candidates = data

    elif isinstance(data, dict):

        candidates = columnar_to_rows(data)

        if candidates is None:
            candidates = columnar_to_rows(
                data.get("data")
            )

        if candidates is None:

            value = data.get("data")

            if isinstance(value, list):
                candidates = value

            elif isinstance(value, dict):

                for key in (
                    "data",
                    "candles",
                    "result",
                    "results"
                ):

                    sub = value.get(key)

                    if isinstance(sub, list):
                        candidates = sub
                        break

        if not candidates:

            for key in (
                "candles",
                "grapthData",
                "graphData",
                "result",
                "results"
            ):

                value = data.get(key)

                if isinstance(value, list):
                    candidates = value
                    break

    if not candidates:
        return []

    parsed = []

    for item in candidates:

        if isinstance(item, dict):

            timestamp = first_value(
                item,
                ["time", "timestamp", "date", "datetime", "t"],
                None
            )

            open_price = safe_float(
                first_value(item, ["open", "Open", "o"], 0)
            )

            high = safe_float(
                first_value(item, ["high", "High", "h"], 0)
            )

            low = safe_float(
                first_value(item, ["low", "Low", "l"], 0)
            )

            close = safe_float(
                first_value(item, ["close", "Close", "c"], 0)
            )

            volume = safe_float(
                first_value(item, ["volume", "Volume", "v"], 0)
            )

        elif isinstance(item, list):

            if len(item) < 5:
                continue

            timestamp = item[0]
            open_price = safe_float(item[1])
            high = safe_float(item[2])
            low = safe_float(item[3])
            close = safe_float(item[4])
            volume = safe_float(item[5]) if len(item) > 5 else 0

        else:
            continue

        if close <= 0:
            continue

        # Index candles sometimes miss O/H/L: use close
        if open_price <= 0:
            open_price = close

        if high <= 0:
            high = max(open_price, close)

        if low <= 0:
            low = min(open_price, close)

        parsed.append({
            "time": timestamp,
            "ts": to_epoch_seconds(timestamp),
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume
        })

    parsed.sort(key=lambda x: x["ts"])

    return parsed


def plain_get(url, params=None, headers=None, timeout=15):

    """GET without NSE-specific session headers."""

    if CURL_CFFI_AVAILABLE:

        return cffi_requests.get(
            url,
            params=params,
            headers=headers,
            timeout=timeout,
            impersonate="chrome"
        )

    return cffi_requests.get(
        url,
        params=params,
        headers=headers,
        timeout=timeout
    )


def fetch_nse_candles(symbol, interval, days, debug):

    token = INDEX_TOKENS.get(symbol)

    if not token:
        return []

    warm_chart()

    end_time = int(time.time())
    start_time = int(time.time() - days * 86400)

    name = INDEX_NAMES.get(symbol, symbol)

    variants = [

        # Classic NSE charting API (columnar response)
        (
            NSE_CHART_LEGACY_URL,
            {
                "exch": "N",
                "instrType": "C",
                "scripCode": int(token),
                "ulToken": int(token),
                "fromDate": start_time,
                "toDate": end_time,
                "timeInterval": int(interval),
                "chartPeriod": "I",
                "chartStart": 0
            }
        ),

        # Same, index instrument type
        (
            NSE_CHART_LEGACY_URL,
            {
                "exch": "N",
                "instrType": "I",
                "scripCode": int(token),
                "ulToken": int(token),
                "fromDate": start_time,
                "toDate": end_time,
                "timeInterval": int(interval),
                "chartPeriod": "I",
                "chartStart": 0
            }
        ),

        # v1 endpoint, original payload
        (
            CHART_URL,
            {
                "token": str(token),
                "fromDate": start_time,
                "toDate": end_time,
                "symbol": name,
                "symbolType": "Index",
                "chartType": "I",
                "timeInterval": int(interval)
            }
        ),

        # v1 endpoint, alternate payload
        (
            CHART_URL,
            {
                "exch": "N",
                "tradingSymbol": name,
                "fromDate": start_time,
                "toDate": end_time,
                "timeInterval": int(interval),
                "chartPeriod": "I",
                "chartStart": 0
            }
        ),
    ]

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/140.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Content-Type": "application/json",
        "Origin": "https://charting.nseindia.com",
        "Referer": "https://charting.nseindia.com/"
    }

    for index, (url, payload) in enumerate(variants):

        try:

            response = session.post(
                url,
                json=payload,
                headers=headers,
                timeout=20
            )

            if response.status_code != 200:

                debug["attempts"].append(
                    f"NSE#{index + 1}: HTTP {response.status_code}"
                )

                continue

            data = response.json()

            candles = parse_chart_response(data)

            if candles:

                debug["attempts"].append(
                    f"NSE#{index + 1}: OK {len(candles)} candles"
                )

                return candles

            debug["attempts"].append(
                f"NSE#{index + 1}: 200 but no candles parsed"
            )

        except Exception as exc:

            debug["attempts"].append(
                f"NSE#{index + 1}: {type(exc).__name__}: {str(exc)[:80]}"
            )

    return []


def fetch_yahoo_candles(symbol, interval, debug):

    ysymbol = YAHOO_SYMBOLS.get(symbol)

    if not ysymbol:
        return []

    try:

        response = plain_get(
            YAHOO_CHART_URL.format(symbol=ysymbol),
            params={
                "interval": f"{int(interval)}m",
                "range": "5d"
            },
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/140.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json"
            }
        )

        if response.status_code != 200:

            debug["attempts"].append(
                f"YAHOO: HTTP {response.status_code}"
            )

            return []

        payload = response.json()

        result = (
            payload.get("chart", {}).get("result") or [None]
        )[0]

        if not result:

            debug["attempts"].append("YAHOO: empty result")

            return []

        meta = result.get("meta", {}) or {}

        prev_close = safe_float(
            first_value(
                meta,
                ["chartPreviousClose", "previousClose"],
                0
            )
        )

        if prev_close > 0:
            debug["prev_close"] = prev_close

        quote = (
            (result.get("indicators", {}).get("quote") or [{}])[0]
        )

        block = {
            "t": result.get("timestamp") or [],
            "o": quote.get("open") or [],
            "h": quote.get("high") or [],
            "l": quote.get("low") or [],
            "c": quote.get("close") or [],
            "v": quote.get("volume") or [],
        }

        # Yahoo returns None for missing candles
        rows = columnar_to_rows(block) or []

        candles = parse_chart_response(rows)

        debug["attempts"].append(
            f"YAHOO: OK {len(candles)} candles"
        )

        return candles

    except Exception as exc:

        debug["attempts"].append(
            f"YAHOO: {type(exc).__name__}: {str(exc)[:80]}"
        )

        return []


def fetch_chart(
    symbol,
    interval=5,
    days=5
):

    """
    Returns (candles, debug).

    Order: NSE charting -> Yahoo Finance fallback.
    Result cached for CHART_CACHE_TTL seconds.
    """

    cached = CHART_CACHE.get(symbol)

    if (
        cached
        and cached["candles"]
        and time.time() - cached["time"] < CHART_CACHE_TTL
    ):
        return cached["candles"], cached["debug"]

    debug = {
        "source": "NONE",
        "attempts": []
    }

    candles = fetch_nse_candles(
        symbol,
        interval,
        days,
        debug
    )

    if candles:
        debug["source"] = "NSE"

    else:

        candles = fetch_yahoo_candles(
            symbol,
            interval,
            debug
        )

        if candles:
            debug["source"] = "YAHOO"

    CHART_CACHE[symbol] = {
        "time": time.time(),
        "candles": candles,
        "debug": debug
    }

    return candles, debug


def last_session_candles(candles):

    """Candles of the most recent trading day only."""

    if not candles:
        return []

    last_ts = candles[-1].get("ts", 0)

    if last_ts <= 0:
        return candles

    day = int(last_ts // 86400)

    session_rows = [
        x for x in candles
        if int(x.get("ts", 0) // 86400) == day
    ]

    return session_rows or candles


def previous_close_from_candles(candles):

    if not candles:
        return 0

    last_ts = candles[-1].get("ts", 0)

    if last_ts <= 0:
        return 0

    day = int(last_ts // 86400)

    for candle in reversed(candles):

        if int(candle.get("ts", 0) // 86400) < day:
            return candle["close"]

    return 0


def resolve_change(price, quote, candles, debug):

    """Returns (points, percent) for the day."""

    change = safe_float(quote.get("change"), 0)
    percent = safe_float(quote.get("percent"), 0)

    if change != 0 or percent != 0:
        return change, percent

    prev = safe_float(quote.get("prev"), 0)

    if prev <= 0:
        prev = safe_float(debug.get("prev_close"), 0)

    if prev <= 0:
        prev = previous_close_from_candles(candles)

    if prev > 0 and price > 0:

        change = price - prev

        return change, change / prev * 100

    return 0, 0


# ============================================================
# TECHNICAL INDICATORS
# ============================================================

def ema(values, period):

    if not values:
        return 0

    if len(values) < period:

        return sum(values) / len(
            values
        )

    multiplier = (
        2 / (period + 1)
    )

    result = sum(
        values[:period]
    ) / period

    for value in values[period:]:

        result = (
            (value - result)
            * multiplier
            + result
        )

    return result


def rsi(values, period=14):

    if len(values) <= period:
        return 50

    gains = []
    losses = []

    for i in range(
        1,
        len(values)
    ):

        change = (
            values[i]
            - values[i - 1]
        )

        gains.append(
            max(change, 0)
        )

        losses.append(
            max(-change, 0)
        )

    avg_gain = (
        sum(gains[-period:])
        / min(
            period,
            len(gains)
        )
    )

    avg_loss = (
        sum(losses[-period:])
        / min(
            period,
            len(losses)
        )
    )

    if avg_loss == 0:

        if avg_gain > 0:
            return 70

        return 50

    rs = (
        avg_gain
        / avg_loss
    )

    return (
        100
        - (
            100
            / (1 + rs)
        )
    )


def atr(
    candles,
    period=14
):

    if len(candles) < 2:
        return 0

    trs = []

    for i in range(
        1,
        len(candles)
    ):

        current = candles[i]
        previous = candles[i - 1]

        tr = max(

            current["high"]
            - current["low"],

            abs(
                current["high"]
                - previous["close"]
            ),

            abs(
                current["low"]
                - previous["close"]
            )
        )

        trs.append(tr)

    if not trs:
        return 0

    sample = trs[-period:]

    return (
        sum(sample)
        / len(sample)
    )


def calculate_vwap(
    candles
):

    """
    Returns (value, kind)
      kind = "VWAP" -> true volume-weighted
      kind = "AVG"  -> index candles have no volume, so
                       average typical price of the session
      kind = "NONE" -> no data
    """

    if not candles:
        return 0, "NONE"

    total_pv = 0
    total_volume = 0

    typical_sum = 0
    typical_count = 0

    for candle in candles:

        typical = (
            candle["high"]
            + candle["low"]
            + candle["close"]
        ) / 3

        typical_sum += typical
        typical_count += 1

        volume = safe_float(
            candle.get("volume", 0)
        )

        if volume <= 0:
            continue

        total_pv += typical * volume
        total_volume += volume

    if total_volume > 0:
        return total_pv / total_volume, "VWAP"

    if typical_count > 0:
        return typical_sum / typical_count, "AVG"

    return 0, "NONE"


def structure_analysis(
    candles
):

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

    if (
        higher_highs
        and higher_lows
    ):
        return "HH-HL / UPTREND"

    if (
        lower_highs
        and lower_lows
    ):
        return "LH-LL / DOWNTREND"

    return "MIXED / RANGE"


def volume_state(
    candles
):

    if len(candles) < 20:
        return "NORMAL"

    volumes = [
        x["volume"]
        for x in candles[-20:]
        if x["volume"] > 0
    ]

    if not volumes:
        return "UNAVAILABLE"

    average = (
        sum(volumes)
        / len(volumes)
    )

    current = candles[-1]["volume"]

    if average <= 0:
        return "NORMAL"

    ratio = (
        current / average
    )

    if ratio >= 1.5:
        return "HIGH"

    if ratio <= 0.7:
        return "LOW"

    return "NORMAL"


# ============================================================
# OI ENGINE
# ============================================================

def oi_score(options):

    score = 0
    reasons = []

    call_pos = (
        options["callPositioning"]
    )

    put_pos = (
        options["putPositioning"]
    )

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

    return (
        clamp(
            score,
            -25,
            25
        ),
        reasons
    )


# ============================================================
# TECHNICAL SCORE
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
    if (
        ema9_value
        > ema21_value
    ):

        score += 6

        reasons.append(
            "EMA9 is above EMA21"
        )

    elif (
        ema9_value
        < ema21_value
    ):

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
    if structure == (
        "HH-HL / UPTREND"
    ):

        score += 6

        reasons.append(
            "price structure is HH-HL"
        )

    elif structure == (
        "LH-LL / DOWNTREND"
    ):

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

    return (
        clamp(
            score,
            -25,
            25
        ),
        reasons
    )


# ============================================================
# DECISION ENGINE
# ============================================================

def build_engine(
    price,
    vwap,
    ema9_value,
    ema21_value,
    rsi_value,
    structure,
    volume_state_value,
    options
):

    tech, tech_reasons = (
        technical_score(
            price,
            vwap,
            ema9_value,
            ema21_value,
            rsi_value,
            structure,
            volume_state_value
        )
    )

    oi, oi_reasons = (
        oi_score(options)
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

    directional = (
        tech
        + oi
        + pcr_score
    )

    directional = clamp(
        directional,
        -60,
        60
    )

    if directional >= 20:

        condition = "BULLISH"

    elif directional <= -20:

        condition = "BEARISH"

    else:

        condition = "SIDEWAYS"

    # --------------------------------------------------------
    # Buyer confirmation
    # --------------------------------------------------------

    bullish_confirmation = 0
    bearish_confirmation = 0

    if (
        vwap > 0
        and price > vwap
    ):
        bullish_confirmation += 1

    if (
        vwap > 0
        and price < vwap
    ):
        bearish_confirmation += 1

    if ema9_value > ema21_value:
        bullish_confirmation += 1

    elif ema9_value < ema21_value:
        bearish_confirmation += 1

    if rsi_value >= 55:
        bullish_confirmation += 1

    if rsi_value <= 45:
        bearish_confirmation += 1

    if structure == (
        "HH-HL / UPTREND"
    ):
        bullish_confirmation += 1

    if structure == (
        "LH-LL / DOWNTREND"
    ):
        bearish_confirmation += 1

    if options["callPositioning"] in (
        "SHORT COVERING",
        "LONG UNWINDING"
    ):
        bullish_confirmation += 1

    if options["putPositioning"] == (
        "WRITING"
    ):
        bullish_confirmation += 1

    if options["putPositioning"] in (
        "SHORT COVERING",
        "LONG UNWINDING"
    ):
        bearish_confirmation += 1

    if options["callPositioning"] == (
        "WRITING"
    ):
        bearish_confirmation += 1

    # --------------------------------------------------------
    # Score
    # --------------------------------------------------------

    score = (
        50 + directional
    )

    if condition == "BULLISH":

        score += (
            bullish_confirmation * 3
        )

    elif condition == "BEARISH":

        score += (
            bearish_confirmation * 3
        )

    if condition == "SIDEWAYS":

        score = min(
            score,
            58
        )

    score = int(
        clamp(
            score,
            0,
            100
        )
    )

    # --------------------------------------------------------
    # Confidence
    # --------------------------------------------------------

    confirmation_count = max(
        bullish_confirmation,
        bearish_confirmation
    )

    confidence = int(
        clamp(
            55
            + abs(directional) * 0.55
            + confirmation_count * 4,
            50,
            96
        )
    )

    # --------------------------------------------------------
    # Final signal
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
        tech_reasons
        + oi_reasons
    )

    if pcr_score > 0:

        reasons.append(
            "PCR supports bullish positioning"
        )

    elif pcr_score < 0:

        reasons.append(
            "PCR is bearish"
        )

    if not reasons:

        reasons.append(
            "insufficient directional confirmation"
        )

    # --------------------------------------------------------
    # Story
    # --------------------------------------------------------

    reason_text = "; ".join(
        reasons[:5]
    )

    if signal == "BUY CALL":

        story = (
            "Bullish momentum is confirmed because "
            + reason_text
            + ". CALL buying conditions are confirmed."
        )

    elif signal == "BUY PUT":

        story = (
            "Bearish momentum is confirmed because "
            + reason_text
            + ". PUT buying conditions are confirmed."
        )

    elif condition == "BULLISH":

        story = (
            "Bullish momentum is developing because "
            + reason_text
            + ". Waiting for stronger CALL confirmation."
        )

    elif condition == "BEARISH":

        story = (
            "Bearish momentum is developing because "
            + reason_text
            + ". Waiting for stronger PUT confirmation."
        )

    else:

        story = (
            "Market is sideways or conflicting because "
            + reason_text
            + ". Avoiding a low-quality option-buying entry."
        )

    return {

        "condition":
            condition,

        "score":
            score,

        "directional_score":
            directional,

        "confidence":
            confidence,

        "signal":
            signal,

        "story":
            story,

        "technical_score":
            tech,

        "oi_score":
            oi,

        "pcr_score":
            pcr_score,

        "reasons":
            reasons
    }


# ============================================================
# TRADE LEVELS
# ============================================================

def trade_levels(
    signal,
    price,
    atr_value
):

    if (
        signal == "WAIT"
        or price <= 0
    ):

        return {
            "entry": 0,
            "stoploss": 0,
            "target1": 0,
            "target2": 0
        }

    # Fallback ATR only if actual ATR
    # is unavailable.
    if atr_value <= 0:

        atr_value = (
            price * 0.001
        )

    risk = (
        atr_value * 1.25
    )

    if signal == "BUY CALL":

        return {

            "entry":
                rounded(price),

            "stoploss":
                rounded(
                    price - risk
                ),

            "target1":
                rounded(
                    price
                    + risk * 1.4
                ),

            "target2":
                rounded(
                    price
                    + risk * 2.2
                )
        }

    return {

        "entry":
            rounded(price),

        "stoploss":
            rounded(
                price + risk
            ),

        "target1":
            rounded(
                price
                - risk * 1.4
            ),

        "target2":
            rounded(
                price
                - risk * 2.2
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

        if candidates:

            return min(
                candidates,
                key=lambda x:
                abs(
                    x["strike"]
                    - atm
                )
            )["strike"]

    elif signal == "BUY PUT":

        candidates = [
            x for x in rows
            if x["strike"] <= atm
        ]

        if candidates:

            return min(
                candidates,
                key=lambda x:
                abs(
                    x["strike"]
                    - atm
                )
            )["strike"]

    return atm


# ============================================================
# MAIN MARKET DATA
# ============================================================

def generate_market_data(
    symbol
):

    symbol = (
        symbol
        .upper()
        .strip()
    )

    if symbol not in (
        "NIFTY",
        "BANKNIFTY"
    ):

        raise ValueError(
            "Unsupported symbol"
        )

    # --------------------------------------------------------
    # Cache
    # --------------------------------------------------------

    cached = CACHE.get(
        symbol
    )

    if cached:

        if (
            time.time()
            - cached["time"]
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
    # 5-MINUTE CANDLES
    # --------------------------------------------------------

    candles, chart_debug = fetch_chart(
        symbol,
        interval=5,
        days=5
    )

    candle_count = len(
        candles
    )

    session_candles = last_session_candles(
        candles
    )

    quote = fetch_index_quote(
        symbol
    )

    if price <= 0:
        price = safe_float(
            quote.get("last"),
            0
        )

    if price <= 0 and candles:
        price = candles[-1]["close"]

    # Defaults
    ema9_value = price
    ema21_value = price
    rsi_value = 50
    atr_value = 0
    vwap_value = 0
    structure = "MIXED / RANGE"
    volume_state_value = "UNAVAILABLE"
    volume = 0
    vwap_kind = "NONE"

    # --------------------------------------------------------
    # Technical calculations
    # --------------------------------------------------------

    if candles:

        closes = [
            x["close"]
            for x in candles
            if x["close"] > 0
        ]

        if closes:

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

            vwap_value, vwap_kind = (
                calculate_vwap(
                    session_candles
                )
            )

            structure = (
                structure_analysis(
                    candles
                )
            )

            volume_state_value = (
                volume_state(
                    candles
                )
            )

            # Index feeds carry no volume: use the latest
            # non-zero candle volume, else 0.
            volume = 0

            for candle in reversed(session_candles):

                if candle.get("volume", 0) > 0:
                    volume = candle["volume"]
                    break

    # --------------------------------------------------------
    # Options
    # --------------------------------------------------------

    options = analyse_options(
        chain,
        price
    )

    # --------------------------------------------------------
    # Engine
    # --------------------------------------------------------

    engine = build_engine(
        price=price,
        vwap=vwap_value,
        ema9_value=ema9_value,
        ema21_value=ema21_value,
        rsi_value=rsi_value,
        structure=structure,
        volume_state_value=volume_state_value,
        options=options
    )

    # --------------------------------------------------------
    # Trade levels
    # --------------------------------------------------------

    levels = trade_levels(
        engine["signal"],
        price,
        atr_value
    )

    # --------------------------------------------------------
    # Strike
    # --------------------------------------------------------

    strike = suggested_strike(
        engine["signal"],
        options
    )

    # --------------------------------------------------------
    # VWAP position
    # --------------------------------------------------------

    if vwap_value <= 0:

        vwap_status = (
            "VWAP UNAVAILABLE"
        )

        vwap_position = (
            "VWAP UNAVAILABLE"
        )

    elif price > vwap_value:

        vwap_status = (
            "LIVE 5-MIN VWAP"
        )

        vwap_position = (
            "ABOVE VWAP"
        )

    elif price < vwap_value:

        vwap_status = (
            "LIVE 5-MIN VWAP"
        )

        vwap_position = (
            "BELOW VWAP"
        )

    else:

        vwap_status = (
            "LIVE 5-MIN VWAP"
        )

        vwap_position = (
            "AT VWAP"
        )

    if vwap_value > 0 and vwap_kind == "AVG":

        vwap_status = (
            "SESSION AVG PRICE (INDEX HAS NO VOLUME)"
        )

    # --------------------------------------------------------
    # Day change
    # --------------------------------------------------------

    change_points, change_percent = resolve_change(
        price,
        quote,
        candles,
        chart_debug
    )

    # --------------------------------------------------------
    # Data status
    # --------------------------------------------------------

    if candle_count > 0:

        data_status = (
            "LIVE NSE V3 OPTION CHAIN "
            "+ 5-MIN CANDLES ("
            + chart_debug.get("source", "?")
            + ")"
        )

    else:

        data_status = (
            "LIVE NSE V3 OPTION CHAIN "
            "+ CANDLE DATA UNAVAILABLE"
        )

    # --------------------------------------------------------
    # Final result
    # --------------------------------------------------------

    result = {

        "ok": True,

        "symbol":
            symbol,

        "price":
            rounded(price),

        "change":
            rounded(change_points),

        "changePercent":
            rounded(change_percent),

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

        # Technicals
        "vwap":
            rounded(
                vwap_value
            ),

        "vwapStatus":
            vwap_status,

        "vwapPosition":
            vwap_position,

        "ema9":
            rounded(
                ema9_value
            ),

        "ema21":
            rounded(
                ema21_value
            ),

        "rsi":
            rounded(
                rsi_value
            ),

        "atr":
            rounded(
                atr_value
            ),

        "structure":
            structure,

        "volume":
            rounded(
                volume
            ),

        "volumeState":
            volume_state_value,

        "candleCount":
            candle_count,

        # Option chain
        "pcr":
            rounded(
                options["pcr"],
                2
            ),

        "iv":
            rounded(
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
                "CE "
                + options[
                    "callPositioning"
                ],

            "put":
                "PE "
                + options[
                    "putPositioning"
                ]
        },

        "callVolume":
            options["callVolume"],

        "putVolume":
            options["putVolume"],

        # S/R
        "resistance":
            options["resistance"],

        "resistance2":
            options["resistance2"],

        "support":
            options["support"],

        "support2":
            options["support2"],

        # Trade
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

        # Option chain metadata
        "max_pain":
            options["maxPain"],

        "expiry":
            chain["expiry"],

        "expiryDates":
            chain["expiryDates"][:5],

        # Status
        "data_status":
            data_status,

        "engine": {

            "technical_score":
                engine[
                    "technical_score"
                ],

            "oi_score":
                engine[
                    "oi_score"
                ],

            "pcr_score":
                engine[
                    "pcr_score"
                ],

            "reasons":
                engine[
                    "reasons"
                ]
        },

        "chartDebug":
            chart_debug,

        "timestamp":
            datetime.now(
                timezone.utc
            ).isoformat()
    }

    CACHE[symbol] = {

        "time":
            time.time(),

        "data":
            result
    }

    return result


# ============================================================
# ROUTES
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

    symbol = (
        symbol
        .upper()
        .strip()
    )

    try:

        return generate_market_data(
            symbol
        )

    except Exception as exc:

        return JSONResponse(
            status_code=503,
            content={

                "ok":
                    False,

                "symbol":
                    symbol,

                "error":
                    str(exc),

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

        warm_nse(
            force=True
        )

    except Exception:
        pass

    try:

        warm_chart()

    except Exception:
        pass
