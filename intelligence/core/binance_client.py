"""
EXP-124 -- read-only Binance USDT-M Futures REST client.

RESEARCH / PAPER ONLY. This module calls exclusively PUBLIC, UNAUTHENTICATED, READ-ONLY
endpoints. It imports no exchange client, no signing code and no credential, and cannot place,
modify or cancel an order. It does not import from scripts/ -- it is independent of EXP-107's
own client code in R3_shadow_run.py, by the isolation contract in
EXP-124/ARCHITECTURE_PLAN.md section 1.

Endpoints used, all public:
    GET /fapi/v1/klines            closed 1-minute (or other interval) bars
    GET /fapi/v1/ticker/bookTicker best bid/ask
    GET /fapi/v1/depth             order-book snapshot (for imbalance)
    GET /fapi/v1/premiumIndex      funding rate, mark price, index price (for basis)
    GET /fapi/v1/openInterest      open interest

No API key, no secret, no trading permission is read from the environment or required for any
of these.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

# hard safety assertion: nothing that could trade may be importable in this process
for _forbidden in ("ccxt", "binance.client"):
    assert _forbidden not in sys.modules, f"{_forbidden} must not be imported in this process"

FAPI = "https://fapi.binance.com"
USER_AGENT = "exp124-intelligence/1.0"


class BinanceClientError(RuntimeError):
    """Raised after all retries are exhausted. Never raised for a trading-related reason --
    this client has no trading capability to fail at."""


@dataclass(frozen=True)
class BookTicker:
    symbol: str
    bid: float
    ask: float
    kind: str  # "bid_ask" or "mid_fallback"

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread_bp(self) -> float:
        m = self.mid
        return (self.ask - self.bid) / m * 1e4 if m > 0 else float("nan")


class BinanceClient:
    """Stateless, read-only. One instance can be shared across symbols and cycles."""

    def __init__(self, base: str = FAPI, timeout: float = 15.0, retries: int = 4) -> None:
        self.base = base
        self.timeout = timeout
        self.retries = retries

    # ---- transport -----------------------------------------------------------------
    def _get(self, path: str, params: dict) -> object:
        q = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{self.base}{path}?{q}" if params else f"{self.base}{path}"
        last_err: Exception | None = None
        for i in range(self.retries):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return json.loads(r.read().decode())
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
                last_err = e
                if i == self.retries - 1:
                    break
                time.sleep(1.5 * (i + 1))
        raise BinanceClientError(f"GET {path} failed after {self.retries} attempts: {last_err}")

    # ---- market data -----------------------------------------------------------------
    def klines(self, symbol: str, interval: str = "1m", limit: int = 500,
               end_ms: int | None = None) -> list:
        """Raw kline rows: [open_time, open, high, low, close, volume, close_time,
        quote_asset_volume, n_trades, taker_buy_base_volume, taker_buy_quote_volume, ignore]."""
        p = {"symbol": symbol, "interval": interval, "limit": min(limit, 1500)}
        if end_ms:
            p["endTime"] = end_ms
        return self._get("/fapi/v1/klines", p)

    def book_ticker(self, symbol: str) -> BookTicker:
        """Best bid/ask. Falls back to last close, marked as such -- never reported as real."""
        try:
            t = self._get("/fapi/v1/ticker/bookTicker", {"symbol": symbol})
            b, a = float(t["bidPrice"]), float(t["askPrice"])
            if b > 0 and a >= b:
                return BookTicker(symbol, b, a, "bid_ask")
        except (BinanceClientError, KeyError, ValueError):
            pass
        k = self.klines(symbol, "1m", 1)
        p = float(k[-1][4])
        return BookTicker(symbol, p, p, "mid_fallback")

    def depth(self, symbol: str, limit: int = 20) -> dict:
        """Order-book snapshot: {"bids": [[price, qty], ...], "asks": [[price, qty], ...]}."""
        return self._get("/fapi/v1/depth", {"symbol": symbol, "limit": limit})

    def premium_index(self, symbol: str) -> dict:
        """Funding rate + mark/index price, for basis. Public, unauthenticated."""
        return self._get("/fapi/v1/premiumIndex", {"symbol": symbol})

    def open_interest(self, symbol: str) -> dict:
        return self._get("/fapi/v1/openInterest", {"symbol": symbol})
