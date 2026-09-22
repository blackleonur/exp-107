"""
EXP-124 -- BTC / market regime engine.

RESEARCH / PAPER ONLY. Pure functions over OHLCV series -> BTC's own regime read plus its
correlation to other symbols. Per the original brief section 11: BTC's movement is never turned
into a standalone trade signal here -- this module returns descriptive regime state and
correlation numbers only, for the confirmation engine (a later phase) to weigh alongside
EXP-107's own signal. It also does not reproduce or assume EXP-116/117's finding that BTC
relates to EXP-107's D signal -- that finding lives outside this repository (CODEBASE_MAP.md
Blocker #1) and is not re-derived here; this module only computes fresh, independently-defined
regime evidence from live data.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from intelligence.core.market_buffer import OHLCV
from intelligence.engines.structure_engine import structure_state
from intelligence.engines.volatility_engine import compute as volatility_compute

MOMENTUM_AUTOCORR_THRESHOLD = 0.15
DEFAULT_CORR_WINDOW = 30
DEFAULT_RETURN_HORIZON = 1


@dataclass(frozen=True)
class BtcRegimeSnapshot:
    open_time: int
    return_1: float                # latest 1-bar return
    return_horizon: float            # return over DEFAULT_RETURN_HORIZON-equivalent bars passed in
    trend_state: str                  # from structure_engine: BULLISH/BEARISH/NEUTRAL/RANGE/TRANSITION
    volatility_regime: str             # from volatility_engine: LOW/NORMAL/HIGH/EXTREME/UNKNOWN
    momentum_state: str                 # MOMENTUM | MEAN_REVERSION | MIXED | UNKNOWN
    lag1_autocorr: float


def _returns(close: np.ndarray) -> np.ndarray:
    close = np.asarray(close, dtype=np.float64)
    r = np.full(len(close), np.nan)
    if len(close) > 1:
        r[1:] = close[1:] / close[:-1] - 1.0
    return r


def lag1_autocorrelation(close: np.ndarray, window: int) -> float:
    """Pearson correlation between r[t] and r[t-1] over the trailing `window` returns. NaN if
    unmeasurable (too little history, or zero variance)."""
    r = _returns(close)
    if len(r) < window + 2:
        return float("nan")
    tail = r[-(window + 1):]
    a, b = tail[1:], tail[:-1]
    if np.isnan(a).any() or np.isnan(b).any():
        return float("nan")
    if np.std(a) == 0 or np.std(b) == 0:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def momentum_vs_mean_reversion(close: np.ndarray, window: int = DEFAULT_CORR_WINDOW) -> tuple[str, float]:
    ac = lag1_autocorrelation(close, window)
    if not np.isfinite(ac):
        return "UNKNOWN", float("nan")
    if ac > MOMENTUM_AUTOCORR_THRESHOLD:
        return "MOMENTUM", ac
    if ac < -MOMENTUM_AUTOCORR_THRESHOLD:
        return "MEAN_REVERSION", ac
    return "MIXED", ac


def rolling_correlation(a_close: np.ndarray, b_close: np.ndarray, window: int = DEFAULT_CORR_WINDOW) -> float:
    """Pearson correlation of simple returns between two close series over the trailing
    `window` bars. NaN if either series lacks enough history or has zero variance in-window."""
    ra, rb = _returns(a_close), _returns(b_close)
    n = min(len(ra), len(rb))
    if n < window + 1:
        return float("nan")
    ta, tb = ra[-window:], rb[-window:]
    if np.isnan(ta).any() or np.isnan(tb).any():
        return float("nan")
    if np.std(ta) == 0 or np.std(tb) == 0:
        return float("nan")
    return float(np.corrcoef(ta, tb)[0, 1])


def btc_regime(btc_ohlcv: OHLCV, return_horizon_bars: int = DEFAULT_RETURN_HORIZON,
              corr_window: int = DEFAULT_CORR_WINDOW) -> BtcRegimeSnapshot | None:
    n = len(btc_ohlcv)
    if n == 0:
        return None
    close = btc_ohlcv.close
    r1 = float(close[-1] / close[-2] - 1.0) if n > 1 else float("nan")
    rh = (float(close[-1] / close[-1 - return_horizon_bars] - 1.0)
          if n > return_horizon_bars else float("nan"))
    struct = structure_state(btc_ohlcv)
    vol_snaps = volatility_compute(btc_ohlcv, percentile_window=min(corr_window * 3, n))
    vol_regime = vol_snaps[-1].regime if vol_snaps else "UNKNOWN"
    momentum, ac = momentum_vs_mean_reversion(close, corr_window)
    return BtcRegimeSnapshot(
        open_time=int(btc_ohlcv.open_time[-1]), return_1=r1, return_horizon=rh,
        trend_state=(struct.trend if struct else "NEUTRAL"), volatility_regime=vol_regime,
        momentum_state=momentum, lag1_autocorr=ac)


def btc_correlations(btc_ohlcv: OHLCV, others: dict[str, OHLCV],
                     window: int = DEFAULT_CORR_WINDOW) -> dict[str, float]:
    """{symbol: rolling correlation to BTC}, NaN for any symbol whose series can't support it."""
    return {sym: rolling_correlation(btc_ohlcv.close, o.close, window) for sym, o in others.items()}
