"""
EXP-124 -- independent direction estimator (SECONDARY evidence only).

RESEARCH / PAPER ONLY. This module is explicitly a SIMPLE, DOCUMENTED, RULE-BASED heuristic --
a multi-timeframe majority vote over already-computed structure/momentum reads. It is NOT a
trained machine-learning model, it does NOT reproduce any EXP-108->123 model (their code and
data are not in this repository -- CODEBASE_MAP.md Blocker #1), and it NEVER claims to
reproduce or substitute for EXP-107's own signal (scripts/shadow_engine.py). Per
ARCHITECTURE_PLAN.md section 0's hard invariant, EXP-107 remains the only thing that can ever
authorize an ENTER decision; this engine only ever produces SECONDARY, SUPPORTING evidence for
the confirmation layer (a later phase) to weigh.

Its own hit-rate is unmeasured until decision_memory (a later phase) has tracked enough
same-condition outcomes -- this module makes no accuracy claim about itself anywhere.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from intelligence.core.market_buffer import MarketBuffer
from intelligence.engines.cross_sectional_engine import direction_from_return
from intelligence.engines.regime_engine import momentum_vs_mean_reversion
from intelligence.engines.structure_engine import structure_state

MIN_TIMEFRAMES_FOR_MEASURED = 3   # below this many measured timeframes, confidence is capped


@dataclass(frozen=True)
class TimeframeRead:
    timeframe_min: int
    direction: int | None       # +1 / -1 / 0 / None (unmeasurable at this timeframe)
    trend_state: str | None      # from structure_engine, or None if unmeasurable
    momentum_state: str | None    # from regime_engine, or None if unmeasurable


@dataclass(frozen=True)
class DirectionEstimate:
    open_time: int
    symbol: str
    direction: str                   # LONG | SHORT | NEUTRAL | UNKNOWN
    confidence: float                 # 0..1, NaN if UNKNOWN
    timeframe_agreement: float         # fraction of MEASURED timeframes agreeing with the majority, NaN if none measured
    uncertainty: float                  # 1 - confidence (NaN if UNKNOWN) -- kept as its own field so callers never have to derive it
    supporting_factors: list[str] = field(default_factory=list)
    conflicting_factors: list[str] = field(default_factory=list)
    n_timeframes_measured: int = 0
    n_timeframes_total: int = 0
    timeframe_reads: list[TimeframeRead] = field(default_factory=list)


_DIR_LABEL = {1: "LONG", -1: "SHORT", 0: "NEUTRAL"}


def _read_timeframe(buffer: MarketBuffer, symbol: str, timeframe_min: int,
                    return_horizon_bars: int, momentum_window: int) -> TimeframeRead:
    ohlcv = buffer.resample(symbol, timeframe_min)
    if len(ohlcv) == 0:
        return TimeframeRead(timeframe_min, None, None, None)
    d = direction_from_return(ohlcv, return_horizon_bars)
    struct = structure_state(ohlcv)
    trend = struct.trend if struct else None
    momentum, _ac = momentum_vs_mean_reversion(ohlcv.close, momentum_window)
    return TimeframeRead(timeframe_min, d, trend, momentum)


def estimate(buffer: MarketBuffer, symbol: str, timeframes_min: list[int],
            return_horizon_bars: int = 1, momentum_window: int = 30) -> DirectionEstimate:
    open_time = int(buffer.grid[-1]) if len(buffer.grid) else 0
    reads = [_read_timeframe(buffer, symbol, tf, return_horizon_bars, momentum_window)
             for tf in timeframes_min]
    measured = [r for r in reads if r.direction is not None]
    n_total, n_measured = len(reads), len(measured)

    if n_measured == 0:
        return DirectionEstimate(
            open_time=open_time, symbol=symbol, direction="UNKNOWN", confidence=float("nan"),
            timeframe_agreement=float("nan"), uncertainty=float("nan"),
            supporting_factors=[], conflicting_factors=[],
            n_timeframes_measured=0, n_timeframes_total=n_total, timeframe_reads=reads)

    n_long = sum(1 for r in measured if r.direction > 0)
    n_short = sum(1 for r in measured if r.direction < 0)
    n_flat = sum(1 for r in measured if r.direction == 0)
    total_directional = n_long + n_short

    if total_directional == 0:
        majority = "NEUTRAL"
        agreement = n_flat / n_measured
    else:
        majority_count = max(n_long, n_short)
        agreement = majority_count / n_measured
        majority = "LONG" if n_long > n_short else ("SHORT" if n_short > n_long else "NEUTRAL")

    # sample-size penalty: fewer measured timeframes -> capped confidence, never inflated by a
    # small, possibly-noisy sample
    sample_factor = min(1.0, n_measured / MIN_TIMEFRAMES_FOR_MEASURED)
    confidence = agreement * sample_factor

    support: list[str] = []
    conflict: list[str] = []
    majority_sign = {"LONG": 1, "SHORT": -1, "NEUTRAL": 0}[majority]
    expected_trend = {"LONG": "BULLISH", "SHORT": "BEARISH"}.get(majority)
    for r in measured:
        tf_label = f"{r.timeframe_min}m"
        dir_label = _DIR_LABEL[r.direction]
        if majority != "NEUTRAL" and r.direction == majority_sign:
            support.append(f"{tf_label}: direction={dir_label}")
            if r.trend_state and r.trend_state == expected_trend:
                support.append(f"{tf_label}: structure trend={r.trend_state} (agrees)")
            elif r.trend_state and r.trend_state in ("BULLISH", "BEARISH") and r.trend_state != expected_trend:
                conflict.append(f"{tf_label}: structure trend={r.trend_state} (disagrees with direction)")
        elif majority != "NEUTRAL" and r.direction == -majority_sign:
            conflict.append(f"{tf_label}: direction={dir_label} (opposes majority)")
        if r.momentum_state == "MEAN_REVERSION":
            conflict.append(f"{tf_label}: momentum_state=MEAN_REVERSION "
                            "(a continuation read is weaker in a mean-reverting regime)")
        elif r.momentum_state == "MOMENTUM":
            support.append(f"{tf_label}: momentum_state=MOMENTUM (supports continuation)")

    if n_measured < n_total:
        conflict.append(f"only {n_measured}/{n_total} timeframes had enough history to measure "
                        "-- confidence capped for sample size")

    return DirectionEstimate(
        open_time=open_time, symbol=symbol, direction=majority, confidence=confidence,
        timeframe_agreement=agreement, uncertainty=1.0 - confidence,
        supporting_factors=support, conflicting_factors=conflict,
        n_timeframes_measured=n_measured, n_timeframes_total=n_total, timeframe_reads=reads)
