from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
import os
import time
import math
import statistics
from datetime import datetime, timedelta, timezone

# Prefer Chrome-impersonating requests when available.
# This helps with NSE anti-bot protection.
try:
    from curl_cffi import requests as http_requests
    CURL_CFFI = True
except Exception:
    import requests as http_requests
    CURL_CFFI = False


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="Option Intelligence Free",
    version="1.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# URLS
# ============================================================

NSE_HOME = "https://www.nseindia.com"

NSE_OPTION_CHAIN = (
    NSE_HOME +
    "/api/option-chain-indices"
)

CHART_HOME = "https://charting.nseindia.com"

CHART_SYMBOLS = (
    CHART_HOME +
    "/v1/exchanges/symbolsDynamic"
)

CHART_HISTORY = (
    CHART_HOME +
    "/v1/charts/symbolHistoricalData"
)


# ============================================================
# INDEX SETTINGS
# ============================================================

INDEX_INFO = {
    "NIFTY": {
        "name": "NIFTY 50",
        "token": "26000",
        "step": 50,
    },

    "BANKNIFTY": {
        "name": "NIFTY BANK",
        "token": "26004",
        "step": 100,
    },
}


# ============================================================
# CACHE
# ============================================================

CACHE = {}
CACHE_TTL = 8.0


# ============================================================
# HTTP SESSION
# ============================================================

def make_session():

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/154.0.0.0 Safari/537.36"
        ),

        "Accept":
            "application/json,text/plain,*/*",

        "Accept-Language":
            "en-US,en;q=0.9,en-IN;q=0.8",

        "Referer":
            "https://www.nseindia.com/option-chain",

        "Origin":
            "https://www.nseindia.com",

        "Connection":
            "keep-alive",

        "sec-ch-ua":
            '"Chromium";v="154", '
            '"Google Chrome";v="154", '
            '"Not-A.Brand";v="99"',

        "sec-ch-ua-mobile":
            "?0",

        "sec-ch-ua-platform":
            '"Windows"',

        "Sec-Fetch-Dest":
            "empty",

        "Sec-Fetch-Mode":
            "cors",

        "Sec-Fetch-Site":
            "same-origin",
    }

    if CURL_CFFI:

        session = http_requests.Session(
            impersonate="chrome"
        )

    else:

        session = http_requests.Session()

    session.headers.update(headers)

    return session


# ============================================================
# GENERIC REQUEST
# ============================================================

def get_json(
    session,
    url,
    params=None,
    timeout=20,
    tries=3
):

    last_error = None

    for attempt in range(tries):

        try:

            response = session.get(
                url,
                params=params,
                timeout=timeout
            )

            if response.status_code == 200:

                return response.json()

            last_error = RuntimeError(
                f"HTTP {response.status_code} from {url}"
            )

        except Exception as exc:

            last_error = exc

        time.sleep(
            0.7 * (attempt + 1)
        )

    raise (
        last_error
        or RuntimeError("Request failed")
    )


# ============================================================
# NSE SESSION WARMUP
# ============================================================

def warm_nse(session):

    urls = [
        NSE_HOME,
        NSE_HOME + "/option-chain"
    ]

    for url in urls:

        try:

            session.get(
                url,
                timeout=12
            )

        except Exception:

            pass


def warm_chart(session):

    try:

        session.get(
            CHART_HOME,
            timeout=12
        )

    except Exception:

        pass


# ============================================================
# NUMBER HELPERS
# ============================================================

def clean_number(
    value,
    default=0.0
):

    try:

        if value is None or value == "":
            return default

        return float(value)

    except Exception:

        return default


def clean_int(
    value,
    default=0
):

    try:

        if value is None or value == "":
            return default

        return int(float(value))

    except Exception:

        return default


def round_or_zero(
    value,
    digits=2
):

    try:

        if value is None:
            return 0

        if not math.isfinite(
            float(value)
        ):
            return 0

        return round(
            float(value),
            digits
        )

    except Exception:

        return 0


# ============================================================
# EMA
# ============================================================

def ema(
    values,
    period
):

    if not values:
        return 0.0

    period = min(
        period,
        len(values)
    )

    period = max(
        1,
        period
    )

    alpha = 2.0 / (
        period + 1.0
    )

    result = float(
        values[0]
    )

    for value in values[1:]:

        result = (
            alpha * float(value)
            +
            (1 - alpha) * result
        )

    return result


# ============================================================
# RSI
# ============================================================

def rsi(
    values,
    period=14
):

    if len(values) < 2:
        return 50.0

    gains = []
    losses = []

    for i in range(
        1,
        len(values)
    ):

        difference = (
            values[i]
            -
            values[i - 1]
        )

        gains.append(
            max(difference, 0)
        )

        losses.append(
            max(-difference, 0)
        )

    p = min(
        period,
        len(gains)
    )

    if p <= 0:
        return 50.0

    avg_gain = (
        sum(gains[:p])
        /
        p
    )

    avg_loss = (
        sum(losses[:p])
        /
        p
    )

    for i in range(
        p,
        len(gains)
    ):

        avg_gain = (
            (
                avg_gain * (p - 1)
                +
                gains[i]
            )
            /
            p
        )

        avg_loss = (
            (
                avg_loss * (p - 1)
                +
                losses[i]
            )
            /
            p
        )

    if avg_loss == 0:

        if avg_gain > 0:
            return 100.0

        return 50.0

    rs = (
        avg_gain
        /
        avg_loss
    )

    return (
        100.0
        -
        (
            100.0
            /
            (1.0 + rs)
        )
    )


# ============================================================
# ATR
# ============================================================

def atr(
    highs,
    lows,
    closes,
    period=14
):

    if len(closes) < 2:
        return 0.0

    true_ranges = []

    for i in range(
        1,
        len(closes)
    ):

        tr = max(
            highs[i] - lows[i],

            abs(
                highs[i]
                -
                closes[i - 1]
            ),

            abs(
                lows[i]
                -
                closes[i - 1]
            )
        )

        true_ranges.append(tr)

    if not true_ranges:
        return 0.0

    p = min(
        period,
        len(true_ranges)
    )

    value = (
        sum(true_ranges[:p])
        /
        p
    )

    for value_now in true_ranges[p:]:

        value = (
            (
                value * (p - 1)
                +
                value_now
            )
            /
            p
        )

    return value


# ============================================================
# VWAP
# ============================================================

def vwap(
    prices,
    volumes
):

    if not prices or not volumes:
        return 0.0

    total_volume = sum(
        max(
            0.0,
            float(v)
        )
        for v in volumes
    )

    if total_volume <= 0:
        return 0.0

    weighted = sum(
        float(price)
        *
        max(
            0.0,
            float(volume)
        )

        for price, volume
        in zip(
            prices,
            volumes
        )
    )

    return (
        weighted
        /
        total_volume
    )


# ============================================================
# MARKET STRUCTURE
# ============================================================

def structure_state(
    highs,
    lows,
    closes
):

    count = len(closes)

    if count < 8:

        return "INSUFFICIENT DATA"

    recent = min(
        8,
        count
    )

    recent_highs = highs[-recent:]
    recent_lows = lows[-recent:]

    half = recent // 2

    first_high = max(
        recent_highs[:half]
    )

    second_high = max(
        recent_highs[half:]
    )

    first_low = min(
        recent_lows[:half]
    )

    second_low = min(
        recent_lows[half:]
    )

    if (
        second_high > first_high
        and
        second_low > first_low
    ):

        return "HH-HL / BULLISH"

    if (
        second_high < first_high
        and
        second_low < first_low
    ):

        return "LH-LL / BEARISH"

    return "MIXED / RANGE"


# ============================================================
# VOLUME STATE
# ============================================================

def volume_state(volumes):

    if len(volumes) < 10:

        return "NORMAL"

    recent = float(
        volumes[-1]
    )

    baseline = statistics.median(
        volumes[-11:-1]
    )

    if baseline <= 0:

        return "NORMAL"

    ratio = (
        recent
        /
        baseline
    )

    if ratio >= 1.5:

        return "HIGH"

    if ratio <= 0.65:

        return "LOW"

    return "NORMAL"


# ============================================================
# TIME PARSER
# ============================================================

def parse_time(value):

    if value is None:
        return None

    if isinstance(
        value,
        (int, float)
    ):

        timestamp = float(value)

        if timestamp > 1e12:

            timestamp /= 1000.0

        try:

            return datetime.fromtimestamp(
                timestamp,
                tz=timezone.utc
            )

        except Exception:

            return None

    text = str(value).strip()

    if not text:
        return None

    try:

        if text.isdigit():

            return parse_time(
                float(text)
            )

        text = text.replace(
            "Z",
            "+00:00"
        )

        dt = datetime.fromisoformat(
            text
        )

        if dt.tzinfo is None:

            dt = dt.replace(
                tzinfo=timezone.utc
            )

        return dt

    except Exception:

        pass

    formats = [
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    ]

    for fmt in formats:

        try:

            return datetime.strptime(
                str(value),
                fmt
            ).replace(
                tzinfo=timezone.utc
            )

        except Exception:

            pass

    return None


# ============================================================
# CANDLE EXTRACTION
# ============================================================

def find_candle_rows(obj):

    found = []

    def walk(node):

        if isinstance(
            node,
            list
        ):

            if node and isinstance(
                node[0],
                dict
            ):

                keys = {
                    str(k).lower()
                    for k in node[0].keys()
                }

                normal = (
                    "open" in keys
                    and
                    "high" in keys
                    and
                    "low" in keys
                    and
                    "close" in keys
                )

                short = (
                    "o" in keys
                    and
                    "h" in keys
                    and
                    "l" in keys
                    and
                    "c" in keys
                )

                if normal or short:

                    found.append(node)

            for item in node:

                walk(item)

        elif isinstance(
            node,
            dict
        ):

            keys = {
                str(k).lower()
                for k in node.keys()
            }

            normal = (
                "open" in keys
                and
                "high" in keys
                and
                "low" in keys
                and
                "close" in keys
            )

            short = (
                "o" in keys
                and
                "h" in keys
                and
                "l" in keys
                and
                "c" in keys
            )

            if normal or short:

                found.append(
                    [node]
                )

            for value in node.values():

                walk(value)

    walk(obj)

    if not found:

        return []

    return max(
        found,
        key=len
    )


def extract_candles(data):

    rows = find_candle_rows(
        data
    )

    candles = []

    for row in rows:

        if not isinstance(
            row,
            dict
        ):
            continue

        lower = {
            str(k).lower(): v
            for k, v in row.items()
        }

        open_price = lower.get(
            "open",
            lower.get("o")
        )

        high_price = lower.get(
            "high",
            lower.get("h")
        )

        low_price = lower.get(
            "low",
            lower.get("l")
        )

        close_price = lower.get(
            "close",
            lower.get("c")
        )

        volume = lower.get(
            "volume",
            lower.get(
                "v",
                lower.get(
                    "vol",
                    0
                )
            )
        )

        timestamp = (
            lower.get("timestamp")
            or
            lower.get("time")
            or
            lower.get("date")
            or
            lower.get("datetime")
        )

        open_price = clean_number(
            open_price
        )

        high_price = clean_number(
            high_price
        )

        low_price = clean_number(
            low_price
        )

        close_price = clean_number(
            close_price
        )

        volume = clean_number(
            volume
        )

        dt = parse_time(
            timestamp
        )

        if (
            close_price > 0
            and
            high_price > 0
            and
            low_price > 0
        ):

            candles.append({
                "time": dt,
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
                "volume": max(
                    0.0,
                    volume
                ),
            })

    candles.sort(
        key=lambda x:
        x["time"]
        or
        datetime.min.replace(
            tzinfo=timezone.utc
        )
    )

    return candles[-250:]


# ============================================================
# NSE INDEX CANDLES
# ============================================================

def fetch_index_candles(
    symbol,
    session
):

    info = INDEX_INFO[symbol]

    warm_nse(session)
    warm_chart(session)

    now = datetime.now(
        timezone.utc
    )

    start = (
        now
        -
        timedelta(days=5)
    )

    payload = {
        "token":
            info["token"],

        "fromDate":
            int(start.timestamp()),

        "toDate":
            int(now.timestamp()),

        "symbol":
            info["name"],

        "symbolType":
            "Index",

        "chartType":
            "I",

        "timeInterval":
            5,
    }

    data = get_json(
        session,
        CHART_HISTORY,
        params=payload,
        timeout=25,
        tries=3
    )

    candles = extract_candles(
        data
    )

    if not candles:

        payload["symbol"] = (
            "NIFTY 50"
            if symbol == "NIFTY"
            else
            "NIFTY BANK"
        )

        data = get_json(
            session,
            CHART_HISTORY,
            params=payload,
            timeout=25,
            tries=2
        )

        candles = extract_candles(
            data
        )

    return candles


# ============================================================
# FUTURES CANDLES / VOLUME
# ============================================================

def fetch_futures_candles(
    symbol,
    session
):

    warm_nse(session)
    warm_chart(session)

    search_symbol = (
        "NIFTY"
        if symbol == "NIFTY"
        else
        "BANKNIFTY"
    )

    data = get_json(
        session,
        CHART_SYMBOLS,
        params={
            "symbol":
                search_symbol,

            "segment":
                "FO"
        },
        timeout=20,
        tries=3
    )

    rows = (
        data.get(
            "data",
            []
        )
        if isinstance(
            data,
            dict
        )
        else []
    )

    futures = [
        row
        for row in rows
        if
        str(
            row.get(
                "type",
                ""
            )
        ).lower()
        ==
        "futures"
        and
        str(
            row.get(
                "symbol",
                ""
            )
        ).upper().startswith(
            search_symbol
        )
    ]

    if not futures:

        return []

    selected = futures[0]

    token = str(
        selected.get(
            "scripcode"
        )
        or
        selected.get(
            "token"
        )
        or
        ""
    )

    chart_symbol = str(
        selected.get(
            "fullname"
        )
        or
        selected.get(
            "symbol"
        )
        or
        ""
    )

    symbol_type = str(
        selected.get(
            "type"
        )
        or
        "Futures"
    )

    if (
        not token
        or
        not chart_symbol
    ):

        return []

    now = datetime.now(
        timezone.utc
    )

    start = (
        now
        -
        timedelta(days=5)
    )

    payload = {
        "token":
            token,

        "fromDate":
            int(start.timestamp()),

        "toDate":
            int(now.timestamp()),

        "symbol":
            chart_symbol,

        "symbolType":
            symbol_type,

        "chartType":
            "I",

        "timeInterval":
            5,
    }

    try:

        data = get_json(
            session,
            CHART_HISTORY,
            params=payload,
            timeout=25,
            tries=3
        )

        return extract_candles(
            data
        )

    except Exception:

        return []


# ============================================================
# TECHNICAL ENGINE
# ============================================================

def build_technical(
    candles,
    futures_candles
):

    if not candles:

        raise RuntimeError(
            "NSE 5-minute index candle data is unavailable"
        )

    closes = [
        x["close"]
        for x in candles
    ]

    highs = [
        x["high"]
        for x in candles
    ]

    lows = [
        x["low"]
        for x in candles
    ]

    if futures_candles:

        prices = [
            x["close"]
            for x in futures_candles
        ]

        volumes = [
            x["volume"]
            for x in futures_candles
        ]

        technical_price = closes[-1]

        technical_vwap = vwap(
            prices,
            volumes
        )

        technical_volume = (
            volumes[-1]
            if volumes
            else
            0.0
        )

        volume_status = (
            volume_state(
                volumes
            )
        )

        volume_source = "FUTURES"

    else:

        prices = closes

        volumes = [
            x["volume"]
            for x in candles
        ]

        technical_price = closes[-1]

        technical_vwap = vwap(
            prices,
            volumes
        )

        technical_volume = (
            volumes[-1]
            if volumes
            else
            0.0
        )

        if any(volumes):

            volume_status = (
                volume_state(
                    volumes
                )
            )

        else:

            volume_status = (
                "UNAVAILABLE"
            )

        volume_source = "INDEX"

    if (
        technical_vwap <= 0
        and
        any(volumes)
    ):

        technical_vwap = vwap(
            prices,
            volumes
        )

    return {

        "price":
            technical_price,

        "vwap":
            technical_vwap,

        "ema9":
            ema(
                closes,
                9
            ),

        "ema21":
            ema(
                closes,
                21
            ),

        "rsi":
            rsi(
                closes,
                14
            ),

        "atr":
            atr(
                highs,
                lows,
                closes,
                14
            ),

        "structure":
            structure_state(
                highs,
                lows,
                closes
            ),

        "volume":
            technical_volume,

        "volumeState":
            volume_status,

        "volumeSource":
            volume_source,

        "candles":
            len(candles),

        "futuresCandles":
            len(futures_candles),
    }


# ============================================================
# OPTION CHAIN
# ============================================================

def nearest_expiry(records):

    expiries = []

    for record in records:

        expiry = record.get(
            "expiryDate"
        )

        if expiry:

            expiries.append(
                expiry
            )

    if not expiries:

        return ""

    unique = sorted(
        set(expiries),
        key=lambda x:
        parse_time(x)
        or
        datetime.max.replace(
            tzinfo=timezone.utc
        )
    )

    return unique[0]


def fetch_option_chain(
    symbol,
    session
):

    warm_nse(session)

    last_error = None

    for attempt in range(3):

        try:

            nse_symbol = (
                "NIFTY"
                if symbol == "NIFTY"
                else
                "BANKNIFTY"
            )

            data = get_json(
                session,
                NSE_OPTION_CHAIN,
                params={
                    "symbol":
                        nse_symbol
                },
                timeout=25,
                tries=1
            )

            records = data.get(
                "records",
                {}
            )

            rows = (
                records.get(
                    "data",
                    []
                )
                if isinstance(
                    records,
                    dict
                )
                else []
            )

            spot = clean_number(
                records.get(
                    "underlyingValue"
                )
            )

            if (
                not rows
                or
                spot <= 0
            ):

                raise RuntimeError(
                    "NSE option-chain response contained no usable rows"
                )

            expiry = nearest_expiry(
                rows
            )

            filtered = [
                row
                for row in rows
                if
                not expiry
                or
                row.get(
                    "expiryDate"
                )
                ==
                expiry
            ]

            if not filtered:

                filtered = rows

            return {

                "spot":
                    spot,

                "expiry":
                    expiry,

                "rows":
                    filtered,

                "all_rows":
                    rows,

                "raw":
                    data,
            }

        except Exception as exc:

            last_error = exc

            time.sleep(
                1.0
                +
                attempt
            )

    raise (
        last_error
        or
        RuntimeError(
            "NSE option-chain request failed"
        )
    )


# ============================================================
# OPTION ANALYSIS
# ============================================================

def option_side(
    item,
    side
):

    return item.get(
        side,
        {}
    ) or {}


def analyse_options(
    chain,
    symbol
):

    spot = chain["spot"]

    rows = chain["rows"]

    step = INDEX_INFO[
        symbol
    ]["step"]

    calls = []
    puts = []

    for row in rows:

        strike = clean_number(
            row.get(
                "strikePrice"
            )
        )

        if strike <= 0:

            continue

        ce = option_side(
            row,
            "CE"
        )

        pe = option_side(
            row,
            "PE"
        )

        if ce:

            calls.append({

                "strike":
                    strike,

                "oi":
                    clean_int(
                        ce.get(
                            "openInterest"
                        )
                    ),

                "changeOI":
                    clean_int(
                        ce.get(
                            "changeinOpenInterest"
                        )
                    ),

                "ltp":
                    clean_number(
                        ce.get(
                            "lastPrice"
                        )
                    ),

                "change":
                    clean_number(
                        ce.get(
                            "change"
                        )
                    ),

                "volume":
                    clean_int(
                        ce.get(
                            "totalTradedVolume"
                        )
                    ),

                "iv":
                    clean_number(
                        ce.get(
                            "impliedVolatility"
                        )
                    ),
            })

        if pe:

            puts.append({

                "strike":
                    strike,

                "oi":
                    clean_int(
                        pe.get(
                            "openInterest"
                        )
                    ),

                "changeOI":
                    clean_int(
                        pe.get(
                            "changeinOpenInterest"
                        )
                    ),

                "ltp":
                    clean_number(
                        pe.get(
                            "lastPrice"
                        )
                    ),

                "change":
                    clean_number(
                        pe.get(
                            "change"
                        )
                    ),

                "volume":
                    clean_int(
                        pe.get(
                            "totalTradedVolume"
                        )
                    ),

                "iv":
                    clean_number(
                        pe.get(
                            "impliedVolatility"
                        )
                    ),
            })

    call_oi = sum(
        x["oi"]
        for x in calls
    )

    put_oi = sum(
        x["oi"]
        for x in puts
    )

    call_doi = sum(
        x["changeOI"]
        for x in calls
    )

    put_doi = sum(
        x["changeOI"]
        for x in puts
    )

    if call_oi > 0:

        pcr = (
            put_oi
            /
            call_oi
        )

    else:

        pcr = 0

    atm = (
        round(
            spot / step
        )
        *
        step
    )

    nearby_calls = sorted(
        calls,
        key=lambda x:
        abs(
            x["strike"]
            -
            atm
        )
    )[:9]

    nearby_puts = sorted(
        puts,
        key=lambda x:
        abs(
            x["strike"]
            -
            atm
        )
    )[:9]

    # --------------------------------------------------------
    # OI CLASSIFICATION
    # --------------------------------------------------------

    def classify(
        items,
        is_call
    ):

        near = [
            x
            for x in items
            if
            abs(
                x["strike"]
                -
                atm
            )
            <=
            step * 3
        ]

        if not near:

            near = items

        oi_change = sum(
            x["changeOI"]
            for x in near
        )

        premium_change = sum(
            x["change"]
            for x in near
        )

        if (
            oi_change > 0
            and
            premium_change < 0
        ):

            return (
                "CALL WRITING"
                if is_call
                else
                "PUT WRITING"
            )

        if (
            oi_change < 0
            and
            premium_change > 0
        ):

            return (
                "CALL SHORT COVERING"
                if is_call
                else
                "PUT SHORT COVERING"
            )

        if (
            oi_change > 0
            and
            premium_change > 0
        ):

            return (
                "CALL LONG BUILDUP"
                if is_call
                else
                "PUT LONG BUILDUP"
            )

        if (
            oi_change < 0
            and
            premium_change < 0
        ):

            return (
                "CALL LONG UNWINDING"
                if is_call
                else
                "PUT LONG UNWINDING"
            )

        return "OI MIXED"

    call_position = classify(
        calls,
        True
    )

    put_position = classify(
        puts,
        False
    )

    # --------------------------------------------------------
    # SUPPORT / RESISTANCE
    # --------------------------------------------------------

    resistance_candidates = sorted(

        [
            x
            for x in calls
            if
            x["strike"] >= spot
        ],

        key=lambda x:
        x["oi"],

        reverse=True
    )

    support_candidates = sorted(

        [
            x
            for x in puts
            if
            x["strike"] <= spot
        ],

        key=lambda x:
        x["oi"],

        reverse=True
    )

    if resistance_candidates:

        resistance = (
            resistance_candidates[0]
            ["strike"]
        )

    else:

        resistance = (
            atm + step
        )

    if len(
        resistance_candidates
    ) > 1:

        resistance2 = (
            resistance_candidates[1]
            ["strike"]
        )

    else:

        resistance2 = (
            resistance + step
        )

    if support_candidates:

        support = (
            support_candidates[0]
            ["strike"]
        )

    else:

        support = (
            atm - step
        )

    if len(
        support_candidates
    ) > 1:

        support2 = (
            support_candidates[1]
            ["strike"]
        )

    else:

        support2 = (
            support - step
        )

    # --------------------------------------------------------
    # OI MIGRATION
    # --------------------------------------------------------

    call_sorted = sorted(

        [
            x
            for x in calls
            if
            abs(
                x["strike"]
                -
                atm
            )
            <=
            step * 5
        ],

        key=lambda x:
        x["oi"],

        reverse=True
    )

    put_sorted = sorted(

        [
            x
            for x in puts
            if
            abs(
                x["strike"]
                -
                atm
            )
            <=
            step * 5
        ],

        key=lambda x:
        x["oi"],

        reverse=True
    )

    def migration_text(
        items,
        side_name
    ):

        if len(items) < 2:

            return (
                f"{side_name} OI migration unavailable"
            )

        first = items[0]
        second = items[1]

        if (
            first["strike"]
            >
            second["strike"]
            and
            first["changeOI"] > 0
        ):

            return (
                f"{side_name} OI concentrated higher"
            )

        if (
            first["strike"]
            <
            second["strike"]
            and
            first["changeOI"] > 0
        ):

            return (
                f"{side_name} OI concentrated lower"
            )

        return (
            f"{side_name} OI mixed"
        )

    call_migration = migration_text(
        call_sorted,
        "CE"
    )

    put_migration = migration_text(
        put_sorted,
        "PE"
    )

    # --------------------------------------------------------
    # IV
    # --------------------------------------------------------

    iv_values = [
        x["iv"]
        for x in (
            nearby_calls
            +
            nearby_puts
        )
        if x["iv"] > 0
    ]

    if iv_values:

        iv = statistics.median(
            iv_values
        )

    else:

        iv = 0.0

    # --------------------------------------------------------
    # MAX PAIN
    # --------------------------------------------------------

    strikes = sorted(
        set(
            [
                x["strike"]
                for x in calls
            ]
            +
            [
                x["strike"]
                for x in puts
            ]
        )
    )

    max_pain = atm

    if strikes:

        best_loss = None

        for settlement in strikes:

            loss = 0.0

            for call in calls:

                loss += (
                    max(
                        0.0,
                        settlement
                        -
                        call["strike"]
                    )
                    *
                    call["oi"]
                )

            for put in puts:

                loss += (
                    max(
                        0.0,
                        put["strike"]
                        -
                        settlement
                    )
                    *
                    put["oi"]
                )

            if (
                best_loss is None
                or
                loss < best_loss
            ):

                best_loss = loss

                max_pain = settlement

    return {

        "callOI":
            call_oi,

        "putOI":
            put_oi,

        "callDOI":
            call_doi,

        "putDOI":
            put_doi,

        "pcr":
            pcr,

        "iv":
            iv,

        "callPosition":
            call_position,

        "putPosition":
            put_position,

        "resistance":
            resistance,

        "resistance2":
            resistance2,

        "support":
            support,

        "support2":
            support2,

        "maxPain":
            max_pain,

        "atm":
            atm,

        "callMigration":
            call_migration,

        "putMigration":
            put_migration,

        "callRows":
            nearby_calls,

        "putRows":
            nearby_puts,
    }


# ============================================================
# TECHNICAL SCORE
# ============================================================

def technical_direction(
    technical
):

    score = 0

    reasons = []

    # VWAP
    if (
        technical["price"]
        >
        technical["vwap"]
        >
        0
    ):

        score += 20

        reasons.append(
            "price is above VWAP"
        )

    elif technical["vwap"] > 0:

        score -= 20

        reasons.append(
            "price is below VWAP"
        )

    # EMA
    if (
        technical["ema9"]
        >
        technical["ema21"]
    ):

        score += 15

        reasons.append(
            "EMA9 is above EMA21"
        )

    else:

        score -= 15

        reasons.append(
            "EMA9 is below EMA21"
        )

    # RSI
    if technical["rsi"] >= 58:

        score += 10

        reasons.append(
            "RSI confirms bullish momentum"
        )

    elif technical["rsi"] <= 42:

        score -= 10

        reasons.append(
            "RSI confirms bearish momentum"
        )

    # Structure
    if (
        "HH-HL"
        in
        technical["structure"]
    ):

        score += 15

        reasons.append(
            "price structure is HH-HL"
        )

    elif (
        "LH-LL"
        in
        technical["structure"]
    ):

        score -= 15

        reasons.append(
            "price structure is LH-LL"
        )

    if (
        technical["volumeState"]
        ==
        "HIGH"
    ):

        reasons.append(
            "volume is elevated"
        )

    return (
        score,
        reasons
    )


# ============================================================
# OPTION SCORE
# ============================================================

def option_direction(
    options
):

    score = 0

    reasons = []

    # CE
    if (
        options["callPosition"]
        ==
        "CALL WRITING"
    ):

        score -= 15

        reasons.append(
            "call-side OI is building with premium weakness, adding resistance"
        )

    elif (
        options["callPosition"]
        ==
        "CALL SHORT COVERING"
    ):

        score += 20

        reasons.append(
            "call short covering is supporting the upside"
        )

    elif (
        options["callPosition"]
        ==
        "CALL LONG BUILDUP"
    ):

        score += 8

        reasons.append(
            "call-side long buildup is present"
        )

    elif (
        options["callPosition"]
        ==
        "CALL LONG UNWINDING"
    ):

        score -= 3

        reasons.append(
            "call longs are unwinding"
        )

    # PE
    if (
        options["putPosition"]
        ==
        "PUT WRITING"
    ):

        score += 15

        reasons.append(
            "put writing is strengthening support"
        )

    elif (
        options["putPosition"]
        ==
        "PUT SHORT COVERING"
    ):

        score -= 15

        reasons.append(
            "put short covering is weakening support"
        )

    elif (
        options["putPosition"]
        ==
        "PUT LONG BUILDUP"
    ):

        score -= 8

        reasons.append(
            "put-side long buildup is bearish"
        )

    elif (
        options["putPosition"]
        ==
        "PUT LONG UNWINDING"
    ):

        score += 3

        reasons.append(
            "put longs are unwinding"
        )

    # PCR
    if options["pcr"] >= 1.15:

        score += 10

        reasons.append(
            "PCR is supportive"
        )

    elif options["pcr"] <= 0.85:

        score -= 10

        reasons.append(
            "PCR is bearish"
        )

    else:

        reasons.append(
            "PCR is neutral"
        )

    return (
        score,
        reasons
    )


# ============================================================
# NATURAL LANGUAGE STORY
# ============================================================

def build_story(
    condition,
    signal,
    reasons
):

    if condition == "BULLISH":

        lead = (
            "Bullish momentum is strengthening"
        )

    elif condition == "BEARISH":

        lead = (
            "Bearish momentum is strengthening"
        )

    else:

        lead = (
            "Market momentum is mixed/sideways"
        )

    if signal == "BUY CALL":

        ending = (
            "CALL buying conditions are confirmed."
        )

    elif signal == "BUY PUT":

        ending = (
            "PUT buying conditions are confirmed."
        )

    else:

        ending = (
            "Waiting for stronger confirmation."
        )

    selected = reasons[:5]

    if selected:

        return (
            lead
            +
            " because "
            +
            "; ".join(selected)
            +
            ". "
            +
            ending
        )

    return (
        lead
        +
        ". "
        +
        ending
    )


# ============================================================
# DECISION ENGINE
# ============================================================

def build_decision(
    technical,
    options
):

    technical_score, technical_reasons = (
        technical_direction(
            technical
        )
    )

    oi_score, oi_reasons = (
        option_direction(
            options
        )
    )

    directional_score = (
        technical_score
        +
        oi_score
    )

    raw_score = (
        50
        +
        directional_score * 0.5
    )

    raw_score = max(
        0,
        min(
            100,
            raw_score
        )
    )

    bullish = (
        directional_score >= 35
    )

    bearish = (
        directional_score <= -35
    )

    # --------------------------------------------------------
    # BUYER CONFIRMATION
    # --------------------------------------------------------

    bullish_confirm = (

        technical["price"]
        >
        technical["vwap"]
        >
        0

        and

        technical["ema9"]
        >
        technical["ema21"]

        and

        technical["rsi"]
        >=
        52

        and

        (
            options["callPosition"]
            in
            (
                "CALL SHORT COVERING",
                "OI MIXED",
                "CALL LONG BUILDUP"
            )

            or

            options["putPosition"]
            ==
            "PUT WRITING"
        )
    )

    bearish_confirm = (

        technical["price"]
        <
        technical["vwap"]

        and

        technical["vwap"]
        >
        0

        and

        technical["ema9"]
        <
        technical["ema21"]

        and

        technical["rsi"]
        <=
        48

        and

        (
            options["putPosition"]
            in
            (
                "PUT SHORT COVERING",
                "OI MIXED",
                "PUT LONG BUILDUP"
            )

            or

            options["callPosition"]
            ==
            "CALL WRITING"
        )
    )

    # --------------------------------------------------------
    # CONFLICT FILTER
    # --------------------------------------------------------

    conflict = (

        (
            technical["price"]
            >
            technical["vwap"]
            and
            directional_score < 0
        )

        or

        (
            technical["price"]
            <
            technical["vwap"]
            and
            directional_score > 0
        )
    )

    # --------------------------------------------------------
    # FINAL SIGNAL
    # --------------------------------------------------------

    if (
        bullish
        and
        bullish_confirm
        and
        not conflict
        and
        raw_score >= 70
    ):

        condition = "BULLISH"

        signal = "BUY CALL"

    elif (
        bearish
        and
        bearish_confirm
        and
        not conflict
        and
        raw_score >= 70
    ):

        condition = "BEARISH"

        signal = "BUY PUT"

    elif bullish:

        condition = "BULLISH"

        signal = "WAIT"

    elif bearish:

        condition = "BEARISH"

        signal = "WAIT"

    else:

        condition = "SIDEWAYS"

        signal = "WAIT"

    # --------------------------------------------------------
    # CONFIDENCE
    # --------------------------------------------------------

    confidence = int(
        round(
            max(
                50,
                min(
                    95,
                    50
                    +
                    abs(
                        directional_score
                    )
                    *
                    0.45
                )
            )
        )
    )

    reasons = (
        technical_reasons
        +
        oi_reasons
    )

    if conflict:

        reasons.append(
            "price and positioning are conflicting, so buyer confirmation is blocked"
        )

    if signal == "WAIT":

        reasons.append(
            "waiting for stronger confirmation before buying an option"
        )

    statement = build_story(
        condition,
        signal,
        reasons
    )

    return {

        "condition":
            condition,

        "signal":
            signal,

        "score":
            int(
                round(
                    raw_score
                )
            ),

        "directional_score":
            int(
                directional_score
            ),

        "confidence":
            confidence,

        "reasons":
            reasons,

        "statement":
            statement,
    }


# ============================================================
# TRADE LEVELS
# ============================================================

def trade_levels(
    technical,
    options,
    decision
):

    price = technical["price"]

    atr_value = max(
        technical["atr"],
        price * 0.001
    )

    step = INDEX_INFO[
        options["symbol"]
    ]["step"]

    if decision["signal"] == "BUY CALL":

        entry = price

        stop = (
            price
            -
            max(
                atr_value * 1.2,
                step * 0.75
            )
        )

        target1 = (
            price
            +
            max(
                atr_value * 1.5,
                step * 1.0
            )
        )

        target2 = (
            price
            +
            max(
                atr_value * 2.5,
                step * 1.5
            )
        )

    elif decision["signal"] == "BUY PUT":

        entry = price

        stop = (
            price
            +
            max(
                atr_value * 1.2,
                step * 0.75
            )
        )

        target1 = (
            price
            -
            max(
                atr_value * 1.5,
                step * 1.0
            )
        )

        target2 = (
            price
            -
            max(
                atr_value * 2.5,
                step * 1.5
            )
        )

    else:

        entry = 0
        stop = 0
        target1 = 0
        target2 = 0

    return {

        "entry":
            round_or_zero(
                entry
            ),

        "stoploss":
            round_or_zero(
                stop
            ),

        "target1":
            round_or_zero(
                target1
            ),

        "target2":
            round_or_zero(
                target2
            ),
    }


# ============================================================
# MAIN MARKET ENGINE
# ============================================================

def market_payload(
    symbol
):

    symbol = (
        symbol
        .upper()
        .strip()
    )

    if symbol not in INDEX_INFO:

        raise HTTPException(
            status_code=400,
            detail=(
                "symbol must be "
                "NIFTY or BANKNIFTY"
            )
        )

    # --------------------------------------------------------
    # CACHE
    # --------------------------------------------------------

    current_time = time.time()

    cached = CACHE.get(
        symbol
    )

    if (
        cached
        and
        current_time
        -
        cached["time"]
        <
        CACHE_TTL
    ):

        return cached["data"]

    # --------------------------------------------------------
    # SESSION
    # --------------------------------------------------------

    session = make_session()

    try:

        # ----------------------------------------------------
        # OPTION CHAIN
        # ----------------------------------------------------

        chain = fetch_option_chain(
            symbol,
            session
        )

        # ----------------------------------------------------
        # INDEX CANDLES
        # ----------------------------------------------------

        try:

            candles = fetch_index_candles(
                symbol,
                session
            )

        except Exception:

            # Fresh session retry
            session = make_session()

            candles = fetch_index_candles(
                symbol,
                session
            )

        # ----------------------------------------------------
        # FUTURES
        # ----------------------------------------------------

        futures_candles = (
            fetch_futures_candles(
                symbol,
                session
            )
        )

        # ----------------------------------------------------
        # TECHNICAL ENGINE
        # ----------------------------------------------------

        technical = build_technical(
            candles,
            futures_candles
        )

        # ----------------------------------------------------
        # OPTION ENGINE
        # ----------------------------------------------------

        options = analyse_options(
            chain,
            symbol
        )

        options["symbol"] = symbol

        # ----------------------------------------------------
        # DECISION
        # ----------------------------------------------------

        decision = build_decision(
            technical,
            options
        )

        # ----------------------------------------------------
        # TRADE LEVELS
        # ----------------------------------------------------

        levels = trade_levels(
            technical,
            options,
            decision
        )

        # ----------------------------------------------------
        # PRICE
        # ----------------------------------------------------

        price = (
            chain["spot"]
            if chain["spot"] > 0
            else
            technical["price"]
        )

        # ----------------------------------------------------
        # 5-MIN CHANGE
        # ----------------------------------------------------

        change = 0.0

        if len(candles) >= 2:

            change = (
                candles[-1]["close"]
                -
                candles[-2]["close"]
            )

        # ----------------------------------------------------
        # FINAL RESPONSE
        # ----------------------------------------------------

        data = {

            "ok":
                True,

            "symbol":
                symbol,

            "price":
                round_or_zero(
                    price
                ),

            "change":
                round_or_zero(
                    change
                ),

            "market_condition":
                decision[
                    "condition"
                ],

            "score":
                decision[
                    "score"
                ],

            "directional_score":
                decision[
                    "directional_score"
                ],

            "confidence":
                decision[
                    "confidence"
                ],

            "signal":
                decision[
                    "signal"
                ],

            "statement":
                decision[
                    "statement"
                ],

            # ------------------------------------------------
            # TECHNICALS
            # ------------------------------------------------

            "vwap":
                round_or_zero(
                    technical[
                        "vwap"
                    ]
                ),

            "vwapStatus":
                (
                    "ABOVE VWAP"
                    if
                    technical["vwap"] > 0
                    and
                    price >
                    technical["vwap"]

                    else

                    "BELOW VWAP"
                    if
                    technical["vwap"] > 0

                    else

                    "VWAP UNAVAILABLE"
                ),

            "ema9":
                round_or_zero(
                    technical[
                        "ema9"
                    ]
                ),

            "ema21":
                round_or_zero(
                    technical[
                        "ema21"
                    ]
                ),

            "rsi":
                round_or_zero(
                    technical[
                        "rsi"
                    ]
                ),

            "atr":
                round_or_zero(
                    technical[
                        "atr"
                    ]
                ),

            "structure":
                technical[
                    "structure"
                ],

            "volume":
                round_or_zero(
                    technical[
                        "volume"
                    ]
                ),

            "volumeState":
                technical[
                    "volumeState"
                ],

            "volumeSource":
                technical[
                    "volumeSource"
                ],

            # ------------------------------------------------
            # OPTIONS
            # ------------------------------------------------

            "pcr":
                round_or_zero(
                    options[
                        "pcr"
                    ]
                ),

            "iv":
                round_or_zero(
                    options[
                        "iv"
                    ]
                ),

            "callOI":
                options[
                    "callOI"
                ],

            "putOI":
                options[
                    "putOI"
                ],

            "oiTable":
                {
                    "call":
                        options[
                            "callPosition"
                        ],

                    "put":
                        options[
                            "putPosition"
                        ],
                },

            # ------------------------------------------------
            # SUPPORT / RESISTANCE
            # ------------------------------------------------

            "resistance":
                options[
                    "resistance"
                ],

            "resistance2":
                options[
                    "resistance2"
                ],

            "support":
                options[
                    "support"
                ],

            "support2":
                options[
                    "support2"
                ],

            # ------------------------------------------------
            # TRADE LEVELS
            # ------------------------------------------------

            "entry":
                levels[
                    "entry"
                ],

            "stoploss":
                levels[
                    "stoploss"
                ],

            "target1":
                levels[
                    "target1"
                ],

            "target2":
                levels[
                    "target2"
                ],

            # ------------------------------------------------
            # OPTION METADATA
            # ------------------------------------------------

            "expiry":
                chain[
                    "expiry"
                ],

            "max_pain":
                options[
                    "maxPain"
                ],

            "suggested_strike":
                options[
                    "atm"
                ],

            "oi_change":
                {
                    "call":
                        options[
                            "callDOI"
                        ],

                    "put":
                        options[
                            "putDOI"
                        ],
                },

            "oi_positioning":
                {
                    "call":
                        options[
                            "callPosition"
                        ],

                    "put":
                        options[
                            "putPosition"
                        ],
                },

            "oi_migration":
                {
                    "call":
                        options[
                            "callMigration"
                        ],

                    "put":
                        options[
                            "putMigration"
                        ],
                },

            # ------------------------------------------------
            # ENGINE INFORMATION
            # ------------------------------------------------

            "engine":
                {
                    "technical_score":
                        technical_direction(
                            technical
                        )[0],

                    "oi_score":
                        option_direction(
                            options
                        )[0],

                    "reasons":
                        decision[
                            "reasons"
                        ],
                },

            # ------------------------------------------------
            # DATA STATUS
            # ------------------------------------------------

            "data_status":
                (
                    "LIVE NSE OPTION CHAIN + "
                    "LIVE NSE 5-MIN CANDLES + "
                    +
                    (
                        "LIVE FUTURES VOLUME"
                        if futures_candles
                        else
                        "INDEX VOLUME UNAVAILABLE"
                    )
                ),

            "candle_count":
                technical[
                    "candles"
                ],

            "futures_candle_count":
                technical[
                    "futuresCandles"
                ],

            "timestamp":
                datetime.now(
                    timezone.utc
                ).isoformat(),
        }

        # ----------------------------------------------------
        # SAVE CACHE
        # ----------------------------------------------------

        CACHE[symbol] = {
            "time":
                time.time(),

            "data":
                data
        }

        return data

    except HTTPException:

        raise

    except Exception as exc:

        # Important:
        # Render will now show the actual reason in the response
        # instead of an unexplained 503.
        raise HTTPException(
            status_code=503,
            detail=(
                f"{symbol} market data error: "
                f"{type(exc).__name__}: {exc}"
            )
        )


# ============================================================
# HEALTH
# ============================================================

@app.get("/api/health")
def health():

    return {

        "ok":
            True,

        "service":
            "Option Intelligence Free",

        "curl_cffi":
            CURL_CFFI,

        "time":
            datetime.now(
                timezone.utc
            ).isoformat(),
    }


# ============================================================
# MARKET API
# ============================================================

@app.get("/api/market")
def market(
    symbol: str = "NIFTY"
):

    return market_payload(
        symbol
    )


# ============================================================
# FRONTEND
# ============================================================

@app.get(
    "/",
    response_class=HTMLResponse
)
def root():

    frontend_path = os.path.join(

        os.path.dirname(
            os.path.dirname(
                __file__
            )
        ),

        "frontend",

        "index.html"
    )

    if os.path.exists(
        frontend_path
    ):

        return FileResponse(
            frontend_path
        )

    return HTMLResponse(

        """
        <html>

        <head>
            <title>
                Option Intelligence Free
            </title>
        </head>

        <body
            style="
                font-family:Arial;
                text-align:center;
                padding:80px
            "
        >

            <h1>
                Option Intelligence Free
            </h1>

            <p>
                Backend is running,
                but frontend/index.html
                was not found.
            </p>

        </body>

        </html>
        """,

        status_code=404
    )
