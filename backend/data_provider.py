from typing import Dict, Any
from datetime import datetime, timedelta
import time

from curl_cffi import requests


class FreeOptionDataProvider:
    """
    Free NSE option-chain connector.

    Flow:
        NSE session
          -> contract-info
          -> nearest expiry
          -> option-chain-v3

    No Groww API or paid market-data API is required.
    """

    BASE_URL = "https://www.nseindia.com"

    def __init__(self):
        self.session = None
        self.last_data = {}
        self.last_fetch = {}
        self.cache_seconds = 15

        self._create_session()

    def _create_session(self):

        self.session = requests.Session(
            impersonate="chrome"
        )

        self.session.headers.update({
            "accept": (
                "text/html,application/xhtml+xml,"
                "application/xml;q=0.9,image/avif,"
                "image/webp,*/*;q=0.8"
            ),
            "accept-language": "en-US,en;q=0.9",
            "cache-control": "no-cache",
            "pragma": "no-cache",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/154.0.0.0 Safari/537.36"
            ),
        })



        def _get_candles(self, symbol: str):

            index_map = {
                "NIFTY": {
                    "search": "NIFTY",
                    "symbol": "NIFTY 50"
                },
                "BANKNIFTY": {
                    "search": "NIFTY BANK",
                    "symbol": "NIFTY BANK"
                }
            }
    
            info = index_map.get(symbol)
    
            if not info:
                return []

        try:
            chart_session = requests.Session(
                impersonate="chrome"
            )

            chart_session.headers.update({
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Origin": "https://charting.nseindia.com",
                "Referer": "https://charting.nseindia.com/"
            })

            chart_session.get(
                "https://charting.nseindia.com",
                timeout=10
            )

            search_url = (
                "https://charting.nseindia.com/"
                "v1/exchanges/symbolsDynamic"
            )

            search_response = chart_session.get(
                search_url,
                params={
                    "symbol": info["search"],
                    "segment": "IDX"
                },
                timeout=15
            )

            search_response.raise_for_status()

            search_data = search_response.json()

            matches = search_data.get("data", [])

            token_info = None

            for item in matches:
                if str(
                    item.get("symbol", "")
                ).upper() == info["symbol"].upper():
                    token_info = item
                    break

            if token_info is None and matches:
                token_info = matches[0]

            if token_info is None:
                return []

            end_time = int(time.time())
            start_time = end_time - (3 * 24 * 60 * 60)

            historical_url = (
                "https://charting.nseindia.com/"
                "v1/charts/symbolHistoricalData"
            )

            response = chart_session.get(
                historical_url,
                params={
                    "token": str(
                        token_info.get("scripcode")
                    ),
                    "fromDate": start_time,
                    "toDate": end_time,
                    "symbol": token_info.get("symbol"),
                    "symbolType": token_info.get(
                        "type",
                        "Index"
                    ),
                    "chartType": "I",
                    "timeInterval": 5
                },
                timeout=20
            )

            response.raise_for_status()

            payload = response.json()

            raw = payload.get("data", [])

            candles = []

            for candle in raw:

                try:
                    candles.append({
                        "time": candle.get("time"),
                        "open": float(
                            candle.get("open", 0)
                        ),
                        "high": float(
                            candle.get("high", 0)
                        ),
                        "low": float(
                            candle.get("low", 0)
                        ),
                        "close": float(
                            candle.get("close", 0)
                        ),
                        "volume": float(
                            candle.get("volume", 0)
                        )
                    })

                except Exception:
                    continue

            return candles

        except Exception:
            return []
    def _warm_session(self):

        try:

            self.session.get(
                self.BASE_URL,
                timeout=15
            )

            self.session.get(
                self.BASE_URL + "/option-chain",
                timeout=15
            )

            # Additional lightweight NSE endpoint used
            # to establish a normal API session.
            self.session.get(
                self.BASE_URL + "/api/allIndices",
                timeout=15
            )

            return True

        except Exception:

            return False

    def _get_contract_info(self, symbol):

        url = (
            self.BASE_URL
            + "/api/option-chain-contract-info"
        )

        response = self.session.get(
            url,
            params={"symbol": symbol},
            headers={
                "accept": "application/json, text/plain, */*",
                "referer": (
                    self.BASE_URL
                    + "/option-chain"
                )
            },
            timeout=15
        )

        if response.status_code in (401, 403, 404):

            self._create_session()
            self._warm_session()

            response = self.session.get(
                url,
                params={"symbol": symbol},
                headers={
                    "accept": (
                        "application/json, "
                        "text/plain, */*"
                    ),
                    "referer": (
                        self.BASE_URL
                        + "/option-chain"
                    )
                },
                timeout=15
            )

        response.raise_for_status()

        return response.json()

    def _get_option_chain(self, symbol, expiry):

        url = (
            self.BASE_URL
            + "/api/option-chain-v3"
        )

        params = {
            "type": "Indices",
            "symbol": symbol,
            "expiry": expiry
        }

        response = self.session.get(
            url,
            params=params,
            headers={
                "accept": (
                    "application/json, "
                    "text/plain, */*"
                ),
                "referer": (
                    self.BASE_URL
                    + "/option-chain"
                )
            },
            timeout=20
        )

        if response.status_code in (401, 403, 404):

            self._create_session()
            self._warm_session()

            response = self.session.get(
                url,
                params=params,
                headers={
                    "accept": (
                        "application/json, "
                        "text/plain, */*"
                    ),
                    "referer": (
                        self.BASE_URL
                        + "/option-chain"
                    )
                },
                timeout=20
            )

        response.raise_for_status()

        return response.json()

    @staticmethod
    def _safe_float(value, default=0.0):

        try:

            if value is None:
                return default

            return float(value)

        except Exception:

            return default

    @staticmethod
    def _safe_int(value, default=0):

        try:

            if value is None:
                return default

            return int(float(value))

        except Exception:

            return default

    @staticmethod
    def _expiry_matches(value, expiry):

        if value is None:
            return False

        if isinstance(value, list):

            return any(
                str(x).strip().lower()
                == str(expiry).strip().lower()
                for x in value
            )

        return (
            str(value).strip().lower()
            == str(expiry).strip().lower()
        )

    def _calculate_max_pain(self, rows):

        if not rows:
            return 0.0

        strikes = sorted(
            {
                self._safe_float(
                    row.get("strike")
                )
                for row in rows
                if self._safe_float(
                    row.get("strike")
                ) > 0
            }
        )

        if not strikes:
            return 0.0

        best_strike = strikes[0]
        lowest_pain = None

        for settlement in strikes:

            total_pain = 0.0

            for row in rows:

                strike = self._safe_float(
                    row.get("strike")
                )

                ce_oi = self._safe_int(
                    row.get("ce", {}).get("oi")
                )

                pe_oi = self._safe_int(
                    row.get("pe", {}).get("oi")
                )

                if settlement > strike:

                    total_pain += (
                        settlement - strike
                    ) * ce_oi

                if settlement < strike:

                    total_pain += (
                        strike - settlement
                    ) * pe_oi

            if (
                lowest_pain is None
                or total_pain < lowest_pain
            ):

                lowest_pain = total_pain
                best_strike = settlement

        return best_strike

    def get_market_data(
        self,
        symbol: str = "NIFTY"
    ) -> Dict[str, Any]:

        symbol = symbol.upper()

        if symbol not in (
            "NIFTY",
            "BANKNIFTY"
        ):

            symbol = "NIFTY"

        now = time.time()

        # Short cache prevents excessive NSE requests.
        if (
            symbol in self.last_data
            and now - self.last_fetch.get(
                symbol,
                0
            ) < self.cache_seconds
        ):

            return self.last_data[symbol]

        try:

            # Create fresh browser-like session.
            self._create_session()

            # Establish NSE cookies/session.
            if not self._warm_session():

                raise RuntimeError(
                    "Unable to initialize NSE session"
                )

            # -------------------------------------------------
            # STEP 1: Get available expiry dates
            # -------------------------------------------------

            contract_info = (
                self._get_contract_info(symbol)
            )

            expiry_dates = (
                contract_info.get(
                    "expiryDates",
                    []
                )
            )

            if not expiry_dates:

                raise RuntimeError(
                    "NSE returned no expiry dates"
                )

            expiry = str(
                expiry_dates[0]
            )

            # -------------------------------------------------
            # STEP 2: Get current option chain
            # -------------------------------------------------

            payload = self._get_option_chain(
                symbol,
                expiry
            )

            records = payload.get(
                "records",
                {}
            )

            raw_rows = records.get(
                "data",
                []
            )

            if not raw_rows:

                # Some NSE responses may place the
                # usable rows under filtered.data.
                filtered = payload.get(
                    "filtered",
                    {}
                )

                raw_rows = filtered.get(
                    "data",
                    []
                )

            522    rows = []

   candles = self._get_candles(symbol)

   spot = self._safe_float(
                )
            )

            # -------------------------------------------------
            # STEP 3: Convert NSE rows to our standard format
            # -------------------------------------------------

            for item in raw_rows:

                item_expiry = item.get(
                    "expiryDates"
                )

                # v3 may return several expiries.
                if (
                    item_expiry is not None
                    and not self._expiry_matches(
                        item_expiry,
                        expiry
                    )
                ):

                    continue

                strike = self._safe_float(
                    item.get("strikePrice")
                )

                if strike <= 0:
                    continue

                ce = item.get("CE") or {}
                pe = item.get("PE") or {}

                # Underlying value is sometimes present
                # inside CE/PE rather than records.
                if spot <= 0:

                    spot = max(
                        self._safe_float(
                            ce.get(
                                "underlyingValue"
                            )
                        ),
                        self._safe_float(
                            pe.get(
                                "underlyingValue"
                            )
                        )
                    )

                rows.append({
                    "strike": strike,

                    "ce": {
                        "ltp": self._safe_float(
                            ce.get("lastPrice")
                        ),

                        "oi": self._safe_int(
                            ce.get("openInterest")
                        ),

                        "change_oi": self._safe_int(
                            ce.get(
                                "changeinOpenInterest"
                            )
                        ),

                        "volume": self._safe_int(
                            ce.get(
                                "totalTradedVolume"
                            )
                        ),

                        "iv": self._safe_float(
                            ce.get(
                                "impliedVolatility"
                            )
                        )
                    },

                    "pe": {
                        "ltp": self._safe_float(
                            pe.get("lastPrice")
                        ),

                        "oi": self._safe_int(
                            pe.get("openInterest")
                        ),

                        "change_oi": self._safe_int(
                            pe.get(
                                "changeinOpenInterest"
                            )
                        ),

                        "volume": self._safe_int(
                            pe.get(
                                "totalTradedVolume"
                            )
                        ),

                        "iv": self._safe_float(
                            pe.get(
                                "impliedVolatility"
                            )
                        )
                    }
                })

            rows.sort(
                key=lambda x: x["strike"]
            )

            if not rows:

                raise RuntimeError(
                    "NSE returned no option-chain rows "
                    "for the nearest expiry"
                )

            max_pain = (
                self._calculate_max_pain(rows)
            )

            result = {
    "ok": True,
    "symbol": symbol,
    "spot": spot,
    "expiry": expiry,
    "rows": rows,
    "candles": candles,
    "max_pain": max_pain,
                "fetched_at": (
                    datetime.now().isoformat()
                ),
                "source": (
                    "NSE PUBLIC OPTION CHAIN V3"
                )
            }

            self.last_data[symbol] = result
            self.last_fetch[symbol] = now

            return result

        except Exception as error:

            # Keep last good data available if NSE
            # temporarily challenges the connection.
            if symbol in self.last_data:

                cached = dict(
                    self.last_data[symbol]
                )

                cached["cached"] = True
                cached["warning"] = str(error)

                return cached

            return {
                "ok": False,
                "symbol": symbol,
                "error": str(error),
                "fetched_at": (
                    datetime.now().isoformat()
                ),
                "source": (
                    "NSE PUBLIC OPTION CHAIN V3"
                )
            }


_provider = FreeOptionDataProvider()


def get_market_data(
    symbol: str = "NIFTY"
):

    return _provider.get_market_data(symbol)
