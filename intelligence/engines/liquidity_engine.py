"""
EXP-124 -- liquidity / microstructure engine.

RESEARCH / PAPER ONLY. Pure functions -- this module makes NO exchange calls itself; every
piece of raw exchange data (order-book snapshot, book ticker, premium index, open interest) is
FETCHED ELSEWHERE (intelligence/core/binance_client.py, called by a later orchestration phase)
and handed in already-parsed. That keeps this module fully unit-testable offline and keeps the
"what data did we actually have at this instant" question explicit and auditable.

Per the original brief section 7: liquidation data and true historical order-flow are not
obtainable from Binance's public REST surface without infrastructure this repository doesn't
have. Every such field is reported as the literal string "UNKNOWN" rather than guessed --
"Veri yoksa uydurma."
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from intelligence.core.market_buffer import OHLCV

UNKNOWN = "UNKNOWN"
DEFAULT_DEPTH_LEVELS = 10
DEFAULT_EXTREMES_WINDOW = 20
DEFAULT_VOLUME_WINDOW = 20
EQUAL_LEVEL_TOLERANCE_BP = 5.0
VOLUME_SPIKE_Z = 2.0


@dataclass(frozen=True)
class LiquiditySnapshot:
    open_time: int
    spread_bp: float | None
    order_book_imbalance: float | None      # (bid_vol - ask_vol) / (bid_vol + ask_vol), top-N levels
    funding_rate: float | None
    basis_bp: float | None                   # (mark - index) / index, in bp
    open_interest: float | None
    volume_zscore: float                       # NaN if unmeasurable, never fabricated
    volume_spike: bool
    recent_high: float | None
    recent_low: float | None
    equal_high: bool                             # current bar's high sits at/near recent_high
    equal_low: bool
    wick_rejection: str                            # "upper" | "lower" | "none" -- from the caller's own candle snapshot
    liquidation_data: str = UNKNOWN                  # always UNKNOWN -- no endpoint for this is used


def order_book_imbalance(depth: dict | None, levels: int = DEFAULT_DEPTH_LEVELS) -> float | None:
    if not depth:
        return None
    bids, asks = depth.get("bids") or [], depth.get("asks") or []
    if not bids or not asks:
        return None
    try:
        bid_vol = sum(float(q) for _, q in bids[:levels])
        ask_vol = sum(float(q) for _, q in asks[:levels])
    except (TypeError, ValueError):
        return None
    total = bid_vol + ask_vol
    return (bid_vol - ask_vol) / total if total > 0 else None


def funding_and_basis(premium_index: dict | None) -> tuple[float | None, float | None]:
    if not premium_index:
        return None, None
    funding = None
    try:
        funding = float(premium_index["lastFundingRate"])
    except (KeyError, TypeError, ValueError):
        pass
    basis = None
    try:
        mark, index = float(premium_index["markPrice"]), float(premium_index["indexPrice"])
        if index != 0:
            basis = (mark - index) / index * 1e4
    except (KeyError, TypeError, ValueError):
        pass
    return funding, basis


def open_interest_value(open_interest: dict | None) -> float | None:
    if not open_interest:
        return None
    try:
        return float(open_interest["openInterest"])
    except (KeyError, TypeError, ValueError):
        return None


def volume_evidence(ohlcv: OHLCV, window: int = DEFAULT_VOLUME_WINDOW) -> tuple[float, bool]:
    """Z-score of the LATEST bar's volume against the `window` bars BEFORE it (the current bar
    is never included in its own baseline). NaN/False if there isn't enough trailing history."""
    n = len(ohlcv)
    if n < window + 1:
        return float("nan"), False
    w = ohlcv.volume[n - window - 1:n - 1]
    if np.isnan(w).any():
        return float("nan"), False
    mu, sd = float(np.mean(w)), float(np.std(w))
    if sd <= 0:
        return float("nan"), False
    z = (float(ohlcv.volume[-1]) - mu) / sd
    return z, bool(z > VOLUME_SPIKE_Z)


def recent_extremes(ohlcv: OHLCV, window: int = DEFAULT_EXTREMES_WINDOW,
                    exclude_current: bool = True) -> tuple[float | None, float | None]:
    """Highest high / lowest low over the trailing `window` bars BEFORE the current one (so the
    current bar can be meaningfully compared against them for an equal-high/equal-low read)."""
    n = len(ohlcv)
    end = n - 1 if exclude_current else n
    if end < window:
        return None, None
    return float(np.max(ohlcv.high[end - window:end])), float(np.min(ohlcv.low[end - window:end]))


def _near(a: float, b: float | None, tol_bp: float) -> bool:
    if b is None or b == 0:
        return False
    return abs(a - b) / abs(b) * 1e4 <= tol_bp


def compute(ohlcv: OHLCV, wick_rejection: str = "none", book_ticker=None, depth: dict | None = None,
           premium_index: dict | None = None, open_interest: dict | None = None,
           extremes_window: int = DEFAULT_EXTREMES_WINDOW,
           volume_window: int = DEFAULT_VOLUME_WINDOW) -> LiquiditySnapshot | None:
    """Compose one LiquiditySnapshot for the latest bar in `ohlcv`. Every exchange-sourced
    argument is optional and independently allowed to be missing -- a caller that only has
    OHLCV (no book/depth/funding/OI fetched yet) still gets a valid snapshot with those fields
    as None, never a crash and never a guess."""
    n = len(ohlcv)
    if n == 0:
        return None
    rh, rl = recent_extremes(ohlcv, extremes_window)
    vz, spike = volume_evidence(ohlcv, volume_window)
    funding, basis = funding_and_basis(premium_index)
    cur_high, cur_low = float(ohlcv.high[-1]), float(ohlcv.low[-1])
    return LiquiditySnapshot(
        open_time=int(ohlcv.open_time[-1]),
        spread_bp=(book_ticker.spread_bp if book_ticker is not None else None),
        order_book_imbalance=order_book_imbalance(depth),
        funding_rate=funding, basis_bp=basis,
        open_interest=open_interest_value(open_interest),
        volume_zscore=vz, volume_spike=spike,
        recent_high=rh, recent_low=rl,
        equal_high=_near(cur_high, rh, EQUAL_LEVEL_TOLERANCE_BP),
        equal_low=_near(cur_low, rl, EQUAL_LEVEL_TOLERANCE_BP),
        wick_rejection=wick_rejection)
