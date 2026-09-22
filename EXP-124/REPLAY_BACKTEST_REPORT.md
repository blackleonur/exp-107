# EXP-124 — EXP-107 Frozen Gate Replay / Backtest Report

Status: **PARTIAL**. Sections A–D, F(side-only), I, J, K are measured. Sections
E/F(return-based) and G are **NOT COMPUTED** — no source of real historical
price/OHLCV data was reachable in this session (see §A). No verdict on
whether the gate "works" or has "edge" is given anywhere in this document,
per the task's explicit instruction — only raw measurements are reported.

No file under `scripts/`, `artifact/`, `deploy/`, or any existing
`intelligence/` module was modified to produce this report
(`git diff --stat -- scripts/ artifact/ deploy/ PRE_DECLARATION.md` is empty
as of this commit). Everything below comes from (1) reading existing frozen
code, (2) a fresh, new, read-only Python script run against the real,
hash-verified artifact and the repo's pre-existing replay CSVs, and
(3) pre-existing repo evidence files that were already present before this
session (`verification.json`, `gate2.json`, `gate2_all_decisions.csv`,
`gate2_replay_trades.csv`).

---

## A) Replay ortamı (environment)

**Blocker discovered this session, reported before any other section:**
`fapi.binance.com` is unreachable from this sandbox. A direct test call
(`BinanceClient().klines("BTCUSDT","1m",5)` via
`intelligence/core/binance_client.py`) failed on the first and only attempt
with `Tunnel connection failed: 403 Forbidden`. The proxy's own status log
(`recentRelayFailures`) confirms this as an organization network-policy
denial, the same class of denial already established earlier in this
session against the VPS (`31.57.77.4`) and `https://example.com` — not a
transient error. Per the standing rule in this session, this was **not
retried**. There is also no local archive of historical OHLCV data
anywhere in the repository or filesystem (`tradebot`/`results/exp092`/
`results/exp105` packages are absent, re-confirmed by search at the start
of this task).

**Consequence:** it is not possible, in this session, to fetch fresh
historical klines and run a true "TEST 1 → TEST 2" pipeline (score
generation followed by independently-computed forward returns) for the
frozen gate. There is no source of real bar-level price data reachable at
all — fabricating one was ruled out per the task's explicit "never
fabricate" instruction.

**What was used instead (real, not synthetic):**

1. The 12 real EXP-107 model files (`booster_*.pkl`, `isotonic_*.pkl`),
   already transferred to this session via chat upload in an earlier
   stage, hash-verified byte-for-byte against `artifact/artifact.json`'s
   recorded SHA-256 prefixes, and successfully loaded this session via
   `ShadowEngine.load(path=<verified staging dir>)` (`threshold =
   0.964579822530864`, `weights=[0.5, 0.5]`, all 6 boosters/isotonics
   present, `ref_model`/`ref_rv30` each length 129,600).
2. The repository's own **pre-existing** (not generated this session)
   replay outputs from `scripts/R2_gate2_replay.py`:
   `gate2_all_decisions.csv` (23,040 rows — every decision point evaluated
   over the replay window), `gate2_replay_trades.csv` (the 38 deduped ENTER
   trades), `gate2.json` (summary stats), and `verification.json`/`.csv`
   (feature-parity checks). These already encode `raw_score`, `rv30`,
   `rank_model`, `rank_rv30`, `comb`, `fired`, `side` for real historical
   market data that was fetched in a prior EXP-107 run — this session did
   not and could not regenerate this dataset, since it requires both
   Binance access and the `tradebot`/`d_features` live pipeline context
   that produced it originally.
3. One fresh computation performed this session (see §I): re-deriving
   `comb` from `gate2_all_decisions.csv`'s own `raw_score`/`rv30` columns
   using the real `ref_model.npy`/`ref_rv30.npy`/`PINNED_THRESHOLD` loaded
   from `artifact/`, and diffing against the CSV's own recorded `comb`
   column.

Window covered by this pre-existing dataset: **2026-08-27T00:00:00 →
2026-09-11T23:00:00** (16 days), taken from `scripts/R2_gate2_replay.py`'s
own `WIN_FROM_ISO`/`WIN_TO_ISO` constants — this is the same window
`gate2.json` reports results for, and the same window EXP-105 comparison
was run over (see §J).

---

## B) Frozen gate'in gerçek implementasyonu (exact code, no modification)

Read directly from `scripts/shadow_engine.py`, `ShadowEngine.evaluate()`
(lines 75–110), unmodified:

```python
# lines 90-93
conf = np.abs(raw - 0.5)
r_m = np.searchsorted(self.ref_model, conf) / len(self.ref_model)
r_v = np.searchsorted(self.ref_rv30, rv) / len(self.ref_rv30)
comb = 0.5 * r_m + 0.5 * r_v
```

```python
# lines 96-103
for j, s in enumerate(SYMBOLS):
    ok = np.isfinite(rv[j]) and np.isfinite(raw[j])
    side = 1.0 if raw[j] >= 0.5 else -1.0
    fired = bool(ok and comb[j] >= self.threshold and side > 0)
    reason = ("" if fired else
              "nonfinite" if not ok else
              "below_threshold" if comb[j] < self.threshold else
              "short_dropped")
```

Exact semantics, as written (no paraphrase beyond variable naming):

- `raw` = booster's calibration-input probability for the horizon (`raw_score`
  in the CSVs); `conf = |raw - 0.5|` is the model's confidence away from a
  coin flip.
- `r_m` = rank of `conf` against a frozen reference distribution
  `ref_model.npy` (129,600 values, `searchsorted`, normalized to [0,1]).
- `r_v` = rank of `rv30` (30-bar realized volatility) against a frozen
  reference distribution `ref_rv30.npy` (129,600 values), same mechanism.
- `comb = 0.5*r_m + 0.5*r_v` — an **equal-weighted average of two
  percentile ranks**, not a weighted sum of raw values. `meta["weights"] ==
  [0.5, 0.5]` is asserted at `load()` time (line 71), so this 0.5/0.5 split
  is enforced, not merely documented.
- `side = 1.0 if raw >= 0.5 else -1.0` — direction is read directly off the
  raw (uncalibrated) probability's side of 0.5, not off the calibrated
  score.
- `fired` requires **three** simultaneous conditions: both `rv30` and `raw`
  finite, `comb >= self.threshold` (the single pinned scalar
  `PINNED_THRESHOLD = 0.964579822530864` from `artifact.json`), **and**
  `side > 0`. The `side > 0` condition is what makes this LONG-only: a
  short-direction signal that clears the `comb` threshold is explicitly
  logged as `reason="short_dropped"` and never fires, regardless of `comb`.
- `self.threshold` is loaded once, unchanged, from `artifact.json`'s
  `PINNED_THRESHOLD` field — nothing in `evaluate()` recomputes or adjusts
  it.

Deduplication (`dedup_earliest()`, lines 113–120, also read but not
modified): for repeated fires at different decision minutes for the same
`(symbol, anchor_ms)`, only the **earliest** qualifying minute is kept as
the trade. This is why 41 raw per-decision-point fires collapse to 38
deduped trades (see §D) — 3 anchors fired at more than one minute and are
counted once.

---

## C) Dataset / zaman aralığı

- Source: `gate2_all_decisions.csv` (pre-existing repo file, produced by
  `scripts/R2_gate2_replay.py`, not regenerated this session).
- Window: `2026-08-27T00:00:00` → `2026-09-11T23:00:00` (16 days),
  hourly anchors × `DECISION_MINUTES = (10, 30, 60, 120, 240, 480)` × 10
  symbols (`SYMBOLS`, from `d_features.py`).
- Row count: 23,040 (confirmed by direct count of the CSV this session —
  matches `gate2.json`'s recorded `decision_points: 23040` exactly).
- `verification.json` (pre-existing) separately documents a feature-parity
  check over a different, earlier sub-window (`2026-08-24 12:00:00` →
  `2026-08-26 23:00:00`, 60 anchors, 9,360 cells, 0 NaN mismatches, worst
  relative error `1.46e-05` on `rkurt_30`) — this is Gate 1 (feature
  computation parity between full-buffer and live-buffer paths), a
  different check from the gate-replay dataset in §D–J, cited here only
  because it is relevant evidence for §I (leakage).

---

## D) Signal count (TEST 6)

Freshly counted this session, directly from `gate2_all_decisions.csv`
(23,040 rows), by recomputing `comb` from the CSV's own `raw_score`/`rv30`
columns using the real `ref_model.npy`/`ref_rv30.npy`/`threshold` loaded
from `artifact/artifact.json` (see §I for the recomputation-consistency
check itself):

| Quantity | Count |
|---|---|
| Total decision points evaluated | 23,040 |
| Rows crossing `comb >= threshold` ("raw fires", pre LONG-only filter) | 41 |
| Raw fires with `side = -1` (SHORT) | 0 |
| Raw fires with `side = +1` (LONG) | 41 |
| Fires dropped by the LONG-only filter (`short_dropped`) | 0 |
| Final deduped ENTER trades (`dedup_earliest`) | 38 |
| Side distribution across **all** 23,040 decision rows | `side=-1`: 8,526 &nbsp;&nbsp; `side=+1`: 14,514 |

**Explicit statement per the task's own instruction ("Eğer sample sayısı çok
düşükse bunu açıkça belirt"): 38 trades (and 41 pre-dedup raw fires) over a
16-day window is a low sample count.** It is too small to support any
statistical claim about hit rate, drawdown, Sharpe, or profit factor even
if the underlying return data existed — this is stated independent of and
in addition to the fact that no return data is actually computable this
session (§E–G).

An incidental, real pattern visible in this count: in this specific
16-day window, **every** raw fire (comb ≥ threshold) that occurred already
had `side = +1`. The LONG-only filter therefore rejected zero decision
points in this window — not because the filter is inactive, but because no
short-direction decision point happened to clear the `comb` threshold here.
This is a property of this particular 16-day sample, not a property of the
gate logic itself (which does define and would enforce a short-drop path,
per §B).

---

## E) Long sonuçları / F) Short sonuçları

**Side composition (measured):** 38/38 deduped trades are LONG (`side=+1`);
0 are SHORT. This matches the frozen gate design itself (`fired` requires
`side > 0`, per §B), so "short results" for *executed trades* is
definitionally an empty set under this gate, not a data limitation.

**Return-based content (hit rate, mean/median return, drawdown, Sharpe,
etc.) for the 38 LONG trades: NOT COMPUTED.** This requires forward price
paths from the actual trade entry bar, which requires OHLCV data this
session cannot obtain (§A). No estimate, proxy, or placeholder value is
substituted here.

---

## G) Forward-return sonuçları (TEST 2)

**NOT COMPUTED.** No real price/OHLCV data source was reachable this
session for any of the +1/+3/+5/+10/+30 bar horizons, for either the 38
ENTER trades or the 22,999 non-firing decision points needed for a
no-gate/all-eligible baseline. This is the direct consequence of the
Binance-access blocker in §A. Nothing in this section is estimated or
fabricated.

---

## H) Baseline karşılaştırması (TEST 3)

Only the **non-return** portion is measurable this session:

| | A) EXP-107 gate | B) No-gate / all-eligible | C) Random / top-percentile |
|---|---|---|---|
| Sample count | 38 trades (41 raw fires) | 23,040 decision points | not constructed — would require the same return data as B, additionally not computed |
| Long/short split | 38 / 0 | 14,514 / 8,526 (all rows, not just eligible-direction ones) | — |
| Hit rate, mean/median/cumulative return, drawdown, volatility, profit factor, Sharpe | **NOT COMPUTED — no price data** | **NOT COMPUTED — no price data** | **NOT COMPUTED — no price data** |

The gate's *selectivity* is measurable without price data: 38 of 23,040
decision points (≈0.16%) become trades. This matches the artifact's
documented design as a **top-percentile-style rank gate** (§B: `comb` is
an average of two percentile ranks, and `PINNED_THRESHOLD ≈ 0.9646`, i.e.
the gate requires being in roughly the top ~3.5 combined-percentile band
of `comb` before the LONG-only and finiteness filters are even applied) —
this is a structural observation about the gate's selectivity, not a
return-based comparison, and is not a verdict on whether that selectivity
correlates with subsequent price movement.

---

## I) Leakage kontrolleri (TEST 5) — ÇOK ÖNEMLİ

Three independent pieces of evidence, none fabricated this session:

1. **Fresh recomputation-consistency check (performed this session).**
   Loaded the real `ref_model.npy`, `ref_rv30.npy`, and
   `PINNED_THRESHOLD` from `artifact/artifact.json`. For all 23,040 rows
   of `gate2_all_decisions.csv`, recomputed `conf = |raw_score - 0.5|`,
   `r_m = searchsorted(ref_model, conf)/len(ref_model)`, `r_v =
   searchsorted(ref_rv30, rv30)/len(ref_rv30)`, `comb_recomputed = 0.5*r_m
   + 0.5*r_v`, and compared to the CSV's own recorded `comb` column.
   **Result: max |comb_recomputed − comb_recorded| = 0.000e+00 across all
   23,040 rows**; recomputed fired-count (`comb_recomputed >= threshold`)
   = 41, exactly matching the recorded fired-count = 41. This shows the
   rank-combination step is exactly reproducible from `ref_model.npy`/
   `ref_rv30.npy` (both frozen, fixed-size 129,600-entry arrays, not
   recomputed per-window) — i.e. the reference distributions used for
   ranking are **not** being refit on the replay window itself. If they
   were, "rank against a distribution built from data including the
   future" would be a live leakage path; this check does not fully rule
   that out (it doesn't independently verify how `ref_model.npy`/
   `ref_rv30.npy` were originally built), but it does rule out any
   *runtime* recomputation of the ranking reference on replay data.

2. **`d_features.py`'s own causal assertions** (read, not modified, this
   session): `compute_block()` asserts `end.max() < C.shape[0]` (line 104,
   "decision minute is beyond the buffer") and, inside the 5-bar variance
   ratio feature, `assert pts.max() <= end[idx].max()` (line 136, "vratio
   would read past minute t"). Both are runtime guards against reading
   buffer rows after the decision anchor + minute — i.e. the feature
   computation code itself refuses to run if it would need future bars,
   rather than merely being documented as causal.

3. **Pre-existing Gate 1 evidence (`verification.json`, not generated this
   session)**: a feature-parity check between the "full buffer" and "live
   buffer" computation paths over 60 anchors / 9,360 cells found 0 NaN
   mismatches and a worst relative error of `1.46e-05`. This is evidence
   that the live (causal, buffer-only) feature path numerically matches
   the full-history path, which is consistent with (but not, by itself,
   a complete proof of) the absence of look-ahead — a live path that
   accidentally saw future data would be expected, though not guaranteed,
   to diverge from a purely causal full-buffer computation on at least
   some cells.

**What is NOT verified this session:** the training-time construction of
`ref_model.npy`/`ref_rv30.npy` themselves (i.e., whether the training
process that produced these two reference arrays used any data that would
not have been available at each historical point in time), and the
booster/isotonic training pipeline's own train/validation split
methodology — both are outside what this session's replay/analysis over
already-frozen artifacts and already-existing CSVs can establish. This
gap is stated explicitly per the task's "don't fabricate/assume" rule
rather than left implicit.

---

## J) EXP-105 karşılaştırması (TEST 7)

**Comparable, and already validated — but via pre-existing repo evidence,
not something freshly re-run this session** (re-running
`R2_gate2_replay.py` from scratch was not possible: it requires the
`tradebot` package and live Binance access, neither available this
session).

From `gate2.json` (pre-existing file, produced by `R2_gate2_replay.py`
against exactly EXP-105's own replay window, by that script's own
construction — `WIN_FROM_ISO`/`WIN_TO_ISO` match EXP-105's window):

```json
{
  "gate2": true,
  "exp105": 38,
  "replay": 38,
  "intersection": 38,
  "missing": 0,
  "extra": 0,
  "minute_agreement": 1.0,
  "max_score_diff": 1.6913157097064868e-08,
  "decision_points": 23040,
  "raw_fires": 41
}
```

This states: EXP-105 recorded 38 trades over this window; this replay
(`shadow_engine.py`'s `ShadowEngine.evaluate()`, the same code read in §B)
produced 38 trades; all 38 intersect (0 missing from replay, 0 extra in
replay), with 100% decision-minute agreement and a max score difference of
`1.69e-08` (floating-point-level, not a logic difference). This is
consistent with the fresh recomputation-consistency result in §I (both
independently arrive at `raw_fires = 41`, `decision_points = 23040`).

Since this data is being cited, not regenerated, this session cannot
independently re-verify that `R2_gate2_replay.py` was actually run
correctly at the time `gate2.json` was produced — only that the file's
own content is internally consistent with the other pre-existing evidence
files (`gate2_all_decisions.csv`, `gate2_replay_trades.csv`) and with this
session's fresh recomputation.

---

## K) Henüz cevaplanmamış noktalar (open, unanswered points)

1. **No forward-return or return-based risk/performance measurement of any
   kind was possible this session** (§E–H) — the central question the
   task frames ("does the gate carry forward-return information") is
   **not answered by this report**. Answering it requires real historical
   OHLCV data, which needs either (a) network access to Binance (or
   another OHLCV source) from this sandbox — currently blocked by
   organization policy — or (b) a locally-transferred price dataset
   (e.g. via chat upload, the same channel that delivered the model
   files), which has not been provided.
2. Sample size (38 trades / 41 raw fires over 16 days) is low enough that
   even a future return computation would need to be interpreted with
   that constraint stated explicitly, not glossed over.
3. The training-time provenance of `ref_model.npy`/`ref_rv30.npy` (how the
   129,600-entry reference distributions were built, and whether that
   construction could have used data unavailable at each historical
   decision point) is not established by this report — see §I's explicit
   "not verified" note.
4. The booster/isotonic models' own train/validation temporal split
   (e.g., whether any decision point in the 2026-08-27→09-11 replay window
   overlaps the models' training window) was not checked this session;
   `MODEL_ARTIFACT_ANALYSIS.md` (prior stage) covers model-internals
   analysis but not a direct row-level train/replay-window overlap check.
5. Whether `gate2_all_decisions.csv`/`gate2.json` themselves were produced
   under conditions matching what a fresh, independent re-run in this
   session would produce cannot be fully re-verified here, since
   `R2_gate2_replay.py` could not be re-executed (missing `tradebot`
   dependency, no network access) — §J relies on citing this pre-existing
   file, cross-checked only for internal consistency, not independently
   reproduced end-to-end.
