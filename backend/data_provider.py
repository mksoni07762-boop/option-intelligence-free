from typing import Dict, Any, List
from datetime import datetime

try:
    from option_chain_live import NiftyTraderClient
except ImportError:
    NiftyTraderClient = None


class FreeOptionDataProvider:
    """
    Free option-chain provider.

    Source:
    option-chain-live / public endpoint

    IMPORTANT:
    This is an unofficial data source.
    Data must be treated as indicative and verified
    before making trading decisions.
    """

    def __init__(self):
        self.client = None

        if NiftyTraderClient is not None:
            self.client = NiftyTraderClient()

    def available(self) -> bool:
        return self.client is not None

    def get_market_data(self, symbol: str = "NIFTY") -> Dict[str, Any]:

        symbol = symbol.upper()

        if symbol not in ["NIFTY", "BANKNIFTY"]:
            symbol = "NIFTY"

        if self.client is None:
            return {
                "ok": False,
                "symbol": symbol,
                "error": "option-chain-live package is not installed"
            }

        try:
            chain = self.client.get_chain(symbol)

            rows = []

            for row in chain.rows:

                rows.append({
                    "strike": float(row.strike),

                    "ce": {
                        "ltp": float(row.calls.ltp or 0),
                        "oi": int(row.calls.oi or 0),
                        "change_oi": int(
                            row.calls.change_oi or 0
                        ),
                        "volume": int(
                            row.calls.volume or 0
                        ),
                        "iv": float(
                            row.calls.iv or 0
                        )
                    },

                    "pe": {
                        "ltp": float(row.puts.ltp or 0),
                        "oi": int(row.puts.oi or 0),
                        "change_oi": int(
                            row.puts.change_oi or 0
                        ),
                        "volume": int(
                            row.puts.volume or 0
                        ),
                        "iv": float(
                            row.puts.iv or 0
                        )
                    }
                })

            spot = float(chain.spot or 0)

            return {
                "ok": True,
                "symbol": symbol,
                "spot": spot,
                "expiry": str(chain.expiry),
                "rows": rows,
                "max_pain": float(
                    chain.max_pain_estimate or 0
                ),
                "fetched_at": datetime.now().isoformat()
            }

        except Exception as error:

            return {
                "ok": False,
                "symbol": symbol,
                "error": str(error),
                "fetched_at": datetime.now().isoformat()
            }


_provider = FreeOptionDataProvider()


def get_market_data(symbol: str = "NIFTY"):

    return _provider.get_market_data(symbol)
