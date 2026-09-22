"""
EXP-124 -- continuous decision cycle runner.

RESEARCH / PAPER ONLY. Re-evaluates every symbol roughly every 10-15 seconds (original brief
section 20). Candle-based features are NEVER faked between candle closes:
`MarketBuffer.poll()` only ever returns CLOSED bars (its own guarantee, market_buffer.py), and
EXP-107's own signal is only re-evaluated at ITS OWN schedule -- hourly anchors, decision
minutes {10,30,60,120,240,480} (mirroring scripts/d_features.py's DECISION_MINUTES read-only,
not importing scripts/ internals beyond that tuple) -- and CACHED between those points rather
than recomputed every 10-15s cycle. That satisfies "cached rolling data, incremental
calculations, event timestamps, state transitions, change detection" (brief) without ever
pretending a new EXP-107 reading exists when none does.

This is a SEPARATE process from scripts/R3_shadow_run.py: it never imports, starts, or
communicates with it, and places no order of any kind -- the same forbidden-imports assertion
R3_shadow_run.py itself carries is repeated here, independently.
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from intelligence.confirmation import evidence_collector
from intelligence.confirmation.confirmation_engine import confirm
from intelligence.confirmation.feature_registry import FeatureRegistry, Outcome
from intelligence.core.exp107_signal import Exp107Signal, Exp107SignalProvider
from intelligence.core.market_buffer import SYMBOLS, MarketBuffer
from intelligence.decision.decision_engine import Decision, decide
from intelligence.decision.journal import render as render_journal
from intelligence.engines import volatility_engine
from intelligence.memory.decision_memory import DecisionRecord, write_decision
from intelligence.opportunity.opportunity_manager import OpportunityManager
from intelligence.position.position_decision import PositionDecisionEngine
from intelligence.position.position_monitor import PositionMonitor
from intelligence.risk.portfolio_state import PortfolioState
from intelligence.risk.risk_engine import assess as risk_assess

# Repeated independently of scripts/R3_shadow_run.py's own identical assertion -- this process
# must never be able to place an order even if something upstream of it changes.
for _forbidden in ("ccxt", "binance.client"):
    assert _forbidden not in sys.modules, f"{_forbidden} must not be imported in this process"

# Mirrors scripts/d_features.py's DECISION_MINUTES -- a plain tuple, not an import of
# scripts/ internals, so this scheduling logic cannot accidentally pull in anything beyond the
# published schedule itself.
EXP107_DECISION_MINUTES = (10, 30, 60, 120, 240, 480)
CYCLE_SECONDS = 12
ATR_TIMEFRAME_MIN_FOR_RISK = 60


@dataclass(frozen=True)
class CycleSnapshot:
    """Everything one cycle produced for one symbol -- the auditable snapshot the brief
    sections 20/22 require."""
    open_time: int
    symbol: str
    exp107: Exp107Signal
    confirmation_label: str
    confirmation_score: float
    opportunity_state: str
    decision: Decision
    journal_text: str


class CycleRunner:
    def __init__(self, buffer: MarketBuffer, exp107: Exp107SignalProvider, mem_con,
                starting_balance_usd: float = 100_000.0) -> None:
        self.buffer = buffer
        self.exp107 = exp107
        self.mem_con = mem_con
        self.opportunity_mgr = OpportunityManager()
        self.position_monitor = PositionMonitor()
        self.position_decisions = PositionDecisionEngine()
        self.portfolio = PortfolioState(starting_balance_usd)
        self._last_exp107: dict[str, Exp107Signal] = {}
        self._last_exp107_point: dict[str, tuple[int, int]] = {}
        self._last_decision_id: dict[str, str] = {}
        self._decision_seq = 0

    # ---- EXP-107 scheduling / caching ----------------------------------------------
    def _current_exp107_point(self, now_ms: int) -> tuple[int, int]:
        """The most recent (anchor_ms, minute) EXP-107 decision point that should already be
        knowable given the current time -- mirrors R3_shadow_run.py's own hourly-anchor
        schedule, read-only, without importing its Runner class.

        Searches backward from the CURRENT hour (least hours_back first) until an anchor with
        at least one already-elapsed decision minute is found, then uses that anchor's LATEST
        eligible minute. The longest decision minute is 480 (8 hours), so up to 8 hours must be
        searched back before concluding nothing is eligible yet."""
        current_hour = (now_ms // 3_600_000) * 3_600_000
        for hours_back in range(9):   # 8h covers the longest decision minute, +1 for safety
            anchor = current_hour - hours_back * 3_600_000
            eligible = [m for m in EXP107_DECISION_MINUTES if anchor + m * 60_000 <= now_ms]
            if eligible:
                return anchor, max(eligible)
        # extremely early in the buffer's life -- nothing anywhere in the last 9 hours has
        # reached even its first decision minute yet
        return current_hour - 9 * 3_600_000, EXP107_DECISION_MINUTES[-1]

    def _get_exp107_signal(self, symbol: str, now_ms: int) -> Exp107Signal:
        anchor, minute = self._current_exp107_point(now_ms)
        if (self._last_exp107_point.get(symbol) == (anchor, minute)
                and symbol in self._last_exp107):
            return self._last_exp107[symbol]   # no new EXP-107 decision point yet -- reuse

        apos = self.buffer.pos.get(anchor)
        if apos is None:
            sig = Exp107Signal(status="UNAVAILABLE", symbol=symbol,
                               error=f"anchor {anchor} not yet in the held buffer")
        else:
            signals = self.exp107.evaluate(self.buffer.C, self.buffer.QV, self.buffer.TB,
                                           apos, anchor, minute)
            sig = next((s for s in signals if s.symbol == symbol),
                      Exp107Signal(status="UNAVAILABLE", symbol=symbol,
                                  error="symbol not present in evaluate() result"))
        self._last_exp107[symbol] = sig
        self._last_exp107_point[symbol] = (anchor, minute)
        return sig

    # ---- feature registry, rebuilt from decision_memory's resolved outcomes ---------
    def _registry(self) -> FeatureRegistry:
        """Rebuilt fresh each cycle from whatever this system has itself resolved so far --
        holds no state of its own (feature_registry.py's own design goal). Empty until enough
        resolved decisions exist, which is the correct, honest starting point (every feature
        UNSET, weight 0.0), not a gap to fill with an assumed weight."""
        rows = self.mem_con.execute(
            "SELECT supporting_evidence, conflicting_evidence, symbol, outcome_correct, "
            "outcome_pnl_bp FROM decisions WHERE outcome_resolved=1").fetchall()
        outcomes: list[Outcome] = []
        for support_json, conflict_json, symbol, correct, pnl in rows:
            magnitude = abs(pnl) if pnl is not None else 0.0
            for entry in json.loads(support_json or "[]"):
                feature = entry.split("=")[0].split(".")[-1] if "." in entry else entry
                outcomes.append(Outcome(feature=feature, correct=bool(correct),
                                        magnitude_bp=magnitude, symbol=symbol))
        return FeatureRegistry(outcomes)

    # ---- one symbol, one cycle -------------------------------------------------------
    def run_cycle_for_symbol(self, symbol: str, now_ms: int,
                             correlations: dict[str, float] | None = None,
                             cross_sectional_directions: dict[str, int | None] | None = None,
                             btc_ohlcv=None, book_ticker=None, depth: dict | None = None,
                             premium_index: dict | None = None,
                             open_interest: dict | None = None) -> CycleSnapshot:
        correlations = correlations or {}
        open_time = int(self.buffer.grid[-1]) if len(self.buffer.grid) else now_ms

        exp107_sig = self._get_exp107_signal(symbol, now_ms)
        bundle = evidence_collector.collect(
            symbol, self.buffer, btc_ohlcv=btc_ohlcv,
            cross_sectional_directions=cross_sectional_directions, book_ticker=book_ticker,
            depth=depth, premium_index=premium_index, open_interest=open_interest)
        conf_result = confirm(exp107_sig, bundle, self._registry())

        has_open = symbol in self.portfolio.positions
        pos_rec = None
        if has_open:
            pos = self.portfolio.positions[symbol]
            j = SYMBOLS.index(symbol)
            price = (float(self.buffer.C[-1, j]) if len(self.buffer.grid) else pos.entry_price)
            self.position_monitor.update(pos, now_ms, price)
            pd = self.position_decisions.decide(symbol, conf_result.label)
            pos_rec = pd.recommendation

        opp = self.opportunity_mgr.update(symbol, exp107_sig.is_long_fire, conf_result.label,
                                          conf_result.confirmation_score, has_open, open_time)

        risk = None
        if not has_open and exp107_sig.is_long_fire:
            vol_ohlcv = self.buffer.resample(symbol, ATR_TIMEFRAME_MIN_FOR_RISK)
            vol_snap = volatility_engine.latest(vol_ohlcv)
            j = SYMBOLS.index(symbol)
            price = float(self.buffer.C[-1, j]) if len(self.buffer.grid) else 0.0
            atr_bp = (vol_snap.atr / price * 1e4
                     if vol_snap and vol_snap.atr == vol_snap.atr and price > 0
                     else float("nan"))
            risk = risk_assess(symbol, conf_result.confirmation_score, atr_bp, self.portfolio,
                               correlations)

        decision = decide(symbol, open_time, exp107_sig, conf_result, has_open, pos_rec, risk)

        previous_id = self._last_decision_id.get(symbol)
        self._decision_seq += 1
        decision_id = f"{symbol}_{open_time}_{self._decision_seq}"
        record = DecisionRecord(
            decision_id=decision_id, open_time=open_time, symbol=symbol,
            exp107_status=exp107_sig.status, exp107_fired=bool(exp107_sig.fired),
            exp107_side=exp107_sig.side, exp107_comb=exp107_sig.comb,
            direction_estimate="", direction_confidence=float("nan"),
            confirmation_label=conf_result.label,
            confirmation_score=conf_result.confirmation_score,
            supporting_evidence=conf_result.weighted_support,
            conflicting_evidence=conf_result.weighted_conflict,
            opportunity_state=opp.state.value, position_state=("OPEN" if has_open else "NONE"),
            previous_decision_id=previous_id, decision=decision.action,
            reason=decision.primary_reason, risk_note=decision.risk,
            confidence=decision.confidence,
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        write_decision(self.mem_con, record)
        self._last_decision_id[symbol] = decision_id

        journal_text = render_journal(decision, exp107_sig.side, conf_result.label,
                                      opp.state.value)
        return CycleSnapshot(open_time, symbol, exp107_sig, conf_result.label,
                             conf_result.confirmation_score, opp.state.value, decision,
                             journal_text)

    # ---- every symbol, one cycle ------------------------------------------------------
    def run_cycle(self, now_ms: int, correlations: dict[str, float] | None = None,
                 cross_sectional_directions: dict[str, int | None] | None = None,
                 btc_ohlcv=None) -> list[CycleSnapshot]:
        """Every symbol is evaluated independently, every cycle -- an open position on one
        symbol never skips evaluation of another (brief sections 15-16)."""
        return [self.run_cycle_for_symbol(s, now_ms, correlations, cross_sectional_directions,
                                          btc_ohlcv)
                for s in SYMBOLS]
