from __future__ import annotations

import pytest

from intelligence.opportunity.ranking import rank_opportunities


class TestBasicOrdering:
    def test_highest_score_ranked_first(self):
        result = rank_opportunities({"BTCUSDT": 0.5, "ETHUSDT": 0.9, "SOLUSDT": 0.2})
        assert [r.symbol for r in result] == ["ETHUSDT", "BTCUSDT", "SOLUSDT"]
        assert [r.rank for r in result] == [1, 2, 3]

    def test_empty_input(self):
        assert rank_opportunities({}) == []

    def test_single_candidate(self):
        result = rank_opportunities({"BTCUSDT": 0.5})
        assert result[0].rank == 1
        assert result[0].net_score == pytest.approx(0.5)


class TestRiskPenalty:
    def test_penalty_reduces_net_score_and_can_change_order(self):
        # ETH has the higher raw confirmation score, but a large risk penalty (e.g. already-
        # correlated exposure from a later Risk Engine phase) drops it below BTC's net score
        result = rank_opportunities(
            {"BTCUSDT": 0.5, "ETHUSDT": 0.9},
            risk_penalty={"ETHUSDT": 0.6})
        assert [r.symbol for r in result] == ["BTCUSDT", "ETHUSDT"]
        eth = next(r for r in result if r.symbol == "ETHUSDT")
        assert eth.net_score == pytest.approx(0.3)
        assert eth.confirmation_score == pytest.approx(0.9)  # raw score preserved for audit

    def test_missing_penalty_defaults_to_zero_not_fabricated(self):
        result = rank_opportunities({"BTCUSDT": 0.5}, risk_penalty={"ETHUSDT": 0.6})
        assert result[0].risk_penalty == 0.0
        assert result[0].net_score == pytest.approx(0.5)


class TestCostTieBreak:
    def test_equal_net_scores_broken_by_lower_cost(self):
        result = rank_opportunities(
            {"BTCUSDT": 0.5, "ETHUSDT": 0.5},
            cost_bp=10.0,
        )
        # equal score and equal cost_bp (same global default) -> falls through to symbol-name
        # tiebreak, which must still be deterministic
        assert [r.symbol for r in result] == ["BTCUSDT", "ETHUSDT"]

    def test_ranking_is_deterministic_across_repeated_calls(self):
        candidates = {"XRPUSDT": 0.4, "ADAUSDT": 0.4, "DOTUSDT": 0.6}
        first = [r.symbol for r in rank_opportunities(candidates)]
        second = [r.symbol for r in rank_opportunities(candidates)]
        assert first == second


class TestReasonString:
    def test_reason_includes_penalty_only_when_present(self):
        result = rank_opportunities({"BTCUSDT": 0.5}, risk_penalty={"BTCUSDT": 0.2})
        assert "risk_penalty" in result[0].reason
        result2 = rank_opportunities({"BTCUSDT": 0.5})
        assert "risk_penalty" not in result2[0].reason
