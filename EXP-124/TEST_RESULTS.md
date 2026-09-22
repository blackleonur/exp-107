# EXP-124 — TEST_RESULTS.md

Updated at the end of every phase. Numbers are pasted from the actual `pytest` run, not
paraphrased.

---

## Phase 3 — market data core

**Scope**: `intelligence/core/binance_client.py`, `intelligence/core/market_buffer.py`,
`intelligence/core/kline_store.py`.

**Isolation check** (`ARCHITECTURE_PLAN.md` §1, item 5): `git diff --stat -- scripts/ artifact/
deploy/ PRE_DECLARATION.md` → empty. No file EXP-107 owns was touched.

**Unit tests** (offline, no network — `binance_client` tests monkeypatch the transport layer;
`market_buffer` and `kline_store` tests use synthetic fixture bars):

```
$ python3 -m pytest intelligence/tests/unit -q
.....................                                                    [100%]
21 passed in 0.22s
```

Breakdown:
- `test_binance_client.py` (8 tests) — no-trading-import guard, bid/ask vs mid-fallback book
  ticker logic (including the crossed-book edge case), spread-bp math, retry/backoff exhaustion
  and recovery.
- `test_market_buffer.py` (8 tests) — SYMBOLS list parity against `scripts/d_features.py`
  (read-only test-time import, asserted equal, not depended on at runtime), closed-bar-only
  rebuild, cross-symbol grid alignment with a synthetic gap, native 1m passthrough, 5-minute
  OHLCV aggregation checked against manually computed open/high/low/close/volume, partial
  trailing-bucket exclusion, forming-candle bar count and boundary correctness, forming-candle
  `None` on an empty buffer.
- `test_kline_store.py` (5 tests) — empty-store coverage, append + coverage round-trip,
  append-is-idempotent (a later write never overwrites an existing (symbol, open_time) row),
  per-symbol isolation, range-filtered load ordering.

**Integration / replay tests**: none yet — `intelligence/tests/integration/` and
`intelligence/tests/replay/` are scaffolded (empty) pending a phase that has live data to
exercise (Phase 14, cycle_runner) and pending `kline_store.py` actually accumulating real bars
from a running process.

**Known limitation, stated plainly**: `MarketBuffer.resample()`'s partial-bucket exclusion is
symmetric — it also drops a bucket that is only partial because it sits at the *start* of the
held history (not just the live trailing edge), which is correct behavior but means the very
first bucket of any freshly-backfilled buffer for a non-1-minute timeframe is routinely
dropped. This is intended, not a bug, and is exercised by
`TestResample.test_partial_trailing_bucket_excluded`.

---

## Phase 4 — candle / price-action + market structure engines

**Scope**: `intelligence/engines/common.py` (shared true-range/ATR/rolling-percentile-rank
helpers), `intelligence/engines/candle_engine.py`, `intelligence/engines/structure_engine.py`.

**Isolation check**: `git diff --stat -- scripts/ artifact/ deploy/ PRE_DECLARATION.md` →
empty.

**Unit tests**:

```
$ python3 -m pytest intelligence/tests/unit -q
.......................................................                  [100%]
55 passed in 0.29s
```

Breakdown of the 34 new tests:
- `test_common.py` (9) — true range on the first bar and on a gap, ATR warm-up NaN region,
  ATR matched against a manually computed mean of true range, all-NaN on a too-short series,
  rolling percentile rank at its max/min and during warm-up.
- `test_candle_engine.py` (18) — body/wick/range arithmetic, close-location-value at both
  bounds, the zero-range bar producing NaN instead of a crash, bullish and bearish engulfing
  (and a case that must NOT engulf), upper- and lower-wick rejection (and a genuinely balanced
  bar that must NOT be flagged), inside-bar detection (positive and negative), breakout-up
  detection against a synthetic prior range, no-breakout inside that same range, expansion and
  compression flags against a freshly-established ATR, prior-high/prior-low take-out, and the
  `latest()` convenience wrapper (including the empty-input `None` case).
- `test_structure_engine.py` (7) — a hand-verified 7-bar fixture whose exact swing indices,
  prices and HH/LL labels are computed by hand in the test's own comments (not just asserted
  blindly) and shown to produce no BOS/CHoCH when price never re-crosses a confirmed reference;
  a 6-bar fixture showing the *first* break of structure from a NEUTRAL bias is labeled BOS,
  never CHoCH; a 10-bar extension of that fixture showing a later break AGAINST an established
  BULLISH bias is correctly labeled CHoCH, flips bias to BEARISH, and — being the latest bar —
  reports `trend=TRANSITION`; a 30-bar flat series producing zero swings and a NEUTRAL trend;
  and the empty-input `None` case.

**Design choices flagged for the record** (not defects, but assumptions worth remembering when
later phases consume these engines):
- `structure_engine`'s BOS/CHoCH definitions are this project's own causal, testable
  definitions (documented in the module docstring), not a claim of matching any one named
  external methodology exactly.
- `candle_engine`'s `expansion`/`compression`/`atr_normalized_range` fields are `NaN`/`False`
  until `atr_period` bars are available — no synthetic warm-up ATR is invented, consistent with
  `common.atr`'s own warm-up behavior.
- Both engines operate on a single symbol's `OHLCV` series at a time (called once per symbol
  per timeframe by a later orchestration phase), not vectorized across all 10 symbols at once —
  a deliberate simplicity-over-throughput choice at this scale (~2.4 EXP-107 signals/day; this
  is not a low-latency path).

---

## Phase 5 — volatility + magnitude engines

**Scope**: `intelligence/engines/volatility_engine.py`, `intelligence/engines/magnitude_engine.py`,
plus a shared `bucket_percentile()` added to `intelligence/engines/common.py` so both engines
(and later ones) answer "is this reading unusual" the same way.

**Isolation check**: `git diff --stat -- scripts/ artifact/ deploy/ PRE_DECLARATION.md` →
empty.

**Unit tests**:

```
$ python3 -m pytest intelligence/tests/unit -q
........................................................................ [ 90%]
........                                                                 [100%]
80 passed in 0.31s
```

Breakdown of the 25 new tests:
- `test_common.py` (+6) — `bucket_percentile()` at each named bucket, inclusive-lower boundary
  behavior at the three cutoffs (0.25/0.75/0.95), and `NaN`/`None` both mapping to `UNKNOWN`.
- `test_volatility_engine.py` (12) — `compute()`'s `atr` and `atr_percentile` fields checked
  for exact agreement with direct calls to `common.atr` / `common.rolling_percentile_rank` (a
  wiring check, since the math itself is already covered in `test_common.py`), realized
  volatility at exactly zero on a constant-price series and `NaN` before its own warm-up,
  expansion/contraction flags on a deliberately separated abrupt range change (old-block and
  new-block bars kept far enough apart that the two ATR readings being compared never overlap
  the transition), neither flag on a stable range, and empty/short-series edge cases.
- `test_magnitude_engine.py` (13) — move/pct-move arithmetic against a linear price series
  where the expected answer is closed-form, `NaN` (never a fabricated value) when a horizon
  exceeds available history including the exact boundary `n == horizon_bars`, correct sign of
  the ATR-normalized move on an uptrend vs a downtrend, `state` checked for exact agreement with
  `bucket_percentile()` on the engine's own `percentile_rank` output across a full random
  series (not just one hand-picked bar), and `compute_all_horizons()` correctly reporting `None`
  substance (a real snapshot object, `NaN` move, `UNKNOWN` state — not a missing key) for a
  horizon longer than the held buffer.

**Design choice flagged for the record**: `MAG_*` bucket thresholds and `volatility_engine`'s
regime buckets both come from `bucket_percentile()`'s fixed 0.25/0.75/0.95 rank cutoffs applied
to a *rolling* percentile computed from this system's own data — never from a specific number
carried over from EXP-123 (which this repository does not have the underlying data for, per
`CODEBASE_MAP.md` Blocker #1). This satisfies `ARCHITECTURE_PLAN.md` section 3.6 but means these
bucket labels should not yet be read as calibrated against any real-world base rate — they
describe "unusual relative to this symbol's own recent history," nothing more, until the
feature registry (a later phase) has enough tracked outcomes to say whether that relative
reading is actually informative.

---

## Phase 6 — liquidity, cross-sectional, and BTC/market regime engines

**Scope**: `intelligence/engines/liquidity_engine.py`, `intelligence/engines/cross_sectional_engine.py`,
`intelligence/engines/regime_engine.py`.

**Isolation check**: `git diff --stat -- scripts/ artifact/ deploy/ PRE_DECLARATION.md` →
empty.

**Unit tests**:

```
$ python3 -m pytest intelligence/tests/unit -q
........................................................................ [ 57%]
.....................................................                    [100%]
125 passed in 0.33s
```

Breakdown of the 45 new tests:
- `test_liquidity_engine.py` (17) — order-book imbalance on a balanced and a bid-heavy
  synthetic book (plus `None`/malformed-input handling), funding-rate/basis extraction from a
  synthetic `premiumIndex` payload (including the partial-data case), open-interest extraction,
  a volume z-score spike correctly detected against a noisy baseline, a **separate** test
  proving a perfectly flat (zero-variance) baseline reports `NaN`/no-spike rather than a
  divide-by-zero crash or a fabricated number, recent-high/recent-low correctly excluding the
  current bar from its own baseline, and the composed `LiquiditySnapshot` correctly reporting
  every exchange-sourced field as `None`/`"UNKNOWN"` when no exchange data was supplied at all
  (this module makes no network calls itself — see the module docstring).
- `test_cross_sectional_engine.py` (16) — direction-from-return sign in all three cases plus
  the insufficient-history `None` case, agreement-ratio arithmetic on a unanimous read, a
  partial split, an exact tie (→ `NEUTRAL`, not an arbitrary tiebreak), `None`-valued symbols
  excluded from the count, an all-flat book producing a `NaN` ratio rather than a fabricated
  0/1, and `symbol_alignment()`'s four outcomes (ALIGNED/DIVERGENT/NEUTRAL/UNKNOWN), including
  the case where the market itself is tied.
- `test_regime_engine.py` (12) — lag-1 return autocorrelation sign on a constructed trending
  series (positive) and an alternating series (negative), the `MOMENTUM`/`MEAN_REVERSION`
  labels attached to those same two constructions, `UNKNOWN` on too little history,
  `rolling_correlation()` verified against two fixtures built so the expected correlation is
  exactly ±1 in RETURN space (not price space — see the note below), an uncorrelated
  short-series `NaN` case, `btc_regime()`'s returned snapshot fields sanity-checked against
  their declared enums, and `btc_correlations()`'s per-symbol dict shape.

**Correctness note worth keeping** (caught by the tests themselves, not found later): a price
series that is a pure *affine* transform of another (e.g. `b = 2*a + 5`) does **not** have
returns that are perfectly linearly related to `a`'s returns, because simple returns are
ratios, not differences — only a pure *scalar* multiple (`b = k*a`, no additive offset)
preserves that exactly. The first draft of the correlation tests assumed otherwise and failed
against the real implementation; the fixtures were corrected to construct the expected return
relationship directly (via `b[t] = b[t-1] * (1 - ra[t])` for the −1 case) rather than relying on
a price-space mirror. This is a property of return-space correlation in general, not a defect
in `rolling_correlation()`.

**Design choices flagged for the record**:
- `liquidity_engine.compute()` makes no exchange calls itself — every exchange-sourced argument
  (`book_ticker`, `depth`, `premium_index`, `open_interest`) is optional and independently
  fetched elsewhere by a later orchestration phase. `liquidation_data` is unconditionally
  `"UNKNOWN"`: no endpoint for it is used anywhere in this codebase, live or research.
- `cross_sectional_engine` never produces anything resembling a trade decision — only a ratio
  and a label. `symbol_alignment()` is the sole function relating one symbol's own read to the
  market majority, and it never substitutes the majority for the symbol's own value.
- `regime_engine` does not reproduce or assume EXP-116/117's finding that BTC relates to
  EXP-107's D signal (that finding's underlying data is Blocker #1, not in this repository) —
  it computes independent, freshly-defined regime evidence from live data only.

---

## Phase 7 — independent direction estimator (secondary evidence)

**Scope**: `intelligence/engines/direction_engine.py`.

**Isolation check**: empty.

**Unit tests** (part of the combined run below): 6 tests in `test_direction_engine.py` —
unanimous agreement across three timeframes producing `confidence=1.0`/`uncertainty=0.0`, the
mirrored downtrend case, `UNKNOWN` (never a fabricated LONG/SHORT) on an empty buffer, an
explicit assertion that `UNKNOWN` is never converted into a directional read, the sample-size
confidence penalty when only 1 of 2 requested timeframes has enough history, and a
hand-constructed disagreement case (documented arithmetic in the test's own comments: a
1-minute-bar dip just large enough to flip the 1-minute read negative while staying too small
to flip the 5-minute/15-minute bucket reads) proving the majority vote and the
`conflicting_factors` list both behave correctly when timeframes genuinely disagree.

**Design choice flagged for the record**: this engine is an explicitly simple, documented
multi-timeframe majority vote — not a trained model, and not a reproduction of any EXP-108→123
model (their code/data are Blocker #1, not in this repository). `ARCHITECTURE_PLAN.md`'s hard
invariant holds: this module's output is SECONDARY evidence only and cannot, by itself, produce
an ENTER decision — see Phase 8's `Exp107Signal.is_long_fire`, the one function in the whole
package permitted to answer that question.

---

## Phase 8 — read-only EXP-107 adapter

**Scope**: `intelligence/core/exp107_signal.py`.

**Isolation check**: empty. Zero lines changed in `scripts/shadow_engine.py` or
`scripts/d_features.py` — `Exp107SignalProvider` imports and calls `ShadowEngine` verbatim.

**Unit tests** (part of the combined run below): 13 tests in `test_exp107_signal.py`, including
one deliberately **not mocked**: `TestRealRepoState` runs the real
`scripts/shadow_engine.ShadowEngine.load()` against this repository's real, currently
incomplete `artifact/` directory (Blocker #2 — `booster_*.pkl`/`isotonic_*.pkl` are gitignored
and absent) and asserts `status == "UNAVAILABLE"` with a non-empty `error` — proving today's
actual behavior end-to-end rather than only asserting it against a mock. The other tests cover:
status/error caching (loading is attempted once, not retried every call), `evaluate()`
returning one `Exp107Signal(status="UNAVAILABLE", ...)` per symbol with every score field `None`
(never fabricated) when unavailable, `Exp107Signal.is_long_fire`'s four truth-table cases, the
Decision→Exp107Signal field mapping including the numeric `side` (±1.0) → `"LONG"`/`"SHORT"`
string translation (exercised via a fake engine so this doesn't require real `.pkl` files), and
`recent_shadow_trades()`'s missing-db/missing-table/row-ordering/limit/never-writes behavior
against a synthetic SQLite file matching `shadow_trades`'s real key columns.

**Combined run after Phases 7+8**:

```
$ python3 -m pytest intelligence/tests/unit -q
........................................................................ [ 50%]
........................................................................ [100%]
144 passed in 0.39s
```

**Design choice flagged for the record**: `Exp107Signal.is_long_fire` is documented as "the
ONE function in the entire intelligence/ package permitted to answer 'did EXP-107 say ENTER'."
This is a structural enforcement of `ARCHITECTURE_PLAN.md`'s hard invariant (section 0) that
EXP-107 must say LONG before anything downstream can ever ENTER — every later phase that needs
that answer calls this property rather than re-deriving the fired/side condition itself, so
there is exactly one place in the codebase where that rule could be gotten wrong, and it is
covered by the four `TestExp107SignalIsLongFire` cases above.

---

## Phase 9 — confirmation layer (evidence, feature registry, confirmation engine)

**Scope**: `intelligence/confirmation/evidence.py`, `intelligence/confirmation/feature_registry.py`,
`intelligence/confirmation/confirmation_engine.py`.

**Isolation check**: empty.

**Unit tests**:

```
$ python3 -m pytest intelligence/tests/unit -q
........................................................................ [ 41%]
........................................................................ [ 83%]
.............................                                            [100%]
173 passed in 0.56s
```

Breakdown of the 29 new tests:
- `test_evidence.py` (3) — `EvidenceBundle.by_strength()` filtering, `.counts()` tallying, and
  the empty-bundle case.
- `test_feature_registry.py` (14) — the tail-dependency ratio on an even distribution vs. one
  dominated by a single observation (and the empty/all-zero `NaN` cases), an unseen feature
  reporting `UNSET`/weight `0.0`, a feature below `MIN_N_FOR_LOW` staying `UNSET` regardless of
  its raw hit rate, a feature with plenty of data but zero edge staying capped at `LOW`
  (never promoted just because `n` is large), `MEDIUM` and `HIGH` promotion each checked against
  their exact `n`/edge thresholds, and — the test written specifically to guard the lesson from
  EXP-120/121 — a feature with `HIGH`-qualifying sample size AND edge that is nonetheless capped
  at `LOW` because nearly all of its apparent edge comes from one extreme observation. Also:
  symbol/regime diversity counting, `all_stats()` coverage, and proof that the registry snapshots
  its input list rather than referencing it live (mutating the caller's list after construction
  does not change already-computed stats).
- `test_confirmation_engine.py` (12) — all three ways `exp107.is_long_fire` can be false
  (`UNAVAILABLE`, `OK` but not fired, `OK` fired but `SHORT`) each producing the explicit
  `NO_SIGNAL` label rather than a silently-defaulted `DEFER`; an `UNSET`/never-scored feature's
  support or conflict contributing exactly `0.0` to the score either way (proving "absence of
  evidence is not evidence"); `UNKNOWN` evidence excluded from the score entirely and landing in
  `n_unknown`, with the resulting label `DEFER` — never `WEAKEN` or `INVALIDATE` — directly
  testing the brief's "do not convert UNKNOWN into a negative signal" rule; `CONFIRM` from a
  `STRONG_SUPPORT` on a `HIGH`-confidence feature, and a second case landing exactly on
  `CONFIRM_THRESHOLD`'s inclusive boundary; `WEAKEN` and `INVALIDATE` each checked against their
  own threshold boundaries; the no-evidence-at-all `DEFER` default; and mixed support+conflict
  evidence netting out to the arithmetically expected score.

**Design choices flagged for the record**:
- `confirm()` only ever runs when `exp107.is_long_fire` is true — reusing Phase 8's single
  gate function rather than re-deriving the fired/side condition, so this layer cannot
  accidentally confirm/weaken/invalidate a signal EXP-107 never actually produced.
- `FeatureRegistry` is deliberately decoupled from `intelligence/memory/` (a later phase): it
  consumes a plain `list[Outcome]`, however sourced, so the promotion logic that guards against
  repeating EXP-120's unreplicated cells is fully unit-tested today without depending on a
  decision-memory database schema that doesn't exist yet. When that phase lands, it only needs
  to produce `Outcome` records — this registry does not change.
- The evidence→score arithmetic (`STRENGTH_SIGN * registry.weight_for(feature)`) is
  intentionally simple and fully auditable — every contribution is individually visible in
  `weighted_support`/`weighted_conflict` for the eventual decision journal, rather than folded
  into an opaque single number.
