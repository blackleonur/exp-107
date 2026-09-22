from __future__ import annotations

import pytest

from intelligence.position.position_monitor import PositionMonitor
from intelligence.risk.portfolio_state import PaperPosition


def _pos(symbol="BTCUSDT", entry_price=100.0, entry_time=1):
    return PaperPosition(symbol, "LONG", 1000.0, entry_time, entry_price)


class TestFirstUpdate:
    def test_first_update_sets_mfe_mae_to_current_pnl(self):
        mon = PositionMonitor()
        snap = mon.update(_pos(), current_time=2, current_price=101.0)
        assert snap.unrealized_pnl_bp == pytest.approx(100.0)  # (101/100 - 1) * 1e4
        assert snap.mfe_bp == pytest.approx(100.0)
        assert snap.mae_bp == pytest.approx(100.0)
        assert snap.n_updates == 1


class TestIncrementalMfeMae:
    def test_mfe_tracks_running_max(self):
        mon = PositionMonitor()
        pos = _pos()
        mon.update(pos, 1, 100.0)   # pnl 0
        mon.update(pos, 2, 102.0)   # pnl +200bp -> new MFE
        mon.update(pos, 3, 101.0)   # pnl +100bp -> MFE stays at +200bp
        snap = mon.update(pos, 4, 100.5)
        assert snap.mfe_bp == pytest.approx(200.0, abs=0.5)

    def test_mae_tracks_running_min(self):
        mon = PositionMonitor()
        pos = _pos()
        mon.update(pos, 1, 100.0)
        mon.update(pos, 2, 98.0)    # pnl -200bp -> new MAE
        mon.update(pos, 3, 99.0)    # pnl -100bp -> MAE stays at -200bp
        snap = mon.update(pos, 4, 99.5)
        assert snap.mae_bp == pytest.approx(-200.0, abs=0.5)

    def test_mfe_and_mae_diverge_over_a_volatile_path(self):
        mon = PositionMonitor()
        pos = _pos()
        prices = [100.0, 103.0, 97.0, 101.0]
        for i, p in enumerate(prices):
            snap = mon.update(pos, i, p)
        assert snap.mfe_bp == pytest.approx(300.0, abs=0.5)
        assert snap.mae_bp == pytest.approx(-300.0, abs=0.5)

    def test_n_updates_increments_every_call(self):
        mon = PositionMonitor()
        pos = _pos()
        for i in range(5):
            snap = mon.update(pos, i, 100.0)
        assert snap.n_updates == 5


class TestRemove:
    def test_remove_clears_tracking_state(self):
        mon = PositionMonitor()
        pos = _pos()
        mon.update(pos, 1, 102.0)
        assert mon.is_tracking("BTCUSDT") is True
        mon.remove("BTCUSDT")
        assert mon.is_tracking("BTCUSDT") is False

    def test_fresh_position_after_remove_does_not_inherit_old_mfe(self):
        mon = PositionMonitor()
        pos = _pos(entry_price=100.0)
        mon.update(pos, 1, 110.0)   # MFE = 1000bp
        mon.remove("BTCUSDT")
        new_pos = _pos(entry_price=50.0)   # a brand-new position on the same symbol
        snap = mon.update(new_pos, 2, 50.0)
        assert snap.mfe_bp == pytest.approx(0.0)   # not contaminated by the old position's MFE


class TestSymbolsIndependent:
    def test_two_symbols_tracked_independently(self):
        mon = PositionMonitor()
        btc = _pos("BTCUSDT", entry_price=100.0)
        eth = _pos("ETHUSDT", entry_price=50.0)
        mon.update(btc, 1, 105.0)
        mon.update(eth, 1, 49.0)
        btc_snap = mon.update(btc, 2, 105.0)
        eth_snap = mon.update(eth, 2, 49.0)
        assert btc_snap.mfe_bp > 0
        assert eth_snap.mfe_bp <= 0
