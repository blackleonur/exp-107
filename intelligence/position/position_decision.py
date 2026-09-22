"""
EXP-124 -- position decision (HOLD / REDUCE / EXIT recommendations, with hysteresis).

RESEARCH / PAPER ONLY. Emits recommendations for an already-open paper position, using the
confirmation engine's own CONFIRM/WEAKEN/DEFER/INVALIDATE read plus a consecutive-streak
hysteresis so a single noisy cycle never flips the recommendation on its own -- brief section
19: "confidence 0.61 -> 0.60 diye pozisyon kapatma."

EXP-107 itself has NO stop-loss/take-profit to move (CODEBASE_MAP.md section 2.4: exit is a
fixed 24-hour time exit only). This module therefore NEVER emits a MOVE_SL/MOVE_TP
recommendation as if such an order existed on the real system -- it only logs a research-only
`would_adjust_sl_tp_note`, explicitly labeled as such, per ARCHITECTURE_PLAN.md section 5's
named simplification.
"""
from __future__ import annotations

from dataclasses import dataclass

# Consecutive same-direction confirmation reads required before HOLD's default is overridden --
# the hysteresis brief section 19 asks for. Tuned conservatively (more cycles required to EXIT
# than to REDUCE) so a position is never closed on a single bad reading.
WEAKEN_STREAK_FOR_REDUCE = 3
INVALIDATE_STREAK_FOR_EXIT = 2


@dataclass
class _PositionDecisionState:
    symbol: str
    consecutive_weaken: int = 0
    consecutive_invalidate: int = 0
    last_recommendation: str = "HOLD"


@dataclass(frozen=True)
class PositionDecision:
    symbol: str
    recommendation: str          # HOLD | REDUCE | EXIT
    reason: str
    consecutive_weaken: int
    consecutive_invalidate: int
    would_adjust_sl_tp_note: str = ""   # RESEARCH ONLY -- EXP-107 has no real SL/TP to move


class PositionDecisionEngine:
    """One hysteresis counter per OPEN symbol, independent of every other symbol."""

    def __init__(self) -> None:
        self._states: dict[str, _PositionDecisionState] = {}

    def decide(self, symbol: str, confirmation_label: str,
              mfe_bp: float | None = None, mae_bp: float | None = None) -> PositionDecision:
        st = self._states.setdefault(symbol, _PositionDecisionState(symbol))

        if confirmation_label == "INVALIDATE":
            st.consecutive_invalidate += 1
            st.consecutive_weaken = 0
        elif confirmation_label == "WEAKEN":
            st.consecutive_weaken += 1
            st.consecutive_invalidate = 0
        else:
            # CONFIRM, DEFER, or NO_SIGNAL all reset both streaks -- only a run of genuinely
            # negative reads accumulates toward an action
            st.consecutive_weaken = 0
            st.consecutive_invalidate = 0

        sl_note = ""
        if st.consecutive_invalidate >= INVALIDATE_STREAK_FOR_EXIT:
            rec = "EXIT"
            reason = f"{st.consecutive_invalidate} consecutive INVALIDATE reads"
        elif st.consecutive_weaken >= WEAKEN_STREAK_FOR_REDUCE:
            rec = "REDUCE"
            reason = f"{st.consecutive_weaken} consecutive WEAKEN reads"
            if mfe_bp is not None and mfe_bp > 0:
                sl_note = (f"RESEARCH ONLY, not a real order: would tighten an equivalent stop "
                          f"toward {mfe_bp * 0.5:.1f}bp given MFE={mfe_bp:.1f}bp")
        else:
            rec = "HOLD"
            reason = (f"confirmation={confirmation_label}; weaken={st.consecutive_weaken}, "
                     f"invalidate={st.consecutive_invalidate}, below hysteresis threshold")

        st.last_recommendation = rec
        return PositionDecision(symbol, rec, reason, st.consecutive_weaken,
                                st.consecutive_invalidate, sl_note)

    def remove(self, symbol: str) -> None:
        self._states.pop(symbol, None)
