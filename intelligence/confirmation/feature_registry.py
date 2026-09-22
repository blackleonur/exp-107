"""
EXP-124 -- feature weighting / out-of-sample validation registry.

RESEARCH / PAPER ONLY. Tracks measured out-of-sample hit rate, sample size, and a simple
tail-dependency proxy for each named feature bucket (e.g. "VOLATILITY_REGIME_HIGH",
"MAG_4H_HIGH"), computed ONLY from observations this system has itself recorded -- never a
number carried over from EXP-108->123 (ARCHITECTURE_PLAN.md section 3.6; the underlying data
for those experiments is not in this repository, CODEBASE_MAP.md Blocker #1). A feature's
confidence starts UNSET and is only promoted to LOW/MEDIUM/HIGH once it has enough tracked
same-condition outcomes with a measured edge over a coin flip AND a tail-dependency ratio below
the threshold below -- this is the mechanism that answers the original brief's actual question
(section 27): "does this condition add incremental information on top of EXP-107's own signal",
from THIS system's own data, not by assumption. It is also the mechanism that guards against
repeating EXP-120's failure mode (apparent improvement that did not survive EXP-121's
independent replication): a feature whose apparent edge is dominated by one or two extreme
observations is capped at LOW regardless of its raw hit rate or sample size.

Deliberately decoupled from intelligence/memory/ (a later phase): this module consumes a plain
list of Outcome records, however they were sourced, so it is fully unit-testable today without
depending on a database schema that doesn't exist yet.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np


class Confidence(str, Enum):
    UNSET = "UNSET"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


MIN_N_FOR_LOW = 20
MIN_N_FOR_MEDIUM = 60
MIN_N_FOR_HIGH = 150
# a feature must beat a coin flip by at least this much, on its own tracked data, before
# promotion above LOW -- never assumed, always measured from decision_memory outcomes
MIN_EDGE_FOR_MEDIUM = 0.03
MIN_EDGE_FOR_HIGH = 0.05
# share of the total realized magnitude among CORRECT calls coming from the single largest one
# -- at/above this, the apparent edge is treated as tail-dependent and capped at LOW regardless
# of n or hit rate
TAIL_DEPENDENCY_SHARE_THRESHOLD = 0.5


@dataclass(frozen=True)
class Outcome:
    """One tracked, resolved observation: did the direction this feature bucket supported turn
    out correct, and how large was the resulting move (for tail-dependency measurement)."""
    feature: str
    correct: bool
    magnitude_bp: float = 0.0
    symbol: str = ""
    regime: str = ""


@dataclass(frozen=True)
class FeatureStats:
    feature: str
    n: int
    hit_rate: float                    # NaN if n==0
    edge_vs_coin_flip: float             # hit_rate - 0.5, NaN if n==0
    tail_dependency_ratio: float           # NaN if n==0 or nothing to measure
    confidence: Confidence
    n_symbols: int
    n_regimes: int


def tail_dependency_ratio(magnitudes: list[float]) -> float:
    """The largest single |magnitude|'s share of the total -- a simple, honest proxy for
    'is this apparent edge actually driven by one or two extreme observations'. NaN if there is
    nothing to measure (empty, or every magnitude is exactly zero)."""
    if not magnitudes:
        return float("nan")
    abs_mags = [abs(m) for m in magnitudes]
    total = sum(abs_mags)
    if total <= 0:
        return float("nan")
    return max(abs_mags) / total


def _confidence_for(n: int, edge: float, tail_ratio: float) -> Confidence:
    if n < MIN_N_FOR_LOW:
        return Confidence.UNSET
    if not np.isfinite(tail_ratio) or tail_ratio >= TAIL_DEPENDENCY_SHARE_THRESHOLD:
        return Confidence.LOW
    if n >= MIN_N_FOR_HIGH and edge >= MIN_EDGE_FOR_HIGH:
        return Confidence.HIGH
    if n >= MIN_N_FOR_MEDIUM and edge >= MIN_EDGE_FOR_MEDIUM:
        return Confidence.MEDIUM
    return Confidence.LOW


class FeatureRegistry:
    """Holds no hidden state beyond the outcome list it was built from -- trivially safe to
    rebuild every cycle from decision_memory (a later phase) rather than mutated in place."""

    def __init__(self, outcomes: list[Outcome]) -> None:
        self._outcomes = list(outcomes)

    def stats_for(self, feature: str) -> FeatureStats:
        rows = [o for o in self._outcomes if o.feature == feature]
        n = len(rows)
        if n == 0:
            return FeatureStats(feature, 0, float("nan"), float("nan"), float("nan"),
                                Confidence.UNSET, 0, 0)
        hit_rate = sum(1 for o in rows if o.correct) / n
        edge = hit_rate - 0.5
        correct_magnitudes = [o.magnitude_bp for o in rows if o.correct]
        tail_ratio = tail_dependency_ratio(correct_magnitudes)
        confidence = _confidence_for(n, edge, tail_ratio)
        n_symbols = len({o.symbol for o in rows if o.symbol})
        n_regimes = len({o.regime for o in rows if o.regime})
        return FeatureStats(feature, n, hit_rate, edge, tail_ratio, confidence, n_symbols,
                            n_regimes)

    def all_stats(self) -> dict[str, FeatureStats]:
        features = {o.feature for o in self._outcomes}
        return {f: self.stats_for(f) for f in features}

    def weight_for(self, feature: str) -> float:
        """0.0 (UNSET/LOW) / 0.5 (MEDIUM) / 1.0 (HIGH) -- an explicit, auditable mapping, never
        a hand-tuned per-feature constant. A feature this registry has never seen weighs 0.0,
        the same as one it HAS seen but found unproven -- absence of evidence is not evidence."""
        c = self.stats_for(feature).confidence
        return {Confidence.UNSET: 0.0, Confidence.LOW: 0.0, Confidence.MEDIUM: 0.5,
                Confidence.HIGH: 1.0}[c]
