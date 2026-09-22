from __future__ import annotations

import numpy as np
import pytest

from intelligence.risk.portfolio_state import PaperPosition, PortfolioState
from intelligence.risk.risk_engine import assess, estimate_sl_equivalent_bp


class TestEstimateSlEquivalent:
    def test_scales_atr_by_multiplier(self):
        assert estimate_sl_equivalent_bp(100.0, atr_mult=1.5) == pytest.approx(150.0)

    def test_nan_in_nan_out(self):
        assert np.isnan(estimate_sl_equivalent_bp(float("nan")))

    def test_none_is_nan(self):
        assert np.isnan(estimate_sl_equivalent_bp(None))

    def test_zero_or_negative_is_nan(self):
        assert np.isnan(estimate_sl_equivalent_bp(0.0))
        assert np.isnan(estimate_sl_equivalent_bp(-5.0))


class TestAssess:
    def test_basic_assessment_no_competing_exposure(self):
        pf = PortfolioState(starting_balance_usd=10_000.0)
        result = assess("BTCUSDT", confirmation_score=1.0, atr_bp=50.0, portfolio=pf,
                        correlations={})
        assert result.symbol == "BTCUSDT"
        assert result.expected_reward_bp == pytest.approx(10.0)   # 1.0 * EXPECTED_REWARD_BP_PER_SCORE_UNIT
        assert result.net_expected_bp == pytest.approx(10.0 - 14.38)
        assert result.estimated_sl_loss_bp == pytest.approx(75.0)  # 50 * 1.5
        assert result.correlated_exposure_usd == pytest.approx(0.0)
        assert result.risk_penalty == pytest.approx(0.0)
        assert "no competing" in result.opportunity_cost_note

    def test_nan_confirmation_score_propagates_honestly(self):
        pf = PortfolioState(starting_balance_usd=10_000.0)
        result = assess("BTCUSDT", confirmation_score=float("nan"), atr_bp=50.0, portfolio=pf,
                        correlations={})
        assert np.isnan(result.expected_reward_bp)
        assert np.isnan(result.net_expected_bp)

    def test_correlated_exposure_produces_penalty(self):
        pf = PortfolioState(starting_balance_usd=10_000.0)
        pf.open_position(PaperPosition("ETHUSDT", "LONG", 4000.0, 1, 100.0))
        result = assess("BTCUSDT", confirmation_score=1.0, atr_bp=50.0, portfolio=pf,
                        correlations={"ETHUSDT": 0.9})
        assert result.correlated_exposure_usd == pytest.approx(4000.0)
        assert result.risk_penalty == pytest.approx(0.4)  # 4000/10000
        assert "correlated exposure already open" in result.opportunity_cost_note

    def test_penalty_capped_at_one(self):
        pf = PortfolioState(starting_balance_usd=1_000.0)
        pf.open_position(PaperPosition("ETHUSDT", "LONG", 900.0, 1, 100.0))
        pf.open_position(PaperPosition("SOLUSDT", "LONG", 90.0, 1, 20.0))
        result = assess("BTCUSDT", confirmation_score=1.0, atr_bp=50.0, portfolio=pf,
                        correlations={"ETHUSDT": 0.9, "SOLUSDT": 0.9})
        assert result.risk_penalty <= 1.0

    def test_portfolio_context_carried_through(self):
        pf = PortfolioState(starting_balance_usd=10_000.0)
        pf.open_position(PaperPosition("ETHUSDT", "LONG", 2000.0, 1, 100.0))
        result = assess("BTCUSDT", confirmation_score=0.5, atr_bp=30.0, portfolio=pf,
                        correlations={})
        assert result.portfolio_open_notional_usd == pytest.approx(2000.0)
        assert result.portfolio_available_margin_usd == pytest.approx(8000.0)

    def test_atr_unavailable_gives_nan_sl_but_does_not_crash(self):
        pf = PortfolioState(starting_balance_usd=10_000.0)
        result = assess("BTCUSDT", confirmation_score=1.0, atr_bp=float("nan"), portfolio=pf,
                        correlations={})
        assert np.isnan(result.estimated_sl_loss_bp)
        assert result.expected_reward_bp == pytest.approx(10.0)  # unaffected by ATR being unavailable
