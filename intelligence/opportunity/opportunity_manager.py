"""
EXP-124 -- opportunity manager.

RESEARCH / PAPER ONLY. One INDEPENDENT state machine PER SYMBOL. Per the original brief
sections 15/16: a signal on one symbol is never dropped just because another symbol already has
an open (paper) position -- "Sistem sadece mevcut açık işlemi izleyip diğerlerini
unutmayacak... Her fırsatı bağımsız değerlendirecek." `OpportunityManager` below holds one
`SymbolOpportunity` per symbol and updates each independently every cycle; nothing here ever
skips a symbol because another symbol's state changed.

States: WATCHING, CANDIDATE, CONFIRMED, OPEN, WEAKENING, EXIT_CANDIDATE, INVALIDATED. The
transition rules are in `transition()` below, not implied anywhere else.

This module tracks ONLY the new intelligence layer's own paper-side state. It never reads or
writes EXP-107's own shadow_trades table (that stays intelligence/core/exp107_signal.py's
read-only job) and places no order of any kind -- `has_open_position` is an input this module
receives from a caller (a later Position Monitor phase), never something it decides on its own.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class OpportunityState(str, Enum):
    WATCHING = "WATCHING"
    CANDIDATE = "CANDIDATE"
    CONFIRMED = "CONFIRMED"
    OPEN = "OPEN"
    WEAKENING = "WEAKENING"
    EXIT_CANDIDATE = "EXIT_CANDIDATE"
    INVALIDATED = "INVALIDATED"


WEAKENING_LABELS = {"WEAKEN"}
EXIT_LABELS = {"INVALIDATE"}


@dataclass
class SymbolOpportunity:
    symbol: str
    state: OpportunityState = OpportunityState.WATCHING
    entered_state_at: int | None = None      # open_time this state was entered
    cycles_in_state: int = 0
    last_confirmation_label: str | None = None
    last_confirmation_score: float = 0.0
    # (open_time, from_state, to_state) for every transition, oldest first -- append-only,
    # never rewritten (mirrors the "do not allow future decisions to overwrite historical
    # decisions" rule this layer applies everywhere, not just in decision_memory)
    history: list[tuple[int, str, str]] = field(default_factory=list)


def _next_state(current: OpportunityState, exp107_fired_long: bool, confirmation_label: str,
                has_open_position: bool) -> OpportunityState:
    if has_open_position:
        if confirmation_label in EXIT_LABELS:
            return OpportunityState.EXIT_CANDIDATE
        if confirmation_label in WEAKENING_LABELS:
            return OpportunityState.WEAKENING
        return OpportunityState.OPEN

    if not exp107_fired_long:
        # INVALIDATED is a transient label attached to one firing episode, not a sticky
        # terminal state -- once EXP-107 stops firing and there is no open position, the
        # symbol always returns to plain WATCHING, ready to be evaluated fresh next time.
        return OpportunityState.WATCHING

    if confirmation_label in EXIT_LABELS:
        return OpportunityState.INVALIDATED
    if confirmation_label == "CONFIRM":
        return OpportunityState.CONFIRMED
    return OpportunityState.CANDIDATE   # DEFER or WEAKEN, but EXP-107 is still firing


class OpportunityManager:
    def __init__(self) -> None:
        self._opps: dict[str, SymbolOpportunity] = {}

    def get(self, symbol: str) -> SymbolOpportunity:
        if symbol not in self._opps:
            self._opps[symbol] = SymbolOpportunity(symbol=symbol)
        return self._opps[symbol]

    def all(self) -> dict[str, SymbolOpportunity]:
        return dict(self._opps)

    def update(self, symbol: str, exp107_fired_long: bool, confirmation_label: str,
              confirmation_score: float, has_open_position: bool,
              open_time: int) -> SymbolOpportunity:
        """Advances exactly ONE symbol's state machine by one cycle. Independent of every other
        symbol's state -- calling this for BTCUSDT never reads or changes ETHUSDT's entry."""
        opp = self.get(symbol)
        new_state = _next_state(opp.state, exp107_fired_long, confirmation_label,
                                has_open_position)
        if new_state != opp.state:
            opp.history.append((open_time, opp.state.value, new_state.value))
            opp.state = new_state
            opp.entered_state_at = open_time
            opp.cycles_in_state = 1
        else:
            opp.cycles_in_state += 1
        opp.last_confirmation_label = confirmation_label
        opp.last_confirmation_score = confirmation_score
        return opp
