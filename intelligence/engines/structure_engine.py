"""
EXP-124 -- market structure engine.

RESEARCH / PAPER ONLY. Pure functions over an OHLCV series -> swing highs/lows, HH/HL/LH/LL
labeling, BOS/CHoCH events and a per-timeframe trend state in
{BULLISH, BEARISH, NEUTRAL, RANGE, TRANSITION}.

Definitions used here (documented explicitly, since "BOS"/"CHoCH" have no single universal
definition):

- A swing high at bar i is CONFIRMED once `right` bars have closed after it (its high is the
  strict max over [i-left, i+right]) -- it cannot be known before then, and this engine never
  looks ahead of what a live system could actually have known at each point.
- BOS (break of structure) = the close crosses the current reference swing extreme IN THE
  DIRECTION OF the prevailing bias (continuation).
- CHoCH (change of character) = the close crosses the reference extreme AGAINST the prevailing
  bias (the first sign of a possible reversal).
- The very first break ever seen (bias starts NEUTRAL) establishes the initial bias and is
  labeled BOS, not CHoCH -- there is no prior trend for it to change.

This is a defensible, causal, and testable definition -- not a claim that it matches any one
named methodology exactly.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from intelligence.core.market_buffer import OHLCV
from intelligence.engines.common import atr as _atr

DEFAULT_LEFT = 2
DEFAULT_RIGHT = 2
RANGE_TIGHTNESS_ATR_MULT = 1.5   # swing-high-to-swing-low spread below this * ATR => RANGE
TRANSITION_WINDOW_BARS = 6       # a CHoCH this recent still labels the trend TRANSITION


@dataclass(frozen=True)
class Swing:
    index: int          # bar index within the OHLCV series passed in
    open_time: int
    kind: str            # "high" | "low"
    price: float
    label: str            # "HH" | "LH" | "HL" | "LL" | "" (first swing of its kind, unlabeled)
    confirmed_index: int  # index + right -- the bar at which this swing became knowable


@dataclass(frozen=True)
class StructureEvent:
    index: int
    kind: str        # "BOS" | "CHoCH"
    direction: str     # "up" | "down"


@dataclass(frozen=True)
class StructureState:
    open_time: int                       # timestamp of the last bar evaluated
    trend: str                             # BULLISH | BEARISH | NEUTRAL | RANGE | TRANSITION
    bias: str                              # BULLISH | BEARISH | NEUTRAL (raw, pre-RANGE/TRANSITION override)
    last_event: StructureEvent | None
    recent_swings: list[Swing] = field(default_factory=list)   # most recent few, newest last


def find_swings(ohlcv: OHLCV, left: int = DEFAULT_LEFT, right: int = DEFAULT_RIGHT) -> list[Swing]:
    n = len(ohlcv)
    swings: list[Swing] = []
    last_high_price: float | None = None
    last_low_price: float | None = None
    for i in range(left, n - right):
        window_h = ohlcv.high[i - left:i + right + 1]
        window_l = ohlcv.low[i - left:i + right + 1]
        h, l = ohlcv.high[i], ohlcv.low[i]
        if h == np.max(window_h) and np.sum(window_h == h) == 1:
            label = "" if last_high_price is None else ("HH" if h > last_high_price else "LH")
            swings.append(Swing(i, int(ohlcv.open_time[i]), "high", float(h), label, i + right))
            last_high_price = h
        if l == np.min(window_l) and np.sum(window_l == l) == 1:
            label = "" if last_low_price is None else ("HL" if l > last_low_price else "LL")
            swings.append(Swing(i, int(ohlcv.open_time[i]), "low", float(l), label, i + right))
            last_low_price = l
    swings.sort(key=lambda s: s.index)
    return swings


def structure_state(ohlcv: OHLCV, left: int = DEFAULT_LEFT, right: int = DEFAULT_RIGHT,
                    atr_period: int = 14) -> StructureState | None:
    n = len(ohlcv)
    if n == 0:
        return None
    swings = find_swings(ohlcv, left, right)
    highs = [s for s in swings if s.kind == "high"]
    lows = [s for s in swings if s.kind == "low"]

    bias = "NEUTRAL"
    ref_high: Swing | None = None
    ref_low: Swing | None = None
    hi_ptr = lo_ptr = 0
    last_event: StructureEvent | None = None

    for i in range(n):
        confirmed_by = i  # a swing is usable once ITS OWN confirmed_index <= i
        while hi_ptr < len(highs) and highs[hi_ptr].confirmed_index <= confirmed_by:
            ref_high = highs[hi_ptr]
            hi_ptr += 1
        while lo_ptr < len(lows) and lows[lo_ptr].confirmed_index <= confirmed_by:
            ref_low = lows[lo_ptr]
            lo_ptr += 1
        c = ohlcv.close[i]
        broke_up = ref_high is not None and c > ref_high.price
        broke_down = ref_low is not None and c < ref_low.price
        if broke_up and not broke_down:
            kind = "BOS" if bias in ("BULLISH", "NEUTRAL") else "CHoCH"
            bias = "BULLISH"
            last_event = StructureEvent(i, kind, "up")
            ref_high = None
        elif broke_down and not broke_up:
            kind = "BOS" if bias in ("BEARISH", "NEUTRAL") else "CHoCH"
            bias = "BEARISH"
            last_event = StructureEvent(i, kind, "down")
            ref_low = None
        # broke_up and broke_down simultaneously (a wide bar spanning both refs) is left as a
        # no-event tie rather than guessing a direction -- both refs stay in place and can
        # still trigger cleanly on a later, unambiguous bar

    trend = bias
    if bias == "NEUTRAL":
        atr_arr = _atr(ohlcv.high, ohlcv.low, ohlcv.close, atr_period)
        last_atr = atr_arr[-1] if len(atr_arr) and np.isfinite(atr_arr[-1]) else None
        recent_highs = [s.price for s in highs[-2:]]
        recent_lows = [s.price for s in lows[-2:]]
        if last_atr and recent_highs and recent_lows:
            spread = max(recent_highs) - min(recent_lows)
            trend = "RANGE" if spread < RANGE_TIGHTNESS_ATR_MULT * last_atr else "NEUTRAL"
    elif (last_event is not None and last_event.kind == "CHoCH"
          and (n - 1 - last_event.index) <= TRANSITION_WINDOW_BARS):
        trend = "TRANSITION"

    return StructureState(open_time=int(ohlcv.open_time[-1]), trend=trend, bias=bias,
                          last_event=last_event, recent_swings=swings[-8:])
