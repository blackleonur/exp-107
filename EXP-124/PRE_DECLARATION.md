# EXP-124 — PRE-DECLARATION
## Adaptive Intelligence / Continuous Decision Engine over EXP-107

**Written and frozen BEFORE any intelligence-layer code is written.**

**Status: ANALYSIS PHASE ONLY. Phase 1 (repository audit) complete; Phases 2–15 not started.**

---

## 0. Absolute rules (restated from the task, binding for the rest of this work)

- EXP-107's existing signal (`ShadowEngine.evaluate`, `scripts/shadow_engine.py:75-110`) is
  **never modified** by this work. It remains the single core signal source.
- No EXP-108→123 finding is treated as proven edge. All of it enters the system as
  **decision evidence**, never as a standalone entry trigger. The ~55–56% conditional-direction
  results (EXP-123) are explicitly named in the brief as supporting evidence only, not a gate.
- No accuracy target (60%, 90%, or otherwise) is set anywhere in this work.
- Accuracy alone never decides anything; confidence, expectancy, risk, cost and market
  condition are evaluated together.
- Modes: **RESEARCH** and **PAPER** only, for the entirety of this phase. No LIVE execution
  code is written, touched, or enabled. A separate `LIVE_GATE.md` will exist before LIVE is
  even discussed, and this document does not authorize writing it yet.
- No real order is ever placed, no API key or secret is ever read/logged, no leverage is
  changed. These properties already hold throughout the existing EXP-107 code (confirmed in
  `EXP-124/CODEBASE_MAP.md` §2.15, §4 of the existing `PRE_DECLARATION.md`) and this work adds
  nothing that breaks them.
- Results are reported as measured, not as hoped for. A 50% result is written as 50%.

---

## 1. What Phase 1 found, in one paragraph

This repository is a narrow, self-contained export of EXP-107's pre-live shadow rehearsal for
one frozen model ("D = MODEL + RV30, LONG ONLY") — not the broader trading-bot codebase the
EXP-124 brief assumes exists alongside it. It has no multi-timeframe candle engine, no
liquidity/microstructure-signal engine (D consumes microstructure *features* internally, but
exposes no standalone liquidity read), no cross-sectional agreement engine, no opportunity
manager, no portfolio/risk engine, no decision memory, and no stop-loss/take-profit or
position-sizing logic of any kind — exits are purely time-based (fixed 24h hold). Full detail
is in `EXP-124/CODEBASE_MAP.md`.

More importantly, **four things this brief depends on are not present anywhere on this
machine**, confirmed by filesystem search:

1. The EXP-108→123 experiment artifacts (code, data, results) — only the prose summary in the
   task instructions is available, which is a claim, not verifiable evidence.
2. The trained D model files (`booster_*.pkl`, `isotonic_*.pkl`) — gitignored, absent from this
   checkout. `ShadowEngine.load()` cannot currently succeed.
3. The `tradebot.features.loaders.scan_symbol` data-loading dependency and the historical kline
   archive it reads (`results/exp092_microstructure_direction`,
   `results/exp105_long_only_validation`) — absent.
4. A bulk historical price archive for the 2020→2026 backtest/replay the brief asks for
   (§26) — the live runner only ever holds a rolling ~2.4-day buffer; no long-history store
   exists in this environment.

These are reported as blockers, not silently worked around, per the task's own instruction
(§1, "ÖNEMLİ: ... rapor et"). Full detail and their downstream implications are in
`CODEBASE_MAP.md` §5.

---

## 2. Consequence for the phased plan (brief §32)

Given §1, the 15-phase plan as written cannot proceed as a straight line from Phase 3 onward
without a decision from the user on how to treat the blockers. Two of the four blockers are
plausibly just missing files that exist elsewhere (the `.pkl` artifacts, the `tradebot`
package + its data) and could be supplied; the other two (EXP-108→123 artifacts, multi-year
archive) may not exist in a form that can be handed over at all, in which case the relevant
brief sections (§27's incremental-information test against real data, §26's multi-year replay)
would need to be redefined as **forward-collecting** exercises — i.e., this session starts
gathering live/paper evidence from today rather than replaying history that isn't available.

This document does not choose an answer for the user. It records that:

- Phases 3–11 (Market State / Multi-Timeframe / Candle / Volatility / Magnitude / Liquidity /
  Cross-Sectional / Direction Confirmation / Opportunity Manager / Position Monitor) can be
  **built and unit-tested** against live Binance REST data independent of all four blockers —
  none of them need D's own predictions or historical archives to exist as engines.
- Wiring those engines' output *against D's actual signal* (the whole point of §13, §27) is
  blocked until Blocker #2 (and, for anything claiming causal/historical validation, Blocker
  #3) is resolved.
- Everything claiming to validate an EXP-108→123 finding against real data is blocked by
  Blocker #1 until further notice; until then, those findings can only be encoded as
  *hypotheses to test going forward*, exactly as the brief's own evidence hierarchy demands
  (§2 of the brief: "Hiçbiri tek başına ENTRY emri oluşturamaz").
- Phase 13 (2020→2026 replay) is blocked by #4 and would need to be re-scoped to whatever
  history the live poller can accumulate from this point forward, or to an externally supplied
  archive.

---

## 3. Proposed order of work once blockers are addressed (not started)

1. Confirm with the user which of Blockers #2/#3 can be supplied (artifact files + the
   `tradebot` package/data), and whether Blocker #1/#4 should be treated as "build forward,
   don't try to backtest" for now.
2. Stand up the engines that don't need D's predictions (Market State, Multi-Timeframe,
   Candle/Price-Action, Volatility, Magnitude, Liquidity-so-far-as-public-data-allows,
   Cross-Sectional) as independent, individually unit-tested modules reading only Binance
   public REST data — same "no order path" discipline as `R3_shadow_run.py`.
3. Only once Blocker #2 is resolved: wire a read-only Confirmation layer that logs
   CONFIRM/WEAKEN/DEFER/INVALIDATE recommendations next to D's actual fired signals, without
   altering them (brief §28) — and hold every new engine to the same parity/replay-gate
   discipline EXP-107 already uses for itself (`CODEBASE_MAP.md` §3), before any of its output
   is trusted.
4. Opportunity Manager / Position Monitor / Decision Memory / Risk Engine, in that order, all
   in PAPER mode, all logging full DECISION records (brief §22).
5. Dashboard extension, on the existing read-only/token-gated pattern.
6. Only at that point does an honest §27/§35 report become possible — and it will state
   whatever the numbers say, including "no incremental edge found," which is an explicitly
   acceptable and expected outcome per the brief's own §36.

**No implementation work begins until the user has seen `CODEBASE_MAP.md` and this document
and has responded on how to handle the blockers.**
