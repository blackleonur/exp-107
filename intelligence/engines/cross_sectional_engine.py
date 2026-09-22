"""
EXP-124 -- cross-sectional agreement engine.

RESEARCH / PAPER ONLY. Pure functions over a dict of per-symbol direction reads -> an agreement
ratio and a symbol-vs-market alignment read.

Per the original brief section 10 and ARCHITECTURE_PLAN.md section 3.6: EXP-123 flagged HIGH
cross-sectional agreement as one of its (still modest, still not cost-positive) conditions.
That motivates tracking this evidence -- `HIGH AGREEMENT != ENTRY` is enforced structurally
here: this module never returns anything resembling a trade decision, only a ratio and a label
that a later confirmation engine can weigh. The brief also asks that symbol-specific direction
be kept explicitly separate from market-wide direction -- `symbol_alignment()` below is the
one function that relates the two, and it never collapses a symbol's own read into the market
majority.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from intelligence.core.market_buffer import OHLCV

DIRECTIONS = {1: "LONG", -1: "SHORT", 0: "FLAT"}


@dataclass(frozen=True)
class CrossSectionalSnapshot:
    open_time: int | None
    per_symbol_direction: dict[str, int] = field(default_factory=dict)  # +1 / -1 / 0
    n_long: int = 0
    n_short: int = 0
    n_flat: int = 0
    agreement_ratio: float = float("nan")   # majority_count / (n_long + n_short); NaN if both zero
    majority_direction: str = "NEUTRAL"       # LONG | SHORT | NEUTRAL


def direction_from_return(ohlcv: OHLCV, horizon_bars: int) -> int | None:
    """+1 / -1 / 0 from the sign of the close-to-close return over `horizon_bars`. None if the
    series doesn't hold enough history to measure it -- never defaulted to 0 in that case, since
    0 already means a genuinely measured flat/unchanged read."""
    n = len(ohlcv)
    if n <= horizon_bars:
        return None
    prior, cur = float(ohlcv.close[n - 1 - horizon_bars]), float(ohlcv.close[n - 1])
    if not (np.isfinite(prior) and np.isfinite(cur)):
        return None
    if cur > prior:
        return 1
    if cur < prior:
        return -1
    return 0


def cross_sectional_state(per_symbol_direction: dict[str, int | None],
                          open_time: int | None = None) -> CrossSectionalSnapshot:
    measured = {s: d for s, d in per_symbol_direction.items() if d is not None}
    n_long = sum(1 for d in measured.values() if d > 0)
    n_short = sum(1 for d in measured.values() if d < 0)
    n_flat = sum(1 for d in measured.values() if d == 0)
    total_directional = n_long + n_short
    if total_directional == 0:
        ratio, majority = float("nan"), "NEUTRAL"
    else:
        majority_count = max(n_long, n_short)
        ratio = majority_count / total_directional
        majority = "LONG" if n_long > n_short else ("SHORT" if n_short > n_long else "NEUTRAL")
    return CrossSectionalSnapshot(open_time=open_time, per_symbol_direction=dict(measured),
                                  n_long=n_long, n_short=n_short, n_flat=n_flat,
                                  agreement_ratio=ratio, majority_direction=majority)


def symbol_alignment(symbol: str, snapshot: CrossSectionalSnapshot) -> str:
    """ALIGNED / DIVERGENT / NEUTRAL / UNKNOWN -- how this ONE symbol's own direction compares
    to the market-wide majority. This is the only place symbol-specific and market-wide reads
    are related to each other; the symbol's own direction is never overwritten by this."""
    d = snapshot.per_symbol_direction.get(symbol)
    if d is None:
        return "UNKNOWN"
    if snapshot.majority_direction == "NEUTRAL" or d == 0:
        return "NEUTRAL"
    symbol_label = DIRECTIONS[1 if d > 0 else -1]
    return "ALIGNED" if symbol_label == snapshot.majority_direction else "DIVERGENT"
