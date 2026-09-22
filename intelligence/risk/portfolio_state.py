"""
EXP-124 -- paper portfolio state.

RESEARCH / PAPER ONLY. NO REAL MONEY, NO REAL BALANCE, NO REAL MARGIN -- every number here is
simulated bookkeeping for the new intelligence layer's own paper positions, mirroring (never
touching) the pattern EXP-107's own shadow runner already uses for a single symbol
(scripts/R3_shadow_run.py's `POSITION_SIZE_USD`, explicitly "notional, for reporting only -- no
money moves" per that module's own comment), extended here across the whole book. No leverage
is modeled anywhere in this module -- EXP-107 itself uses none (CODEBASE_MAP.md section 2.5),
and this layer introduces none either.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PaperPosition:
    symbol: str
    direction: str        # "LONG" -- EXP-107 is LONG-only (CODEBASE_MAP.md section 2.2)
    notional_usd: float
    entry_time: int
    entry_price: float


@dataclass
class PortfolioState:
    starting_balance_usd: float
    positions: dict[str, PaperPosition] = field(default_factory=dict)

    @property
    def open_notional_usd(self) -> float:
        return sum(p.notional_usd for p in self.positions.values())

    @property
    def available_margin_usd(self) -> float:
        """No leverage is modeled: available margin is simply starting balance minus notional
        already committed, 1:1."""
        return self.starting_balance_usd - self.open_notional_usd

    @property
    def n_open(self) -> int:
        return len(self.positions)

    def open_position(self, position: PaperPosition) -> None:
        if position.symbol in self.positions:
            raise ValueError(f"{position.symbol} already has an open paper position")
        if position.notional_usd > self.available_margin_usd:
            raise ValueError(
                f"insufficient available margin for {position.symbol}: need "
                f"{position.notional_usd}, have {self.available_margin_usd}")
        self.positions[position.symbol] = position

    def close_position(self, symbol: str) -> PaperPosition | None:
        return self.positions.pop(symbol, None)

    def direction_exposure_usd(self, direction: str) -> float:
        return sum(p.notional_usd for p in self.positions.values() if p.direction == direction)

    def symbol_exposure_usd(self, symbol: str) -> float:
        p = self.positions.get(symbol)
        return p.notional_usd if p else 0.0

    def correlated_exposure_usd(self, symbol: str, correlations: dict[str, float],
                                threshold: float = 0.6) -> float:
        """Sum of notional in OTHER open positions whose |correlation| to `symbol` is >=
        `threshold`. `correlations`: {other_symbol: correlation_to_symbol}, e.g. from
        regime_engine.btc_correlations() or a future pairwise-correlation read. A symbol with
        no measured correlation contributes 0.0 -- never guessed, never assumed correlated."""
        total = 0.0
        for other_symbol, pos in self.positions.items():
            if other_symbol == symbol:
                continue
            corr = correlations.get(other_symbol)
            if corr is not None and abs(corr) >= threshold:
                total += pos.notional_usd
        return total
