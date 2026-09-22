"""
EXP-124 -- confirmation engine.

RESEARCH / PAPER ONLY. Combines an EvidenceBundle with FeatureRegistry-measured weights into a
CONFIRMATION SCORE and a CONFIRM / WEAKEN / DEFER / INVALIDATE read relative to EXP-107's own
signal at that same moment. Per ARCHITECTURE_PLAN.md section 3.4 and the original brief section
28: this NEVER writes back into EXP-107 or changes what it reports -- it only ever attaches a
recommendation next to EXP-107's already-recorded signal (or its UNAVAILABLE / not-fired state).

"HIGH AGREEMENT != ENTRY", "VOLATILITY_HIGH != BUY" (brief sections 8, 10): this module does
not gate an entry -- see intelligence/core/exp107_signal.py's Exp107Signal.is_long_fire, the
only function permitted to answer that question. confirm() below is read purely as advisory
input to a later Decision Engine phase.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from intelligence.confirmation.evidence import EvidenceBundle, Strength
from intelligence.confirmation.feature_registry import FeatureRegistry
from intelligence.core.exp107_signal import Exp107Signal

_STRENGTH_SIGN = {
    Strength.STRONG_SUPPORT: 1.0, Strength.SUPPORT: 0.5, Strength.NEUTRAL: 0.0,
    Strength.CONFLICT: -1.0,
    Strength.UNKNOWN: 0.0,   # UNKNOWN NEVER counts as negative -- brief: "Do not convert
}                             # UNKNOWN into a negative signal."

CONFIRM_THRESHOLD = 0.5
WEAKEN_THRESHOLD = -0.25       # between WEAKEN_THRESHOLD and CONFIRM_THRESHOLD -> DEFER
INVALIDATE_THRESHOLD = -1.0


@dataclass(frozen=True)
class ConfirmationResult:
    open_time: int
    symbol: str
    exp107_status: str                # "OK" | "UNAVAILABLE" -- carried through for audit
    exp107_fired_long: bool
    confirmation_score: float           # weighted sum in evidence-units; unbounded, typically small
    label: str                            # CONFIRM | WEAKEN | DEFER | INVALIDATE | NO_SIGNAL
    n_evidence_considered: int
    n_unknown: int
    weighted_support: list[str] = field(default_factory=list)
    weighted_conflict: list[str] = field(default_factory=list)


def confirm(exp107: Exp107Signal, evidence: EvidenceBundle,
           registry: FeatureRegistry) -> ConfirmationResult:
    """Nothing here is meaningful unless EXP-107 actually fired a LONG signal at this moment --
    otherwise there is nothing to confirm, weaken, defer or invalidate, and that is reported as
    its own explicit label (NO_SIGNAL) rather than silently defaulting to DEFER, so a caller can
    tell "EXP-107 didn't fire" apart from "EXP-107 fired but the evidence is thin"."""
    if not exp107.is_long_fire:
        n_unknown = sum(1 for e in evidence.items if e.strength == Strength.UNKNOWN)
        return ConfirmationResult(evidence.open_time, evidence.symbol, exp107.status, False,
                                  0.0, "NO_SIGNAL", len(evidence.items), n_unknown)

    score = 0.0
    support: list[str] = []
    conflict: list[str] = []
    n_unknown = 0
    for e in evidence.items:
        if e.strength == Strength.UNKNOWN:
            n_unknown += 1
            continue
        weight = registry.weight_for(e.feature)
        contribution = _STRENGTH_SIGN[e.strength] * weight
        score += contribution
        label = f"{e.source}.{e.feature}={e.strength.value} (weight={weight:.2f})"
        if contribution > 0:
            support.append(label)
        elif contribution < 0:
            conflict.append(label)

    if score >= CONFIRM_THRESHOLD:
        label = "CONFIRM"
    elif score <= INVALIDATE_THRESHOLD:
        label = "INVALIDATE"
    elif score <= WEAKEN_THRESHOLD:
        label = "WEAKEN"
    else:
        label = "DEFER"

    return ConfirmationResult(evidence.open_time, evidence.symbol, exp107.status, True, score,
                              label, len(evidence.items), n_unknown, support, conflict)
