from __future__ import annotations

import numpy as np
import pytest

from intelligence.core.binance_client import BookTicker
from intelligence.core.market_buffer import OHLCV
from intelligence.engines.liquidity_engine import (compute, funding_and_basis,
                                                    open_interest_value, order_book_imbalance,
                                                    recent_extremes, volume_evidence)


def _ohlcv(closes, volumes=None, rng=1.0):
    n = len(closes)
    ot = np.arange(n, dtype=np.int64) * 60_000
    c = np.array(closes, dtype=np.float64)
    h, l, o = c + rng, c - rng, c.copy()
    v = np.array(volumes, dtype=np.float64) if volumes else np.full(n, 100.0)
    return OHLCV(ot, o, h, l, c, v, v * 10, v * 5)


class TestOrderBookImbalance:
    def test_balanced_book_near_zero(self):
        depth = {"bids": [["10.0", "5"], ["9.9", "5"]], "asks": [["10.1", "5"], ["10.2", "5"]]}
        assert order_book_imbalance(depth) == pytest.approx(0.0)

    def test_bid_heavy_book_positive(self):
        depth = {"bids": [["10.0", "9"]], "asks": [["10.1", "1"]]}
        assert order_book_imbalance(depth) == pytest.approx(0.8)

    def test_none_when_missing(self):
        assert order_book_imbalance(None) is None
        assert order_book_imbalance({"bids": [], "asks": [["10.1", "1"]]}) is None

    def test_none_on_malformed_data(self):
        assert order_book_imbalance({"bids": [["x", "y"]], "asks": [["10.1", "1"]]}) is None


class TestFundingAndBasis:
    def test_valid_data(self):
        pi = {"lastFundingRate": "0.0001", "markPrice": "101.0", "indexPrice": "100.0"}
        funding, basis = funding_and_basis(pi)
        assert funding == pytest.approx(0.0001)
        assert basis == pytest.approx((101.0 - 100.0) / 100.0 * 1e4)

    def test_none_when_missing(self):
        assert funding_and_basis(None) == (None, None)

    def test_partial_data_partial_result(self):
        funding, basis = funding_and_basis({"lastFundingRate": "0.0002"})
        assert funding == pytest.approx(0.0002)
        assert basis is None


class TestOpenInterest:
    def test_valid(self):
        assert open_interest_value({"openInterest": "12345.6"}) == pytest.approx(12345.6)

    def test_none_when_missing(self):
        assert open_interest_value(None) is None
        assert open_interest_value({}) is None


class TestVolumeEvidence:
    def test_spike_detected(self):
        # a zero-variance baseline can't produce a meaningful z-score (see the NaN-baseline
        # test below), so the baseline here carries a small amount of noise, same as any real
        # volume series would.
        rng = np.random.default_rng(9)
        vols = list(100.0 + rng.normal(0, 2.0, 20)) + [500.0]
        ohlcv = _ohlcv(list(range(21)), volumes=vols)
        z, spike = volume_evidence(ohlcv, window=20)
        assert spike is True
        assert z > 2.0

    def test_zero_variance_baseline_is_nan_not_a_crash(self):
        vols = [100.0] * 20 + [500.0]
        ohlcv = _ohlcv(list(range(21)), volumes=vols)
        z, spike = volume_evidence(ohlcv, window=20)
        assert np.isnan(z)
        assert spike is False

    def test_no_spike_on_normal_volume(self):
        rng = np.random.default_rng(1)
        vols = list(100.0 + rng.normal(0, 5, 21))
        ohlcv = _ohlcv(list(range(21)), volumes=vols)
        z, spike = volume_evidence(ohlcv, window=20)
        assert spike is False

    def test_nan_before_warmup(self):
        ohlcv = _ohlcv(list(range(5)))
        z, spike = volume_evidence(ohlcv, window=20)
        assert np.isnan(z)
        assert spike is False


class TestRecentExtremes:
    def test_excludes_current_bar_by_default(self):
        closes = list(range(1, 22))  # 21 bars, current (last) close=21
        ohlcv = _ohlcv(closes, rng=0.5)
        rh, rl = recent_extremes(ohlcv, window=20)
        # window is bars[0:20] (indices 0..19), i.e. closes 1..20 with rng=0.5
        assert rh == pytest.approx(20 + 0.5)
        assert rl == pytest.approx(1 - 0.5)

    def test_none_before_warmup(self):
        ohlcv = _ohlcv(list(range(5)))
        assert recent_extremes(ohlcv, window=20) == (None, None)


class TestComposeSnapshot:
    def test_all_none_with_no_exchange_data(self):
        ohlcv = _ohlcv(list(range(25)))
        s = compute(ohlcv)
        assert s.spread_bp is None
        assert s.order_book_imbalance is None
        assert s.funding_rate is None
        assert s.basis_bp is None
        assert s.open_interest is None
        assert s.liquidation_data == "UNKNOWN"

    def test_full_data_populates_all_fields(self):
        ohlcv = _ohlcv(list(range(25)))
        bt = BookTicker("BTCUSDT", 100.0, 100.1, "bid_ask")
        depth = {"bids": [["100.0", "5"]], "asks": [["100.1", "5"]]}
        pi = {"lastFundingRate": "0.0001", "markPrice": "100.05", "indexPrice": "100.0"}
        oi = {"openInterest": "999.0"}
        s = compute(ohlcv, wick_rejection="upper", book_ticker=bt, depth=depth,
                   premium_index=pi, open_interest=oi)
        assert s.spread_bp == pytest.approx(bt.spread_bp)
        assert s.order_book_imbalance == pytest.approx(0.0)
        assert s.funding_rate == pytest.approx(0.0001)
        assert s.open_interest == pytest.approx(999.0)
        assert s.wick_rejection == "upper"

    def test_none_on_empty_series(self):
        assert compute(_ohlcv([])) is None
