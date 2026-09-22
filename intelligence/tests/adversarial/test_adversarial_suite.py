"""
EXP-124 Phase 15 -- adversarial / edge-case test suite.

RESEARCH / PAPER ONLY. This file exists to satisfy the task instruction's explicit pre-
FINAL_REPORT checklist: missing data, stale data, conflicting indicators, multiple
simultaneous opportunities, position already open, signal reversal, rapid signal changes,
insufficient timeframe history, liquidity UNKNOWN, Binance/API failure, restart/recovery of
decision memory, no lookahead, no EXP-107 modification. Each numbered class below corresponds
to exactly one item on that list -- most exercise integration paths (several engines chained
together) rather than re-testing a single function already covered in
intelligence/tests/unit/, though a few genuinely new checks (file-integrity hashing, poll-
failure resilience, cross-truncation causality) are introduced here because nothing in the
per-phase unit suites covered them directly.
"""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from intelligence.confirmation.confirmation_engine import ConfirmationResult, confirm
from intelligence.confirmation.evidence import Evidence, EvidenceBundle, Strength
from intelligence.confirmation.evidence_collector import collect
from intelligence.confirmation.feature_registry import FeatureRegistry
from intelligence.core.binance_client import BinanceClient, BinanceClientError
from intelligence.core.exp107_signal import Exp107Signal
from intelligence.core.market_buffer import BAR_MS, SYMBOLS, MarketBuffer
from intelligence.decision.decision_engine import decide
from intelligence.engines import candle_engine, magnitude_engine, structure_engine
from intelligence.loop.cycle_runner import CycleRunner
from intelligence.memory.decision_memory import (DecisionRecord, connect,
                                                  last_decision_for_symbol, write_decision)
from intelligence.opportunity.opportunity_manager import OpportunityManager, OpportunityState
from intelligence.opportunity.ranking import rank_opportunities
from intelligence.position.position_decision import PositionDecisionEngine

REPO_ROOT = Path(__file__).resolve().parents[3]


def _row(open_time, px, v=100.0):
    return [open_time, px, px + 0.3, px - 0.3, px + 0.1, v, open_time + BAR_MS - 1,
           v * 10, 10, v * 5, v * 5]


def _buffer_monotonic(n_bars: int = 600, step: float = 0.3,
                      symbols: list[str] | None = None) -> MarketBuffer:
    """`symbols`: which symbols get actual bars -- every OTHER symbol in the fixed SYMBOLS list
    still gets an entry (an empty list), matching how scripts/R3_shadow_run.py's real
    Bars.backfill()/poll() always populate `per[s]` for every symbol even when a particular
    symbol's fetch legitimately returns nothing. MarketBuffer._rebuild() assumes every SYMBOLS
    key is present in `per` -- that is its documented contract, not a bug to work around here."""
    start = (1_700_000_000_000 // 3_600_000) * 3_600_000
    have_data = set(symbols or SYMBOLS)
    per = {}
    for s in SYMBOLS:
        if s not in have_data:
            per[s] = []
            continue
        rows = []
        px = 100.0
        for i in range(n_bars):
            px += step
            rows.append(_row(start + i * BAR_MS, px))
        per[s] = rows
    buf = MarketBuffer(keep_bars=max(n_bars, 10))
    buf._rebuild(per)
    return buf


class Test1_MissingData:
    """Some symbols never report any bars at all; others have internal gaps."""

    def test_symbol_with_zero_bars_does_not_crash_collector(self):
        buf = _buffer_monotonic(symbols=["BTCUSDT"])   # only BTCUSDT has any data at all
        bundle = collect("ETHUSDT", buf)   # ETHUSDT has NO rows anywhere in the buffer
        assert bundle.symbol == "ETHUSDT"
        assert all(e.strength in (Strength.UNKNOWN, Strength.NEUTRAL) for e in bundle.items)

    def test_internal_gap_does_not_crash_engines(self):
        start = 1_700_000_000_000
        per = {}
        for s in SYMBOLS:
            rows = [_row(start + i * BAR_MS, 100.0 + i * 0.2) for i in range(300)]
            if s == "BTCUSDT":
                rows = [r for r in rows if r[0] not in
                       (start + 50 * BAR_MS, start + 51 * BAR_MS, start + 52 * BAR_MS)]
            per[s] = rows
        buf = MarketBuffer(keep_bars=300)
        buf._rebuild(per)
        ohlcv = buf.resample("BTCUSDT", 1)
        assert np.isnan(ohlcv.close[50])   # the gap shows up as NaN, not a fabricated value
        # engines must tolerate the NaN without raising
        structure_engine.structure_state(ohlcv)
        candle_engine.compute(ohlcv)
        magnitude_engine.compute(ohlcv, horizon_bars=100)


class Test2_StaleData:
    """The buffer's most recent bar is far in the past relative to 'now'."""

    def test_cycle_runs_against_a_stale_buffer_without_crashing(self):
        buf = _buffer_monotonic()   # last bar is a fixed historical timestamp
        very_late_now_ms = int(buf.grid[-1]) + 30 * 24 * 3_600_000   # 30 days after the last bar
        runner = CycleRunner(buf, _fake_unavailable_provider(), connect(":memory:"))
        snap = runner.run_cycle_for_symbol("BTCUSDT", very_late_now_ms)
        assert snap.decision.action == "IGNORE"   # no fresh EXP-107 fire -- safe default
        assert snap.decision.action != "ENTER"

    def test_stale_buffer_never_fabricates_a_fresh_candle(self):
        buf = _buffer_monotonic(n_bars=50)
        last_grid_before = buf.grid.copy()
        # nothing calls poll() here -- the buffer is simply queried "later" without new data
        ohlcv = buf.resample("BTCUSDT", 1)
        assert len(ohlcv) == len(last_grid_before)   # no new bars appeared out of nowhere


def _nan_aware_equal(a: float, b: float) -> bool:
    if isinstance(a, float) and isinstance(b, float) and np.isnan(a) and np.isnan(b):
        return True
    return a == pytest.approx(b)


def _fake_unavailable_provider():
    class _P:
        status = "UNAVAILABLE"

        def evaluate(self, *a, **k):
            from intelligence.core.market_buffer import SYMBOLS as _S
            return [Exp107Signal(status="UNAVAILABLE", symbol=s) for s in _S]
    return _P()


class Test3_ConflictingIndicators:
    def test_confirm_and_decide_handle_strong_support_and_strong_conflict_together(self):
        exp107 = Exp107Signal(status="OK", symbol="BTCUSDT", fired=True, side="LONG")
        bundle = EvidenceBundle(1, "BTCUSDT", [
            Evidence("structure_engine", "TREND_STATE", Strength.STRONG_SUPPORT),
            Evidence("regime_engine", "BTC_TREND", Strength.CONFLICT),
        ])
        from intelligence.confirmation.feature_registry import Outcome
        high = [Outcome("TREND_STATE", True, 10.0) for _ in range(90)] + \
               [Outcome("TREND_STATE", False, 10.0) for _ in range(60)]
        high += [Outcome("BTC_TREND", True, 10.0) for _ in range(90)] + \
               [Outcome("BTC_TREND", False, 10.0) for _ in range(60)]
        registry = FeatureRegistry(high)
        result = confirm(exp107, bundle, registry)
        # STRONG_SUPPORT (1.0 * 1.0) + CONFLICT (-1.0 * 1.0) nets to exactly 0.0 -> DEFER, not
        # a crash, not an arbitrary tiebreak toward either side
        assert result.confirmation_score == pytest.approx(0.0)
        assert result.label == "DEFER"
        decision = decide("BTCUSDT", 1, exp107, result, has_open_position=False)
        assert decision.action == "WAIT"
        assert decision.action != "ENTER"


class Test4_MultipleSimultaneousOpportunities:
    def test_five_confirmed_candidates_are_ranked_not_all_auto_entered(self):
        candidates = {"BTCUSDT": 1.2, "ETHUSDT": 0.9, "SOLUSDT": 0.6, "BNBUSDT": 0.55,
                     "XRPUSDT": 0.51}
        ranked = rank_opportunities(candidates)
        assert [r.symbol for r in ranked] == ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT",
                                              "XRPUSDT"]
        # ranking alone does not decide anything -- it is still the caller's job to gate each
        # one through decide(), which is exercised elsewhere; this test only proves ordering
        # never silently collapses distinct opportunities into "just open everything"
        assert len({r.rank for r in ranked}) == 5

    def test_opportunity_manager_tracks_all_five_independently_in_one_cycle(self):
        mgr = OpportunityManager()
        inputs = [("BTCUSDT", True, "CONFIRM", 1.0, True), ("ETHUSDT", True, "CONFIRM", 0.8, False),
                 ("SOLUSDT", True, "DEFER", 0.1, False), ("BNBUSDT", False, "NO_SIGNAL", 0.0, False),
                 ("XRPUSDT", True, "INVALIDATE", -1.0, False)]
        for symbol, fired, label, score, has_open in inputs:
            mgr.update(symbol, fired, label, score, has_open, open_time=1)
        states = {s: o.state for s, o in mgr.all().items()}
        assert states["BTCUSDT"] == OpportunityState.OPEN
        assert states["ETHUSDT"] == OpportunityState.CONFIRMED
        assert states["SOLUSDT"] == OpportunityState.CANDIDATE
        assert states["BNBUSDT"] == OpportunityState.WATCHING
        assert states["XRPUSDT"] == OpportunityState.INVALIDATED


class Test5_PositionAlreadyOpen:
    def test_open_position_never_produces_a_second_enter(self):
        exp107 = Exp107Signal(status="OK", symbol="BTCUSDT", fired=True, side="LONG")
        result = ConfirmationResult(1, "BTCUSDT", "OK", True, 1.0, "CONFIRM", 1, 0, [], [])
        decision = decide("BTCUSDT", 1, exp107, result, has_open_position=True,
                          position_recommendation="HOLD")
        assert decision.action != "ENTER"
        assert decision.action == "HOLD"

    def test_other_symbols_still_evaluated_while_one_is_open(self):
        buf = _buffer_monotonic()
        runner = CycleRunner(buf, _fake_unavailable_provider(), connect(":memory:"))
        from intelligence.risk.portfolio_state import PaperPosition
        runner.portfolio.open_position(PaperPosition("BTCUSDT", "LONG", 1000.0, 1, 100.0))
        now_ms = int(buf.grid[-1])
        snaps = runner.run_cycle(now_ms)
        assert len(snaps) == len(SYMBOLS)   # every symbol still produced a snapshot


class Test6_SignalReversal:
    def test_opportunity_confirmed_then_invalidated_transitions_cleanly(self):
        mgr = OpportunityManager()
        o1 = mgr.update("BTCUSDT", True, "CONFIRM", 1.0, False, 1)
        assert o1.state == OpportunityState.CONFIRMED
        o2 = mgr.update("BTCUSDT", True, "INVALIDATE", -1.0, False, 2)
        assert o2.state == OpportunityState.INVALIDATED
        o3 = mgr.update("BTCUSDT", False, "NO_SIGNAL", 0.0, False, 3)
        assert o3.state == OpportunityState.WATCHING   # reversal does not leave a stuck state

    def test_single_reversal_cycle_does_not_instantly_exit_an_open_position(self):
        eng = PositionDecisionEngine()
        d = eng.decide("BTCUSDT", "INVALIDATE")   # signal just reversed, ONE cycle so far
        assert d.recommendation != "EXIT"   # hysteresis: one bad cycle is not enough


class Test7_RapidSignalChanges:
    def test_alternating_confirm_weaken_never_destabilizes_into_exit(self):
        eng = PositionDecisionEngine()
        for i in range(50):
            label = "CONFIRM" if i % 2 == 0 else "WEAKEN"
            d = eng.decide("BTCUSDT", label)
            # WEAKEN never runs two in a row here (alternating), so the streak resets every
            # other cycle -- REDUCE/EXIT must never trigger
            assert d.recommendation == "HOLD"

    def test_rapid_fire_toggling_does_not_crash_opportunity_manager(self):
        mgr = OpportunityManager()
        for i in range(100):
            fired = i % 3 != 0
            label = ["CONFIRM", "DEFER", "WEAKEN", "INVALIDATE"][i % 4]
            mgr.update("BTCUSDT", fired, label if fired else "NO_SIGNAL", 0.1, False, i)
        # no assertion beyond "did not raise" -- stability under churn is the point


class Test8_InsufficientTimeframeHistory:
    def test_evidence_collector_on_ten_bars_reports_mostly_unknown_not_a_crash(self):
        buf = _buffer_monotonic(n_bars=10)
        bundle = collect("BTCUSDT", buf)
        assert bundle.items   # still produces a bundle
        unknown = [e for e in bundle.items if e.strength == Strength.UNKNOWN]
        assert len(unknown) >= 4   # most sources cannot measure anything yet


class Test9_LiquidityUnknown:
    def test_liquidation_data_unknown_never_treated_as_negative_end_to_end(self):
        exp107 = Exp107Signal(status="OK", symbol="BTCUSDT", fired=True, side="LONG")
        bundle = EvidenceBundle(1, "BTCUSDT", [
            Evidence("liquidity_engine", "LIQUIDATION_DATA", Strength.UNKNOWN,
                    "no endpoint available"),
        ])
        result = confirm(exp107, bundle, FeatureRegistry([]))
        assert result.n_unknown == 1
        assert result.confirmation_score == 0.0
        assert result.label != "INVALIDATE"
        assert result.label != "WEAKEN"


class Test10_BinanceApiFailure:
    def test_client_retries_exhaust_then_raises_cleanly(self, monkeypatch):
        c = BinanceClient(retries=2, timeout=1.0)
        monkeypatch.setattr("urllib.request.urlopen",
                            lambda *a, **k: (_ for _ in ()).throw(TimeoutError("down")))
        monkeypatch.setattr("time.sleep", lambda s: None)
        with pytest.raises(BinanceClientError):
            c.klines("BTCUSDT")

    def test_market_buffer_poll_failure_does_not_corrupt_existing_data(self):
        buf = _buffer_monotonic(n_bars=50)
        grid_before = buf.grid.copy()
        c_before = buf.C.copy()

        class FailingClient:
            def klines(self, *a, **k):
                raise BinanceClientError("simulated Binance outage")

        with pytest.raises(BinanceClientError):
            buf.poll(FailingClient())
        # the buffer must still hold exactly what it held before the failed poll -- no partial
        # rebuild, no corrupted half-state
        np.testing.assert_array_equal(buf.grid, grid_before)
        np.testing.assert_array_equal(buf.C, c_before)


class Test11_DecisionMemoryRestartRecovery:
    def test_decisions_survive_reconnecting_to_the_same_file(self, tmp_path):
        db_path = tmp_path / "intelligence.db"
        con1 = connect(db_path)
        rec = DecisionRecord(
            decision_id="D1", open_time=1, symbol="BTCUSDT", exp107_status="OK",
            exp107_fired=True, exp107_side="LONG", exp107_comb=0.97,
            direction_estimate="LONG", direction_confidence=0.6, confirmation_label="CONFIRM",
            confirmation_score=1.0, supporting_evidence=[], conflicting_evidence=[],
            opportunity_state="CONFIRMED", position_state="NONE", previous_decision_id=None,
            decision="ENTER", reason="test", risk_note="LOW", confidence=0.8,
            created_at="2026-01-01T00:00:00Z")
        write_decision(con1, rec)
        con1.close()   # simulate the process ending ("restart")

        con2 = connect(db_path)   # a fresh connection, as a restarted process would open
        recovered = last_decision_for_symbol(con2, "BTCUSDT")
        assert recovered is not None
        assert recovered["decision_id"] == "D1"
        assert recovered["decision"] == "ENTER"

    def test_writes_after_restart_append_rather_than_replace(self, tmp_path):
        db_path = tmp_path / "intelligence.db"

        def _rec(did, ot):
            return DecisionRecord(
                decision_id=did, open_time=ot, symbol="BTCUSDT", exp107_status="OK",
                exp107_fired=True, exp107_side="LONG", exp107_comb=0.9,
                direction_estimate="LONG", direction_confidence=0.5,
                confirmation_label="CONFIRM", confirmation_score=1.0, supporting_evidence=[],
                conflicting_evidence=[], opportunity_state="CONFIRMED", position_state="NONE",
                previous_decision_id=None, decision="ENTER", reason="t", risk_note="LOW",
                confidence=0.5, created_at="t")

        con1 = connect(db_path)
        write_decision(con1, _rec("D1", 1))
        con1.close()

        con2 = connect(db_path)
        write_decision(con2, _rec("D2", 2))
        rows = con2.execute("SELECT decision_id FROM decisions ORDER BY open_time").fetchall()
        assert [r[0] for r in rows] == ["D1", "D2"]


class Test12_NoLookahead:
    """Truncating a series at bar i and recomputing must reproduce EXACTLY what the
    full-history computation reported AT bar i -- if a feature secretly used bar i+1 or later,
    truncating would change the value at i."""

    def _series(self, n=80):
        start = 1_700_000_000_000
        rows = [_row(start + k * BAR_MS, 100.0 + np.sin(k / 5.0) * 3 + k * 0.05) for k in range(n)]
        per = {s: rows for s in SYMBOLS}
        buf = MarketBuffer(keep_bars=n)
        buf._rebuild(per)
        return buf

    def test_candle_engine_is_causal(self):
        buf = self._series()
        full = candle_engine.compute(buf.resample("BTCUSDT", 1))
        i = 50
        truncated_ohlcv = buf.resample("BTCUSDT", 1)
        truncated_ohlcv = type(truncated_ohlcv)(
            truncated_ohlcv.open_time[:i + 1], truncated_ohlcv.open[:i + 1],
            truncated_ohlcv.high[:i + 1], truncated_ohlcv.low[:i + 1],
            truncated_ohlcv.close[:i + 1], truncated_ohlcv.volume[:i + 1],
            truncated_ohlcv.quote_volume[:i + 1], truncated_ohlcv.taker_buy_quote_volume[:i + 1])
        truncated = candle_engine.compute(truncated_ohlcv)
        assert truncated[-1].body == pytest.approx(full[i].body)
        assert _nan_aware_equal(truncated[-1].close_location_value, full[i].close_location_value)
        assert truncated[-1].breakout == full[i].breakout

    def test_magnitude_engine_is_causal(self):
        buf = self._series()
        ohlcv = buf.resample("BTCUSDT", 1)
        full = magnitude_engine.compute(ohlcv, horizon_bars=20)
        i = 60
        truncated_ohlcv = type(ohlcv)(
            ohlcv.open_time[:i + 1], ohlcv.open[:i + 1], ohlcv.high[:i + 1], ohlcv.low[:i + 1],
            ohlcv.close[:i + 1], ohlcv.volume[:i + 1], ohlcv.quote_volume[:i + 1],
            ohlcv.taker_buy_quote_volume[:i + 1])
        truncated = magnitude_engine.compute(truncated_ohlcv, horizon_bars=20)
        assert truncated[-1].move == pytest.approx(full[i].move)
        assert truncated[-1].atr_normalized_move == pytest.approx(full[i].atr_normalized_move)

    def test_structure_engine_swings_are_causal_once_confirmed(self):
        buf = self._series()
        ohlcv = buf.resample("BTCUSDT", 1)
        full_swings = structure_engine.find_swings(ohlcv, left=2, right=2)
        # any swing CONFIRMED by bar i (confirmed_index <= i) must be identical whether we
        # hand the function the full series or only the first i+1 bars
        i = 40
        confirmed_in_full = [s for s in full_swings if s.confirmed_index <= i]
        truncated_ohlcv = type(ohlcv)(
            ohlcv.open_time[:i + 1], ohlcv.open[:i + 1], ohlcv.high[:i + 1], ohlcv.low[:i + 1],
            ohlcv.close[:i + 1], ohlcv.volume[:i + 1], ohlcv.quote_volume[:i + 1],
            ohlcv.taker_buy_quote_volume[:i + 1])
        truncated_swings = structure_engine.find_swings(truncated_ohlcv, left=2, right=2)
        truncated_confirmed = [s for s in truncated_swings if s.confirmed_index <= i]
        assert [(s.index, s.kind, s.price) for s in confirmed_in_full] == \
              [(s.index, s.kind, s.price) for s in truncated_confirmed]


class Test13_NoExp107Modification:
    """Tamper-evident: every file EXP-107 owns must hash EXACTLY to the value recorded when
    EXP-124's work began (see EXP-124/CODEBASE_MAP.md Phase 1). If this test ever fails, some
    change in this session touched a file it was never supposed to touch."""

    BASELINE_HASHES = {
        'PRE_DECLARATION.md': '4fe96672de7674ddab7200c749a331e9a89fccbba2e989e06f9b043a140e7314',
        'artifact/artifact.json': '57ed6715a8a65bd9e13dd8a1fcdfce78bb0d8084c4c3096418b0def6275d098e',
        'artifact/ref_model.npy': '12e8cd12f166ca45bcf70daa4a97dc33fce49731813f60ce49178a2950d5b5de',
        'artifact/ref_rv30.npy': '81d6de913de9ebf7b1f02293510851895a54ce308d0d36e40a0ce4f3b3ce6e79',
        'deploy/README.md': 'e3a342b791814bbeaa1ee74533977a4e863c973a67db749d9b77dbc45a694f9e',
        'deploy/VPS_KURULUM.md': 'f6aa89bc68ea7179e466c06c1184461dde40786e41b190c25b57e573dc12281c',
        'deploy/borsabot-exp107-dashboard.service':
            '27d3a1b623deb70126ef5c4d477baecef749234f68daacaa5465feeaa74a36dc',
        'deploy/borsabot-exp107-shadow.service':
            'b9459dabedeac80f7dac2ed76382f42f5b9521c293a568449814ca1945bef82d',
        'scripts/R0_verify.py': 'c557263f86333e08661b1d0dcd5fe7d382ee2ece0481d675551e5d956399461c',
        'scripts/R1_freeze_artifact.py': 'c52d086a5350a3e5d47a5068c3600edb2433fe48b95e330185c77cd5f7ac517a',
        'scripts/R2_gate2_replay.py': 'a76c98eb2c60815ffa3c1ce2ed99e4778851bf786ef809f286e481b18a00e007',
        'scripts/R3_shadow_run.py': '045c5311fd7a625eb8f778368a04949a13f8ae0f4bc17f84b6154b47aa8caf2d',
        'scripts/R4_report.py': '5cad4e16bc935497eea9cc7f24b7e1b9620705e3a91f2e854d5b9ae724ecd148',
        'scripts/R5_dashboard.py': 'f5539322c4f0b32df00e02a647c19535490741a942557912cb13028f33d4bb35',
        'scripts/d_features.py': 'f09299a0827bd4bac807989f8a0c9aa8bdbd2a54b116ba23df9bb0468c748c4f',
        'scripts/shadow_engine.py': '2290f03ca55a97e45c9622fff7dd4f0cad0b0722a95cb10f434a33382eadff03',
    }

    @pytest.mark.parametrize("relpath", sorted(BASELINE_HASHES.keys()))
    def test_file_hash_unchanged(self, relpath):
        full = REPO_ROOT / relpath
        assert full.exists(), f"{relpath} was expected to exist and does not"
        actual = hashlib.sha256(full.read_bytes()).hexdigest()
        assert actual == self.BASELINE_HASHES[relpath], f"{relpath} was modified"

    def test_no_new_pkl_files_were_added_to_artifact(self):
        """A supplied model would add files, which is fine -- but nothing in THIS session
        added one, and this test documents that fact for the current state."""
        pkls = sorted((REPO_ROOT / "artifact").glob("*.pkl"))
        assert pkls == []   # confirms Blocker #2 still holds at the time of this test run
