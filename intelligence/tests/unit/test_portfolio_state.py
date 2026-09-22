from __future__ import annotations

import pytest

from intelligence.risk.portfolio_state import PaperPosition, PortfolioState


def _pos(symbol, notional=1000.0, entry_time=1, entry_price=100.0, direction="LONG"):
    return PaperPosition(symbol, direction, notional, entry_time, entry_price)


class TestOpenAndClose:
    def test_open_position_tracked(self):
        pf = PortfolioState(starting_balance_usd=10_000.0)
        pf.open_position(_pos("BTCUSDT"))
        assert pf.n_open == 1
        assert pf.open_notional_usd == pytest.approx(1000.0)
        assert pf.available_margin_usd == pytest.approx(9000.0)

    def test_close_position_removes_and_returns_it(self):
        pf = PortfolioState(starting_balance_usd=10_000.0)
        pf.open_position(_pos("BTCUSDT"))
        closed = pf.close_position("BTCUSDT")
        assert closed.symbol == "BTCUSDT"
        assert pf.n_open == 0
        assert pf.available_margin_usd == pytest.approx(10_000.0)

    def test_close_nonexistent_returns_none(self):
        pf = PortfolioState(starting_balance_usd=10_000.0)
        assert pf.close_position("BTCUSDT") is None

    def test_duplicate_open_raises(self):
        pf = PortfolioState(starting_balance_usd=10_000.0)
        pf.open_position(_pos("BTCUSDT"))
        with pytest.raises(ValueError):
            pf.open_position(_pos("BTCUSDT"))

    def test_insufficient_margin_raises_no_leverage(self):
        pf = PortfolioState(starting_balance_usd=500.0)
        with pytest.raises(ValueError):
            pf.open_position(_pos("BTCUSDT", notional=1000.0))
        assert pf.n_open == 0  # the failed open must not partially apply


class TestExposure:
    def test_direction_exposure(self):
        pf = PortfolioState(starting_balance_usd=10_000.0)
        pf.open_position(_pos("BTCUSDT", notional=1000.0))
        pf.open_position(_pos("ETHUSDT", notional=500.0))
        assert pf.direction_exposure_usd("LONG") == pytest.approx(1500.0)
        assert pf.direction_exposure_usd("SHORT") == pytest.approx(0.0)

    def test_symbol_exposure(self):
        pf = PortfolioState(starting_balance_usd=10_000.0)
        pf.open_position(_pos("BTCUSDT", notional=750.0))
        assert pf.symbol_exposure_usd("BTCUSDT") == pytest.approx(750.0)
        assert pf.symbol_exposure_usd("ETHUSDT") == pytest.approx(0.0)


class TestCorrelatedExposure:
    def test_correlated_position_counted(self):
        pf = PortfolioState(starting_balance_usd=10_000.0)
        pf.open_position(_pos("ETHUSDT", notional=1000.0))
        corr = {"ETHUSDT": 0.8}
        assert pf.correlated_exposure_usd("BTCUSDT", corr, threshold=0.6) == pytest.approx(1000.0)

    def test_below_threshold_not_counted(self):
        pf = PortfolioState(starting_balance_usd=10_000.0)
        pf.open_position(_pos("ETHUSDT", notional=1000.0))
        corr = {"ETHUSDT": 0.3}
        assert pf.correlated_exposure_usd("BTCUSDT", corr, threshold=0.6) == pytest.approx(0.0)

    def test_unmeasured_correlation_not_counted(self):
        pf = PortfolioState(starting_balance_usd=10_000.0)
        pf.open_position(_pos("ETHUSDT", notional=1000.0))
        assert pf.correlated_exposure_usd("BTCUSDT", {}, threshold=0.6) == pytest.approx(0.0)

    def test_own_symbol_excluded(self):
        pf = PortfolioState(starting_balance_usd=10_000.0)
        pf.open_position(_pos("BTCUSDT", notional=1000.0))
        corr = {"BTCUSDT": 1.0}
        assert pf.correlated_exposure_usd("BTCUSDT", corr, threshold=0.6) == pytest.approx(0.0)

    def test_negative_correlation_counted_by_magnitude(self):
        pf = PortfolioState(starting_balance_usd=10_000.0)
        pf.open_position(_pos("ETHUSDT", notional=1000.0))
        corr = {"ETHUSDT": -0.9}
        assert pf.correlated_exposure_usd("BTCUSDT", corr, threshold=0.6) == pytest.approx(1000.0)
