from __future__ import annotations

import sqlite3

import numpy as np
import pytest

from intelligence.core.exp107_signal import Exp107Signal, Exp107SignalProvider
from intelligence.core.market_buffer import BAR_MS, SYMBOLS, MarketBuffer
from intelligence.loop.cycle_runner import CycleRunner
from intelligence.memory.decision_memory import connect


def _row(open_time, px, v=100.0):
    return [open_time, px, px + 0.3, px - 0.3, px + 0.1, v, open_time + BAR_MS - 1,
           v * 10, 10, v * 5, v * 5]


def _hour_aligned_start() -> int:
    raw = 1_700_000_000_000
    return (raw // 3_600_000) * 3_600_000


def _buffer_monotonic(n_bars: int = 600, step: float = 0.3) -> MarketBuffer:
    start = _hour_aligned_start()
    per = {}
    for s in SYMBOLS:
        rows = []
        px = 100.0
        for i in range(n_bars):
            px += step
            rows.append(_row(start + i * BAR_MS, px))
        per[s] = rows
    buf = MarketBuffer(keep_bars=n_bars)
    buf._rebuild(per)
    return buf


class FakeProvider:
    """Mimics Exp107SignalProvider's interface without touching real .pkl files, so caching
    behavior can be tested independent of this repo's actual UNAVAILABLE artifact state."""

    def __init__(self, status="OK"):
        self.status = status
        self.calls = 0

    def evaluate(self, C, QV, TB, apos, anchor_ms, minute):
        self.calls += 1
        return [Exp107Signal(status=self.status, symbol=s, fired=False, side="LONG")
               for s in SYMBOLS]


class TestExp107Scheduling:
    """H is a clean hourly boundary (a multiple of 3_600_000), chosen arbitrarily far from
    epoch. Every case below is worked out by hand in its own comment -- `now_ms // 3_600_000`
    determines which hour is "current" BEFORE any decision-minute logic runs, and offsets at or
    past 60 minutes push `now_ms` into the NEXT hour, which is exactly the subtlety the first
    draft of `_current_exp107_point` got wrong (see cycle_runner.py's own comment)."""
    H = 100 * 3_600_000

    def test_within_current_hour_uses_it_directly(self):
        # 45 min past H -- still within H's own hour, so no lookback is needed
        now_ms = self.H + 45 * 60_000
        runner = CycleRunner(_buffer_monotonic(), Exp107SignalProvider(), connect(":memory:"))
        a, m = runner._current_exp107_point(now_ms)
        assert a == self.H
        assert m == 30   # eligible={10,30} at 45 min elapsed; 60 not yet reached

    def test_less_than_ten_minutes_into_current_hour_looks_back_one_hour(self):
        # 65 min past H: now_ms's OWN hour floor is H+1h (65 >= 60), with only 5 min elapsed
        # there -- no minute is eligible yet in that hour, so this must look back to H itself,
        # where 65 min have elapsed and {10,30,60} are all eligible (120 is not: 120 > 65)
        now_ms = self.H + 65 * 60_000
        runner = CycleRunner(_buffer_monotonic(), Exp107SignalProvider(), connect(":memory:"))
        a, m = runner._current_exp107_point(now_ms)
        assert a == self.H
        assert m == 60

    def test_only_three_minutes_elapsed_looks_back_further(self):
        # 3 min past H: nothing is eligible in H's own hour (3 < 10), and nothing is eligible
        # in H-1h's hour either at that same absolute instant (63 min have elapsed there, so
        # {10,30,60} ARE eligible -- this is really the same shape as the test above, phrased
        # from "only 3 minutes in" to make the lookback trigger obvious)
        now_ms = self.H + 3 * 60_000
        runner = CycleRunner(_buffer_monotonic(), Exp107SignalProvider(), connect(":memory:"))
        a, m = runner._current_exp107_point(now_ms)
        assert a == self.H - 3_600_000
        assert m == 60


class TestExp107Caching:
    def test_same_decision_point_not_recomputed(self):
        buf = _buffer_monotonic()
        provider = FakeProvider()
        runner = CycleRunner(buf, provider, connect(":memory:"))
        start = _hour_aligned_start()
        now_ms = start + 550 * BAR_MS
        runner._get_exp107_signal("BTCUSDT", now_ms)
        runner._get_exp107_signal("BTCUSDT", now_ms + 15_000)   # 15s later, same decision point
        assert provider.calls == 1

    def test_new_decision_point_triggers_recompute(self):
        buf = _buffer_monotonic()
        provider = FakeProvider()
        runner = CycleRunner(buf, provider, connect(":memory:"))
        start = _hour_aligned_start()
        runner._get_exp107_signal("BTCUSDT", start + 550 * BAR_MS)   # minute=10 of that anchor
        runner._get_exp107_signal("BTCUSDT", start + 570 * BAR_MS)   # minute=30 of that anchor
        assert provider.calls == 2

    def test_anchor_not_in_buffer_gives_unavailable_without_crashing(self):
        buf = _buffer_monotonic(n_bars=10)   # far too short to contain a distant anchor
        provider = FakeProvider()
        runner = CycleRunner(buf, provider, connect(":memory:"))
        far_future = _hour_aligned_start() + 100 * 3_600_000
        sig = runner._get_exp107_signal("BTCUSDT", far_future)
        assert sig.status == "UNAVAILABLE"
        assert "not yet in the held buffer" in sig.error
        assert provider.calls == 0   # never even attempted, since apos was never resolvable


class TestRunCycleForSymbolAgainstRealRepoState:
    """Uses the REAL Exp107SignalProvider (no mock) -- against this repo's real, currently
    incomplete artifact/, exp107.status is UNAVAILABLE, so every symbol must end in IGNORE,
    never ENTER, structurally -- the same invariant test_decision_engine.py already proves at
    the unit level, exercised here through the full cycle."""

    def test_ignore_and_watching_when_exp107_unavailable(self):
        buf = _buffer_monotonic()
        runner = CycleRunner(buf, Exp107SignalProvider(), connect(":memory:"))
        now_ms = _hour_aligned_start() + 550 * BAR_MS
        snap = runner.run_cycle_for_symbol("BTCUSDT", now_ms)
        assert snap.exp107.status == "UNAVAILABLE"
        assert snap.decision.action == "IGNORE"
        assert snap.decision.action != "ENTER"
        assert snap.opportunity_state == "WATCHING"

    def test_decision_written_to_memory(self):
        buf = _buffer_monotonic()
        mem = connect(":memory:")
        runner = CycleRunner(buf, Exp107SignalProvider(), mem)
        now_ms = _hour_aligned_start() + 550 * BAR_MS
        snap = runner.run_cycle_for_symbol("BTCUSDT", now_ms)
        mem.row_factory = sqlite3.Row
        row = mem.execute("SELECT * FROM decisions").fetchone()
        assert row is not None
        assert row["symbol"] == "BTCUSDT"
        assert row["decision"] == "IGNORE"
        assert row["exp107_status"] == "UNAVAILABLE"

    def test_previous_decision_id_links_consecutive_cycles(self):
        buf = _buffer_monotonic()
        mem = connect(":memory:")
        runner = CycleRunner(buf, Exp107SignalProvider(), mem)
        start = _hour_aligned_start()
        snap1 = runner.run_cycle_for_symbol("BTCUSDT", start + 550 * BAR_MS)
        mem.row_factory = sqlite3.Row
        first_id = mem.execute(
            "SELECT decision_id FROM decisions ORDER BY open_time").fetchone()["decision_id"]
        buf2 = _buffer_monotonic(n_bars=700)
        runner.buffer = buf2
        snap2 = runner.run_cycle_for_symbol("BTCUSDT", start + 690 * BAR_MS)
        rows = mem.execute(
            "SELECT decision_id, previous_decision_id FROM decisions "
            "ORDER BY open_time").fetchall()
        assert len(rows) == 2
        assert rows[1]["previous_decision_id"] == first_id

    def test_journal_text_contains_symbol_and_decision(self):
        buf = _buffer_monotonic()
        runner = CycleRunner(buf, Exp107SignalProvider(), connect(":memory:"))
        now_ms = _hour_aligned_start() + 550 * BAR_MS
        snap = runner.run_cycle_for_symbol("BTCUSDT", now_ms)
        assert "DECISION: BTCUSDT" in snap.journal_text
        assert "DECISION: IGNORE" in snap.journal_text


class TestRunCycleAllSymbolsIndependent:
    def test_every_symbol_gets_its_own_snapshot(self):
        buf = _buffer_monotonic()
        runner = CycleRunner(buf, Exp107SignalProvider(), connect(":memory:"))
        now_ms = _hour_aligned_start() + 550 * BAR_MS
        snaps = runner.run_cycle(now_ms)
        assert len(snaps) == len(SYMBOLS)
        assert {s.symbol for s in snaps} == set(SYMBOLS)

    def test_one_symbol_open_position_does_not_block_others(self):
        """Directly exercises brief sections 15-16 at the cycle-runner level: an existing
        (paper) position on one symbol must not prevent independent evaluation of the rest."""
        from intelligence.risk.portfolio_state import PaperPosition
        buf = _buffer_monotonic()
        runner = CycleRunner(buf, Exp107SignalProvider(), connect(":memory:"))
        runner.portfolio.open_position(PaperPosition("BTCUSDT", "LONG", 1000.0, 1, 100.0))
        now_ms = _hour_aligned_start() + 550 * BAR_MS
        snaps = runner.run_cycle(now_ms)
        btc_snap = next(s for s in snaps if s.symbol == "BTCUSDT")
        eth_snap = next(s for s in snaps if s.symbol == "ETHUSDT")
        assert btc_snap.decision.action in ("HOLD", "REDUCE", "EXIT")
        assert eth_snap.decision.action == "IGNORE"   # evaluated independently, not skipped


class TestFeatureRegistryRebuild:
    def test_empty_when_no_resolved_decisions(self):
        buf = _buffer_monotonic()
        runner = CycleRunner(buf, Exp107SignalProvider(), connect(":memory:"))
        reg = runner._registry()
        assert reg.stats_for("ANYTHING").n == 0

    def test_builds_outcomes_from_resolved_decisions(self):
        from intelligence.memory.decision_memory import DecisionRecord, update_outcome, write_decision
        buf = _buffer_monotonic()
        mem = connect(":memory:")
        runner = CycleRunner(buf, Exp107SignalProvider(), mem)
        rec = DecisionRecord(
            decision_id="D1", open_time=1, symbol="BTCUSDT", exp107_status="OK",
            exp107_fired=True, exp107_side="LONG", exp107_comb=0.97,
            direction_estimate="LONG", direction_confidence=0.6, confirmation_label="CONFIRM",
            confirmation_score=1.0, supporting_evidence=["structure_engine.TREND_STATE=SUPPORT"],
            conflicting_evidence=[], opportunity_state="CONFIRMED", position_state="NONE",
            previous_decision_id=None, decision="ENTER", reason="test", risk_note="LOW",
            confidence=0.8, created_at="2026-01-01T00:00:00Z")
        write_decision(mem, rec)
        update_outcome(mem, "D1", correct=True, pnl_bp=30.0, mfe_bp=40.0, mae_bp=-5.0,
                       resolved_at="2026-01-02T00:00:00Z")
        reg = runner._registry()
        assert reg.stats_for("TREND_STATE").n == 1


class TestNoForbiddenImports:
    def test_ccxt_and_binance_client_never_imported(self):
        import sys
        assert "ccxt" not in sys.modules
        assert "binance.client" not in sys.modules
