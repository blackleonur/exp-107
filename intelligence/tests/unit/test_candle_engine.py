from __future__ import annotations

import numpy as np
import pytest

from intelligence.core.market_buffer import OHLCV
from intelligence.engines.candle_engine import compute, latest


def _ohlcv(bars: list[tuple]) -> OHLCV:
    """bars: list of (open, high, low, close[, volume]) tuples."""
    ot = np.arange(len(bars), dtype=np.int64) * 60_000
    o = np.array([b[0] for b in bars], dtype=np.float64)
    h = np.array([b[1] for b in bars], dtype=np.float64)
    l = np.array([b[2] for b in bars], dtype=np.float64)
    c = np.array([b[3] for b in bars], dtype=np.float64)
    v = np.array([b[4] if len(b) > 4 else 100.0 for b in bars], dtype=np.float64)
    return OHLCV(ot, o, h, l, c, v, v * 10, v * 5)


class TestBasicShapeMetrics:
    def test_body_wick_range(self):
        # open=10, high=12, low=8, close=11 -> body=1, upper_wick=1, lower_wick=2, range=4
        snaps = compute(_ohlcv([(10, 12, 8, 11)]))
        s = snaps[0]
        assert s.body == pytest.approx(1.0)
        assert s.upper_wick == pytest.approx(1.0)
        assert s.lower_wick == pytest.approx(2.0)
        assert s.candle_range == pytest.approx(4.0)
        assert s.is_green is True

    def test_close_location_value_bounds(self):
        # close at the high -> CLV = 1
        s = compute(_ohlcv([(10, 12, 8, 12)]))[0]
        assert s.close_location_value == pytest.approx(1.0)
        # close at the low -> CLV = -1
        s2 = compute(_ohlcv([(10, 12, 8, 8)]))[0]
        assert s2.close_location_value == pytest.approx(-1.0)

    def test_zero_range_bar_is_nan_not_crash(self):
        s = compute(_ohlcv([(10, 10, 10, 10)]))[0]
        assert np.isnan(s.body_to_range)
        assert np.isnan(s.close_location_value)


class TestEngulfing:
    def test_bullish_engulfing(self):
        bars = [(10, 10.5, 9.5, 9.6),    # red bar
                (9.5, 11.0, 9.4, 10.8)]  # green bar engulfing the red body
        s = compute(_ohlcv(bars))[1]
        assert s.engulfing == "bullish"

    def test_bearish_engulfing(self):
        bars = [(9.6, 10.5, 9.5, 10.2),   # green bar
                (10.3, 10.4, 9.0, 9.2)]    # red bar engulfing the green body
        s = compute(_ohlcv(bars))[1]
        assert s.engulfing == "bearish"

    def test_no_engulfing_when_body_smaller(self):
        bars = [(10, 10.5, 9.5, 9.6), (9.7, 9.9, 9.6, 9.8)]
        s = compute(_ohlcv(bars))[1]
        assert s.engulfing == "none"


class TestRejection:
    def test_upper_wick_rejection(self):
        # tiny body, huge upper wick
        s = compute(_ohlcv([(10.0, 15.0, 9.9, 10.1)]))[0]
        assert s.rejection == "upper"

    def test_lower_wick_rejection(self):
        s = compute(_ohlcv([(10.0, 10.1, 5.0, 9.9)]))[0]
        assert s.rejection == "lower"

    def test_no_rejection_on_balanced_bar(self):
        s = compute(_ohlcv([(10.0, 10.3, 9.7, 10.2)]))[0]
        assert s.rejection == "none"


class TestInsideBar:
    def test_inside_bar_detected(self):
        bars = [(10, 12, 8, 11), (10.5, 11.5, 9.0, 10.8)]
        s = compute(_ohlcv(bars))[1]
        assert s.inside_bar is True

    def test_not_inside_when_high_exceeds(self):
        bars = [(10, 12, 8, 11), (10.5, 12.5, 9.0, 10.8)]
        s = compute(_ohlcv(bars))[1]
        assert s.inside_bar is False


class TestBreakout:
    def test_breakout_up_detected(self):
        base = [(10, 10.2, 9.8, 10.0)] * 25
        bars = base + [(10.0, 11.0, 9.9, 10.9)]  # closes above the prior 20-bar high
        s = compute(_ohlcv(bars), breakout_lookback=20)[-1]
        assert s.breakout == "up"

    def test_no_breakout_within_range(self):
        base = [(10, 10.2, 9.8, 10.0)] * 25
        s = compute(_ohlcv(base), breakout_lookback=20)[-1]
        assert s.breakout == "none"


class TestExpansionCompression:
    def test_expansion_flagged_on_large_range_bar(self):
        # build 20 small-range bars to establish ATR, then one huge-range bar
        base = [(10, 10.1, 9.9, 10.0)] * 20
        bars = base + [(10, 12.0, 8.0, 11.0)]
        s = compute(_ohlcv(bars), atr_period=14)[-1]
        assert s.expansion is True

    def test_compression_flagged_on_tiny_range_bar(self):
        base = [(10, 11.0, 9.0, 10.0)] * 20
        bars = base + [(10, 10.05, 9.98, 10.0)]
        s = compute(_ohlcv(bars), atr_period=14)[-1]
        assert s.compression is True


class TestTookOutPriorHighLow:
    def test_took_out_prior_high(self):
        bars = [(10, 11, 9, 10.5), (10.5, 11.5, 10.0, 11.2)]
        s = compute(_ohlcv(bars))[1]
        assert s.took_out_prior_high is True
        assert s.took_out_prior_low is False


class TestLatestHelper:
    def test_latest_returns_last_snapshot(self):
        bars = [(10, 11, 9, 10.5), (10.5, 11.5, 10.0, 11.2)]
        s = latest(_ohlcv(bars))
        assert s.open_time == 60_000

    def test_latest_none_on_empty(self):
        assert latest(_ohlcv([])) is None
