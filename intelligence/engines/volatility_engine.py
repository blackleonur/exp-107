"""
EXP-124 -- volatility engine.

RESEARCH / PAPER ONLY. Pure functions over an OHLCV series -> volatility evidence: ATR, ATR
percentile (rolling, self-measured), realised volatility, and a LOW/NORMAL/HIGH/EXTREME regime
bucket plus expansion/contraction flags.

Per the original brief section 8 and ARCHITECTURE_PLAN.md section 3.6: EXP-123 found HIGH
volatility conditions correlated with a stronger (still modest, still not cost-positive)
direction signal. That finding is used only as a reason to compute and track this evidence
carefully -- it is NOT hard-coded as "volatility high => bullish" or any other directional
assumption anywhere in this module. `VOLATILITY_HIGH != BUY`, exactly as the brief states.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from intelligence.core.market_buffer import OHLCV
from intelligence.engines.common import atr as _atr
from intelligence.engines.common import bucket_percentile, rolling_percentile_rank

DEFAULT_ATR_PERIOD = 14
DEFAULT_PERCENTILE_WINDOW = 100
DEFAULT_RV_WINDOW = 30
EXPANSION_RATIO = 1.3     # current ATR > this * ATR from `EXPANSION_LOOKBACK` bars ago
CONTRACTION_RATIO = 0.7
EXPANSION_LOOKBACK = 10


@dataclass(frozen=True)
class VolatilitySnapshot:
    open_time: int
    atr: float
    atr_percentile: float        # 0..1 rolling rank of ATR vs its own trailing history, NaN if unmeasurable
    realized_vol: float           # std of simple 1-bar returns over DEFAULT_RV_WINDOW bars, in bp
    regime: str                    # LOW | NORMAL | HIGH | EXTREME | UNKNOWN
    expansion: bool
    contraction: bool


def _realized_vol_bp(close: np.ndarray, window: int) -> np.ndarray:
    """Rolling std of simple 1-bar returns, in basis points. NaN until `window`+1 closes are
    available -- consistent with scripts/d_features.py's own rv30 in spirit (same "realised
    vol over a trailing window ending at this bar" idea), but this is an independent
    implementation for the new engines, not a call into EXP-107's frozen code."""
    close = np.asarray(close, dtype=np.float64)
    n = len(close)
    out = np.full(n, np.nan)
    if n < window + 1:
        return out
    ret = np.full(n, np.nan)
    ret[1:] = close[1:] / close[:-1] - 1.0
    for i in range(window, n):
        w = ret[i - window + 1:i + 1]
        if np.isnan(w).any():
            continue
        out[i] = float(np.std(w)) * 1e4
    return out


def compute(ohlcv: OHLCV, atr_period: int = DEFAULT_ATR_PERIOD,
            percentile_window: int = DEFAULT_PERCENTILE_WINDOW,
            rv_window: int = DEFAULT_RV_WINDOW) -> list[VolatilitySnapshot]:
    n = len(ohlcv)
    if n == 0:
        return []
    atr_arr = _atr(ohlcv.high, ohlcv.low, ohlcv.close, atr_period)
    pct = rolling_percentile_rank(atr_arr, percentile_window)
    rv = _realized_vol_bp(ohlcv.close, rv_window)

    out = []
    for i in range(n):
        p = pct[i]
        expansion = contraction = False
        if i >= EXPANSION_LOOKBACK and np.isfinite(atr_arr[i]) and np.isfinite(atr_arr[i - EXPANSION_LOOKBACK]) \
                and atr_arr[i - EXPANSION_LOOKBACK] > 0:
            ratio = atr_arr[i] / atr_arr[i - EXPANSION_LOOKBACK]
            expansion = bool(ratio > EXPANSION_RATIO)
            contraction = bool(ratio < CONTRACTION_RATIO)
        out.append(VolatilitySnapshot(
            open_time=int(ohlcv.open_time[i]),
            atr=float(atr_arr[i]) if np.isfinite(atr_arr[i]) else float("nan"),
            atr_percentile=float(p) if np.isfinite(p) else float("nan"),
            realized_vol=float(rv[i]) if np.isfinite(rv[i]) else float("nan"),
            regime=bucket_percentile(p), expansion=expansion, contraction=contraction))
    return out


def latest(ohlcv: OHLCV, **kwargs) -> VolatilitySnapshot | None:
    snaps = compute(ohlcv, **kwargs)
    return snaps[-1] if snaps else None
