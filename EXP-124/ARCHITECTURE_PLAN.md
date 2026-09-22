# EXP-124 — ARCHITECTURE_PLAN.md

**Phase 2 deliverable. Concrete implementation plan, based only on what exists in this
repository (`EXP-124/CODEBASE_MAP.md`). No fabricated data sources, no reproduced EXP-108→123
code. RESEARCH + PAPER only.**

---

## 0. Decision on the blockers (per user instruction, overriding the earlier pause)

- The four blockers in `CODEBASE_MAP.md` §5 stand as documented, but work proceeds anyway:
  - EXP-108→123 findings are used **only** as qualitative hypotheses (§2 of the brief's evidence
    hierarchy) — never as data, never as trained weights.
  - `booster_*.pkl` / `isotonic_*.pkl` remain absent. **No substitute model is fabricated.**
    `ShadowEngine.load()` will keep failing until real files are supplied. The new
    `core/exp107_signal.py` wrapper (§3.1 below) treats this as a first-class `UNAVAILABLE`
    state, not an error to paper over.
  - `tradebot` / EXP-092 / EXP-105 stay untouched and unimported — nothing in the new code
    depends on them.
  - No multi-year archive is backfilled. Instead, §3.2 adds a local, append-only kline store
    that starts accumulating real history from today, so a genuine (if young) replay dataset
    exists going forward without inventing one.
- **Hard invariant carried through every phase below**: EXP-107 (D) still has to say LONG
  before anything in this system can ENTER. With the model artifact missing, that means the
  system currently cannot reach ENTER at all — and it must say so plainly, not substitute
  something else that looks like a signal. Everything else (evidence engines, opportunity
  tracking, decision memory, dashboard) is fully buildable and testable today regardless.

---

## 1. Isolation contract (non-negotiable, checked by tests)

1. Nothing under the new `intelligence/` package **imports, modifies, or deletes** anything
   under `scripts/`, `artifact/`, or `.gitignore`'d runtime files (`shadow.db`, `shadow.log`).
   It only ever **reads** `shadow.db` — exactly the way `R5_dashboard.py` does
   (`sqlite3.connect(f"file:{DB}?mode=ro", uri=True)`) — and only ever **calls** the two
   existing read-only functions from `d_features.py` (`compute_block`, `rv30`) and, once
   available, `ShadowEngine.evaluate()`, never a rewritten copy of them.
2. The new package gets its own database, `intelligence.db`, with its own schema. It never
   writes a row into any `shadow_*` table.
3. Same "no trade-capable import" discipline as `R3_shadow_run.py`: `ccxt` / `binance.client`
   are asserted absent from `sys.modules` at process start in every new long-running process.
4. Same Binance surface as today: public REST only (`klines`, `bookTicker`, and, newly, the
   public `depth`, `premiumIndex` and `openInterest` endpoints — see §3.5 — all
   unauthenticated). No websocket, no signed request, anywhere in the new code either.
5. A `git diff` against `scripts/`, `artifact/`, `deploy/`, and the root-level `PRE_DECLARATION.md`
   at the end of every phase must be empty. This is the literal test for "EXP-107's signal
   logic is not modified."

---

## 2. Package layout

```
exp-107/
  scripts/, artifact/, deploy/, ...          UNCHANGED
  intelligence/                               NEW — everything from here down
    __init__.py
    core/
      binance_client.py       read-only REST client (klines, bookTicker, depth, premiumIndex,
                               openInterest) — extends the retry/backoff pattern in
                               R3_shadow_run.py:_get, does not import it (no cross-dependency
                               on scripts/, kept independently testable)
      market_buffer.py        rolling multi-symbol OHLCV buffer on the 1m grid (same C/QV/TB
                               layout d_features.py already expects) + a resampler to the
                               required higher timeframes
      kline_store.py          append-only local archive (sqlite or parquet), written every
                               poll cycle from today onward — the honestly-labeled substitute
                               for the missing multi-year archive (§0)
      exp107_signal.py        thin read-only wrapper: (a) tries ShadowEngine.load(), catches
                               the missing-artifact failure, exposes status=UNAVAILABLE instead
                               of crashing or substituting a fake score; (b) once real, calls
                               ShadowEngine.evaluate() UNCHANGED and also tails shadow.db for
                               D's own already-fired shadow_trades, read-only
    engines/                  each: pure function(s) over the rolling buffer -> typed evidence,
                               each independently unit-testable against fixture bars, each
                               explicitly marking UNKNOWN where public data can't support it
      candle_engine.py         body/wick/range ratios, engulfing/rejection/breakout/inside bar/
                               expansion/compression, ATR-normalized size, close-location value,
                               prior high/low interaction — per timeframe, plus a separate
                               CURRENT_FORMING_CANDLE view that updates every cycle without
                               mutating the closed-candle series (brief §5, §20)
      structure_engine.py      swing highs/lows, HH/HL/LH/LL, BOS/CHoCH, per-timeframe state in
                               {BULLISH, BEARISH, NEUTRAL, RANGE, TRANSITION}
      volatility_engine.py     ATR, ATR percentile, realised vol, rolling vol, regime
                               (expansion/contraction)
      magnitude_engine.py      MAG_{5M..24H} = |move| / ATR, per horizon, bucketed
                               {LOW, NORMAL, HIGH, EXTREME} — thresholds fit from THIS system's
                               own accumulating data, never from the EXP-123 numbers directly
      liquidity_engine.py      recent/equal highs & lows, wick rejection, volume spike (z-score
                               vs rolling), spread (from bookTicker), order-book imbalance
                               (from public depth endpoint), funding + basis (from
                               premiumIndex), open interest (from openInterest) — each field
                               UNKNOWN if its endpoint/response is unavailable, never guessed
      cross_sectional_engine.py agreement ratio across the 10 symbols' own direction reads,
                               symbol-specific vs market-wide separation kept explicit
      regime_engine.py         BTC return/vol/trend/range/momentum/mean-reversion,
                               BTC/alt correlation, cross-sectional dispersion
      direction_engine.py      an INDEPENDENT direction read (not EXP-107's), explicitly a
                               simple, documented heuristic/statistical scorer — NOT a trained
                               ML replica of any EXP-108→123 model (§0) — whose own hit-rate is
                               tracked via decision memory (§7) and reported as measured, never
                               assumed
    confirmation/
      evidence.py              typed Evidence / EvidenceBundle records, one per symbol per
                               cycle, timestamped, fully attributable to the engine that
                               produced each field
      feature_registry.py      per-feature OOS accuracy/stability/regime-stability/symbol-
                               stability/tail-dependency/sample-size tracker, computed only
                               from data this system has itself collected — every feature
                               starts at confidence=UNSET until it has enough tracked
                               observations to be scored (brief §14, §24)
      confirmation_engine.py   combines the evidence bundle into a CONFIRMATION SCORE and a
                               CONFIRM/WEAKEN/DEFER/INVALIDATE read relative to EXP-107's own
                               signal (brief §13, §28) — weighted ONLY by feature_registry's
                               measured values, never by hand-picked constants, and NEVER
                               changes what EXP-107 itself reports
    opportunity/
      opportunity_manager.py   per-symbol state machine: WATCHING -> CANDIDATE -> CONFIRMED ->
                               OPEN -> WEAKENING -> EXIT_CANDIDATE -> INVALIDATED, one instance
                               per symbol per cycle, independent of what else is open (brief
                               §15, §16)
      ranking.py                when N candidates are simultaneously attractive, ranks by
                               confirmation score, expectancy net of cost, and marginal
                               portfolio risk — never by raw accuracy alone (brief §12, §24
                               "IS IT COST-SENSITIVE?")
    risk/
      portfolio_state.py       paper balance/margin/open-exposure/correlated-exposure
                               accounting (no real balance touched — this mirrors what EXP-107's
                               own shadow trades already do with POSITION_SIZE_USD, extended to
                               the whole book)
      risk_engine.py           per-opportunity: expected reward, estimated SL-equivalent loss
                               (there is no real SL — this is a risk *estimate* for ranking, see
                               §5 below), correlated exposure, portfolio risk, opportunity cost
    position/
      position_monitor.py      tracks the new layer's own PAPER positions (mirrors, never
                               touches, EXP-107's real `shadow_trades`) against live MFE/MAE/
                               distance-to-exit/current evidence
      position_decision.py     HOLD/REDUCE/EXIT/MOVE_SL/MOVE_TP-equivalent RECOMMENDATIONS with
                               explicit hysteresis/cooldown (brief §19) — recommendations only;
                               EXP-107's real exit (fixed 24h) is never altered
    memory/
      decision_memory.py       intelligence.db schema: full decision records (brief §21, §22),
                               keyed so future cycles can query "last N similar setups" —
                               explicitly advisory, never treated as a guarantee
    decision/
      decision_engine.py       final PRIMARY_REASON / SUPPORTING_EVIDENCE /
                               CONTRADICTING_EVIDENCE / RISK / CONFIDENCE / ALTERNATIVE_ACTION
                               record; output in {ENTER, HOLD, WAIT, EXIT, REDUCE, IGNORE}
                               (brief §23) — ENTER is unreachable while exp107_signal is
                               UNAVAILABLE (§0), by construction, not by a special-cased check
      journal.py                writes the human-readable decision journal (brief §22) next to
                               the structured record
    loop/
      cycle_runner.py           the 10–15s continuous loop (brief §20), orchestrating: data
                               refresh -> forming-candle update -> engines -> confirmation ->
                               opportunity manager -> risk -> position monitor -> decision
                               engine -> memory write -> journal. PAPER mode only. A distinct
                               process from R3_shadow_run.py — never started by it, never
                               starts it.
    dashboard/
      intelligence_dashboard.py extends the R5_dashboard.py pattern (read-only DB handle,
                               stdlib http.server, token-gated) on a different port, showing
                               the brief §29/§30 metrics and opportunity queue
    tests/
      unit/                    one file per engine, fixture-bar based, deterministic
      integration/             cycle_runner run against a short live poll window
      replay/                  runs the pipeline over whatever kline_store.py has accumulated
                               so far — honestly small at first, growing over time
  EXP-124/
    CODEBASE_MAP.md            done (Phase 1)
    PRE_DECLARATION.md         done (Phase 1)
    ARCHITECTURE_PLAN.md       this file (Phase 2)
    FEATURE_DEFINITIONS.md     written as engines land (Phase 3+)
    DECISION_ENGINE.md         written with decision/ (Phase 13)
    OPPORTUNITY_MANAGER.md     written with opportunity/ (Phase 10)
    RISK_ENGINE.md             written with risk/ (Phase 11)
    DECISION_MEMORY.md         written with memory/ (Phase 13)
    TEST_RESULTS.md            updated every phase
    LIVE_GATE.md               written last, blocking placeholder — not authorization
    REPORT.md                  written last (brief §35), numbers as measured
  LIVE_GATE.md                 mirrored at repo root once written, per brief §25
```

---

## 3. Key design decisions and why

### 3.1 `exp107_signal.py` — the seam between old and new code

This is the single point of contact with EXP-107. It does exactly three things:

1. Attempt `ShadowEngine.load()` (unmodified import from `scripts/shadow_engine.py`). If it
   raises (today: it will, per Blocker #2), catch it once, log it, and set
   `status = "UNAVAILABLE"` for the whole process lifetime — it does not retry every cycle
   pretending the files might appear, since that would mask a real deployment mistake; a
   restart is what picks up newly supplied artifact files, matching the existing "crash gets a
   restart only" discipline already in `PRE_DECLARATION.md`.
2. If loaded, call `ShadowEngine.evaluate()` verbatim — no copy, no reimplementation — exactly
   like `R2_gate2_replay.py` and `R3_shadow_run.py` already do.
3. Separately (always available, no model needed), read-only tail `shadow.db`'s `shadow_trades`
   table for D's own already-recorded real fires — this works today with zero blockers, since
   it doesn't need the model, only a `shadow.db` that a running `R3_shadow_run.py` produces
   (which itself still needs Blocker #2 resolved to ever populate).

Everything downstream keys off `status`. The Decision Engine's ENTER branch has a single
upstream guard: `exp107_signal.status == "OK" and exp107_signal.fired and exp107_signal.side ==
"LONG"`. With the artifact missing, this guard is never true — so the system runs end-to-end in
a genuine, honestly-labeled `EXP107_UNAVAILABLE` mode where every decision is at best WATCHING
or WAIT, never ENTER. That is the correct behavior, not a gap to hide.

### 3.2 `kline_store.py` — building a real archive instead of faking one

Every poll cycle, closed 1m bars for all 10 symbols are appended to a local, deduplicated
store (SQLite table keyed `(symbol, open_time)`, or Parquet partitioned by day — SQLite chosen
first for consistency with the rest of the repo). This is **not** a substitute for the
2020→2026 history the brief asks for; it is labeled everywhere it's used as "accumulated since
`<first recorded timestamp>`," and any replay/statistic built from it must show its actual date
range and sample size, the same way `R4_report.py` already flags <5-observation exit stats
rather than presenting them as measurements.

### 3.3 Multi-timeframe, built from 1m bars, not from a candle-per-timeframe illusion

`market_buffer.py` resamples the same closed 1m series into 5m/15m/30m/1H/2H/3H/4H/5H/6H/8H/
12H/24H bars, standard OHLCV aggregation, closed-bar only (an in-progress higher-timeframe
candle is exposed separately as `CURRENT_FORMING_CANDLE`, never mixed into the closed series —
brief §20). This reuses the *shape* of data `d_features.py` already works with (`C, QV, TB`
matrices on a shared grid) so the existing feature functions remain callable without
modification if a future phase wants to reuse them for something other than D itself.

### 3.4 "Confirmation," never "override"

`confirmation_engine.py` only ever produces a **label plus a score** attached to a symbol,
timestamped against whatever EXP-107 said at that moment (including "EXP-107 said nothing /
UNAVAILABLE"). It has no write access to anything EXP-107 owns and no code path back into
`ShadowEngine`. This is checked by the isolation tests in §1.

### 3.5 What "liquidity" honestly means here

Binance Futures public REST also exposes `/fapi/v1/depth` (order-book snapshot),
`/fapi/v1/premiumIndex` (funding rate + mark/index basis), and `/fapi/v1/openInterest` — all
unauthenticated, same trust tier as the two endpoints EXP-107 already uses. These give real
(not fabricated) values for order-book imbalance, spread, funding, basis, and open interest.
Liquidation data and true historical order-flow are **not** available this way and will be
marked `UNKNOWN` — exactly as the brief instructs ("Veri yoksa uydurma. UNKNOWN olarak
işaretle").

### 3.6 Feature weighting starts at zero trust, on purpose

`feature_registry.py` never reads a weight from the EXP-108→123 prose summary. Every new
feature (magnitude, volatility regime, cross-sectional agreement, multi-timeframe agreement,
etc.) starts `confidence=UNSET`, accumulates OOS observations only from this system's own
`decision_memory`, and is only promoted to LOW/MEDIUM/HIGH once it has enough tracked
same-condition outcomes — mirroring the caution the brief itself demands after EXP-120's cells
failed to replicate in EXP-121. This is the mechanism that answers brief §27's real question
("does this condition add incremental information on top of EXP-107's own signal?") — honestly,
from this system's own data, once enough of it exists, not by assumption on day one.

### 3.7 Cost and risk are inputs to every decision, not a filter bolted on after

`risk_engine.py` and `decision_engine.py` both carry `COST_BP = 14.38` (the same constant
`R3_shadow_run.py` and `R4_report.py` already use, kept identical for comparability) into every
expectancy calculation, from the first phase that computes an expectancy at all — never added
retroactively once something "looks good."

---

## 4. Phased build order (adapted from brief §32 to this repo's real constraints)

| Phase | Scope | Needs artifact/model? | Needs kline archive? |
|---|---|---|---|
| 1 | Repository audit (`CODEBASE_MAP.md`) | no | no | **DONE** |
| 2 | This plan (`ARCHITECTURE_PLAN.md`) | no | no | **DONE** |
| 3 | `core/binance_client.py`, `core/market_buffer.py`, `core/kline_store.py` | no | building it now | next |
| 4 | `engines/candle_engine.py`, `engines/structure_engine.py` | no | no (fixture-based unit tests) | |
| 5 | `engines/volatility_engine.py`, `engines/magnitude_engine.py` | no | no | |
| 6 | `engines/liquidity_engine.py` (depth/funding/OI), `engines/cross_sectional_engine.py`, `engines/regime_engine.py` | no | no | |
| 7 | `engines/direction_engine.py` (heuristic, explicitly not a trained-model replica) | no | no | |
| 8 | `core/exp107_signal.py` (UNAVAILABLE-aware wrapper) | detects absence, doesn't need it | no | |
| 9 | `confirmation/` (evidence, registry, confirmation engine) | no (feeds off phases 3–8) | no | |
| 10 | `opportunity/` (manager, ranking) | no | no | |
| 11 | `risk/` (portfolio state, risk engine) | no | no | |
| 12 | `position/` (monitor, decision + hysteresis) | reads shadow_trades read-only if present, degrades gracefully if not | no | |
| 13 | `memory/` (decision_memory), `decision/` (decision_engine, journal) | no | no | |
| 14 | `loop/cycle_runner.py` — wires all of the above, PAPER mode, 10–15s cadence | inherits UNAVAILABLE from phase 8 | uses whatever phase 3 has accumulated | |
| 15 | `dashboard/intelligence_dashboard.py`, `TEST_RESULTS.md`, `LIVE_GATE.md`, `REPORT.md` | — | — | |

Every phase ends with: unit tests green, the §1 isolation `git diff` check clean, and a short
note appended to `EXP-124/TEST_RESULTS.md`. Per the brief's own testing rule (§33): if any
existing EXP-107 behavior is found to break, implementation stops and the break is fixed before
continuing — there is no such breakage expected, since nothing under `scripts/` is ever edited,
but the check is real, not decorative.

---

## 5. Named simplification versus the original brief

The brief's §19 imagery ("MOVE_SL", "MOVE_TP") assumes EXP-107 has stop-loss/take-profit
levels to move. It doesn't (`CODEBASE_MAP.md` §2.4) — exit is fixed-time only. `position_decision.py`
therefore emits **HOLD / REDUCE / EXIT recommendations only**, plus a logged
"would-have-adjusted-SL/TP-to-X" field for research purposes — it never claims to move a stop
that does not exist in the real system. This is stated now so a later report doesn't imply
SL/TP management capability that was never built.

---

## 6. Next step

Proceeding to Phase 3 now: `core/binance_client.py`, `core/market_buffer.py`,
`core/kline_store.py`, with unit tests. Progress will be committed incrementally per phase
rather than as one large change, so each step stays reviewable and the isolation check in §1
can run at every commit.
