from __future__ import annotations

import numpy as np
import pytest

from intelligence.engines.common import atr, rolling_percentile_rank, true_range


class TestTrueRange:
    def test_first_bar_is_high_minus_low(self):
        tr = true_range([10.0], [8.0], [9.0])
        assert tr[0] == pytest.approx(2.0)

    def test_gap_up_uses_prev_close(self):
        # bar 2 gaps up: low=20 is above prev close=10, so TR should be high-prev_close-ish
        high = [10, 25]
        low = [8, 20]
        close = [9, 24]
        tr = true_range(high, low, close)
        assert tr[1] == pytest.approx(max(25 - 20, abs(25 - 9), abs(20 - 9)))


class TestATR:
    def test_warmup_is_nan(self):
        h = np.arange(1, 11) + 1.0
        l = np.arange(1, 11) - 1.0
        c = np.arange(1, 11) + 0.0
        a = atr(h, l, c, period=5)
        assert np.all(np.isnan(a[:4]))
        assert np.isfinite(a[4])

    def test_matches_manual_mean_of_true_range(self):
        h = [10, 11, 12, 13, 14, 15]
        l = [8, 9, 10, 11, 12, 13]
        c = [9, 10, 11, 12, 13, 14]
        a = atr(h, l, c, period=3)
        tr = true_range(h, l, c)
        # first valid ATR value is at index period-1=2: mean of true range over bars 0,1,2
        assert a[2] == pytest.approx(np.mean(tr[0:3]))

    def test_too_short_series_all_nan(self):
        a = atr([1, 2], [0, 1], [0.5, 1.5], period=5)
        assert np.all(np.isnan(a))


class TestRollingPercentileRank:
    def test_rank_of_max_is_one(self):
        x = [1, 2, 3, 4, 5]
        r = rolling_percentile_rank(x, window=5)
        assert r[4] == pytest.approx(1.0)

    def test_rank_of_min_is_zero(self):
        x = [5, 4, 3, 2, 1]
        r = rolling_percentile_rank(x, window=5)
        assert r[4] == pytest.approx(0.0)

    def test_warmup_is_nan(self):
        x = [1, 2, 3]
        r = rolling_percentile_rank(x, window=5)
        assert np.all(np.isnan(r))
