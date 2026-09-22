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
