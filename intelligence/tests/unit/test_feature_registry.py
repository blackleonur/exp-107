from __future__ import annotations

import numpy as np
import pytest

from intelligence.confirmation.feature_registry import (Confidence, FeatureRegistry, Outcome,
                                                         tail_dependency_ratio)


def _outcomes(feature, n_correct, n_wrong, magnitude=10.0, symbol="BTCUSDT", regime="NORMAL"):
    rows = [Outcome(feature, True, magnitude, symbol, regime) for _ in range(n_correct)]
    rows += [Outcome(feature, False, magnitude, symbol, regime) for _ in range(n_wrong)]
    return rows


class TestTailDependencyRatio:
    def test_even_distribution_gives_low_ratio(self):
        r = tail_dependency_ratio([10.0] * 10)
        assert r == pytest.approx(0.1)

    def test_one_dominant_observation(self):
        r = tail_dependency_ratio([1.0] * 9 + [1000.0])
        assert r > 0.9

    def test_empty_is_nan(self):
        assert np.isnan(tail_dependency_ratio([]))

    def test_all_zero_is_nan(self):
        assert np.isnan(tail_dependency_ratio([0.0, 0.0]))


class TestFeatureRegistryUnseenFeature:
    def test_stats_for_unseen_feature_is_unset(self):
        reg = FeatureRegistry([])
        s = reg.stats_for("NEVER_SEEN")
        assert s.n == 0
        assert s.confidence == Confidence.UNSET
        assert np.isnan(s.hit_rate)

    def test_weight_for_unseen_feature_is_zero(self):
        reg = FeatureRegistry([])
        assert reg.weight_for("NEVER_SEEN") == 0.0


class TestConfidencePromotion:
    def test_below_min_n_stays_unset(self):
        outcomes = _outcomes("F1", n_correct=10, n_wrong=5)  # n=15 < MIN_N_FOR_LOW=20
        reg = FeatureRegistry(outcomes)
        assert reg.stats_for("F1").confidence == Confidence.UNSET
        assert reg.weight_for("F1") == 0.0

    def test_enough_n_but_no_edge_stays_low(self):
        # n=60, hit_rate exactly 0.5 -> edge=0, never promoted past LOW even with plenty of data
        outcomes = _outcomes("F1", n_correct=30, n_wrong=30, magnitude=10.0)
        reg = FeatureRegistry(outcomes)
        s = reg.stats_for("F1")
        assert s.n == 60
        assert s.edge_vs_coin_flip == pytest.approx(0.0)
        assert s.confidence == Confidence.LOW
        assert reg.weight_for("F1") == 0.0

    def test_medium_confidence_requires_n_and_edge(self):
        # n=60, hit_rate ~ 0.55 (edge 0.05 >= MIN_EDGE_FOR_MEDIUM=0.03) -> MEDIUM
        outcomes = _outcomes("F1", n_correct=33, n_wrong=27, magnitude=10.0)
        reg = FeatureRegistry(outcomes)
        s = reg.stats_for("F1")
        assert s.confidence == Confidence.MEDIUM
        assert reg.weight_for("F1") == pytest.approx(0.5)

    def test_high_confidence_requires_large_n_and_edge(self):
        # n=150, hit_rate ~0.60 (edge 0.10) -> HIGH
        outcomes = _outcomes("F1", n_correct=90, n_wrong=60, magnitude=10.0)
        reg = FeatureRegistry(outcomes)
        s = reg.stats_for("F1")
        assert s.n == 150
        assert s.confidence == Confidence.HIGH
        assert reg.weight_for("F1") == pytest.approx(1.0)

    def test_tail_dependent_feature_capped_at_low_despite_large_n_and_edge(self):
        # same n/edge as the HIGH case above, but almost all the magnitude of correct calls
        # comes from a single extreme observation -- must be capped at LOW, exactly the
        # EXP-120/121 replication-failure lesson this registry exists to guard against
        rows = [Outcome("F1", True, 1.0, "BTCUSDT", "NORMAL") for _ in range(89)]
        rows.append(Outcome("F1", True, 10000.0, "BTCUSDT", "NORMAL"))
        rows += [Outcome("F1", False, 1.0, "BTCUSDT", "NORMAL") for _ in range(60)]
        reg = FeatureRegistry(rows)
        s = reg.stats_for("F1")
        assert s.n == 150
        assert s.edge_vs_coin_flip == pytest.approx(0.10)
        assert s.tail_dependency_ratio > 0.5
        assert s.confidence == Confidence.LOW
        assert reg.weight_for("F1") == 0.0


class TestSymbolAndRegimeDiversity:
    def test_n_symbols_and_regimes_counted(self):
        rows = [
            Outcome("F1", True, 5.0, "BTCUSDT", "HIGH_VOL"),
            Outcome("F1", True, 5.0, "ETHUSDT", "HIGH_VOL"),
            Outcome("F1", False, 5.0, "BTCUSDT", "LOW_VOL"),
        ]
        reg = FeatureRegistry(rows)
        s = reg.stats_for("F1")
        assert s.n_symbols == 2
        assert s.n_regimes == 2


class TestAllStats:
    def test_all_stats_covers_every_feature_seen(self):
        rows = _outcomes("F1", 10, 10) + _outcomes("F2", 5, 5)
        reg = FeatureRegistry(rows)
        all_s = reg.all_stats()
        assert set(all_s.keys()) == {"F1", "F2"}


class TestRegistryIsImmutableSnapshot:
    def test_mutating_input_list_after_construction_does_not_affect_registry(self):
        outcomes = _outcomes("F1", 10, 10)
        reg = FeatureRegistry(outcomes)
        outcomes.append(Outcome("F1", True, 5.0))
        assert reg.stats_for("F1").n == 20  # unaffected by the later append
