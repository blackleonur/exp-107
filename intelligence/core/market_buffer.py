"""
EXP-124 -- rolling multi-symbol OHLCV buffer and multi-timeframe resampler.

RESEARCH / PAPER ONLY. Read-only with respect to the exchange (uses BinanceClient only).
Deliberately independent of scripts/d_features.py's C/QV/TB layout -- this buffer carries full
OHLCV (not just close/quote-volume/taker-buy-quote-volume) because the candle/price-action and
structure engines need open/high/low, which d_features.py's D-specific feature set does not.

Per EXP-124/ARCHITECTURE_PLAN.md section 1 (isolation contract): this module does not import
anything from scripts/. SYMBOLS below is intentionally a separate copy of the list in
scripts/d_features.py -- tests/unit/test_market_buffer.py asserts the two never drift apart.

Only CLOSED bars enter the buffer, exactly like R3_shadow_run.py's Bars class: Binance returns
the in-progress bar last, and a bar is usable only once its close time has passed.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from intelligence.core.binance_client import BinanceClient

SYMBOLS = ["ADAUSDT", "AVAXUSDT", "BNBUSDT", "BTCUSDT", "DOGEUSDT",
           "DOTUSDT", "ETHUSDT", "LINKUSDT", "SOLUSDT", "XRPUSDT"]
NS = len(SYMBOLS)
BAR_MS = 60_000

# Higher timeframes this system evaluates, in minutes. 1 (native) is always available as the
# buffer's own grid; everything else is built by resampling it.
TIMEFRAMES_MIN = (1, 5, 15, 30, 60, 120, 180, 240, 300, 360, 480, 720, 1440)


@dataclass
class OHLCV:
    """Closed-bar OHLCV for one symbol on one timeframe. Arrays are aligned 1:1 by index."""
    open_time: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    quote_volume: np.ndarray
    taker_buy_quote_volume: np.ndarray

    def __len__(self) -> int:
        return len(self.open_time)


@dataclass
class FormingCandle:
    """The partial aggregate of already-CLOSED smaller bars since a timeframe's last boundary.

    This is never the native exchange in-progress bar (that is never fetched -- see module
    docstring); for the native 1-minute grid itself there is no 'forming' view, only the most
    recent fully-closed bar. For any coarser timeframe, this is a legitimate, fully-closed-data
    view of "what this candle looks like so far."
    """
    timeframe_min: int
    boundary_open_time: int
    bars_included: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class MarketBuffer:
    """Rolling closed-1m OHLCV for all SYMBOLS on a shared minute grid. numpy only."""

    def __init__(self, keep_bars: int) -> None:
        self.keep_bars = keep_bars
        self.grid = np.zeros(0, dtype=np.int64)
        self.O = np.zeros((0, NS))
        self.H = np.zeros((0, NS))
        self.L = np.zeros((0, NS))
        self.C = np.zeros((0, NS))
        self.V = np.zeros((0, NS))
        self.QV = np.zeros((0, NS))
        self.TB = np.zeros((0, NS))
        self.pos: dict[int, int] = {}

    # ---- fetch + rebuild -------------------------------------------------------------
    def backfill(self, client: BinanceClient, bars: int | None = None) -> None:
        bars = self.keep_bars if bars is None else bars
        per: dict[str, list] = {}
        for s in SYMBOLS:
            rows: list = []
            end = None
            while len(rows) < bars:
                chunk = client.klines(s, "1m", 1500, end)
                if not chunk:
                    break
                rows = chunk + rows
                end = int(chunk[0][0]) - 1
                if len(chunk) < 1500:
                    break
            per[s] = rows[-bars:]
        self._rebuild(per)

    def poll(self, client: BinanceClient, fetch_limit: int = 60) -> int:
        """Fetch the most recent bars and merge. Returns the newest closed bar timestamp."""
        per: dict[str, list] = {}
        for j, s in enumerate(SYMBOLS):
            by_ts: dict[int, list] = {}
            for i, t in enumerate(self.grid.tolist()):
                by_ts[int(t)] = [int(t), self.O[i, j], self.H[i, j], self.L[i, j],
                                 self.C[i, j], self.V[i, j], 0, self.QV[i, j], 0, 0,
                                 self.TB[i, j]]
            for r in client.klines(s, "1m", fetch_limit):
                by_ts[int(r[0])] = r
            per[s] = [by_ts[k] for k in sorted(by_ts)][-self.keep_bars:]
        self._rebuild(per)
        return int(self.grid[-1]) if len(self.grid) else 0

    def _rebuild(self, per: dict[str, list]) -> None:
        now = int(time.time() * 1000)
        grids = []
        for s in SYMBOLS:
            ts = np.array([int(r[0]) for r in per[s] if int(r[0]) + BAR_MS <= now],
                          dtype=np.int64)
            grids.append(ts)
        grid = np.unique(np.concatenate(grids)) if grids else np.zeros(0, np.int64)
        gp = {int(t): i for i, t in enumerate(grid)}
        shape = (len(grid), NS)
        O = np.full(shape, np.nan); H = np.full(shape, np.nan); L = np.full(shape, np.nan)
        C = np.full(shape, np.nan); V = np.full(shape, np.nan)
        QV = np.full(shape, np.nan); TB = np.full(shape, np.nan)
        for j, s in enumerate(SYMBOLS):
            for r in per[s]:
                i = gp.get(int(r[0]))
                if i is None:
                    continue
                O[i, j] = float(r[1]); H[i, j] = float(r[2]); L[i, j] = float(r[3])
                C[i, j] = float(r[4]); V[i, j] = float(r[5])
                QV[i, j] = float(r[7]); TB[i, j] = float(r[10])
        self.grid, self.O, self.H, self.L = grid, O, H, L
        self.C, self.V, self.QV, self.TB = C, V, QV, TB
        self.pos = gp

    # ---- resampling --------------------------------------------------------------
    def resample(self, symbol: str, timeframe_min: int) -> OHLCV:
        """Standard closed-bar OHLCV aggregation to a coarser timeframe, right-aligned on
        boundaries that are multiples of timeframe_min minutes since the epoch (i.e. an hourly
        candle always starts on the hour, a 4-hour candle always starts at 00/04/08/... UTC).
        Only fully-covered buckets are emitted -- a partial trailing bucket is never silently
        included as if it were closed (use forming_candle() for that view instead)."""
        j = SYMBOLS.index(symbol)
        if timeframe_min == 1:
            return OHLCV(self.grid.copy(), self.O[:, j].copy(), self.H[:, j].copy(),
                         self.L[:, j].copy(), self.C[:, j].copy(), self.V[:, j].copy(),
                         self.QV[:, j].copy(), self.TB[:, j].copy())
        if len(self.grid) == 0:
            z = np.zeros(0)
            return OHLCV(self.grid.copy(), z, z, z, z, z, z, z)
        tf_ms = timeframe_min * BAR_MS
        bucket = (self.grid // tf_ms) * tf_ms
        uniq = np.unique(bucket)
        ot, o, h, l, c, v, qv, tb = [], [], [], [], [], [], [], []
        for b in uniq:
            idx = np.where(bucket == b)[0]
            n_expected = timeframe_min
            if len(idx) < n_expected:
                continue  # partial bucket at either edge of the held history -- not closed data
            closes = self.C[idx, j]
            if np.all(np.isnan(closes)):
                continue
            ot.append(int(b))
            o.append(self.O[idx[0], j])
            h.append(np.nanmax(self.H[idx, j]))
            l.append(np.nanmin(self.L[idx, j]))
            c.append(self.C[idx[-1], j])
            v.append(np.nansum(self.V[idx, j]))
            qv.append(np.nansum(self.QV[idx, j]))
            tb.append(np.nansum(self.TB[idx, j]))
        return OHLCV(np.array(ot, dtype=np.int64), np.array(o), np.array(h), np.array(l),
                     np.array(c), np.array(v), np.array(qv), np.array(tb))

    def forming_candle(self, symbol: str, timeframe_min: int) -> FormingCandle | None:
        """The in-progress higher-timeframe candle, built ONLY from already-closed 1m bars
        since the last boundary. None if no closed bars fall in the current bucket yet."""
        if len(self.grid) == 0:
            return None
        j = SYMBOLS.index(symbol)
        tf_ms = timeframe_min * BAR_MS
        last = int(self.grid[-1])
        boundary = (last // tf_ms) * tf_ms
        idx = np.where((self.grid >= boundary) & (self.grid <= last))[0]
        if len(idx) == 0 or np.all(np.isnan(self.C[idx, j])):
            return None
        return FormingCandle(
            timeframe_min=timeframe_min, boundary_open_time=boundary, bars_included=len(idx),
            open=float(self.O[idx[0], j]), high=float(np.nanmax(self.H[idx, j])),
            low=float(np.nanmin(self.L[idx, j])), close=float(self.C[idx[-1], j]),
            volume=float(np.nansum(self.V[idx, j])))
