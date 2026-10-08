from typing import Dict, Any
from datetime import datetime
import time
import requests


class FreeOptionDataProvider:
    """
    Free NSE option-chain connector.

    Uses NSE's publicly accessible option-chain endpoint.
    No Groww API or paid market-data API is required.

    Data is indicative and should be verified before trading.
    """

    BASE_URL = "https://www.nseindia.com"

    def __init__(self):
        self.session = None
        self.last_data = {}
        self.last_fetch = 0
        self.cache_seconds = 15

        self._create_session()

    def _create_session(self):
        self.session = requests.Session()

        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/154.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.nseindia.com/option-chain",
            "Connection": "keep-alive"
        })

    def _initialize_nse_session(self):
        """
        Open NSE pages first so the session receives
        the cookies required by the API.
        """

        try:
            self.session.get(
                self.BASE_URL,
                timeout=10
            )

            self.session.get(
                self.BASE_URL + "/option-chain",
                timeout=10
            )

            return True

        except Exception:
            return False

    def _fetch_chain(self, symbol: str):

        api_url = (
            self.BASE_URL
            + "/api/option-chain-indices?symbol="
            + symbol
        )

        # First establish NSE session/cookies.
        self._initialize_nse_session()

        response = self.session.get(
            api_url,
            timeout=15
        )

        # If NSE rejects the session, create a fresh one
        # and retry once.
        if response.status_code in (401, 403):

            self._create_session()
            self._initialize_nse_session()

            response = self.session.get(
                api_url,
                timeout=15
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

    def _calculate_max_pain(self, rows):

        if not rows:
            return 0.0

        strikes = [
            self._safe_float(row.get("strike"))
            for row in rows
        ]

        strikes = [x for x in strikes if x > 0]

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

                # Call writer loss
                if settlement > strike:
                    total_pain += (
                        settlement - strike
                    ) * ce_oi

                # Put writer loss
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

        # Use short cache to avoid unnecessary NSE requests.
        if (
            symbol in self.last_data
            and now - self.last_fetch < self.cache_seconds
        ):
            return self.last_data[symbol]

        try:

            payload = self._fetch_chain(symbol)

            records = payload.get(
                "records",
                {}
            )

            raw_rows = records.get(
                "data",
                []
            )

            spot = self._safe_float(
                records.get("underlyingValue")
            )

            expiry_dates = records.get(
                "expiryDates",
                []
            )

            expiry = (
                expiry_dates[0]
                if expiry_dates
                else ""
            )

            rows = []

            for item in raw_rows:

                strike = self._safe_float(
                    item.get("strikePrice")
                )

                ce = item.get("CE") or {}
                pe = item.get("PE") or {}

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

            # Keep only useful rows.
            rows = [
                row for row in rows
                if row["strike"] > 0
            ]

            max_pain = self._calculate_max_pain(
                rows
            )

            result = {
                "ok": True,
                "symbol": symbol,
                "spot": spot,
                "expiry": expiry,
                "rows": rows,
                "max_pain": max_pain,
                "fetched_at": datetime.now().isoformat(),
                "source": "NSE PUBLIC OPTION CHAIN"
            }

            self.last_data[symbol] = result
            self.last_fetch = now

            return result

        except Exception as error:

            # If fresh data fails but cached data exists,
            # return the last successful data.
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
                "fetched_at": datetime.now().isoformat(),
                "source": "NSE PUBLIC OPTION CHAIN"
            }


_provider = FreeOptionDataProvider()


def get_market_data(
    symbol: str = "NIFTY"
):

    return _provider.get_market_data(symbol)
