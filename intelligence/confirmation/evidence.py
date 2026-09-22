"""
EXP-124 -- typed evidence records.

RESEARCH / PAPER ONLY. An Evidence is one piece of information about a symbol at a point in
time, always attributable to the engine that produced it (`source`), always carrying a
STRENGTH label from the closed set {STRONG_SUPPORT, SUPPORT, NEUTRAL, CONFLICT, UNKNOWN} rather
than a raw number a caller might mistake for a calibrated probability.

Per the original brief sections 13/24 and ARCHITECTURE_PLAN.md section 3.4: an EvidenceBundle
is attached to EXP-107's own signal (or its UNAVAILABLE state) at that same timestamp -- it
never stands alone as a decision. "Do not convert UNKNOWN into a negative signal" (brief) is
enforced downstream in confirmation_engine.py, not here -- this module only carries the label.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Strength(str, Enum):
    STRONG_SUPPORT = "STRONG_SUPPORT"
    SUPPORT = "SUPPORT"
    NEUTRAL = "NEUTRAL"
    CONFLICT = "CONFLICT"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Evidence:
    source: str                    # producing engine, e.g. "volatility_engine"
    feature: str                    # feature/bucket name, e.g. "VOLATILITY_REGIME_HIGH"
    strength: Strength
    detail: str = ""                  # human-readable "why", for the decision journal
    value: float | None = None          # raw numeric value if applicable, kept for audit


@dataclass(frozen=True)
class EvidenceBundle:
    open_time: int
    symbol: str
    items: list[Evidence] = field(default_factory=list)

    def by_strength(self, strength: Strength) -> list[Evidence]:
        return [e for e in self.items if e.strength == strength]

    def counts(self) -> dict[str, int]:
        out = {s.value: 0 for s in Strength}
        for e in self.items:
            out[e.strength.value] += 1
        return out
