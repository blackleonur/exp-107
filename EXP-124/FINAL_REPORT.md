# EXP-124 — FINAL_REPORT.md
## Adaptive Intelligence / Continuous Decision Engine over EXP-107

**RESEARCH + PAPER ONLY. No LIVE code exists. No real order has been or can be placed by
anything described here.**

This report covers Phases 1–15 as executed in this session. It states results as measured, not
as hoped for. Where a number cannot be measured — and for most of this report, it cannot,
because EXP-107's own trained model is unavailable in this repository — that is stated plainly
rather than approximated or omitted.

---

## 1. One-paragraph summary

EXP-107 remains, unmodified, the system's only source of a real trading signal. A new,
independent `intelligence/` package (26 implementation modules, 29 test files, 338 passing
tests) was built around it: read-only market data, eight evidence engines, a confirmation
layer with an out-of-sample feature-weighting registry, per-symbol opportunity tracking, a
paper risk/portfolio engine, position monitoring with hysteresis, append-only decision memory,
a final decision engine, a 10–15-second continuous orchestrator, and a read-only dashboard.
**None of it has ever run against a real EXP-107 signal**, because the trained model artifact
(`booster_*.pkl`/`isotonic_*.pkl`) is not present in this repository and was never supplied
during this session (`CODEBASE_MAP.md` Blocker #2). Every question in §16 that depends on real
signal data is therefore answered "cannot be measured — zero observations," not with an
estimate.

---

## 2. Architecture

```
EXP-107 CORE SIGNAL  (scripts/shadow_engine.py, frozen, unmodified)
        │  read-only, via intelligence/core/exp107_signal.py
        ▼
MARKET DATA CORE      intelligence/core/{binance_client,market_buffer,kline_store}.py
        │
        ▼
EVIDENCE ENGINES       intelligence/engines/{candle,structure,volatility,magnitude,
                        liquidity,cross_sectional,regime,direction}_engine.py
        │
        ▼
EVIDENCE COLLECTOR     intelligence/confirmation/evidence_collector.py
        │
        ▼
CONFIRMATION LAYER     intelligence/confirmation/{evidence,feature_registry,
                        confirmation_engine}.py
        │
        ▼
OPPORTUNITY MANAGER    intelligence/opportunity/{opportunity_manager,ranking}.py
        │
        ▼
RISK / PORTFOLIO       intelligence/risk/{portfolio_state,risk_engine}.py
        │
        ▼
POSITION MONITOR       intelligence/position/{position_monitor,position_decision}.py
        │
        ▼
DECISION ENGINE        intelligence/decision/{decision_engine,journal}.py
        │
        ▼
DECISION MEMORY        intelligence/memory/decision_memory.py   (append-only, own DB)
        │
        ▼
CYCLE RUNNER (10–15s)  intelligence/loop/cycle_runner.py   (orchestrates everything above)
        │
        ▼
DASHBOARD (read-only)  intelligence/dashboard/intelligence_dashboard.py
```

**Isolation, enforced not just documented**: `intelligence/` never imports, modifies, or
deletes anything under `scripts/`, `artifact/`, or `deploy/`. It reads `scripts/shadow_engine.py`
and calls its `ShadowEngine.evaluate()` verbatim (one function, two callers — the same pattern
EXP-107's own `R2_gate2_replay.py`/`R3_shadow_run.py` already use, per `CODEBASE_MAP.md` §2.1).
It owns a separate database (`intelligence.db`, never `shadow.db`). Every long-running process
repeats EXP-107's own "no trading library importable" assertion independently. A tamper-evident
SHA-256 hash test (`Test13_NoExp107Modification`) now runs as part of the standard test suite
and will fail the build if any of EXP-107's 16 owned files is ever touched.

---

## 3. Every engine, in the order the pipeline calls them

| Engine | File | What it produces | Directional claim |
|---|---|---|---|
| Market buffer | `core/market_buffer.py` | Rolling closed-1m OHLCV for all 10 symbols, resampled to any timeframe, plus a `FormingCandle` view for in-progress higher-timeframe candles | none |
| Candle / price action | `engines/candle_engine.py` | body/wick/range ratios, engulfing, rejection, inside bar, breakout/failed breakout, ATR-normalized expansion/compression, close-location-value, prior-high/low interaction | none — evidence only |
| Market structure | `engines/structure_engine.py` | Causal, confirmation-lagged swing highs/lows, HH/HL/LH/LL, BOS/CHoCH events, trend state (BULLISH/BEARISH/NEUTRAL/RANGE/TRANSITION) | none — evidence only |
| Volatility | `engines/volatility_engine.py` | ATR, self-measured ATR percentile, realized volatility, LOW/NORMAL/HIGH/EXTREME regime, expansion/contraction flags | none — "VOLATILITY_HIGH != BUY" enforced structurally |
| Magnitude | `engines/magnitude_engine.py` | ATR-normalized prior-move magnitude at MAG_5M…MAG_24H, bucketed by self-measured percentile | none |
| Liquidity | `engines/liquidity_engine.py` | Order-book imbalance, funding/basis, open interest, volume z-score spikes, recent/equal highs-lows — `LIQUIDATION_DATA` always `"UNKNOWN"` (no endpoint exists) | none |
| Cross-sectional | `engines/cross_sectional_engine.py` | Agreement ratio across the 10 symbols' own directions, with explicit symbol-vs-market-majority separation | none — "HIGH AGREEMENT != ENTRY" enforced structurally |
| BTC / regime | `engines/regime_engine.py` | BTC trend/volatility/momentum-vs-mean-reversion state, BTC/alt correlation | none — does not reproduce or assume EXP-116/117's finding |
| Direction (secondary) | `engines/direction_engine.py` | An explicitly heuristic multi-timeframe majority vote — direction, confidence, timeframe agreement, supporting/conflicting factors | SECONDARY evidence only, never reproduces EXP-107 or any EXP-108→123 model |
| EXP-107 adapter | `core/exp107_signal.py` | Read-only wrapper: `status` (`OK`/`UNAVAILABLE`), and `is_long_fire` — the ONE function in the whole package permitted to say "EXP-107 said ENTER" | THE primary signal (unmodified, pass-through) |

---

## 4. Data flow, one cycle

1. `MarketBuffer.poll()` fetches only newly-closed bars (public REST, no auth) and merges them
   — no full re-backfill, no re-fetch of history already held.
2. `CycleRunner._get_exp107_signal()` checks whether a NEW EXP-107 decision point (hourly
   anchor × {10,30,60,120,240,480} min) has arrived since the last check; if not, the cached
   `Exp107Signal` is reused unchanged — EXP-107 is never re-evaluated more often than it
   actually produces new information.
3. `evidence_collector.collect()` calls all eight engines for the symbol and returns a typed
   `EvidenceBundle`.
4. `confirmation_engine.confirm()` combines the bundle with `FeatureRegistry`-measured weights
   into a `CONFIRM`/`WEAKEN`/`DEFER`/`INVALIDATE`/`NO_SIGNAL` label and score — but only ever
   runs this arithmetic when `exp107.is_long_fire` is already true; otherwise it's `NO_SIGNAL`
   immediately.
5. `OpportunityManager.update()` advances that ONE symbol's independent state machine.
6. If a position is open, `PositionMonitor` updates MFE/MAE and `PositionDecisionEngine`
   applies hysteresis to produce a HOLD/REDUCE/EXIT recommendation.
7. If no position is open and EXP-107 fired, `risk_engine.assess()` produces an expected-
   reward/cost/correlated-exposure read.
8. `decision_engine.decide()` combines all of the above into one final `Decision`.
9. `decision_memory.write_decision()` appends a full, immutable record.
10. `journal.render()` produces the human-readable block.

Every symbol goes through this independently, every cycle — an open position on one symbol
never skips or short-circuits evaluation of another (`intelligence/tests/adversarial/…::Test4`,
`Test5`).

---

## 5. Decision hierarchy

```
IF has_open_position:
    EXIT  if position hysteresis says EXIT
    REDUCE if position hysteresis says REDUCE
    HOLD  otherwise
ELIF NOT exp107.is_long_fire:
    IGNORE   (nothing to act on)
ELIF confirmation.label == "CONFIRM":
    WAIT  if portfolio risk is HIGH
    ENTER otherwise
ELIF confirmation.label == "INVALIDATE":
    IGNORE
ELSE (WEAKEN or DEFER):
    WAIT
```

`ENTER` appears in exactly one branch of `decision_engine.decide()`, and that branch is only
reachable when `exp107.is_long_fire` is true. This is checked by five dedicated tests in
`test_decision_engine.py::TestHardInvariantEnterRequiresExp107Fire`, re-verified at the full
cycle-orchestration level in `test_cycle_runner.py`, and — because the model artifact is
`UNAVAILABLE` in this repository — is provably unreachable today, which `test_exp107_signal.py`
confirms against the real (unmocked) `ShadowEngine.load()` failure.

---

## 6. Confidence calculation

There is no single "confidence" number manufactured from nothing:

- **`direction_engine`'s confidence** = (fraction of measured timeframes agreeing with the
  majority) × (a sample-size penalty that caps confidence when fewer than 3 timeframes could be
  measured at all). Documented as a heuristic, never a calibrated probability.
- **`confirmation_engine`'s score** = Σ over evidence items of `(strength_sign × feature_weight)`,
  where `strength_sign` ∈ {+1.0 STRONG_SUPPORT, +0.5 SUPPORT, 0 NEUTRAL, −1.0 CONFLICT, 0
  UNKNOWN (never negative)} and `feature_weight` ∈ {0.0 UNSET/LOW, 0.5 MEDIUM, 1.0 HIGH} comes
  **only** from `FeatureRegistry`, which computes it from this system's own tracked, resolved
  outcomes — never from a number copied out of the EXP-108→123 prose summary. Every feature
  starts at weight 0.0 and stays there until it has ≥20 tracked observations (`UNSET`→`LOW`
  boundary), ≥60 with a ≥3-point edge over a coin flip for `MEDIUM`, ≥150 with a ≥5-point edge
  for `HIGH` — **and is capped at `LOW` regardless of `n` or edge if ≥50% of its apparent edge's
  magnitude comes from a single observation** (`tail_dependency_ratio`), directly encoding the
  EXP-120→121 replication-failure lesson.
- **`decision_engine`'s risk label** (LOW/MEDIUM/HIGH) is a simple threshold on
  `risk_engine.assess()`'s `risk_penalty`, itself derived from correlated exposure ÷ starting
  balance — bounded, auditable, never a black box.

**Because `FeatureRegistry` has zero tracked outcomes in this repository** (no paper run has
ever produced a resolved decision — see §9), every feature weight used by `confirm()` today is
exactly `0.0`. This means the confirmation score currently reduces to `0.0` for every symbol
regardless of what the evidence engines report — which is the mathematically correct
consequence of "no feature has proven itself yet," not a bug.

---

## 7. Conflict resolution

`Evidence.strength` ∈ {STRONG_SUPPORT, SUPPORT, NEUTRAL, CONFLICT, UNKNOWN}. `confirm()` nets
these (weighted) into one score; `Decision.supporting_evidence`/`contradicting_evidence` carry
the individual, human-readable contributions through unchanged into the decision record and the
journal, so no conflict is ever silently hidden inside an aggregate number.
`UNKNOWN` is excluded from the sum entirely — never treated as `CONFLICT` — enforced by a
dedicated test (`TestUnknownNeverCountsNegative`) and re-verified end-to-end for the one
concretely `UNKNOWN` feature that exists today, `LIQUIDATION_DATA`
(`Test9_LiquidityUnknown`).

---

## 8. Opportunity ranking

`opportunity/ranking.py::rank_opportunities()` orders simultaneous candidates by
`confirmation_score − risk_penalty` (same evidence-score units), with `cost_bp` kept as a
**separate-unit** tiebreak rather than blended numerically into the score — the module
explicitly documents that it does not know a score-to-basis-points exchange rate and does not
pretend to. Ranking never decides anything by itself; `decision_engine.decide()` still has to
independently clear the `ENTER` gate for each ranked candidate. `Test4_MultipleSimultaneousOpportunities`
proves five candidates get five distinct ranks rather than collapsing into "open everything."

---

## 9. Position-aware logic

`intelligence/risk/portfolio_state.py` is pure paper bookkeeping — **no leverage is modeled
anywhere**, matching EXP-107's own leverage-free design. `PortfolioState.correlated_exposure_usd()`
sums notional in OTHER open positions whose |correlation| to a candidate symbol exceeds a
threshold, feeding `risk_engine.assess()`'s penalty. `position/position_monitor.py` tracks
MFE/MAE incrementally per symbol (never recomputed from a re-fetched history).
`position/position_decision.py` requires **3 consecutive `WEAKEN` reads before recommending
REDUCE, and 2 consecutive `INVALIDATE` reads before recommending EXIT** — a single noisy cycle
can never flip a position decision (`brief §19`, tested by
`TestHysteresisPreventsSingleCycleFlip`, and stress-tested with 50 alternating cycles in
`Test7_RapidSignalChanges`). EXP-107 itself has **no real stop-loss or take-profit** (fixed
24-hour time exit only, `CODEBASE_MAP.md` §2.4) — this layer never emits a `MOVE_SL`/`MOVE_TP`
recommendation as if one existed; the one place a stop-like distance is mentioned is explicitly
prefixed `"RESEARCH ONLY, not a real order"`.

---

## 10. Memory / state machine

**Opportunity state machine** (one instance per symbol, fully independent):
`WATCHING → CANDIDATE → CONFIRMED → OPEN → WEAKENING → EXIT_CANDIDATE → (back to) WATCHING`,
with `INVALIDATED` reachable from `CANDIDATE`/`CONFIRMED` and explicitly non-sticky — it clears
back to `WATCHING` the moment EXP-107 stops firing, rather than permanently blacklisting a
symbol.

**Decision memory** (`intelligence/memory/decision_memory.py`, its own `intelligence.db`,
schema listed below) is **append-only at the database layer**: a duplicate `decision_id` raises
`sqlite3.IntegrityError` rather than silently overwriting (`TestAppendOnlyGuarantee`), and the
only function permitted to mutate an existing row (`update_outcome()`) can touch only its
`outcome_*` columns, never what was decided or why. Proven to survive a simulated process
restart — a fresh connection to the same file recovers prior rows exactly
(`Test11_DecisionMemoryRestartRecovery`).

Columns: `decision_id, open_time, symbol, exp107_status, exp107_fired, exp107_side,
exp107_comb, direction_estimate, direction_confidence, confirmation_label,
confirmation_score, supporting_evidence (JSON), conflicting_evidence (JSON),
opportunity_state, position_state, previous_decision_id, decision, reason, risk_note,
confidence, created_at, outcome_resolved, outcome_correct, outcome_pnl_bp, outcome_mfe_bp,
outcome_mae_bp, outcome_resolved_at`. `previous_decision_id` links each symbol's decisions into
a chain; `similar_setups()` queries the last N resolved decisions at a given confirmation
label for a symbol — explicitly advisory, per the brief's own "past result is not a guarantee
of the future" instruction, never treated as one by any code in this package.

---

## 11. The 10–15-second evaluation loop

`intelligence/loop/cycle_runner.py::CycleRunner`. Key property: **EXP-107 is only ever
re-evaluated at ITS OWN schedule** (hourly anchor × the six real decision minutes), cached
between cycles — the 10–15s cadence re-evaluates evidence engines and the downstream pipeline,
never fabricates a new EXP-107 reading between its actual decision points. A dedicated bug was
caught and fixed here during test-writing (see §14): the first draft of the scheduling function
could only ever find the CURRENT hour's own anchor, making decision minutes 60/120/240/480
unreachable except through a wrong hardcoded fallback; it now searches backward up to 9 hours
and returns the first anchor with a genuinely eligible minute.

---

## 12. Safety rules (checked, not just stated)

- No real order anywhere: `assert "ccxt"/"binance.client" not in sys.modules`, repeated
  independently in every long-running process.
- No credential read, logged, or printed anywhere in `intelligence/`.
- EXP-107's signal logic, thresholds, model, training logic, and semantics are **provably**
  unmodified — `Test13_NoExp107Modification` hashes all 16 files it owns.
- `ENTER` is structurally unreachable without a real, loaded EXP-107 fire.
- No historical finding from EXP-108→123 is hard-coded as a rule (no `"55% = trade"`, no
  `"volatility high = trade"`) — every such finding enters only as a reason to compute and
  track evidence, weighted solely by this system's own out-of-sample tracking.
- RESEARCH/PAPER/LIVE are structurally separated: LIVE execution code does not exist in this
  repository; `LIVE_GATE.md` blocks it behind explicit, unmet preconditions.

---

## 13. Answers to the brief's §35 questions

1. **EXP-107's original performance?** Cannot be measured from this repository. The historical
   evaluation (EXP-105's 38 trades, the verification gates) exists only as already-recorded
   CSV/JSON evidence (`verification.json`, `gate2.json` at repo root) — no script that could
   reproduce or extend it can currently run (`CODEBASE_MAP.md` §0, Blocker #3).
2. **Does the new layer add incremental information to EXP-107?** Cannot be measured — zero
   real EXP-107 signals were ever produced in this session (model artifact unavailable), so
   nothing exists to be incremental to.
3–5. **Do MAG_LONG=HIGH / HIGH volatility / HIGH cross-sectional agreement genuinely help?**
   Cannot be measured for the same reason; `FeatureRegistry` has zero tracked outcomes.
6. **Is multi-timeframe confirmation useful?** Not measured. `direction_engine` demonstrably
   changes its read based on timeframe agreement (tested), but whether that read correlates
   with anything real is unmeasured.
7–10. **Which features are redundant / unstable / tail-dependent / regime-specific?**
   Unmeasurable with zero tracked outcomes — the MACHINERY to answer this
   (`feature_registry.tail_dependency_ratio`, `n_symbols`, `n_regimes`) exists and is unit-
   tested against synthetic data, but has never been run against real observations.
11. **Does open-position management help?** Not measured — no position has ever actually been
   opened against a real signal.
12. **Does portfolio-aware ranking help?** Not measured.
13. **Is decision memory useful?** Not measured — the database has zero real decisions in it
   outside of tests.
14. **Does the 10–15s loop reduce wrong decisions?** Not measured.
15–16. **Cost/slippage-adjusted result?** Not measured — there is no result to adjust.
17. **How often does the layer CONFIRM/WEAKEN/INVALIDATE EXP-107?** Zero times, ever, in this
   repository — EXP-107 has never fired.
18. **Does the new system increase EXP-107's edge?** **No claim is made.** Nothing in this
   report should be read as implying one. The infrastructure to eventually measure this exists
   and is tested; the measurement itself does not exist.

---

## 14. Bugs found and fixed during this work (not left for later)

1. `evidence_collector.collect()` was feeding `magnitude_engine` the 60-minute-resampled series
   with a horizon count meant for 1-minute bars, silently turning "4 hours" into "10 days."
   Fixed; regression test added.
2. `cycle_runner._current_exp107_point()` could only find the current hour's own anchor, making
   decision minutes 60/120/240/480 permanently unreachable. Fixed by searching backward up to
   9 hours; three hand-verified test cases added.
3. A test fixture bug (`support or [...]` silently replacing an intentionally empty list) was
   caught by `test_journal.py`'s own assertions and fixed in the test helper, not the
   implementation under test.
4. A test fixture for `MarketBuffer._rebuild()` omitted absent symbols from `per` entirely,
   triggering a `KeyError`; fixed to match the real contract (every symbol always has an entry).
5. A cosmetic `RuntimeWarning` from `numpy.nanmax` on an all-NaN true-range bar (a genuine data
   gap) was silenced — the underlying NaN-propagation behavior was already correct.

---

## 15. Exact files changed

**26 implementation modules** (all new; nothing under `scripts/`, `artifact/`, or `deploy/`
touched):
`intelligence/core/{binance_client,market_buffer,kline_store,exp107_signal}.py`,
`intelligence/engines/{common,candle_engine,structure_engine,volatility_engine,
magnitude_engine,liquidity_engine,cross_sectional_engine,regime_engine,direction_engine}.py`,
`intelligence/confirmation/{evidence,feature_registry,confirmation_engine,
evidence_collector}.py`, `intelligence/opportunity/{opportunity_manager,ranking}.py`,
`intelligence/risk/{portfolio_state,risk_engine}.py`,
`intelligence/position/{position_monitor,position_decision}.py`,
`intelligence/memory/decision_memory.py`, `intelligence/decision/{decision_engine,journal}.py`,
`intelligence/loop/cycle_runner.py`, `intelligence/dashboard/intelligence_dashboard.py`.

**29 test files** under `intelligence/tests/unit/` (one per module above, plus
`test_intelligence_dashboard.py`) and `intelligence/tests/adversarial/test_adversarial_suite.py`.

**Documentation**: `EXP-124/{CODEBASE_MAP.md, PRE_DECLARATION.md, ARCHITECTURE_PLAN.md,
TEST_RESULTS.md, LIVE_GATE.md, FINAL_REPORT.md}` (this file).

**Zero files under `scripts/`, `artifact/`, `deploy/`, or the root `PRE_DECLARATION.md` were
ever modified** — verified at the end of every phase and now enforced by a standing test.

---

## 16. Test count and results

**338 tests, all passing, zero skipped, zero xfail**, run via `python3 -m pytest intelligence/tests -q`.
Per-phase breakdown, methodology, and every caught bug are in `EXP-124/TEST_RESULTS.md`.
Composition: ~299 unit tests (one file per module, offline/deterministic, no network), 39
adversarial tests (the explicit pre-report checklist), 5 of the unit tests dedicated to the
dashboard, plus the isolation-hash test.

**What was explicitly NOT run**: no test in this suite makes a real network call. No test in
this suite exercises `ShadowEngine` with a real, loaded model (none is available). No
multi-day/multi-week PAPER run has occurred.

---

## 17. What is explicitly NOT production-ready

Stated plainly, per the task's own rule against making results look better than they are:

- **EXP-107 itself cannot currently produce a signal in this environment.** The trained model
  artifact is absent (`CODEBASE_MAP.md` Blocker #2). Every "ENTER" path in this report is
  theoretical until that is resolved.
- **No real paper-trading run has ever happened.** `intelligence.db` has never been populated
  by a live `cycle_runner` process against real market data across real time — only by unit and
  adversarial tests using synthetic fixtures.
- **`FeatureRegistry` has zero real tracked outcomes.** Every feature weight is `0.0` today.
  The confirmation score is currently mathematically zero for everything, always — this is
  correct given no evidence exists yet, but it means the system currently has no way to say
  "yes" to anything even if EXP-107 did fire.
- **`evidence_collector.py` wires only one representative timeframe** (60-minute) into
  structure/candle evidence, not the full 5m→24h fan-out the original brief describes — a
  deliberate, documented scoping choice for this pass, not a hidden gap.
- **No historical multi-year backtest exists or was run** (`CODEBASE_MAP.md` Blocker #4) — the
  append-only `kline_store.py` starts accumulating real history from whenever it first runs,
  not before.
- **No EXP-108→123 finding has been validated against real data anywhere in this work.** They
  remain exactly what the brief asked them to remain: hypotheses that shaped which evidence to
  collect, never numbers copied into production logic.
- **No cost/slippage-adjusted expectancy has ever been computed from real trades**, because no
  real trade (paper or otherwise) has ever occurred.
- **No security review of a real order-execution path exists**, because no such path exists —
  see `LIVE_GATE.md` §2 for everything that would still need to be built and reviewed before
  LIVE could even be discussed.

**The system, as it stands, is a tested, isolated, honestly-labeled scaffold — not a trading
edge, proven or otherwise.** Whether it becomes one is an empirical question this report
explicitly declines to answer in the absence of data, which is the correct answer given the
data does not exist.
