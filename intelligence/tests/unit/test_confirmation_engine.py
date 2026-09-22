from __future__ import annotations

import pytest

from intelligence.confirmation.confirmation_engine import (CONFIRM_THRESHOLD,
                                                            INVALIDATE_THRESHOLD,
                                                            WEAKEN_THRESHOLD, confirm)
from intelligence.confirmation.evidence import Evidence, EvidenceBundle, Strength
from intelligence.confirmation.feature_registry import FeatureRegistry, Outcome
from intelligence.core.exp107_signal import Exp107Signal


def _fired_long():
    return Exp107Signal(status="OK", symbol="BTCUSDT", fired=True, side="LONG", comb=0.97)


def _outcomes_at_confidence(feature, n_correct, n_wrong, magnitude=10.0):
    rows = [Outcome(feature, True, magnitude) for _ in range(n_correct)]
    rows += [Outcome(feature, False, magnitude) for _ in range(n_wrong)]
    return rows


HIGH_CONF_OUTCOMES = _outcomes_at_confidence("HIGH_FEATURE", 90, 60)   # -> Confidence.HIGH, weight 1.0
MEDIUM_CONF_OUTCOMES = _outcomes_at_confidence("MEDIUM_FEATURE", 33, 27)  # -> Confidence.MEDIUM, weight 0.5
UNSET_OUTCOMES: list[Outcome] = []                                       # never-seen -> weight 0.0


class TestNoSignalCases:
    def test_exp107_unavailable_gives_no_signal(self):
        exp107 = Exp107Signal(status="UNAVAILABLE", symbol="BTCUSDT")
        bundle = EvidenceBundle(1, "BTCUSDT", [])
        result = confirm(exp107, bundle, FeatureRegistry([]))
        assert result.label == "NO_SIGNAL"
        assert result.confirmation_score == 0.0
        assert result.exp107_fired_long is False

    def test_exp107_ok_but_not_fired_gives_no_signal(self):
        exp107 = Exp107Signal(status="OK", symbol="BTCUSDT", fired=False, side="LONG")
        bundle = EvidenceBundle(1, "BTCUSDT", [])
        result = confirm(exp107, bundle, FeatureRegistry([]))
        assert result.label == "NO_SIGNAL"

    def test_exp107_ok_fired_but_short_gives_no_signal(self):
        exp107 = Exp107Signal(status="OK", symbol="BTCUSDT", fired=True, side="SHORT")
        bundle = EvidenceBundle(1, "BTCUSDT", [])
        result = confirm(exp107, bundle, FeatureRegistry([]))
        assert result.label == "NO_SIGNAL"


class TestUnweightedEvidenceNeverMovesTheScore:
    """A feature the registry has never scored (or has scored but found UNSET/LOW) must
    contribute exactly zero -- 'absence of evidence is not evidence' (feature_registry.py)."""

    def test_unset_feature_support_does_not_move_score(self):
        bundle = EvidenceBundle(1, "BTCUSDT", [
            Evidence("some_engine", "NEVER_SCORED", Strength.STRONG_SUPPORT)])
        result = confirm(_fired_long(), bundle, FeatureRegistry(UNSET_OUTCOMES))
        assert result.confirmation_score == 0.0
        assert result.label == "DEFER"
        assert result.weighted_support == []

    def test_unset_feature_conflict_does_not_move_score(self):
        bundle = EvidenceBundle(1, "BTCUSDT", [
            Evidence("some_engine", "NEVER_SCORED", Strength.CONFLICT)])
        result = confirm(_fired_long(), bundle, FeatureRegistry(UNSET_OUTCOMES))
        assert result.confirmation_score == 0.0
        assert result.weighted_conflict == []


class TestUnknownNeverCountsNegative:
    def test_unknown_evidence_excluded_from_score_and_counted_separately(self):
        bundle = EvidenceBundle(1, "BTCUSDT", [
            Evidence("liquidity_engine", "LIQUIDATION_DATA", Strength.UNKNOWN),
            Evidence("liquidity_engine", "OI_DATA", Strength.UNKNOWN),
        ])
        result = confirm(_fired_long(), bundle, FeatureRegistry([]))
        assert result.confirmation_score == 0.0
        assert result.n_unknown == 2
        assert result.label == "DEFER"  # NOT WEAKEN or INVALIDATE -- unknown != negative


class TestConfirmLabel:
    def test_strong_support_from_high_confidence_feature_confirms(self):
        bundle = EvidenceBundle(1, "BTCUSDT", [
            Evidence("volatility_engine", "HIGH_FEATURE", Strength.STRONG_SUPPORT)])
        result = confirm(_fired_long(), bundle, FeatureRegistry(HIGH_CONF_OUTCOMES))
        # contribution = 1.0 (STRONG_SUPPORT sign) * 1.0 (HIGH weight) = 1.0 >= CONFIRM_THRESHOLD
        assert result.confirmation_score == pytest.approx(1.0)
        assert result.label == "CONFIRM"
        assert len(result.weighted_support) == 1

    def test_exactly_at_confirm_threshold_confirms(self):
        # SUPPORT sign=0.5 * MEDIUM weight=0.5 -> exactly 0.25, below threshold; two of them ->
        # 0.5, exactly at CONFIRM_THRESHOLD (boundary is inclusive: score >= threshold)
        bundle = EvidenceBundle(1, "BTCUSDT", [
            Evidence("a", "MEDIUM_FEATURE", Strength.SUPPORT),
            Evidence("b", "MEDIUM_FEATURE", Strength.SUPPORT),
        ])
        result = confirm(_fired_long(), bundle, FeatureRegistry(MEDIUM_CONF_OUTCOMES))
        assert result.confirmation_score == pytest.approx(CONFIRM_THRESHOLD)
        assert result.label == "CONFIRM"


class TestWeakenAndInvalidateLabels:
    def test_moderate_conflict_weakens(self):
        bundle = EvidenceBundle(1, "BTCUSDT", [
            Evidence("a", "MEDIUM_FEATURE", Strength.CONFLICT)])  # -1.0 * 0.5 = -0.5
        result = confirm(_fired_long(), bundle, FeatureRegistry(MEDIUM_CONF_OUTCOMES))
        assert result.confirmation_score == pytest.approx(-0.5)
        assert WEAKEN_THRESHOLD >= result.confirmation_score > INVALIDATE_THRESHOLD
        assert result.label == "WEAKEN"
        assert len(result.weighted_conflict) == 1

    def test_strong_conflict_invalidates(self):
        bundle = EvidenceBundle(1, "BTCUSDT", [
            Evidence("a", "HIGH_FEATURE", Strength.CONFLICT)])  # -1.0 * 1.0 = -1.0
        result = confirm(_fired_long(), bundle, FeatureRegistry(HIGH_CONF_OUTCOMES))
        assert result.confirmation_score == pytest.approx(-1.0)
        assert result.confirmation_score <= INVALIDATE_THRESHOLD
        assert result.label == "INVALIDATE"


class TestDeferIsTheDefault:
    def test_no_evidence_at_all_defers(self):
        bundle = EvidenceBundle(1, "BTCUSDT", [])
        result = confirm(_fired_long(), bundle, FeatureRegistry([]))
        assert result.confirmation_score == 0.0
        assert result.label == "DEFER"


class TestSupportAndConflictBothPresent:
    def test_mixed_evidence_nets_out(self):
        bundle = EvidenceBundle(1, "BTCUSDT", [
            Evidence("a", "HIGH_FEATURE", Strength.STRONG_SUPPORT),   # +1.0
            Evidence("b", "MEDIUM_FEATURE", Strength.CONFLICT),        # -0.5
        ])
        result = confirm(_fired_long(), bundle, FeatureRegistry(HIGH_CONF_OUTCOMES + MEDIUM_CONF_OUTCOMES))
        assert result.confirmation_score == pytest.approx(0.5)
        assert len(result.weighted_support) == 1
        assert len(result.weighted_conflict) == 1
