"""
EXP-124 -- per-opportunity risk engine.

RESEARCH / PAPER ONLY. For each candidate opportunity, estimates expected reward, an
SL-EQUIVALENT loss estimate (EXP-107 itself has NO real stop-loss -- CODEBASE_MAP.md section
2.4, exit is fixed-time only -- so this is explicitly an ESTIMATE FOR RANKING PURPOSES ONLY,
never a real order parameter and never presented as one), correlated exposure, portfolio-level
context, and a cost-aware net expectancy. Risk management is kept SEPARATE from signal
generation throughout this codebase: nothing in this module can turn a low-risk read into a
buy/sell signal -- it only ever feeds intelligence/opportunity/ranking.py's `risk_penalty`
input and the eventual Decision Engine's RISK field.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from intelligence.opportunity.ranking import DEFAULT_COST_BP
from intelligence.risk.portfolio_state import PortfolioState

# An ESTIMATE for ranking only -- EXP-107 has no real stop-loss to multiply an ATR against.
DEFAULT_SL_EQUIVALENT_ATR_MULT = 1.5
# A simple, documented, auditable scale from confirmation_score (evidence-score units,
# typically in [-2, 2]) to an expected-reward PROXY in basis points. This is NOT a calibrated
# return forecast -- no such calibration exists in this repository (CODEBASE_MAP.md Blocker
# #1) -- it exists only to give ranking a directionally-sensible, cost-aware number, and is
# labeled as a proxy everywhere it is used.
EXPECTED_REWARD_BP_PER_SCORE_UNIT = 10.0


@dataclass(frozen=True)
class RiskAssessment:
    symbol: str
    expected_reward_bp: float          # a PROXY, see module docstring; NaN if unmeasurable
    estimated_sl_loss_bp: float          # an ESTIMATE for ranking only, never a real SL order
    cost_bp: float
    net_expected_bp: float                 # expected_reward_bp - cost_bp; NaN if unmeasurable
    correlated_exposure_usd: float
    portfolio_open_notional_usd: float
    portfolio_available_margin_usd: float
    opportunity_cost_note: str
    risk_penalty: float                       # bounded [0, 1], for ranking.py's risk_penalty input


def estimate_sl_equivalent_bp(atr_bp: float,
                              atr_mult: float = DEFAULT_SL_EQUIVALENT_ATR_MULT) -> float:
    """`atr_bp`: ATR already expressed in basis points (a caller normalizes price-unit ATR to
    bp before calling this, since this module has no price scale of its own to convert with).
    NaN in, NaN out -- never a fabricated fallback distance."""
    if atr_bp is None or (isinstance(atr_bp, float) and math.isnan(atr_bp)) or atr_bp <= 0:
        return float("nan")
    return atr_bp * atr_mult


def assess(symbol: str, confirmation_score: float, atr_bp: float, portfolio: PortfolioState,
          correlations: dict[str, float], cost_bp: float = DEFAULT_COST_BP,
          correlation_threshold: float = 0.6) -> RiskAssessment:
    has_score = confirmation_score is not None and not math.isnan(confirmation_score)
    expected_reward_bp = (confirmation_score * EXPECTED_REWARD_BP_PER_SCORE_UNIT
                          if has_score else float("nan"))
    sl_bp = estimate_sl_equivalent_bp(atr_bp)
    net_expected_bp = (expected_reward_bp - cost_bp) if has_score else float("nan")

    corr_exposure = portfolio.correlated_exposure_usd(symbol, correlations, correlation_threshold)
    exposure_ratio = (corr_exposure / portfolio.starting_balance_usd
                      if portfolio.starting_balance_usd > 0 else 0.0)
    penalty = min(1.0, max(0.0, exposure_ratio))
    note = ("no competing correlated exposure open" if corr_exposure == 0 else
           f"${corr_exposure:,.0f} correlated exposure already open "
           f"(threshold={correlation_threshold})")

    return RiskAssessment(
        symbol=symbol, expected_reward_bp=expected_reward_bp, estimated_sl_loss_bp=sl_bp,
        cost_bp=cost_bp, net_expected_bp=net_expected_bp, correlated_exposure_usd=corr_exposure,
        portfolio_open_notional_usd=portfolio.open_notional_usd,
        portfolio_available_margin_usd=portfolio.available_margin_usd,
        opportunity_cost_note=note, risk_penalty=penalty)
