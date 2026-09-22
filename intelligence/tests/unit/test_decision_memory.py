from __future__ import annotations

import json
import sqlite3

import pytest

from intelligence.memory.decision_memory import (DecisionRecord, all_decisions_for_symbol,
                                                  connect, get_decision,
                                                  last_decision_for_symbol, similar_setups,
                                                  update_outcome, write_decision)


def _record(decision_id, symbol="BTCUSDT", open_time=1, confirmation_label="CONFIRM",
           decision="ENTER", support=None, conflict=None):
    return DecisionRecord(
        decision_id=decision_id, open_time=open_time, symbol=symbol, exp107_status="OK",
        exp107_fired=True, exp107_side="LONG", exp107_comb=0.97,
        direction_estimate="LONG", direction_confidence=0.6,
        confirmation_label=confirmation_label, confirmation_score=1.0,
        supporting_evidence=support or ["a.b=SUPPORT"], conflicting_evidence=conflict or [],
        opportunity_state="CONFIRMED", position_state="NONE", previous_decision_id=None,
        decision=decision, reason="test reason", risk_note="LOW", confidence=0.8,
        created_at="2026-01-01T00:00:00Z")


class TestWriteAndRead:
    def test_write_then_get(self, tmp_path):
        con = connect(tmp_path / "t.db")
        write_decision(con, _record("D1"))
        row = get_decision(con, "D1")
        assert row["symbol"] == "BTCUSDT"
        assert row["decision"] == "ENTER"
        assert json.loads(row["supporting_evidence"]) == ["a.b=SUPPORT"]
        assert row["outcome_resolved"] == 0

    def test_get_missing_returns_none(self, tmp_path):
        con = connect(tmp_path / "t.db")
        assert get_decision(con, "NOPE") is None


class TestAppendOnlyGuarantee:
    def test_duplicate_decision_id_raises_not_overwrites(self, tmp_path):
        con = connect(tmp_path / "t.db")
        write_decision(con, _record("D1", decision="ENTER"))
        with pytest.raises(sqlite3.IntegrityError):
            write_decision(con, _record("D1", decision="EXIT"))
        row = get_decision(con, "D1")
        assert row["decision"] == "ENTER"  # the original row is untouched

    def test_write_decision_has_no_update_path(self):
        import inspect
        src = inspect.getsource(write_decision)
        assert "INSERT" in src
        assert "UPDATE" not in src


class TestUpdateOutcomeIsTheOnlyMutation:
    def test_update_outcome_fills_only_outcome_columns(self, tmp_path):
        con = connect(tmp_path / "t.db")
        write_decision(con, _record("D1"))
        update_outcome(con, "D1", correct=True, pnl_bp=42.0, mfe_bp=60.0, mae_bp=-10.0,
                       resolved_at="2026-01-02T00:00:00Z")
        row = get_decision(con, "D1")
        assert row["outcome_resolved"] == 1
        assert row["outcome_correct"] == 1
        assert row["outcome_pnl_bp"] == pytest.approx(42.0)
        # the original decision fields are untouched by update_outcome
        assert row["decision"] == "ENTER"
        assert row["reason"] == "test reason"

    def test_update_outcome_on_missing_id_raises(self, tmp_path):
        con = connect(tmp_path / "t.db")
        with pytest.raises(ValueError):
            update_outcome(con, "NOPE", True, 1.0, 1.0, -1.0, "2026-01-01T00:00:00Z")

    def test_update_outcome_has_no_write_path_to_decision_columns(self):
        import inspect
        src = inspect.getsource(update_outcome)
        assert "SET outcome_resolved" in src
        assert " decision=" not in src   # no assignment to the `decision` column itself


class TestLastDecisionForSymbol:
    def test_returns_most_recent(self, tmp_path):
        con = connect(tmp_path / "t.db")
        write_decision(con, _record("D1", open_time=1))
        write_decision(con, _record("D2", open_time=5))
        write_decision(con, _record("D3", open_time=3))
        last = last_decision_for_symbol(con, "BTCUSDT")
        assert last["decision_id"] == "D2"

    def test_none_when_no_history(self, tmp_path):
        con = connect(tmp_path / "t.db")
        assert last_decision_for_symbol(con, "BTCUSDT") is None

    def test_symbols_are_isolated(self, tmp_path):
        con = connect(tmp_path / "t.db")
        write_decision(con, _record("D1", symbol="BTCUSDT", open_time=1))
        write_decision(con, _record("D2", symbol="ETHUSDT", open_time=2))
        assert last_decision_for_symbol(con, "BTCUSDT")["decision_id"] == "D1"


class TestSimilarSetups:
    def test_only_resolved_rows_returned(self, tmp_path):
        con = connect(tmp_path / "t.db")
        write_decision(con, _record("D1", confirmation_label="CONFIRM", open_time=1))
        write_decision(con, _record("D2", confirmation_label="CONFIRM", open_time=2))
        update_outcome(con, "D1", True, 10.0, 20.0, -5.0, "2026-01-02T00:00:00Z")
        result = similar_setups(con, "BTCUSDT", "CONFIRM")
        assert len(result) == 1
        assert result[0]["decision_id"] == "D1"

    def test_filters_by_confirmation_label(self, tmp_path):
        con = connect(tmp_path / "t.db")
        write_decision(con, _record("D1", confirmation_label="CONFIRM", open_time=1))
        write_decision(con, _record("D2", confirmation_label="WEAKEN", open_time=2))
        update_outcome(con, "D1", True, 10.0, 20.0, -5.0, "t")
        update_outcome(con, "D2", False, -10.0, 5.0, -20.0, "t")
        result = similar_setups(con, "BTCUSDT", "WEAKEN")
        assert len(result) == 1
        assert result[0]["decision_id"] == "D2"

    def test_most_recent_first(self, tmp_path):
        con = connect(tmp_path / "t.db")
        for i, did in enumerate(["D1", "D2", "D3"]):
            write_decision(con, _record(did, confirmation_label="CONFIRM", open_time=i))
            update_outcome(con, did, True, 1.0, 1.0, -1.0, "t")
        result = similar_setups(con, "BTCUSDT", "CONFIRM", limit=10)
        assert [r["decision_id"] for r in result] == ["D3", "D2", "D1"]

    def test_limit_respected(self, tmp_path):
        con = connect(tmp_path / "t.db")
        for i in range(5):
            did = f"D{i}"
            write_decision(con, _record(did, confirmation_label="CONFIRM", open_time=i))
            update_outcome(con, did, True, 1.0, 1.0, -1.0, "t")
        result = similar_setups(con, "BTCUSDT", "CONFIRM", limit=2)
        assert len(result) == 2


class TestAllDecisionsForSymbol:
    def test_ordered_ascending(self, tmp_path):
        con = connect(tmp_path / "t.db")
        write_decision(con, _record("D2", open_time=5))
        write_decision(con, _record("D1", open_time=1))
        result = all_decisions_for_symbol(con, "BTCUSDT")
        assert [r["decision_id"] for r in result] == ["D1", "D2"]
