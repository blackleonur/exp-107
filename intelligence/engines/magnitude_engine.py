"""
EXP-124 -- previous-move / magnitude engine.

RESEARCH / PAPER ONLY. Pure functions over an OHLCV series -> ATR-normalized prior-move
magnitude, per horizon, bucketed LOW/NORMAL/HIGH/EXTREME by ROLLING PERCENTILE RANK of this
system's own accumulating data.

Per the original brief section 9 and ARCHITECTURE_PLAN.md section 3.6: EXP-123 flagged prior
4H move magnitude = HIGH as one of its stronger (still ~55-56%, still not cost-positive)
conditions. That finding motivates computing this evidence at all -- it is explicitly NOT
reproduced here as a fixed numeric threshold from EXP-123's own data (which this repository
does not have, per EXP-124/CODEBASE_MAP.md Blocker #1). Every bucket boundary here is a
percentile rank measured from bars this system has itself seen, exactly like
volatility_engine's regime buckets, using the SAME bucket_percentile() function for
consistency.

Operates on whatever OHLCV series is passed in -- consistent with scripts/d_features.py's own
approach (rolling windows/lags measured directly in bars over the native 1-minute series,
never a resampled "5-minute candle" object), a horizon named "MAG_4H" against a 1-minute
series is `horizon_bars=240`.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from intelligence.core.market_buffer import OHLCV
from intelligence.engines.common import atr as _atr
from intelligence.engines.common import bucket_percentile, rolling_percentile_rank

DEFAULT_ATR_PERIOD = 14
DEFAULT_PERCENTILE_WINDOW = 100

# Horizon name -> bars, against a 1-minute-bar OHLCV series. Matches the brief section 9 list
# exactly (MAG_5M ... MAG_24H).
MAG_HORIZONS_MIN: dict[str, int] = {
    "MAG_5M": 5, "MAG_15M": 15, "MAG_30M": 30, "MAG_1H": 60, "MAG_2H": 120,
    "MAG_3H": 180, "MAG_4H": 240, "MAG_6H": 360, "MAG_8H": 480, "MAG_12H": 720,
    "MAG_24H": 1440,
}


@dataclass(frozen=True)
class MagnitudeSnapshot:
    open_time: int
    horizon_bars: int
    move: float                    # close[i] - close[i-horizon], NaN if unavailable
    pct_move: float                 # move / close[i-horizon]
    atr_normalized_move: float       # move / atr[i]  (signed)
    abs_normalized_move: float        # |atr_normalized_move|
    percentile_rank: float             # rolling percentile rank of abs_normalized_move, 0..1 or NaN
    state: str                          # LOW | NORMAL | HIGH | EXTREME | UNKNOWN


def compute(ohlcv: OHLCV, horizon_bars: int, atr_period: int = DEFAULT_ATR_PERIOD,
            percentile_window: int = DEFAULT_PERCENTILE_WINDOW) -> list[MagnitudeSnapshot]:
    n = len(ohlcv)
    if n == 0:
        return []
    close = ohlcv.close
    atr_arr = _atr(ohlcv.high, ohlcv.low, ohlcv.close, atr_period)

    move = np.full(n, np.nan)
    pct_move = np.full(n, np.nan)
    if n > horizon_bars:
        move[horizon_bars:] = close[horizon_bars:] - close[:-horizon_bars]
        with np.errstate(invalid="ignore", divide="ignore"):
            pct_move[horizon_bars:] = np.where(
                close[:-horizon_bars] != 0, move[horizon_bars:] / close[:-horizon_bars], np.nan)

    with np.errstate(invalid="ignore", divide="ignore"):
        anm = np.where((atr_arr > 0) & np.isfinite(atr_arr), move / atr_arr, np.nan)
    abs_anm = np.abs(anm)
    pct = rolling_percentile_rank(abs_anm, percentile_window)

    out = []
    for i in range(n):
        out.append(MagnitudeSnapshot(
            open_time=int(ohlcv.open_time[i]), horizon_bars=horizon_bars,
            move=float(move[i]) if np.isfinite(move[i]) else float("nan"),
            pct_move=float(pct_move[i]) if np.isfinite(pct_move[i]) else float("nan"),
            atr_normalized_move=float(anm[i]) if np.isfinite(anm[i]) else float("nan"),
            abs_normalized_move=float(abs_anm[i]) if np.isfinite(abs_anm[i]) else float("nan"),
            percentile_rank=float(pct[i]) if np.isfinite(pct[i]) else float("nan"),
            state=bucket_percentile(pct[i])))
    return out


def latest(ohlcv: OHLCV, horizon_bars: int, **kwargs) -> MagnitudeSnapshot | None:
    snaps = compute(ohlcv, horizon_bars, **kwargs)
    return snaps[-1] if snaps else None


def compute_all_horizons(ohlcv: OHLCV, horizons: dict[str, int] = MAG_HORIZONS_MIN,
                         **kwargs) -> dict[str, MagnitudeSnapshot | None]:
    """The latest MagnitudeSnapshot for every named horizon, e.g. {"MAG_4H": <snapshot>, ...}.
    A horizon longer than the held buffer simply reports None for that name -- never a
    fabricated value -- so a caller with a short buffer sees exactly which horizons are
    currently unmeasurable."""
    return {name: latest(ohlcv, bars, **kwargs) for name, bars in horizons.items()}
