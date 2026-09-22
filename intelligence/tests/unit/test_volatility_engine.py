from __future__ import annotations

import numpy as np
import pytest

from intelligence.core.market_buffer import OHLCV
from intelligence.engines.common import atr as _atr
from intelligence.engines.common import bucket_percentile, rolling_percentile_rank
from intelligence.engines.volatility_engine import compute, latest


def _ohlcv_ranges(ranges: list[float], drift: float = 0.01) -> OHLCV:
    """Bars whose (high-low) range follows `ranges`, midpoint drifting gently upward so gap
    terms in true_range stay small relative to the deliberately large range jumps under test."""
    n = len(ranges)
    ot = np.arange(n, dtype=np.int64) * 60_000
    mid = 100.0 + np.arange(n) * drift
    h = mid + np.array(ranges) / 2
    l = mid - np.array(ranges) / 2
    c = mid.copy()
    o = mid.copy()
    v = np.full(n, 100.0)
    return OHLCV(ot, o, h, l, c, v, v * 10, v * 5)


class TestWiring:
    """These tests check compute() wires the shared common.* helpers correctly, rather than
    re-deriving ATR/percentile-rank math already covered in test_common.py."""

    def test_atr_field_matches_common_atr(self):
        ranges = [1.0 + 0.01 * i for i in range(40)]
        ohlcv = _ohlcv_ranges(ranges)
        snaps = compute(ohlcv, atr_period=14, percentile_window=20)
        expected = _atr(ohlcv.high, ohlcv.low, ohlcv.close, 14)
        for i, s in enumerate(snaps):
            if np.isfinite(expected[i]):
                assert s.atr == pytest.approx(expected[i])
            else:
                assert np.isnan(s.atr)

    def test_atr_percentile_matches_common_rolling_rank(self):
        rng = np.random.default_rng(42)
        ranges = list(1.0 + rng.random(60))
        ohlcv = _ohlcv_ranges(ranges)
        snaps = compute(ohlcv, atr_period=10, percentile_window=20)
        atr_arr = _atr(ohlcv.high, ohlcv.low, ohlcv.close, 10)
        expected = rolling_percentile_rank(atr_arr, 20)
        for i, s in enumerate(snaps):
            if np.isfinite(expected[i]):
                assert s.atr_percentile == pytest.approx(expected[i])
                assert s.regime == bucket_percentile(expected[i])
            else:
                assert np.isnan(s.atr_percentile)
                assert s.regime == "UNKNOWN"


class TestRealizedVol:
    def test_zero_vol_on_constant_price(self):
        ohlcv = _ohlcv_ranges([1.0] * 40, drift=0.0)
        s = latest(ohlcv, rv_window=20)
        assert s.realized_vol == pytest.approx(0.0, abs=1e-9)

    def test_nan_before_warmup(self):
        ohlcv = _ohlcv_ranges([1.0] * 10, drift=0.0)
        s = latest(ohlcv, rv_window=20)
        assert np.isnan(s.realized_vol)


class TestExpansionContraction:
    """EXPANSION_LOOKBACK=10, atr_period=5: the comparison point (10 bars back from the last
    bar) must still fall entirely within the OLD range block, and the last atr_period bars must
    fall entirely within the NEW range block, or the two ATR readings being compared would
    overlap the transition and the ratio would be diluted. 40 old-range bars + 5 new-range bars
    achieves exactly that separation."""

    def test_expansion_flagged_on_abrupt_range_increase(self):
        ranges = [1.0] * 40 + [10.0] * 5
        ohlcv = _ohlcv_ranges(ranges)
        s = latest(ohlcv, atr_period=5, percentile_window=10)
        assert s.expansion is True
        assert s.contraction is False

    def test_contraction_flagged_on_abrupt_range_decrease(self):
        ranges = [10.0] * 40 + [1.0] * 5
        ohlcv = _ohlcv_ranges(ranges)
        s = latest(ohlcv, atr_period=5, percentile_window=10)
        assert s.contraction is True
        assert s.expansion is False

    def test_neither_flagged_on_stable_range(self):
        ohlcv = _ohlcv_ranges([2.0] * 40)
        s = latest(ohlcv, atr_period=5, percentile_window=10)
        assert s.expansion is False
        assert s.contraction is False


class TestEmptyAndShort:
    def test_empty_series(self):
        assert compute(_ohlcv_ranges([])) == []
        assert latest(_ohlcv_ranges([])) is None

    def test_short_series_all_unknown(self):
        ohlcv = _ohlcv_ranges([1.0, 1.0, 1.0])
        s = latest(ohlcv, atr_period=14, percentile_window=50)
        assert s.regime == "UNKNOWN"
        assert np.isnan(s.atr)
