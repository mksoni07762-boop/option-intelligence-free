
from pathlib import Path
from datetime import datetime, timedelta, timezone
import math

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from curl_cffi import requests


BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR.parent / "frontend"

NSE = "https://www.nseindia.com"
CHART = "https://charting.nseindia.com"

app = FastAPI(title="Option Intelligence Free")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        x = float(value)
        return default if math.isnan(x) or math.isinf(x) else x
    except (TypeError, ValueError):
        return default


def clamp(value, low, high):
    return max(low, min(high, value))


def session():
    s = requests.Session(impersonate="chrome120")
    s.headers.update({
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": NSE + "/",
    })
    return s


def warm_nse(s):
    try:
        s.get(NSE + "/", timeout=15)
    except Exception:
        pass


def warm_chart(s):
    s.headers.update({
        "Origin": CHART,
        "Referer": CHART + "/",
    })
    try:
        s.get(CHART + "/", timeout=10)
    except Exception:
        pass


def ema_series(values, period):
    if not values:
        return []
    k = 2.0 / (period + 1.0)
    out = [values[0]]
    for value in values[1:]:
        out.append(value * k + out[-1] * (1.0 - k))
    return out


def rsi_value(values, period=14):
    if len(values) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def atr_value(candles, period=14):
    if len(candles) < period + 1:
        return 0.0
    trs = []
    previous_close = safe_float(candles[0].get("close"))
    for candle in candles[1:]:
        high = safe_float(candle.get("high"))
        low = safe_float(candle.get("low"))
        close = safe_float(candle.get("close"))
        trs.append(max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close),
        ))
        previous_close = close
    if len(trs) < period:
        return 0.0
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = ((atr * (period - 1)) + tr) / period
    return atr


def vwap_value(candles):
    if not candles:
        return 0.0

    # Calculate VWAP for the latest trading day represented by the candles.
    dated = []
    for candle in candles:
        try:
            ts = float(candle.get("time"))
            if ts > 10_000_000_000:
                ts /= 1000.0
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            # NSE session date in IST.
            ist_date = (dt + timedelta(hours=5, minutes=30)).date()
        except Exception:
            continue
        dated.append((ist_date, candle))

    if not dated:
        return 0.0

    last_date = dated[-1][0]
    pv = 0.0
    vol = 0.0

    for day, candle in dated:
        if day != last_date:
            continue
        high = safe_float(candle.get("high"))
        low = safe_float(candle.get("low"))
        close = safe_float(candle.get("close"))
        volume = safe_float(candle.get("volume"))
        if volume > 0:
            pv += ((high + low + close) / 3.0) * volume
            vol += volume

    return pv / vol if vol > 0 else 0.0


def market_structure(candles):
    if len(candles) < 10:
        return "WAITING FOR CANDLE DATA"

    highs = [safe_float(c.get("high")) for c in candles]
    lows = [safe_float(c.get("low")) for c in candles]

    rh = max(highs[-5:])
    ph = max(highs[-10:-5])
    rl = min(lows[-5:])
    pl = min(lows[-10:-5])

    if rh > ph and rl > pl:
        return "HH-HL BULLISH"
    if rh < ph and rl < pl:
        return "LH-LL BEARISH"
    return "MIXED / RANGE"


def volume_state(candles):
    if len(candles) < 21:
        return "WAITING"
    current = safe_float(candles[-1].get("volume"))
    average = sum(
        safe_float(c.get("volume")) for c in candles[-21:-1]
    ) / 20.0
    if average <= 0:
        return "NEUTRAL"
    ratio = current / average
    if ratio >= 1.5:
        return "HIGH"
    if ratio >= 1.1:
        return "ABOVE AVG"
    if ratio <= 0.7:
        return "LOW"
    return "NORMAL"


def fetch_candles(symbol):
    """
    Direct 5-minute NSE charting feed.

    Known NSE index scripcodes:
      NIFTY 50  -> 26000
      NIFTY BANK -> 26004

    This follows the current public OpenChart/NSE charting API format:
    Unix timestamps, symbolType='Index', chartType='I', timeInterval=5.
    """
    s = session()
    warm_nse(s)
    warm_chart(s)

    if symbol == "NIFTY":
        token, chart_symbol = "26000", "NIFTY 50"
    else:
        token, chart_symbol = "26004", "NIFTY BANK"

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=7)

    payload = {
        "token": token,
        "fromDate": int(start.timestamp()),
        "toDate": int(end.timestamp()),
        "symbol": chart_symbol,
        "symbolType": "Index",
        "chartType": "I",
        "timeInterval": 5,
    }

    url = CHART + "/v1/charts/symbolHistoricalData"

    try:
        chart_headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/json",
            "Origin": CHART,
            "Referer": CHART + "/",
        }
        response = s.post(
            url,
            json=payload,
            headers=chart_headers,
            timeout=20,
        )
        response.raise_for_status()
        result = response.json()
    except Exception:
        return []

    if not result.get("status") or not result.get("data"):
        return []

    candles = []
    for item in result.get("data", []):
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
        ):
            candles.append(candle)

    candles.sort(key=lambda x: safe_float(x["time"]))
    return candles[-500:]


def fetch_futures_candles(symbol):
    """
    Fetch 5-minute NSE index-futures candles for volume/VWAP.

    Index candles remain the source for EMA, RSI, ATR and price structure.
    Futures are used only where traded volume is required because NSE index
    candles can expose zero/empty volume.
    """
    s = session()
    warm_nse(s)
    warm_chart(s)

    search_symbol = "NIFTY" if symbol == "NIFTY" else "BANKNIFTY"

    try:
        search_headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/json",
            "Origin": CHART,
            "Referer": CHART + "/",
        }
        response = s.post(
            CHART + "/v1/exchanges/symbolsDynamic",
            json={
                "symbol": search_symbol,
                "segment": "FO",
            },
            headers=search_headers,
            timeout=15,
        )
        response.raise_for_status()
        result = response.json()
    except Exception:
        return []

    items = result.get("data", []) if isinstance(result, dict) else []
    if not isinstance(items, list):
        return []

    candidates = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type", "")).strip().lower()
        item_symbol = str(item.get("symbol", "")).strip().upper()
        token = str(item.get("scripcode", "")).strip()
        if not token or not item_symbol:
            continue
        if item_type == "futures" and item_symbol.endswith("FUT"):
            candidates.append(item)

    if not candidates:
        return []

    # Prefer the nearest-expiry contract if the search response exposes
    # expiry metadata; otherwise retain the API's first futures contract.
    today = datetime.now(timezone.utc).date()

    def expiry_date(item):
        for key in ("expiry", "expiryDate", "expiry_date", "contractExpiry"):
            value = item.get(key)
            if not value:
                continue
            text = str(value).strip()
            for fmt in (
                "%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d",
                "%d-%b-%y", "%d-%B-%y",
            ):
                try:
                    return datetime.strptime(text, fmt).date()
                except ValueError:
                    pass
        return None

    dated = [(expiry_date(item), item) for item in candidates]
    future_dated = [pair for pair in dated if pair[0] is not None and pair[0] >= today]
    if future_dated:
        future_dated.sort(key=lambda pair: pair[0])
        info = future_dated[0][1]
    else:
        info = candidates[0]

    token = str(info.get("scripcode", "")).strip()
    chart_symbol = str(info.get("symbol", "")).strip()
    symbol_type = str(info.get("type", "Futures")).strip() or "Futures"

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
        chart_headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/json",
            "Origin": CHART,
            "Referer": CHART + "/",
        }
        response = s.post(
            CHART + "/v1/charts/symbolHistoricalData",
            json=payload,
            headers=chart_headers,
            timeout=20,
        )
        response.raise_for_status()
        result = response.json()
    except Exception:
        return []

    if not isinstance(result, dict) or not result.get("status") or not result.get("data"):
        return []

    candles = []
    for item in result.get("data", []):
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
        ):
            candles.append(candle)

    candles.sort(key=lambda x: safe_float(x["time"]))
    return candles[-500:]


def fetch_option_chain(symbol):
    s = session()
    warm_nse(s)

    try:
        contract_url = NSE + "/api/option-chain-contract-info"
        contract_response = s.get(
            contract_url,
            params={"symbol": symbol},
            timeout=15,
        )
        contract_response.raise_for_status()
        contract_obj = contract_response.json()

        expiries = []

        if isinstance(contract_obj, dict):
            for key in ("expiryDates", "expiries", "data"):
                value = contract_obj.get(key)
                if isinstance(value, list):
                    for item in value:
                        if isinstance(item, str):
                            expiries.append(item)
                        elif isinstance(item, dict):
                            for field in ("expiryDate", "expiry", "date"):
                                if item.get(field):
                                    expiries.append(str(item[field]))
                                    break

        # Some NSE responses expose expiries nested in records.
        records = contract_obj.get("records", {}) if isinstance(contract_obj, dict) else {}
        if isinstance(records, dict):
            value = records.get("expiryDates")
            if isinstance(value, list):
                expiries.extend(str(x) for x in value if x)

        expiries = list(dict.fromkeys(expiries))

        def parse_expiry(value):
            for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d"):
                try:
                    return datetime.strptime(value, fmt).date()
                except ValueError:
                    pass
            return None

        today = datetime.now().date()
        parsed = [
            (parse_expiry(x), x)
            for x in expiries
            if parse_expiry(x) is not None
        ]
        future = [(d, x) for d, x in parsed if d >= today]
        future.sort(key=lambda z: z[0])

        expiry = future[0][1] if future else None

        if not expiry:
            # Fall back to the public v3 response's first expiry.
            expiry = None

        url = NSE + "/api/option-chain-v3"
        params = {
            "type": "Indices",
            "symbol": symbol,
        }
        if expiry:
            params["expiry"] = expiry

        response = s.get(url, params=params, timeout=20)
        response.raise_for_status()
        obj = response.json()

        rows = []
        if isinstance(obj, dict):
            filtered = obj.get("filtered")
            records = obj.get("records")

            if isinstance(filtered, dict) and isinstance(filtered.get("data"), list):
                rows = filtered["data"]

            if not rows and isinstance(records, dict) and isinstance(records.get("data"), list):
                rows = records["data"]

        spot = 0.0
        if isinstance(obj, dict):
            rec = obj.get("records")
            if isinstance(rec, dict):
                spot = safe_float(
                    rec.get("underlyingValue")
                    or rec.get("indexValue")
                    or rec.get("underlying")
                )
                if not expiry:
                    exp_list = rec.get("expiryDates")
                    if isinstance(exp_list, list) and exp_list:
                        expiry = str(exp_list[0])

        if spot <= 0:
            for row in rows:
                spot = safe_float(row.get("underlyingValue"))
                if spot > 0:
                    break

        return {
            "rows": rows,
            "spot": spot,
            "expiry": expiry or "--",
        }

    except Exception:
        return {
            "rows": [],
            "spot": 0.0,
            "expiry": "--",
        }


def max_pain_value(rows):
    if not rows:
        return 0.0

    strikes = []
    for row in rows:
        strike = safe_float(row.get("strikePrice"))
        if strike > 0:
            strikes.append(strike)

    if not strikes:
        return 0.0

    best_strike = strikes[0]
    best_pain = float("inf")

    for settlement in strikes:
        pain = 0.0
        for row in rows:
            strike = safe_float(row.get("strikePrice"))
            ce_oi = safe_float((row.get("CE") or {}).get("openInterest"))
            pe_oi = safe_float((row.get("PE") or {}).get("openInterest"))

            if strike > settlement:
                pain += (strike - settlement) * ce_oi
            elif strike < settlement:
                pain += (settlement - strike) * pe_oi

        if pain < best_pain:
            best_pain = pain
            best_strike = settlement

    return best_strike


def calculate_pcr(put_oi, call_oi):
    return put_oi / call_oi if call_oi > 0 else 0.0


def nearest_oi_levels(rows, spot):
    if not rows or spot <= 0:
        return "--", "--", "--", "--"

    above, below = [], []

    for row in rows:
        strike = safe_float(row.get("strikePrice"))
        if strike <= 0:
            continue
        if strike >= spot:
            above.append(row)
        if strike <= spot:
            below.append(row)

    resistance_rows = sorted(
        above,
        key=lambda r: safe_float((r.get("CE") or {}).get("openInterest")),
        reverse=True,
    )
    support_rows = sorted(
        below,
        key=lambda r: safe_float((r.get("PE") or {}).get("openInterest")),
        reverse=True,
    )

    def level(items, index):
        if len(items) <= index:
            return "--"
        value = safe_float(items[index].get("strikePrice"))
        return str(int(value)) if value > 0 else "--"

    return (
        level(resistance_rows, 0),
        level(resistance_rows, 1),
        level(support_rows, 0),
        level(support_rows, 1),
    )


def technical_analysis(candles, spot, futures_candles=None):
    closes = [
        safe_float(c.get("close"))
        for c in candles
        if safe_float(c.get("close")) > 0
    ]

    if len(closes) < 21:
        return {
            "vwap": 0.0,
            "ema9": 0.0,
            "ema21": 0.0,
            "rsi": 50.0,
            "atr": 0.0,
            "volume": 0.0,
            "volume_state": "WAITING",
            "structure": "WAITING FOR CANDLE DATA",
            "score": 0,
            "reasons": ["5-minute candle data unavailable"],
        }

    ema9 = ema_series(closes, 9)[-1]
    ema21 = ema_series(closes, 21)[-1]

    # Use index candles for price indicators. Use futures candles for
    # VWAP/volume when available because index volume can be zero.
    volume_source = futures_candles if futures_candles else candles
    vwap = vwap_value(volume_source)
    rsi = rsi_value(closes, 14)
    atr = atr_value(candles, 14)
    volume = safe_float(volume_source[-1].get("volume")) if volume_source else 0.0
    vstate = volume_state(volume_source)
    structure = market_structure(candles)

    price = spot if spot > 0 else closes[-1]
    score = 0
    reasons = []

    if vwap > 0:
        if price > vwap:
            score += 8
            reasons.append("price is above VWAP")
        elif price < vwap:
            score -= 8
            reasons.append("price is below VWAP")

    if ema9 > ema21:
        score += 7
        reasons.append("EMA9 is above EMA21")
    elif ema9 < ema21:
        score -= 7
        reasons.append("EMA9 is below EMA21")

    if 55 <= rsi <= 70:
        score += 6
        reasons.append("RSI supports bullish momentum")
    elif 30 <= rsi < 45:
        score -= 6
        reasons.append("RSI supports bearish momentum")
    elif rsi > 75:
        score -= 2
        reasons.append("RSI is overextended")
    elif rsi < 25:
        score += 2
        reasons.append("RSI is deeply oversold")

    if structure == "HH-HL BULLISH":
        score += 7
        reasons.append("HH-HL structure is bullish")
    elif structure == "LH-LL BEARISH":
        score -= 7
        reasons.append("LH-LL structure is bearish")

    if vstate == "HIGH" and len(closes) > 1:
        if closes[-1] > closes[-2]:
            score += 5
            reasons.append("high volume confirms upward move")
        elif closes[-1] < closes[-2]:
            score -= 5
            reasons.append("high volume confirms downward move")

    return {
        "vwap": round(vwap, 2),
        "ema9": round(ema9, 2),
        "ema21": round(ema21, 2),
        "rsi": round(rsi, 2),
        "atr": round(atr, 2),
        "volume": volume,
        "volume_state": vstate,
        "structure": structure,
        "score": int(clamp(score, -33, 33)),
        "reasons": reasons,
    }


def oi_analysis(rows, spot):
    total_call_oi = 0.0
    total_put_oi = 0.0
    total_call_change = 0.0
    total_put_change = 0.0
    iv_values = []

    for row in rows or []:
        ce = row.get("CE") or {}
        pe = row.get("PE") or {}

        total_call_oi += safe_float(ce.get("openInterest"))
        total_put_oi += safe_float(pe.get("openInterest"))
        total_call_change += safe_float(ce.get("changeinOpenInterest"))
        total_put_change += safe_float(pe.get("changeinOpenInterest"))

        ce_iv = safe_float(ce.get("impliedVolatility"))
        pe_iv = safe_float(pe.get("impliedVolatility"))
        if ce_iv > 0:
            iv_values.append(ce_iv)
        if pe_iv > 0:
            iv_values.append(pe_iv)

    pcr = calculate_pcr(total_put_oi, total_call_oi)
    average_iv = sum(iv_values) / len(iv_values) if iv_values else 0.0

    score = 0
    reasons = []

    if total_call_change < 0:
        score += 10
        reasons.append("call-side OI is unwinding, reducing resistance")
    elif total_call_change > 0:
        score -= 8
        reasons.append("call-side OI is building, adding resistance")

    if total_put_change > 0:
        score += 10
        reasons.append("put-side OI is building, indicating support")
    elif total_put_change < 0:
        score -= 10
        reasons.append("put-side OI is unwinding, weakening support")

    if pcr >= 1.20:
        score += 8
        reasons.append("PCR is strongly supportive")
    elif pcr >= 1.05:
        score += 4
        reasons.append("PCR is mildly bullish")
    elif pcr >= 0.90:
        reasons.append("PCR is neutral")
    elif pcr >= 0.75:
        score -= 4
        reasons.append("PCR is mildly bearish")
    elif pcr > 0:
        score -= 8
        reasons.append("PCR is bearish")

    resistance, resistance2, support, support2 = nearest_oi_levels(
        rows, spot
    )

    return {
        "pcr": round(pcr, 2),
        "call_oi": int(total_call_oi),
        "put_oi": int(total_put_oi),
        "call_change": int(total_call_change),
        "put_change": int(total_put_change),
        "iv": round(average_iv, 2),
        "call_position": (
            "CALL WRITING" if total_call_change > 0
            else "CALL UNWINDING" if total_call_change < 0
            else "NEUTRAL"
        ),
        "put_position": (
            "PUT WRITING" if total_put_change > 0
            else "PUT UNWINDING" if total_put_change < 0
            else "NEUTRAL"
        ),
        "score": int(clamp(score, -28, 28)),
        "reasons": reasons,
        "resistance": resistance,
        "resistance2": resistance2,
        "support": support,
        "support2": support2,
    }


def decide(raw_score, spot, vwap, ema9, ema21, technical_ready):
    conflict = False

    if technical_ready and vwap > 0 and abs(raw_score) >= 20:
        price_direction = 1 if spot > vwap else -1
        score_direction = 1 if raw_score > 0 else -1
        if price_direction != score_direction:
            conflict = True

    near_vwap = (
        technical_ready
        and vwap > 0
        and spot > 0
        and abs(spot - vwap) / spot < 0.0015
    )

    ema_flat = (
        technical_ready
        and ema9 > 0
        and ema21 > 0
        and spot > 0
        and abs(ema9 - ema21) / spot < 0.001
    )

    if not technical_ready or conflict or abs(raw_score) < 15 or (near_vwap and ema_flat):
        regime = "SIDEWAYS"
    elif raw_score >= 20:
        regime = "BULLISH"
    elif raw_score <= -20:
        regime = "BEARISH"
    else:
        regime = "TRANSITION"

    if regime == "SIDEWAYS":
        signal = "WAIT"
    elif raw_score >= 35:
        signal = "BUY CALL"
    elif raw_score <= -35:
        signal = "BUY PUT"
    else:
        signal = "WAIT"

    confidence = int(clamp(50 + abs(raw_score) * 1.25, 0, 98))

    if conflict or not technical_ready:
        confidence = min(confidence, 55)
        signal = "WAIT"

    return regime, signal, confidence, conflict


def trade_levels(signal, spot, atr, support, resistance):
    if spot <= 0 or signal == "WAIT":
        return 0, 0, 0, 0

    risk = atr if atr > 0 else spot * 0.0025

    if signal == "BUY CALL":
        entry = spot
        stoploss = spot - max(risk, spot * 0.0015)
        target1 = spot + risk * 1.5
        target2 = spot + risk * 2.5
        resistance_value = safe_float(resistance)
        if resistance_value > spot:
            target1 = min(target1, resistance_value)
    else:
        entry = spot
        stoploss = spot + max(risk, spot * 0.0015)
        target1 = spot - risk * 1.5
        target2 = spot - risk * 2.5
        support_value = safe_float(support)
        if 0 < support_value < spot:
            target1 = max(target1, support_value)

    return (
        round(entry, 2),
        round(stoploss, 2),
        round(target1, 2),
        round(target2, 2),
    )


def build_statement(regime, signal, technical, oi, conflict, technical_ready):
    if not technical_ready:
        oi_text = "; ".join(oi["reasons"][:3]) or "option-chain data is available"
        return (
            "Live option-chain data is available, but 5-minute candle "
            f"data is temporarily unavailable ({oi_text}). "
            "The option-buyer engine is staying WAIT until technical "
            "confirmation is restored."
        )

    if conflict:
        return (
            "Price and supporting indicators are conflicting, so the "
            "engine is staying WAIT rather than chasing a false breakout."
        )

    reasons = (technical["reasons"] + oi["reasons"])[:6]
    reason_text = "; ".join(reasons) if reasons else "waiting for stronger confirmation"

    prefix = {
        "BULLISH": "Bullish momentum is strengthening",
        "BEARISH": "Bearish momentum is strengthening",
        "SIDEWAYS": "Market is sideways",
        "TRANSITION": "Market is transitioning",
    }.get(regime, "Market conditions are changing")

    if signal == "BUY CALL":
        return f"{prefix} because {reason_text}. CALL buying conditions are confirmed."
    if signal == "BUY PUT":
        return f"{prefix} because {reason_text}. PUT buying conditions are confirmed."
    return f"{prefix} because {reason_text}. Waiting for stronger confirmation."


def get_market_data(symbol):
    nse_symbol = "NIFTY" if symbol == "NIFTY" else "BANKNIFTY"

    chain = fetch_option_chain(nse_symbol)
    candles = fetch_candles(symbol)
    futures_candles = fetch_futures_candles(symbol)

    if not chain["rows"] and chain["spot"] <= 0:
        return {
            "ok": False,
            "candles": candles,
            "futures_candles": futures_candles,
            "expiry": chain["expiry"],
        }

    return {
        "ok": True,
        "rows": chain["rows"],
        "spot": chain["spot"],
        "expiry": chain["expiry"],
        "candles": candles,
        "futures_candles": futures_candles,
        "max_pain": max_pain_value(chain["rows"]),
    }


@app.get("/")
def home():
    index_file = FRONTEND_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {"ok": True, "service": "option-intelligence-free"}


@app.get("/api/health")
def health():
    return {"ok": True, "time": datetime.now(timezone.utc).isoformat()}


@app.get("/api/market")
def market(symbol: str = "NIFTY"):
    symbol = symbol.upper()
    if symbol not in ("NIFTY", "BANKNIFTY"):
        symbol = "NIFTY"

    live = get_market_data(symbol)

    if not live.get("ok"):
        return {
            "ok": False,
            "symbol": symbol,
            "price": 0,
            "change": 0,
            "market_condition": "WAIT",
            "score": 0,
            "confidence": 0,
            "signal": "WAIT",
            "statement": "Live market data is currently unavailable. No trading signal is generated.",
            "vwap": 0,
            "ema9": 0,
            "ema21": 0,
            "rsi": 50,
            "atr": 0,
            "volume": 0,
            "vwapStatus": "WAITING FOR CANDLE DATA",
            "structure": "WAITING FOR CANDLE DATA",
            "pcr": 0,
            "callOI": 0,
            "putOI": 0,
            "iv": 0,
            "resistance": "--",
            "resistance2": "--",
            "support": "--",
            "support2": "--",
            "entry": 0,
            "stoploss": 0,
            "target1": 0,
            "target2": 0,
            "expiry": live.get("expiry", "--"),
            "max_pain": 0,
            "oi_change": {"call": 0, "put": 0},
            "oi_positioning": {"call": "N/A", "put": "N/A"},
            "data_status": "LIVE DATA UNAVAILABLE",
            "engine": {"technical_score": 0, "oi_score": 0, "pcr_score": 0, "reasons": []},
        }

    rows = live.get("rows", [])
    candles = live.get("candles", [])
    futures_candles = live.get("futures_candles", [])
    spot = safe_float(live.get("spot"))

    technical = technical_analysis(candles, spot, futures_candles)
    technical_ready = (
        len(candles) >= 21
        and technical["ema9"] > 0
        and technical["ema21"] > 0
        and technical["atr"] > 0
    )

    oi = oi_analysis(rows, spot)
    raw_score = technical["score"] + oi["score"]

    score = int(clamp(50 + raw_score * 1.5, 0, 100))

    market_condition, signal, confidence, conflict = decide(
        raw_score,
        spot,
        technical["vwap"],
        technical["ema9"],
        technical["ema21"],
        technical_ready,
    )

    entry, stoploss, target1, target2 = trade_levels(
        signal,
        spot,
        technical["atr"],
        oi["support"],
        oi["resistance"],
    )

    statement = build_statement(
        market_condition,
        signal,
        technical,
        oi,
        conflict,
        technical_ready,
    )

    if technical["vwap"] > 0 and spot > technical["vwap"]:
        vwap_status = "ABOVE VWAP"
    elif technical["vwap"] > 0 and spot < technical["vwap"]:
        vwap_status = "BELOW VWAP"
    else:
        vwap_status = "WAITING FOR CANDLE DATA"

    return {
        "ok": True,
        "symbol": symbol,
        "price": round(spot, 2),
        "change": 0,
        "market_condition": market_condition,
        "score": score,
        "directional_score": raw_score,
        "confidence": confidence,
        "signal": signal,
        "statement": statement,
        "vwap": technical["vwap"],
        "ema9": technical["ema9"],
        "ema21": technical["ema21"],
        "rsi": technical["rsi"],
        "atr": technical["atr"],
        "volume": technical["volume"],
        "volumeState": technical["volume_state"],
        "pcr": oi["pcr"],
        "callOI": oi["call_oi"],
        "putOI": oi["put_oi"],
        "iv": oi["iv"],
        "vwapStatus": vwap_status,
        "structure": technical["structure"],
        "resistance": oi["resistance"],
        "resistance2": oi["resistance2"],
        "support": oi["support"],
        "support2": oi["support2"],
        "entry": entry,
        "stoploss": stoploss,
        "target1": target1,
        "target2": target2,
        "expiry": live.get("expiry", "--"),
        "max_pain": live.get("max_pain", 0),
        "oi_change": {
            "call": oi["call_change"],
            "put": oi["put_change"],
        },
        "oi_positioning": {
            "call": oi["call_position"],
            "put": oi["put_position"],
        },
        "data_status": (
            "LIVE NSE OPTION CHAIN + LIVE 5-MIN CANDLES + FUTURES VOLUME"
            if technical_ready and futures_candles
            else "LIVE NSE OPTION CHAIN + LIVE 5-MIN CANDLES; FUTURES VOLUME UNAVAILABLE"
            if technical_ready
            else "LIVE NSE OPTION CHAIN; CANDLE DATA TEMPORARILY UNAVAILABLE"
        ),
        "engine": {
            "technical_score": technical["score"],
            "oi_score": oi["score"],
            "pcr_score": 0,
            "reasons": technical["reasons"] + oi["reasons"],
        },
    }


if FRONTEND_DIR.exists():
    app.mount(
        "/static",
        StaticFiles(directory=str(FRONTEND_DIR)),
        name="static",
    )
