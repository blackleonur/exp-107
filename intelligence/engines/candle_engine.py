"""
EXP-124 -- candle / price-action engine.

RESEARCH / PAPER ONLY. Pure functions over an OHLCV series
(intelligence.core.market_buffer.OHLCV) -> price-action evidence, one CandleSnapshot per bar.

Per ARCHITECTURE_PLAN.md section 3.4 and the original brief section 5: nothing here becomes a
standalone trade signal. It is evidence attached to a symbol at a point in time, for the
confirmation engine (a later phase) to weigh alongside EXP-107's own signal -- never used to
gate a decision by itself.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from intelligence.core.market_buffer import OHLCV
from intelligence.engines.common import atr as _atr

DEFAULT_ATR_PERIOD = 14
DEFAULT_BREAKOUT_LOOKBACK = 20
EXPANSION_MULT = 1.5
COMPRESSION_MULT = 0.6


@dataclass(frozen=True)
class CandleSnapshot:
    open_time: int
    body: float
    upper_wick: float
    lower_wick: float
    candle_range: float
    body_to_range: float           # NaN if range == 0
    upper_wick_to_body: float      # NaN if body == 0 (uses a floor to avoid inf, see compute())
    lower_wick_to_body: float
    is_green: bool
    consecutive_same_color: int    # length of the same-color streak ending at this bar
    engulfing: str                 # "bullish" | "bearish" | "none"
    rejection: str                 # "upper" | "lower" | "none"
    inside_bar: bool
    breakout: str                  # "up" | "down" | "none" -- close beyond the prior N-bar extreme
    failed_breakout: bool          # the PRIOR bar broke out; this bar's close fell back inside
    expansion: bool                # range > EXPANSION_MULT * atr
    compression: bool              # range < COMPRESSION_MULT * atr
    atr_normalized_range: float    # range / atr; NaN if atr unavailable at this bar
    close_location_value: float    # ((close-low)-(high-close)) / range, in [-1, 1]; NaN if range==0
    took_out_prior_high: bool      # this bar's high > previous bar's high
    took_out_prior_low: bool       # this bar's low < previous bar's low


def compute(ohlcv: OHLCV, atr_period: int = DEFAULT_ATR_PERIOD,
            breakout_lookback: int = DEFAULT_BREAKOUT_LOOKBACK) -> list[CandleSnapshot]:
    """One CandleSnapshot per bar in `ohlcv`, in order. Every field is computed causally: bar i
    never reads bar i+1 or later."""
    n = len(ohlcv)
    if n == 0:
        return []
    o, h, l, c = ohlcv.open, ohlcv.high, ohlcv.low, ohlcv.close
    body = np.abs(c - o)
    upper_wick = h - np.maximum(o, c)
    lower_wick = np.minimum(o, c) - l
    rng = h - l
    is_green = c >= o

    with np.errstate(invalid="ignore", divide="ignore"):
        body_to_range = np.where(rng > 0, body / rng, np.nan)
        clv = np.where(rng > 0, ((c - l) - (h - c)) / rng, np.nan)
        body_floor = np.maximum(body, 1e-12)
        uw_ratio = np.where(body > 0, upper_wick / body_floor, np.where(upper_wick > 0, np.inf, np.nan))
        lw_ratio = np.where(body > 0, lower_wick / body_floor, np.where(lower_wick > 0, np.inf, np.nan))

    atr_arr = _atr(h, l, c, atr_period)
    with np.errstate(invalid="ignore", divide="ignore"):
        atr_norm_range = np.where(np.isfinite(atr_arr) & (atr_arr > 0), rng / atr_arr, np.nan)
    expansion = np.where(np.isfinite(atr_norm_range), atr_norm_range > EXPANSION_MULT, False)
    compression = np.where(np.isfinite(atr_norm_range), atr_norm_range < COMPRESSION_MULT, False)

    out: list[CandleSnapshot] = []
    streak = 0
    prev_color: bool | None = None
    breakout_hist: list[str] = []
    for i in range(n):
        color = bool(is_green[i])
        streak = streak + 1 if (prev_color is not None and color == prev_color) else 1
        prev_color = color

        engulf = "none"
        rejection = "none"
        inside = False
        took_high = False
        took_low = False
        if i > 0:
            po, pc = o[i - 1], c[i - 1]
            prev_green = pc >= po
            if color and not prev_green and o[i] <= pc and c[i] >= po:
                engulf = "bullish"
            elif (not color) and prev_green and o[i] >= pc and c[i] <= po:
                engulf = "bearish"
            inside = bool(h[i] <= h[i - 1] and l[i] >= l[i - 1])
            took_high = bool(h[i] > h[i - 1])
            took_low = bool(l[i] < l[i - 1])

        uwr, lwr = uw_ratio[i], lw_ratio[i]
        if np.isfinite(uwr) and uwr >= 2.0 and (not np.isfinite(lwr) or uwr > lwr):
            rejection = "upper"
        elif np.isfinite(lwr) and lwr >= 2.0 and (not np.isfinite(uwr) or lwr > uwr):
            rejection = "lower"

        brk = "none"
        if i >= breakout_lookback:
            window_h = np.max(h[i - breakout_lookback:i])
            window_l = np.min(l[i - breakout_lookback:i])
            if c[i] > window_h:
                brk = "up"
            elif c[i] < window_l:
                brk = "down"
        breakout_hist.append(brk)

        failed = False
        if i > 0:
            prev_brk = breakout_hist[i - 1]
            if prev_brk == "up" and i >= breakout_lookback + 1:
                prior_window_h = np.max(h[i - 1 - breakout_lookback:i - 1])
                failed = bool(c[i] < prior_window_h)
            elif prev_brk == "down" and i >= breakout_lookback + 1:
                prior_window_l = np.min(l[i - 1 - breakout_lookback:i - 1])
                failed = bool(c[i] > prior_window_l)

        out.append(CandleSnapshot(
            open_time=int(ohlcv.open_time[i]), body=float(body[i]),
            upper_wick=float(upper_wick[i]), lower_wick=float(lower_wick[i]),
            candle_range=float(rng[i]), body_to_range=float(body_to_range[i]),
            upper_wick_to_body=float(uwr), lower_wick_to_body=float(lwr),
            is_green=color, consecutive_same_color=streak, engulfing=engulf,
            rejection=rejection, inside_bar=inside, breakout=brk, failed_breakout=failed,
            expansion=bool(expansion[i]), compression=bool(compression[i]),
            atr_normalized_range=float(atr_norm_range[i]),
            close_location_value=float(clv[i]), took_out_prior_high=took_high,
            took_out_prior_low=took_low))
    return out


def latest(ohlcv: OHLCV, **kwargs) -> CandleSnapshot | None:
    """The most recently CLOSED bar's snapshot, or None if `ohlcv` is empty."""
    snaps = compute(ohlcv, **kwargs)
    return snaps[-1] if snaps else None
