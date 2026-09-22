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
