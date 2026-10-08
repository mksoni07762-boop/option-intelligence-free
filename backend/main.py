from datetime import datetime, timedelta, timezone
from pathlib import Path
import math
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from curl_cffi import requests


APP_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = APP_DIR.parent / "frontend"

app = FastAPI(title="Option Intelligence Free")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

NSE = "https://www.nseindia.com"
CHART = "https://charting.nseindia.com"
CACHE = {}
CACHE_SECONDS = 15


def f(v, default=0.0):
    try:
        if v is None:
            return default
        x = float(v)
        return default if math.isnan(x) or math.isinf(x) else x
    except Exception:
        return default


def i(v, default=0):
    try:
        return int(float(v))
    except Exception:
        return default


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def session():
    return requests.Session(
        impersonate="chrome",
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/154.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": NSE + "/",
            "Connection": "keep-alive",
        },
    )


def get_json(s, url, params=None, timeout=15):
    r = s.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def ema(values, period):
    if not values:
        return 0.0
    k = 2.0 / (period + 1.0)
    x = values[0]
    for v in values[1:]:
        x = v * k + x * (1.0 - k)
    return x


def rsi(values, period=14):
    if len(values) < period + 1:
        return 50.0
    gains = []
    losses = []
    for a, b in zip(values[:-1], values[1:]):
        d = b - a
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    for n in range(period, len(gains)):
        ag = (ag * (period - 1) + gains[n]) / period
        al = (al * (period - 1) + losses[n]) / period
    if al == 0:
        return 100.0 if ag > 0 else 50.0
    return 100.0 - (100.0 / (1.0 + ag / al))


def atr(candles, period=14):
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for n in range(1, len(candles)):
        h = f(candles[n]["high"])
        l = f(candles[n]["low"])
        pc = f(candles[n - 1]["close"])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-period:]) / period


def vwap(candles):
    if not candles:
        return 0.0
    # Chart data timestamps are treated as India-session data.
    # Reset when the date changes.
    total_pv = 0.0
    total_v = 0.0
    last_date = None
    for c in candles:
        ts = i(c.get("time"))
        if ts:
            dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
            # NSE cash session is 03:45-10:00 UTC. Shift to IST for date.
            ist_date = (dt + timedelta(hours=5, minutes=30)).date()
        else:
            ist_date = last_date
        if last_date is not None and ist_date != last_date:
            total_pv = 0.0
            total_v = 0.0
        last_date = ist_date
        typical = (f(c["high"]) + f(c["low"]) + f(c["close"])) / 3.0
        vol = f(c.get("volume"))
        total_pv += typical * vol
        total_v += vol
    return total_pv / total_v if total_v else 0.0


def structure(candles):
    if len(candles) < 8:
        return "NEUTRAL"
    recent = candles[-8:]
    highs = [f(x["high"]) for x in recent]
    lows = [f(x["low"]) for x in recent]
    if highs[-1] > max(highs[:-2]) and lows[-1] >= lows[-3]:
        return "HH-HL"
    if lows[-1] < min(lows[:-2]) and highs[-1] <= highs[-3]:
        return "LH-LL"
    return "NEUTRAL"


def volume_state(candles):
    if len(candles) < 21:
        return "NORMAL"
    now = f(candles[-1].get("volume"))
    avg = sum(f(x.get("volume")) for x in candles[-21:-1]) / 20.0
    if avg <= 0:
        return "NORMAL"
    if now > avg * 1.5:
        return "HIGH"
    if now < avg * 0.7:
        return "LOW"
    return "NORMAL"


def option_rows(payload):
    records = payload.get("records", {}) if isinstance(payload, dict) else {}
    data = records.get("data", []) or payload.get("data", []) or []
    rows = []
    for item in data:
        strike = f(item.get("strikePrice"))
        ce = item.get("CE") or {}
        pe = item.get("PE") or {}
        if not strike:
            continue
        rows.append({
            "strike": strike,
            "CE": {
                "oi": i(ce.get("openInterest")),
                "changeOI": i(ce.get("changeinOpenInterest")),
                "volume": i(ce.get("totalTradedVolume")),
                "iv": f(ce.get("impliedVolatility")),
                "ltp": f(ce.get("lastPrice")),
            },
            "PE": {
                "oi": i(pe.get("openInterest")),
                "changeOI": i(pe.get("changeinOpenInterest")),
                "volume": i(pe.get("totalTradedVolume")),
                "iv": f(pe.get("impliedVolatility")),
                "ltp": f(pe.get("lastPrice")),
            },
        })
    return rows


def max_pain(rows):
    if not rows:
        return 0.0
    strikes = [x["strike"] for x in rows if x["strike"] > 0]
    best = None
    best_loss = None
    for s in strikes:
        loss = 0.0
        for x in rows:
            k = x["strike"]
            loss += max(s - k, 0) * x["CE"]["oi"]
            loss += max(k - s, 0) * x["PE"]["oi"]
        if best_loss is None or loss < best_loss:
            best_loss = loss
            best = s
    return best or 0.0


def fetch_option_chain(symbol):
    s = session()

    # Warm NSE cookies.
    for url in (
        NSE + "/",
        NSE + "/option-chain",
        NSE + "/api/allIndices",
    ):
        try:
            s.get(url, timeout=12)
        except Exception:
            pass

    ci = get_json(
        s,
        NSE + "/api/option-chain-contract-info",
        params={"symbol": symbol},
    )

    expiries = (
        ci.get("expiryDates")
        or ci.get("records", {}).get("expiryDates")
        or []
    )
    if not expiries:
        raise RuntimeError("NSE did not return an expiry")

    expiry = expiries[0]

    payload = get_json(
        s,
        NSE + "/api/option-chain-v3",
        params={
            "type": "Indices",
            "symbol": symbol,
            "expiry": expiry,
        },
    )

    rows = option_rows(payload)
    if not rows:
        raise RuntimeError("NSE returned no option-chain rows")

    records = payload.get("records", {})
    spot = f(
        records.get("underlyingValue")
        or payload.get("underlyingValue")
    )

    return {
        "spot": spot,
        "expiry": expiry,
        "rows": rows,
        "max_pain": max_pain(rows),
    }


def find_chart_symbol(s, search_symbol):
    data = s.get(
        CHART + "/v1/exchanges/symbolsDynamic",
        json={"symbol": search_symbol, "segment": "IDX"},
        timeout=15,
    )
    data.raise_for_status()
    obj = data.json()
    items = obj.get("data", obj if isinstance(obj, list) else [])
    for x in items:
        text = " ".join(str(x.get(k, "")) for k in x.keys()).upper()
        if "NIFTY 50" in text or search_symbol == "NIFTY BANK":
            token = x.get("token") or x.get("symbolToken")
            if token:
                return x
    return items[0] if items else None


def fetch_candles(symbol):
    search_symbol = "NIFTY" if symbol == "NIFTY" else "NIFTY BANK"
    s = session()

    info = find_chart_symbol(s, search_symbol)
    token = None
    chart_symbol = search_symbol
    symbol_type = "IDX"

    if info:
        token = info.get("token") or info.get("symbolToken")
        chart_symbol = (
            info.get("symbol")
            or info.get("displayName")
            or search_symbol
        )
        symbol_type = info.get("symbolType") or "IDX"

    if not token:
        token = "26000" if symbol == "NIFTY" else "26004"

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=7)

    payload = {
        "token": str(token),
        "fromDate": start.strftime("%Y-%m-%d"),
        "toDate": end.strftime("%Y-%m-%d"),
        "symbol": chart_symbol,
        "symbolType": symbol_type,
        "chartType": "I",
        "timeInterval": 5,
    }

    r = s.post(
        CHART + "/v1/charts/symbolHistoricalData",
        json=payload,
        timeout=20,
    )
    if r.status_code >= 400:
        # Some versions of the endpoint accept GET parameters.
        r = s.get(
            CHART + "/v1/charts/symbolHistoricalData",
            params=payload,
            timeout=20,
        )
    r.raise_for_status()

    obj = r.json()
    data = obj.get("data", obj if isinstance(obj, list) else [])
    candles = []

    for x in data:
        if not isinstance(x, dict):
            continue
        if not all(k in x for k in ("open", "high", "low", "close")):
            continue
        candles.append({
            "time": i(x.get("time")),
            "open": f(x.get("open")),
            "high": f(x.get("high")),
            "low": f(x.get("low")),
            "close": f(x.get("close")),
            "volume": f(x.get("volume")),
        })

    candles.sort(key=lambda x: x["time"])
    return candles[-500:]


def oi_summary(rows, spot):
    if not rows:
        return {
            "call_oi": 0, "put_oi": 0,
            "call_change": 0, "put_change": 0,
            "call_position": "UNKNOWN",
            "put_position": "UNKNOWN",
            "pcr": 0.0, "iv": 0.0,
            "support": 0.0, "support2": 0.0,
            "resistance": 0.0, "resistance2": 0.0,
            "score": 0, "reasons": []
        }

    call_oi = sum(x["CE"]["oi"] for x in rows)
    put_oi = sum(x["PE"]["oi"] for x in rows)
    call_ch = sum(x["CE"]["changeOI"] for x in rows)
    put_ch = sum(x["PE"]["changeOI"] for x in rows)
    pcr = put_oi / call_oi if call_oi else 0.0

    def pos(ch, side):
        if ch > 0:
            return "CALL WRITING" if side == "call" else "PUT WRITING"
        if ch < 0:
            return "CALL UNWINDING" if side == "call" else "PUT UNWINDING"
        return "NEUTRAL"

    call_position = pos(call_ch, "call")
    put_position = pos(put_ch, "put")

    ivs = []
    for x in rows:
        if x["strike"] <= 0:
            continue
        if abs(x["strike"] - spot) <= max(500, spot * 0.02):
            if x["CE"]["iv"] > 0:
                ivs.append(x["CE"]["iv"])
            if x["PE"]["iv"] > 0:
                ivs.append(x["PE"]["iv"])
    iv = sum(ivs) / len(ivs) if ivs else 0.0

    below = [x for x in rows if x["strike"] <= spot]
    above = [x for x in rows if x["strike"] >= spot]

    support_candidates = sorted(
        below, key=lambda x: x["PE"]["oi"], reverse=True
    )
    resistance_candidates = sorted(
        above, key=lambda x: x["CE"]["oi"], reverse=True
    )

    supports = [x["strike"] for x in support_candidates[:2]]
    resistances = [x["strike"] for x in resistance_candidates[:2]]

    score = 0
    reasons = []

    if put_ch > call_ch:
        score += 10
        reasons.append("put-side OI is stronger than call-side OI")
    elif call_ch > put_ch:
        score -= 10
        reasons.append("call-side OI is stronger than put-side OI")

    if put_ch > 0:
        score += 5
        reasons.append("put writing is providing support")
    if call_ch > 0:
        score -= 8
        reasons.append("call writing is creating resistance")
    if put_ch < 0:
        score -= 3
        reasons.append("put OI is unwinding")
    if call_ch < 0:
        score += 5
        reasons.append("call OI is unwinding")

    if pcr >= 1.10:
        score += 8
        reasons.append("PCR is bullish")
    elif pcr >= 0.95:
        score += 3
        reasons.append("PCR is mildly bullish")
    elif pcr <= 0.80:
        score -= 8
        reasons.append("PCR is bearish")
    elif pcr < 0.95:
        score -= 3
        reasons.append("PCR is mildly bearish")

    return {
        "call_oi": call_oi,
        "put_oi": put_oi,
        "call_change": call_ch,
        "put_change": put_ch,
        "call_position": call_position,
        "put_position": put_position,
        "pcr": round(pcr, 2),
        "iv": round(iv, 2),
        "support": supports[0] if supports else 0.0,
        "support2": supports[1] if len(supports) > 1 else 0.0,
        "resistance": resistances[0] if resistances else 0.0,
        "resistance2": resistances[1] if len(resistances) > 1 else 0.0,
        "score": clamp(score, -25, 25),
        "reasons": reasons,
    }


def technical_summary(candles, spot):
    if not candles:
        return {
            "score": 0, "condition": "SIDEWAYS",
            "vwap": 0.0, "ema9": 0.0, "ema21": 0.0,
            "rsi": 50.0, "atr": 0.0, "volume": 0.0,
            "vwap_status": "WAITING FOR CANDLE DATA",
            "structure": "WAITING FOR CANDLE DATA",
            "volume_state": "WAITING FOR CANDLE DATA",
            "reasons": []
        }

    closes = [f(x["close"]) for x in candles]
    e9 = ema(closes[-100:], 9)
    e21 = ema(closes[-150:], 21)
    rv = rsi(closes[-100:], 14)
    av = atr(candles[-100:], 14)
    vw = vwap(candles[-500:])
    vs = volume_state(candles)
    st = structure(candles)

    score = 0
    reasons = []

    if spot > vw > 0:
        score += 12
        reasons.append("price is above VWAP")
    elif spot < vw and vw > 0:
        score -= 12
        reasons.append("price is below VWAP")

    if e9 > e21:
        score += 10
        reasons.append("EMA 9 is above EMA 21")
    elif e9 < e21:
        score -= 10
        reasons.append("EMA 9 is below EMA 21")

    if rv >= 60:
        score += 8
        reasons.append("RSI has bullish momentum")
    elif rv <= 40:
        score -= 8
        reasons.append("RSI has bearish momentum")

    if st == "HH-HL":
        score += 10
        reasons.append("price structure is HH-HL")
    elif st == "LH-LL":
        score -= 10
        reasons.append("price structure is LH-LL")

    if vs == "HIGH":
        score += 3 if score >= 0 else -3
        reasons.append("volume is elevated")

    condition = (
        "BULLISH" if score >= 15
        else "BEARISH" if score <= -15
        else "SIDEWAYS"
    )

    return {
        "score": clamp(score, -45, 45),
        "condition": condition,
        "vwap": round(vw, 2),
        "ema9": round(e9, 2),
        "ema21": round(e21, 2),
        "rsi": round(rv, 2),
        "atr": round(av, 2),
        "volume": round(f(candles[-1].get("volume")), 2),
        "vwap_status": (
            "ABOVE VWAP" if spot > vw and vw > 0
            else "BELOW VWAP" if vw > 0
            else "WAITING"
        ),
        "structure": st,
        "volume_state": vs,
        "reasons": reasons,
    }


def decision(spot, tech, oi):
    raw = tech["score"] + oi["score"]
    score = int(clamp(50 + raw, 0, 100))

    conflict = (
        tech["condition"] == "SIDEWAYS"
        or (tech["score"] > 12 and oi["score"] < -10)
        or (tech["score"] < -12 and oi["score"] > 10)
    )

    if conflict:
        signal = "WAIT"
    elif score >= 72:
        signal = "BUY CALL"
    elif score <= 28:
        signal = "BUY PUT"
    else:
        signal = "WAIT"

    confidence = int(clamp(abs(score - 50) * 2, 0, 100))

    if signal == "BUY CALL":
        entry = spot
        sl = spot - max(tech["atr"] * 1.2, spot * 0.002)
        t1 = spot + max(tech["atr"] * 1.8, spot * 0.003)
        t2 = spot + max(tech["atr"] * 3.0, spot * 0.005)
    elif signal == "BUY PUT":
        entry = spot
        sl = spot + max(tech["atr"] * 1.2, spot * 0.002)
        t1 = spot - max(tech["atr"] * 1.8, spot * 0.003)
        t2 = spot - max(tech["atr"] * 3.0, spot * 0.005)
    else:
        entry = sl = t1 = t2 = 0.0

    return {
        "score": score,
        "confidence": confidence,
        "signal": signal,
        "entry": round(entry, 2) if entry else 0,
        "stoploss": round(sl, 2) if sl else 0,
        "target1": round(t1, 2) if t1 else 0,
        "target2": round(t2, 2) if t2 else 0,
    }


def story(tech, oi, dec):
    reasons = tech["reasons"][:2] + oi["reasons"][:2]
    reason_text = "; ".join(reasons)

    if dec["signal"] == "BUY CALL":
        return "Bullish momentum is strengthening. " + reason_text + "."
    if dec["signal"] == "BUY PUT":
        return "Bearish momentum is strengthening. " + reason_text + "."
    if tech["condition"] == "SIDEWAYS":
        return "Market is sideways. Technical confirmation is insufficient, so the option-buyer engine is waiting."
    if dec["score"] > 50:
        return "Bullish bias is present but confirmation is incomplete. " + reason_text + "."
    return "Bearish bias is present but confirmation is incomplete. " + reason_text + "."


def market(symbol):
    symbol = symbol.upper().replace(" ", "")
    if symbol in ("BANK", "BANKNIFTY", "NIFTYBANK"):
        symbol = "BANKNIFTY"
    else:
        symbol = "NIFTY"

    now = time.time()
    cached = CACHE.get(symbol)
    if cached and now - cached["time"] < CACHE_SECONDS:
        return cached["data"]

    chain = fetch_option_chain(symbol)
    try:
        candles = fetch_candles(symbol)
    except Exception:
        candles = []

    spot = chain["spot"]
    tech = technical_summary(candles, spot)
    oi = oi_summary(chain["rows"], spot)
    dec = decision(spot, tech, oi)

    data = {
        "symbol": symbol,
        "price": round(spot, 2),
        "change": 0,
        "market_condition": (
            "BULLISH" if dec["score"] >= 60
            else "BEARISH" if dec["score"] <= 40
            else "SIDEWAYS"
        ),
        "score": dec["score"],
        "confidence": dec["confidence"],
        "signal": dec["signal"],
        "entry": dec["entry"],
        "stoploss": dec["stoploss"],
        "target1": dec["target1"],
        "target2": dec["target2"],
        "statement": story(tech, oi, dec),
        "vwap": tech["vwap"],
        "ema9": tech["ema9"],
        "ema21": tech["ema21"],
        "rsi": tech["rsi"],
        "atr": tech["atr"],
        "volume": tech["volume"],
        "vwapStatus": tech["vwap_status"],
        "structure": tech["structure"],
        "volumeState": tech["volume_state"],
        "pcr": oi["pcr"],
        "callOI": oi["call_oi"],
        "putOI": oi["put_oi"],
        "iv": oi["iv"],
        "oi_change": {
            "call": oi["call_change"],
            "put": oi["put_change"],
        },
        "oi_positioning": {
            "call": oi["call_position"],
            "put": oi["put_position"],
        },
        "support": oi["support"],
        "support2": oi["support2"],
        "resistance": oi["resistance"],
        "resistance2": oi["resistance2"],
        "expiry": chain["expiry"],
        "max_pain": chain["max_pain"],
        "data_status": (
            "LIVE NSE OPTION CHAIN + LIVE 5-MIN CANDLES"
            if candles else
            "LIVE NSE OPTION CHAIN; CANDLE DATA TEMPORARILY UNAVAILABLE"
        ),
        "engine": {
            "technical_score": tech["score"],
            "oi_score": oi["score"],
            "pcr_score": 0,
            "reasons": tech["reasons"] + oi["reasons"],
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    CACHE[symbol] = {"time": now, "data": data}
    return data


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "option-intelligence-free"}


@app.get("/api/market")
def api_market(symbol: str = "NIFTY"):
    try:
        return market(symbol)
    except Exception as exc:
        return {
            "symbol": symbol.upper(),
            "price": 0,
            "market_condition": "SIDEWAYS",
            "score": 50,
            "confidence": 0,
            "signal": "WAIT",
            "entry": 0,
            "stoploss": 0,
            "target1": 0,
            "target2": 0,
            "statement": "Live data could not be refreshed. The engine is waiting rather than generating a false entry.",
            "error": str(exc),
            "data_status": "DATA REFRESH ERROR",
        }


@app.get("/")
def home():
    index = FRONTEND_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    return {
        "service": "Option Intelligence Free",
        "message": "Backend is running. Open /api/health or /api/market?symbol=NIFTY",
    }
