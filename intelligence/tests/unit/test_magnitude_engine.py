from __future__ import annotations

import numpy as np
import pytest

from intelligence.core.market_buffer import OHLCV
from intelligence.engines.common import bucket_percentile
from intelligence.engines.magnitude_engine import (MAG_HORIZONS_MIN, compute,
                                                    compute_all_horizons, latest)


def _ohlcv_linear(n: int, step: float = 1.0, rng: float = 0.5) -> OHLCV:
    """Bars whose close rises linearly by `step` each bar -- move over any horizon H is exactly
    step*H, closed-form and hand-verifiable."""
    ot = np.arange(n, dtype=np.int64) * 60_000
    c = 100.0 + np.arange(n) * step
    h = c + rng
    l = c - rng
    o = c.copy()
    v = np.full(n, 100.0)
    return OHLCV(ot, o, h, l, c, v, v * 10, v * 5)


class TestMoveArithmetic:
    def test_move_equals_linear_step_times_horizon(self):
        ohlcv = _ohlcv_linear(50, step=2.0)
        s = latest(ohlcv, horizon_bars=10)
        assert s.move == pytest.approx(2.0 * 10)

    def test_pct_move_formula(self):
        ohlcv = _ohlcv_linear(50, step=1.0)
        s = latest(ohlcv, horizon_bars=5)
        prior_close = 100.0 + (49 - 5) * 1.0
        assert s.pct_move == pytest.approx(5.0 / prior_close)

    def test_nan_when_horizon_exceeds_available_history(self):
        ohlcv = _ohlcv_linear(20, step=1.0)
        s = latest(ohlcv, horizon_bars=25)
        assert np.isnan(s.move)
        assert s.state == "UNKNOWN"

    def test_nan_when_horizon_equals_series_length(self):
        # n > horizon_bars is required (need at least one prior close) -- n == horizon_bars
        # must NOT silently produce a value
        ohlcv = _ohlcv_linear(20, step=1.0)
        s = latest(ohlcv, horizon_bars=20)
        assert np.isnan(s.move)


class TestSignAndNormalization:
    def test_uptrend_gives_positive_normalized_move(self):
        ohlcv = _ohlcv_linear(50, step=1.0)
        s = latest(ohlcv, horizon_bars=10, atr_period=10)
        assert s.atr_normalized_move > 0
        assert s.abs_normalized_move == pytest.approx(abs(s.atr_normalized_move))

    def test_downtrend_gives_negative_normalized_move(self):
        ohlcv = _ohlcv_linear(50, step=-1.0)
        s = latest(ohlcv, horizon_bars=10, atr_period=10)
        assert s.atr_normalized_move < 0


class TestWiringToCommon:
    def test_state_matches_bucket_percentile_of_percentile_rank(self):
        rng = np.random.default_rng(7)
        n = 80
        ot = np.arange(n, dtype=np.int64) * 60_000
        c = 100.0 + np.cumsum(rng.normal(0, 1, n))
        h, l, o = c + 0.5, c - 0.5, c.copy()
        v = np.full(n, 100.0)
        ohlcv = OHLCV(ot, o, h, l, c, v, v * 10, v * 5)
        snaps = compute(ohlcv, horizon_bars=5, atr_period=10, percentile_window=20)
        for s in snaps:
            assert s.state == bucket_percentile(
                s.percentile_rank if np.isfinite(s.percentile_rank) else float("nan"))


class TestComputeAllHorizons:
    def test_short_horizons_available_long_ones_none(self):
        # 100 bars: MAG_5M..MAG_1H measurable, MAG_24H (1440 bars) cannot be
        ohlcv = _ohlcv_linear(100, step=0.5)
        result = compute_all_horizons(ohlcv, atr_period=10, percentile_window=20)
        assert set(result.keys()) == set(MAG_HORIZONS_MIN.keys())
        assert result["MAG_5M"] is not None
        assert np.isfinite(result["MAG_5M"].move)
        assert result["MAG_24H"] is not None          # snapshot object always returned
        assert np.isnan(result["MAG_24H"].move)        # but its move is honestly NaN
        assert result["MAG_24H"].state == "UNKNOWN"


class TestEmpty:
    def test_empty_series(self):
        empty = _ohlcv_linear(0)
        assert compute(empty, horizon_bars=5) == []
        assert latest(empty, horizon_bars=5) is None
