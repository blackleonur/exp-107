"""
EXP-124 -- open (paper) position monitor.

RESEARCH / PAPER ONLY. Tracks the new intelligence layer's OWN paper positions (from
intelligence/risk/portfolio_state.py's PaperPosition) against live price -- unrealized PnL,
MFE, MAE, duration -- entirely independent of and never touching EXP-107's own shadow_trades
table (that stays intelligence/core/exp107_signal.py's read-only concern). This module places
no order and closes nothing itself; it only produces a live, incrementally-updated snapshot for
position_decision.py (this phase) and the eventual Decision Engine to read.

MFE/MAE are tracked INCREMENTALLY (one running max/min per symbol, updated on each call), never
recomputed from a re-fetched price history each cycle -- matching the brief's "cached rolling
data, incremental calculations, change detection" instruction for the 10-15s loop (a later
phase).
"""
from __future__ import annotations

from dataclasses import dataclass

from intelligence.risk.portfolio_state import PaperPosition


@dataclass(frozen=True)
class PositionSnapshot:
    symbol: str
    entry_time: int
    entry_price: float
    current_time: int
    current_price: float
    unrealized_pnl_bp: float
    mfe_bp: float
    mae_bp: float
    n_updates: int


class PositionMonitor:
    """One incrementally-tracked path per OPEN symbol. Call `update()` every cycle for every
    currently-open symbol; call `remove()` once a position is closed elsewhere so its tracked
    MFE/MAE state doesn't leak into a later, unrelated position on the same symbol."""

    def __init__(self) -> None:
        self._mfe: dict[str, float] = {}
        self._mae: dict[str, float] = {}
        self._n_updates: dict[str, int] = {}

    def update(self, position: PaperPosition, current_time: int,
              current_price: float) -> PositionSnapshot:
        symbol = position.symbol
        pnl_bp = ((current_price / position.entry_price - 1.0) * 1e4
                  if position.entry_price else float("nan"))
        if symbol not in self._n_updates:
            self._mfe[symbol] = pnl_bp
            self._mae[symbol] = pnl_bp
            self._n_updates[symbol] = 0
        else:
            self._mfe[symbol] = max(self._mfe[symbol], pnl_bp)
            self._mae[symbol] = min(self._mae[symbol], pnl_bp)
        self._n_updates[symbol] += 1
        return PositionSnapshot(
            symbol=symbol, entry_time=position.entry_time, entry_price=position.entry_price,
            current_time=current_time, current_price=current_price, unrealized_pnl_bp=pnl_bp,
            mfe_bp=self._mfe[symbol], mae_bp=self._mae[symbol],
            n_updates=self._n_updates[symbol])

    def remove(self, symbol: str) -> None:
        self._mfe.pop(symbol, None)
        self._mae.pop(symbol, None)
        self._n_updates.pop(symbol, None)

    def is_tracking(self, symbol: str) -> bool:
        return symbol in self._n_updates
