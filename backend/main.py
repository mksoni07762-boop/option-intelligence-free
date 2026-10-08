        technical["ema21"],
    )

    (
        entry,
        stoploss,
        target1,
        target2,
    ) = trade_levels(
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
    )

    if (
        technical["vwap"] > 0
        and spot > technical["vwap"]
    ):
        vwap_status = "ABOVE VWAP"

    elif (
        technical["vwap"] > 0
        and spot < technical["vwap"]
    ):
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

        "expiry": live.get(
            "expiry",
            "--",
        ),

        "max_pain": live.get(
            "max_pain",
            0,
        ),

        "oi_change": {
            "call": oi["call_change"],
            "put": oi["put_change"],
        },

        "oi_positioning": {
            "call": oi["call_position"],
            "put": oi["put_position"],
        },

        "data_status": (
            "LIVE NSE OPTION CHAIN + "
            "LIVE 5-MIN CANDLES"
        ),

        "engine": {
            "technical_score": technical["score"],
            "oi_score": oi["score"],
            "pcr_score": 0,
            "reasons": (
                technical["reasons"]
                + oi["reasons"]
            ),
        },
    }


if FRONTEND_DIR.exists():
    app.mount(
        "/static",
        StaticFiles(
            directory=str(FRONTEND_DIR)
        ),
        name="static",
    )
