from __future__ import annotations

import intelligence.dashboard.intelligence_dashboard as dash
from intelligence.memory.decision_memory import DecisionRecord, connect, write_decision


def _rec(did, symbol, open_time, decision="IGNORE", label="NO_SIGNAL", score=0.0,
        conflict=None):
    return DecisionRecord(
        decision_id=did, open_time=open_time, symbol=symbol, exp107_status="UNAVAILABLE",
        exp107_fired=False, exp107_side=None, exp107_comb=None, direction_estimate="",
        direction_confidence=float("nan"), confirmation_label=label,
        confirmation_score=score, supporting_evidence=[], conflicting_evidence=conflict or [],
        opportunity_state="WATCHING", position_state="NONE", previous_decision_id=None,
        decision=decision, reason="test reason", risk_note="LOW", confidence=0.0,
        created_at="2026-01-01T00:00:00Z")


class TestSnapshotOnMissingDb:
    def test_error_reported_not_a_crash(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dash, "DB", tmp_path / "does_not_exist.db")
        snap = dash.snapshot()
        assert "error" in snap
        text = dash.page(snap)
        assert "does not exist" in text


class TestSnapshotWithData:
    def test_latest_per_symbol_and_counts(self, tmp_path, monkeypatch):
        db_path = tmp_path / "intelligence.db"
        con = connect(db_path)
        write_decision(con, _rec("D1", "BTCUSDT", 1, decision="IGNORE"))
        write_decision(con, _rec("D2", "BTCUSDT", 2, decision="WAIT", score=0.3))
        write_decision(con, _rec("D3", "ETHUSDT", 1, decision="IGNORE"))
        con.close()

        monkeypatch.setattr(dash, "DB", db_path)
        snap = dash.snapshot()
        assert snap["n_total"] == 3
        opps = {o["symbol"]: o for o in snap["opportunities"]}
        assert opps["BTCUSDT"]["decision_id"] == "D2"   # latest open_time for BTCUSDT
        assert opps["ETHUSDT"]["decision_id"] == "D3"
        assert snap["decision_counts"]["IGNORE"] == 2
        assert snap["decision_counts"]["WAIT"] == 1

    def test_contradiction_count_summed_from_json(self, tmp_path, monkeypatch):
        db_path = tmp_path / "intelligence.db"
        con = connect(db_path)
        write_decision(con, _rec("D1", "BTCUSDT", 1, conflict=["a.b=CONFLICT", "c.d=CONFLICT"]))
        write_decision(con, _rec("D2", "ETHUSDT", 1, conflict=["e.f=CONFLICT"]))
        con.close()

        monkeypatch.setattr(dash, "DB", db_path)
        snap = dash.snapshot()
        assert snap["contradiction_total"] == 3

    def test_page_renders_without_crashing(self, tmp_path, monkeypatch):
        db_path = tmp_path / "intelligence.db"
        con = connect(db_path)
        write_decision(con, _rec("D1", "BTCUSDT", 1, decision="ENTER", label="CONFIRM",
                                 score=1.0))
        con.close()
        monkeypatch.setattr(dash, "DB", db_path)
        text = dash.page(dash.snapshot())
        assert "BTCUSDT" in text
        assert "ENTER" in text
        assert "<html>" in text


class TestReadOnlyGuarantee:
    def test_snapshot_never_writes(self, tmp_path, monkeypatch):
        db_path = tmp_path / "intelligence.db"
        con = connect(db_path)
        write_decision(con, _rec("D1", "BTCUSDT", 1))
        con.close()
        monkeypatch.setattr(dash, "DB", db_path)
        mtime_before = db_path.stat().st_mtime_ns
        dash.snapshot()
        dash.snapshot()
        assert db_path.stat().st_mtime_ns == mtime_before
