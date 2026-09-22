"""Unit tests for intelligence.core.exp107_signal.

TestRealRepoState below deliberately does NOT mock anything -- it exercises the REAL
scripts/shadow_engine.py against this repository's REAL (currently incomplete) artifact/
directory, proving today's actual UNAVAILABLE behavior end-to-end rather than just asserting it
in the abstract. Per EXP-124/CODEBASE_MAP.md Blocker #2, artifact/booster_*.pkl and
isotonic_*.pkl are gitignored and absent from this checkout, so ShadowEngine.load() is expected
to fail here -- if a future session supplies those files, this test's assertion will need to
change to status=="OK", which is itself a useful signal that the blocker was resolved.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from intelligence.core.exp107_signal import (Exp107Signal, Exp107SignalProvider,
                                             recent_shadow_trades)
from intelligence.core.market_buffer import SYMBOLS


class TestRealRepoState:
    def test_provider_reports_unavailable_against_the_real_missing_artifact(self):
        provider = Exp107SignalProvider()
        assert provider.status == "UNAVAILABLE"
        assert provider.error is not None and len(provider.error) > 0

    def test_status_is_cached_not_reattempted(self):
        provider = Exp107SignalProvider()
        first = provider.status
        first_error = provider.error
        second = provider.status
        assert first == second == "UNAVAILABLE"
        assert first_error == provider.error

    def test_evaluate_returns_unavailable_per_symbol_never_a_fabricated_score(self):
        provider = Exp107SignalProvider()
        signals = provider.evaluate(None, None, None, 0, 0, 10)
        assert len(signals) == len(SYMBOLS)
        assert {s.symbol for s in signals} == set(SYMBOLS)
        for s in signals:
            assert s.status == "UNAVAILABLE"
            assert s.fired is None
            assert s.raw_score is None
            assert s.is_long_fire is False


class TestExp107SignalIsLongFire:
    def test_true_only_for_ok_fired_long(self):
        s = Exp107Signal(status="OK", symbol="BTCUSDT", fired=True, side="LONG")
        assert s.is_long_fire is True

    def test_false_when_unavailable(self):
        s = Exp107Signal(status="UNAVAILABLE", symbol="BTCUSDT")
        assert s.is_long_fire is False

    def test_false_when_not_fired(self):
        s = Exp107Signal(status="OK", symbol="BTCUSDT", fired=False, side="LONG")
        assert s.is_long_fire is False

    def test_false_when_short(self):
        s = Exp107Signal(status="OK", symbol="BTCUSDT", fired=True, side="SHORT")
        assert s.is_long_fire is False


class _FakeDecision:
    def __init__(self, symbol, side, fired):
        self.symbol = symbol
        self.anchor_ms = 1_700_000_000_000
        self.minute = 60
        self.raw_score = 0.7
        self.cal_score = 0.6
        self.rv30_bp = 12.3
        self.comb = 0.97
        self.threshold = 0.9646
        self.side = side
        self.fired = fired
        self.reason = ""


class _FakeEngine:
    def evaluate(self, C, QV, TB, apos, anchor_ms, minute):
        return [_FakeDecision("BTCUSDT", 1.0, True), _FakeDecision("ETHUSDT", -1.0, False)]


class TestOkPathFieldMapping:
    """Simulates a successfully loaded engine (without needing real .pkl files) to verify the
    Decision -> Exp107Signal field mapping, including the numeric side (+1.0/-1.0) -> "LONG"/
    "SHORT" string translation."""

    def test_field_mapping(self):
        provider = Exp107SignalProvider()
        provider._status = "OK"
        provider._engine = _FakeEngine()
        signals = provider.evaluate(None, None, None, 0, 0, 60)
        assert len(signals) == 2
        btc, eth = signals
        assert btc.status == "OK"
        assert btc.side == "LONG"
        assert btc.fired is True
        assert btc.is_long_fire is True
        assert eth.side == "SHORT"
        assert eth.is_long_fire is False
        assert btc.comb == pytest.approx(0.97)


class TestRecentShadowTrades:
    def test_empty_when_db_missing(self, tmp_path):
        assert recent_shadow_trades(tmp_path / "does_not_exist.db") == []

    def test_empty_when_table_missing(self, tmp_path):
        db = tmp_path / "empty.db"
        sqlite3.connect(db).close()
        assert recent_shadow_trades(db) == []

    def test_reads_rows_read_only(self, tmp_path):
        db = tmp_path / "shadow.db"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE shadow_trades (trade_id TEXT PRIMARY KEY, anchor_ms INTEGER, "
                   "symbol TEXT, status TEXT)")
        con.executemany("INSERT INTO shadow_trades VALUES (?,?,?,?)", [
            ("BTCUSDT_1", 100, "BTCUSDT", "OPEN"),
            ("ETHUSDT_1", 200, "ETHUSDT", "CLOSED"),
        ])
        con.commit()
        con.close()
        rows = recent_shadow_trades(db, limit=10)
        assert len(rows) == 2
        assert rows[0]["anchor_ms"] == 200  # ORDER BY anchor_ms DESC
        assert rows[0]["symbol"] == "ETHUSDT"

    def test_limit_respected(self, tmp_path):
        db = tmp_path / "shadow.db"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE shadow_trades (trade_id TEXT PRIMARY KEY, anchor_ms INTEGER)")
        con.executemany("INSERT INTO shadow_trades VALUES (?,?)",
                        [(f"T{i}", i) for i in range(20)])
        con.commit()
        con.close()
        rows = recent_shadow_trades(db, limit=5)
        assert len(rows) == 5

    def test_never_writes_to_the_database(self, tmp_path):
        db = tmp_path / "shadow.db"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE shadow_trades (trade_id TEXT PRIMARY KEY, anchor_ms INTEGER)")
        con.execute("INSERT INTO shadow_trades VALUES ('T1', 1)")
        con.commit()
        con.close()
        recent_shadow_trades(db)
        # a mode=ro connection would raise on any write attempt -- prove the file is untouched
        mtime_before = db.stat().st_mtime_ns
        recent_shadow_trades(db)
        assert db.stat().st_mtime_ns == mtime_before
