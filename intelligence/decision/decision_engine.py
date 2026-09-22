"""
EXP-124 -- final decision engine.

RESEARCH / PAPER ONLY. Combines EXP-107's own signal (via `Exp107Signal.is_long_fire`),
`confirmation_engine`'s CONFIRM/WEAKEN/DEFER/INVALIDATE/NO_SIGNAL read, open-position state,
`position_decision`'s hysteresis-gated recommendation, and `risk_engine`'s assessment into one
final action in {ENTER, HOLD, WAIT, EXIT, REDUCE, IGNORE}, each carrying PRIMARY_REASON,
SUPPORTING_EVIDENCE, CONTRADICTING_EVIDENCE, RISK, CONFIDENCE, ALTERNATIVE_ACTION (original
brief section 23).

**The hard invariant** (ARCHITECTURE_PLAN.md section 0): ENTER is reachable ONLY when
`exp107.is_long_fire` is True AND the confirmation label is CONFIRM. This is enforced
structurally below -- every return path is examined in order, and ENTER appears in exactly one
branch, gated directly on `exp107.is_long_fire`. With the trained model artifact currently
UNAVAILABLE (CODEBASE_MAP.md Blocker #2), `is_long_fire` is never True anywhere in this
repository's current state, so ENTER is never reachable today. That is the correct, honest
behavior -- not a bug to route around, and not something a future change to this file should
quietly loosen.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from intelligence.confirmation.confirmation_engine import ConfirmationResult
from intelligence.core.exp107_signal import Exp107Signal
from intelligence.risk.risk_engine import RiskAssessment

RISK_HIGH_THRESHOLD = 0.7
RISK_MEDIUM_THRESHOLD = 0.3


@dataclass(frozen=True)
class Decision:
    symbol: str
    open_time: int
    action: str                          # ENTER | HOLD | WAIT | EXIT | REDUCE | IGNORE
    primary_reason: str
    supporting_evidence: list[str] = field(default_factory=list)
    contradicting_evidence: list[str] = field(default_factory=list)
    risk: str = "UNKNOWN"                   # LOW | MEDIUM | HIGH | UNKNOWN -- a label, never a raw number here
    confidence: float = float("nan")
    alternative_action: str = ""


def risk_label(risk: RiskAssessment | None) -> str:
    if risk is None:
        return "UNKNOWN"
    if risk.risk_penalty >= RISK_HIGH_THRESHOLD:
        return "HIGH"
    if risk.risk_penalty >= RISK_MEDIUM_THRESHOLD:
        return "MEDIUM"
    return "LOW"


def decide(symbol: str, open_time: int, exp107: Exp107Signal, confirmation: ConfirmationResult,
          has_open_position: bool, position_recommendation: str | None = None,
          risk: RiskAssessment | None = None) -> Decision:
    r_label = risk_label(risk)
    support, conflict = confirmation.weighted_support, confirmation.weighted_conflict

    if has_open_position:
        if position_recommendation == "EXIT":
            return Decision(symbol, open_time, "EXIT",
                            "position decision engine reached its EXIT hysteresis threshold",
                            support, conflict, r_label, confirmation.confirmation_score,
                            "none -- position is being closed")
        if position_recommendation == "REDUCE":
            return Decision(symbol, open_time, "REDUCE",
                            "position decision engine reached its REDUCE hysteresis threshold",
                            support, conflict, r_label, confirmation.confirmation_score, "HOLD")
        return Decision(symbol, open_time, "HOLD",
                        "position open, no exit/reduce hysteresis threshold reached",
                        support, conflict, r_label, confirmation.confirmation_score,
                        "EXIT if evidence continues to weaken")

    if not exp107.is_long_fire:
        return Decision(symbol, open_time, "IGNORE",
                        "EXP-107 has not fired a LONG signal for this symbol this cycle",
                        [], [], r_label, float("nan"), "continue WATCHING next cycle")

    if confirmation.label == "CONFIRM":
        if r_label == "HIGH":
            return Decision(symbol, open_time, "WAIT",
                            "EXP-107 fired and confirmation is CONFIRM, but portfolio risk "
                            "(correlated exposure) is HIGH",
                            support, conflict, r_label, confirmation.confirmation_score,
                            "ENTER once correlated exposure decreases")
        return Decision(symbol, open_time, "ENTER",
                        "EXP-107 LONG fire with CONFIRM-level multi-engine confirmation and "
                        "risk within bounds",
                        support, conflict, r_label, confirmation.confirmation_score,
                        "WAIT if risk worsens before this decision is acted on")

    if confirmation.label == "INVALIDATE":
        return Decision(symbol, open_time, "IGNORE",
                        "EXP-107 fired but supporting evidence INVALIDATEs the signal",
                        support, conflict, r_label, confirmation.confirmation_score,
                        "re-evaluate fresh next cycle")

    # WEAKEN or DEFER: EXP-107 fired, but not confirmed enough to ENTER
    return Decision(symbol, open_time, "WAIT",
                    f"EXP-107 fired but confirmation is only {confirmation.label}",
                    support, conflict, r_label, confirmation.confirmation_score,
                    "ENTER if confirmation strengthens to CONFIRM")
