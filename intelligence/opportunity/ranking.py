"""
EXP-124 -- opportunity ranking.

RESEARCH / PAPER ONLY. When multiple CONFIRMED/CANDIDATE opportunities exist at the same
cycle, ranks them by confirmation strength net of an optional portfolio-risk penalty -- never
by raw historical accuracy alone (original brief sections 12, 24: "IS IT COST-SENSITIVE?").
Does NOT decide whether to actually enter anything; that is decision_engine's job (a later
phase). It only orders candidates so a downstream decision has a defensible "which one first"
answer when capital or risk limits mean not everything can be acted on at once.
"""
from __future__ import annotations

from dataclasses import dataclass

# Same constant EXP-107's own shadow runner uses (scripts/R3_shadow_run.py's COST_BP), kept
# identical for comparability -- not re-derived, not re-measured here.
DEFAULT_COST_BP = 14.38


@dataclass(frozen=True)
class RankedOpportunity:
    symbol: str
    rank: int                     # 1 = highest priority
    confirmation_score: float
    net_score: float                # confirmation_score - risk_penalty (same evidence-score units)
    cost_bp: float                    # carried for the record / tie-break only -- see module note
    risk_penalty: float
    reason: str


def rank_opportunities(candidates: dict[str, float], cost_bp: float = DEFAULT_COST_BP,
                       risk_penalty: dict[str, float] | None = None) -> list[RankedOpportunity]:
    """`candidates`: {symbol: confirmation_score}, e.g. from confirmation_engine.confirm()
    results for every symbol currently CONFIRMED or CANDIDATE this cycle. `risk_penalty`: an
    optional {symbol: penalty}, in the SAME evidence-score units as confirmation_score (e.g.
    from a portfolio/correlation read supplied by a later Risk Engine phase) -- any symbol not
    given one defaults to 0.0, never a fabricated penalty.

    `cost_bp` is expressed in price-move basis points, a DIFFERENT unit from confirmation_score
    -- it is never implicitly summed into net_score. It is kept on each result and used only as
    a secondary sort key (prefer the cheaper symbol among otherwise-equal net scores), so a
    caller has cost visibility without this function pretending to know the cost/evidence
    exchange rate."""
    risk_penalty = risk_penalty or {}
    rows = []
    for symbol, score in candidates.items():
        penalty = risk_penalty.get(symbol, 0.0)
        net = score - penalty
        rows.append((symbol, score, net, cost_bp, penalty))
    rows.sort(key=lambda r: (-r[2], r[3], r[0]))  # highest net first, then lowest cost, then symbol name for determinism

    out = []
    for i, (symbol, score, net, cost, penalty) in enumerate(rows):
        reason = f"confirmation_score={score:.2f}"
        if penalty:
            reason += f", risk_penalty=-{penalty:.2f}"
        out.append(RankedOpportunity(symbol, i + 1, score, net, cost, penalty, reason))
    return out
