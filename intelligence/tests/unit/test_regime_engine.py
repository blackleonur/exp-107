from __future__ import annotations

import numpy as np
import pytest

from intelligence.core.market_buffer import OHLCV
from intelligence.engines.regime_engine import (btc_correlations, btc_regime,
                                                 lag1_autocorrelation,
                                                 momentum_vs_mean_reversion, rolling_correlation)


def _ohlcv(closes, rng=0.5):
    n = len(closes)
    ot = np.arange(n, dtype=np.int64) * 60_000
    c = np.array(closes, dtype=np.float64)
    h, l, o = c + rng, c - rng, c.copy()
    v = np.full(n, 100.0)
    return OHLCV(ot, o, h, l, c, v, v * 10, v * 5)


class TestLag1Autocorrelation:
    def test_trending_series_is_positively_autocorrelated(self):
        # a steadily-increasing-step series (accelerating returns) -> positive return autocorr
        closes = [100.0]
        step = 1.0
        for _ in range(60):
            step *= 1.05
            closes.append(closes[-1] + step)
        ac = lag1_autocorrelation(np.array(closes), window=40)
        assert ac > 0

    def test_alternating_series_is_negatively_autocorrelated(self):
        closes = [100.0]
        for i in range(60):
            closes.append(closes[-1] + (2.0 if i % 2 == 0 else -1.8))
        ac = lag1_autocorrelation(np.array(closes), window=40)
        assert ac < 0

    def test_nan_before_warmup(self):
        assert np.isnan(lag1_autocorrelation(np.array([100.0, 101.0]), window=40))


class TestMomentumVsMeanReversion:
    def test_momentum_label(self):
        closes = [100.0]
        step = 1.0
        for _ in range(60):
            step *= 1.05
            closes.append(closes[-1] + step)
        label, ac = momentum_vs_mean_reversion(np.array(closes), window=40)
        assert label == "MOMENTUM"
        assert ac > 0

    def test_mean_reversion_label(self):
        closes = [100.0]
        for i in range(60):
            closes.append(closes[-1] + (2.0 if i % 2 == 0 else -1.8))
        label, ac = momentum_vs_mean_reversion(np.array(closes), window=40)
        assert label == "MEAN_REVERSION"

    def test_unknown_on_short_series(self):
        label, ac = momentum_vs_mean_reversion(np.array([100.0, 101.0]), window=40)
        assert label == "UNKNOWN"
        assert np.isnan(ac)


class TestRollingCorrelation:
    def test_perfectly_correlated_series(self):
        # a PURE scalar multiple (no additive offset) has IDENTICAL simple returns to the
        # original series, so their return-correlation is exactly 1.0 up to float error. An
        # additive offset (e.g. 2*a+5) would NOT give exactly 1.0 here, because simple returns
        # are ratios, not differences -- an affine (not purely linear) price relationship does
        # not imply a perfectly linear RETURN relationship.
        base = 100.0 + np.cumsum(np.random.default_rng(3).normal(0, 1, 50))
        corr = rolling_correlation(base, base * 3.0, window=30)
        assert corr == pytest.approx(1.0, abs=1e-9)

    def test_uncorrelated_short_series_nan(self):
        assert np.isnan(rolling_correlation(np.array([1.0, 2.0]), np.array([1.0, 2.0]), window=30))

    def test_inversely_correlated_series(self):
        # construct b's RETURNS to be the exact negation of a's returns (b[t]/b[t-1]-1 ==
        # -(a[t]/a[t-1]-1)), which is the condition rolling_correlation actually measures --
        # unlike a price-space mirror such as 200-a, whose RETURNS are not exactly anti-
        # correlated with a's (again because returns are ratios, not differences).
        a = 100.0 + np.cumsum(np.random.default_rng(4).normal(0, 1, 50))
        b = np.empty(len(a))
        b[0] = 50.0
        for t in range(1, len(a)):
            ra_t = a[t] / a[t - 1] - 1.0
            b[t] = b[t - 1] * (1.0 - ra_t)
        corr = rolling_correlation(a, b, window=30)
        assert corr == pytest.approx(-1.0, abs=1e-9)


class TestBtcRegime:
    def test_returns_snapshot_with_expected_fields(self):
        rng = np.random.default_rng(5)
        closes = 100.0 + np.cumsum(rng.normal(0, 1, 60))
        snap = btc_regime(_ohlcv(list(closes)))
        assert snap is not None
        assert snap.trend_state in {"BULLISH", "BEARISH", "NEUTRAL", "RANGE", "TRANSITION"}
        assert snap.volatility_regime in {"LOW", "NORMAL", "HIGH", "EXTREME", "UNKNOWN"}
        assert snap.momentum_state in {"MOMENTUM", "MEAN_REVERSION", "MIXED", "UNKNOWN"}

    def test_none_on_empty(self):
        assert btc_regime(_ohlcv([])) is None


class TestBtcCorrelations:
    def test_dict_keyed_by_symbol(self):
        rng = np.random.default_rng(6)
        btc = _ohlcv(list(100.0 + np.cumsum(rng.normal(0, 1, 50))))
        eth = _ohlcv(list(50.0 + np.cumsum(rng.normal(0, 1, 50))))
        result = btc_correlations(btc, {"ETHUSDT": eth}, window=30)
        assert set(result.keys()) == {"ETHUSDT"}
        assert -1.0 <= result["ETHUSDT"] <= 1.0
