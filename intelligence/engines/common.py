"""
EXP-124 -- shared numeric helpers for intelligence/engines/*.

Pure functions over numpy arrays. No I/O, no exchange calls, no state. Deliberately separate
from scripts/d_features.py: EXP-107's own feature block is frozen and untouched (isolation
contract, ARCHITECTURE_PLAN.md section 1) -- this is new, independent code for the new engines
only, even though some formulas (true range, ATR) are standard and inevitably similar in shape
to well-known public definitions.
"""
from __future__ import annotations

import numpy as np


def true_range(high, low, close) -> np.ndarray:
    """True range per bar: max(high-low, |high-prev_close|, |low-prev_close|). The first bar
    has no previous close, so its true range is just high-low -- never a fabricated value."""
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    n = len(high)
    if n == 0:
        return np.zeros(0)
    prev_close = np.concatenate([[np.nan], close[:-1]])
    a = high - low
    b = np.abs(high - prev_close)
    c = np.abs(low - prev_close)
    stacked = np.vstack([a, b, c])
    with np.errstate(invalid="ignore"):
        tr = np.nanmax(stacked, axis=0)
    tr[0] = a[0]
    return tr


def atr(high, low, close, period: int = 14) -> np.ndarray:
    """Simple rolling-mean true range over `period` bars (NOT Wilder's exponential smoothing --
    named plainly so it is never mistaken for the RMA variant). The first `period-1` values are
    NaN; no synthetic warm-up value is invented."""
    tr = true_range(high, low, close)
    n = len(tr)
    out = np.full(n, np.nan)
    if n < period:
        return out
    csum = np.concatenate([[0.0], np.cumsum(tr)])
    out[period - 1:] = (csum[period:] - csum[:n - period + 1]) / period
    return out


def rolling_percentile_rank(x: np.ndarray, window: int) -> np.ndarray:
    """For each index i >= window-1, the percentile rank (0..1) of x[i] within the trailing
    `window` values INCLUDING x[i]. NaN where the trailing window isn't fully available -- a
    short warm-up window is never padded with a guessed value."""
    x = np.asarray(x, dtype=np.float64)
    n = len(x)
    out = np.full(n, np.nan)
    for i in range(window - 1, n):
        w = x[i - window + 1:i + 1]
        if np.isnan(w).any():
            continue
        out[i] = float(np.sum(w <= w[-1]) - 1) / (window - 1) if window > 1 else 0.5
    return out


# Shared LOW/NORMAL/HIGH/EXTREME bucketing, reused by volatility_engine and magnitude_engine so
# both "is this reading unusual" questions are answered the same way. Thresholds are on a
# ROLLING PERCENTILE RANK computed from this system's own accumulating data (never a fixed
# magic-number cutoff copied from an EXP-108->123 result) -- per ARCHITECTURE_PLAN.md section
# 3.6, no historical finding's specific numbers are hard-coded into production logic.
PERCENTILE_BUCKET_EDGES = (0.25, 0.75, 0.95)  # LOW | NORMAL | HIGH | EXTREME


def bucket_percentile(p: float) -> str:
    """LOW / NORMAL / HIGH / EXTREME from a 0..1 percentile rank, or UNKNOWN if `p` is NaN --
    an unmeasurable reading is reported as unmeasurable, never guessed into a bucket."""
    if p is None or (isinstance(p, float) and np.isnan(p)):
        return "UNKNOWN"
    lo, mid, hi = PERCENTILE_BUCKET_EDGES
    if p < lo:
        return "LOW"
    if p < mid:
        return "NORMAL"
    if p < hi:
        return "HIGH"
    return "EXTREME"
