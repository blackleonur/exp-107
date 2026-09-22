# EXP-124 — MODEL_ARTIFACT_ANALYSIS.md
## Technical analysis of the 12 real EXP-107 model files

**RESEARCH ONLY. This is a read-only technical inspection of files the user uploaded directly
to this conversation. No model was retrained, modified, or fabricated. Nothing under
`scripts/`, `artifact/`, or `deploy/` was touched. The files analyzed here have NOT been copied
into `artifact/` — this document is analysis only, per the scope of the request that produced
it. Every number below was read directly off the deserialized objects; nothing is inferred
beyond what the file content itself supports.**

---

## 0. Provenance — these are the real files

The uploaded archive (`exp107_models.zip`, 12 files) was extracted to a scratch directory and
checked with `intelligence/core/artifact_import.py --dry-run`:

```
present: 12/12   matching: 12/12
```

Every file's freshly computed SHA-256 matches, in full (64 hex characters, not just the
16-character prefix), the value already recorded in this repository's own
`artifact/artifact.json` (frozen by `scripts/R1_freeze_artifact.py` before this session began).
This is the strongest verification available without a cryptographic signature: the bytes now
in hand produce the exact digest the artifact's own author recorded at freeze time. **They were
not copied into `artifact/` for this analysis** — that step is still pending a separate,
explicit go-ahead, consistent with this session's established sequencing.

---

## 1. Load environment

- `joblib==1.6.0` (already present)
- `lightgbm==4.7.0` (installed for this analysis — matches the version this repository's own
  code comments already reference, e.g. `scripts/shadow_engine.py`'s docstring)
- `scikit-learn==1.9.0` (installed and pinned to this exact version after an
  `InconsistentVersionWarning` on first load reported the isotonic calibrators' internal
  `LabelEncoder` was pickled under 1.9.0 — re-installing the matching version removed the
  warning and eliminates any version-skew risk in what follows)

No prediction call was made on any `LGBMClassifier` in this analysis (only metadata
introspection — `dump_model()`, `feature_importance()`, `num_trees()`, `get_params()`), so the
documented LightGBM/pandas-or-polars co-import crash (`scripts/shadow_engine.py`'s own
docstring, `EXP-094 section 5.9`) was never in play here — neither `pandas` nor `polars` was
imported in this analysis process at any point.

---

## 2. What each file actually is

| File | Python type | Role |
|---|---|---|
| `booster_{t}.pkl` (×6) | `lightgbm.sklearn.LGBMClassifier` | **Not a raw `lightgbm.Booster`** — a full scikit-learn-API wrapper object. Its `.predict_proba()` method is what `scripts/shadow_engine.py:87` calls (`self.boosters[minute].predict_proba(X)`), and that call is consistent with this type: `LGBMClassifier.predict_proba()` returns an `(n, 2)` array, and `shadow_engine.py` takes column `[:, 1]` — `P(class=1)`. |
| `isotonic_{t}.pkl` (×6) | `sklearn.isotonic.IsotonicRegression` | A monotone step-function calibrator, one per horizon, fit on `raw -> outcome` pairs from the VALIDATION slice (per `scripts/R1_freeze_artifact.py`'s own comments). |

`t` ∈ {10, 30, 60, 120, 240, 480} — these are `scripts/d_features.py`'s `DECISION_MINUTES`:
**minutes after each hourly anchor**, not classic timeframe candles. A "horizon" here means "how
many minutes after the anchor this particular booster/calibrator pair is meant to evaluate,"
exactly as `CODEBASE_MAP.md` §2.9 already established from the surrounding code — now confirmed
from the artifact's own filenames and internal feature count (below) rather than from code alone.

---

## 3. Booster structure — identical hyperparameters, radically different realized complexity

### 3.1 Configuration (identical across all six files)

```
boosting_type:      gbdt
objective:          None (LGBMClassifier default -> binary log-loss)
n_estimators:        300   (configured cap)
num_leaves:            31   (configured cap)
max_depth:              5
learning_rate:       0.05
min_child_samples:     20
subsample:            0.8
subsample_freq:         0
colsample_bytree:     0.8
reg_alpha:            0.0
reg_lambda:           0.0
min_split_gain:       0.0
min_child_weight:   0.001
class_weight:    "balanced"
random_state:           42
importance_type:   "split"
```

Every one of the 19 `get_params()` keys is byte-identical across all six files — this is one
hyperparameter configuration, trained six times on six different (per-horizon) label/feature
slices, exactly matching `scripts/R1_freeze_artifact.py`'s own per-`t` training loop.

### 3.2 What was actually realized: ONE tree per horizon, not 300

| Horizon | `num_trees()` | `n_iter_` | Realized leaves | Realized depth | Validation `binary_logloss` |
|---|---|---|---|---|---|
| 10  | **1** | 1 | 18 | 5 | 0.693243 |
| 30  | **1** | 1 | 18 | 5 | 0.693128 |
| 60  | **1** | 1 | 18 | 5 | 0.693287 |
| 120 | **1** | 1 | 19 | 5 | 0.693306 |
| 240 | **1** | 1 | 18 | 5 | 0.693226 |
| 480 | **1** | 1 | 17 | 5 | 0.693206 |

**Despite `n_estimators=300` being configured, every single one of the six frozen boosters
contains exactly one tree.** This is not a truncated read — `booster_.num_trees()`,
`dump_model()['tree_info']` (length 1), and the sklearn wrapper's own `n_iter_` attribute all
agree. This means early stopping (an `eval_set`-driven early-stopping callback, standard for
this kind of fit) halted training after the very first boosting round for all six horizons —
adding a second tree never improved the validation metric enough to be kept, for any of the six
independently-trained models.

**The validation log-loss is the number that explains why.** `ln(2) ≈ 0.6931472` is the exact
binary log-loss of a model that predicts `p = 0.5` for every sample, regardless of the true
label — the textbook no-skill baseline. All six recorded validation log-losses
(0.693128–0.693306) sit within about ±0.0002 of that baseline, a gap fully consistent with
ordinary sampling noise on a large validation set. **On the metric LightGBM itself used to
decide whether a second tree was worth keeping, none of the six models measurably beat
always-guessing-0.5.** This is read directly from each `LGBMClassifier._best_score` attribute,
not inferred.

### 3.3 Each single tree is dominated by one feature

`feature_importance(importance_type='gain')`, mapped back to real feature names via
`scripts/d_features.py`'s own (unmodified) `feature_names()` function — positions 0–65 are the
66 micro-structure features, 66–155 are the 90 cross-sectional features, 156 is the appended
symbol index, exactly matching `scripts/shadow_engine.py:85-86`'s own feature-assembly order:

| Horizon | Dominant feature | Share of top-8 gain | Split-active features (of 157) |
|---|---|---|---|
| 10  | `own_ret_1`      | 86.1% | 13 |
| 30  | `rskew_30`       | 84.7% | 12 |
| 60  | `relstr_60`      | 85.7% | 13 |
| 120 | `rskew_120`      | 86.7% | 15 |
| 240 | `own_ret_180`    | 84.6% | 11 |
| 480 | `rskew_480`      | 85.7% | 11 |

A clean pattern is visible without needing to assume anything beyond the numbers: **for
t ∈ {30, 120, 480}, the dominant feature is `rskew_{W}` where W exactly equals that horizon's
own micro-feature window** (`d_features.py`'s `WINDOWS = (30, 120, 480)`) — the realized
return-skewness over a window matched to the decision horizon itself. For t ∈ {10, 60, 240} the
dominant feature is instead a same-symbol return (`own_ret_1`, `own_ret_180`) or a
cross-sectional relative-strength read (`relstr_60`) — still a "how has this symbol itself been
moving" signal, but not the skewness family. In every one of the six trees, the same small set
of secondary features recurs among the remaining ~15%: `eth_ret_720`, `btc_ret_1440`,
`btc_ret_360`, `disp_1440`, `breadth_1440`, `mkt_ret_1440` — all cross-sectional, market-wide
context features (ETH/BTC return over long lags, cross-sectional dispersion and breadth) —
present, in some combination, as minor splits in every single horizon's tree.

Only 11–15 of the 157 available features are used at all (nonzero split count) in any given
tree — expected and mechanical given a single depth-5 tree has at most 2⁵ = 32 leaves and
therefore at most 31 internal split nodes, most of which reuse a handful of the same features at
different thresholds rather than spreading across the full feature set.

### 3.4 A raw technical observation, reported without over-interpreting it

Every one of the six trees' ROOT node has a split threshold of exactly `1e+300`. This is an
extreme, LightGBM-internal value; it plausibly reflects either (a) LightGBM's default/missing-
value routing convention for a near-degenerate split, or (b) a genuinely extreme value on the
root's chosen feature (several `d_features.py` features — e.g. `amihud`, `kyle` — are scaled by
`1e4`/`1e10` multipliers and can carry very large magnitudes in the raw feature block). This
analysis did not go further to determine which, since doing so would require re-deriving
LightGBM's internal split-finding behavior rather than reading it off the artifact, and the task
instruction is explicit about not assuming beyond what the file content supports.

---

## 4. Isotonic calibrators — narrow domain, coarse output, and why that's consistent with §3

| Horizon | `X_thresholds_` (raw-score domain fit) | `y_thresholds_` range | # threshold points |
|---|---|---|---|
| 10  | [0.49832, 0.50913] | [0.4724, 0.5130] | 4 |
| 30  | [0.49890, 0.50706] | [0.4697, 0.5406] | 5 |
| 60  | [0.49571, 0.50783] | **[0.0000**, 0.4991] | 5 |
| 120 | [0.49013, 0.50760] | **[0.0000**, 0.5001] | 6 |
| 240 | [0.49758, 0.50664] | [0.4772, 0.5250] | 4 |
| 480 | [0.48866, 0.50817] | **[0.0000**, 0.5058] | 5 |

Every calibrator was fit `out_of_bounds="clip"`, `increasing=True`, `y_min=0.0`, `y_max=1.0`
(all read directly off the objects). Two things follow directly from §3's finding that each
booster is a single shallow tree:

- **The domain each calibrator was fit over is razor-thin** — roughly 0.01–0.02 wide, centered
  on 0.5. A single depth-5 tree's raw sigmoid outputs cluster tightly around 0.5 by
  construction (few, coarse leaf values), so the isotonic fit only ever sees raw scores in that
  narrow band — confirmed, not assumed, by `X_min_`/`X_max_` on every one of the six objects.
- **The calibrated output is piecewise-constant with only 2–3 distinct plateaus** (sampled at
  x ∈ {0.3, 0.5, 0.7, 0.9, 0.99}, each calibrator returns at most 3 distinct values across that
  whole range, with everything above ~0.51 already clipped to the top plateau). This is exactly
  what a 4–6-point isotonic fit over a domain this narrow produces.
- **Three of the six calibrators (t=60, 120, 480) have their lowest plateau pinned to exactly
  `0.0`** — meaning, in the VALIDATION calibration data, the lowest observed raw-score bin
  contained zero positive outcomes. With bins this few and a domain this narrow, that reads as a
  small-sample artifact of the calibration bin, not a claim that the true win rate is genuinely
  zero at low raw scores — stated as an observation, not an inference about ground truth.

**This corroborates, from the artifact's own internals, something `CODEBASE_MAP.md` §2.1 already
established by reading `shadow_engine.py`'s code**: the isotonic-calibrated score (`cal_score`)
is computed and stored on every decision but **does not gate anything** — the actual entry gate
(`comb = 0.5·rank(|raw−0.5|) + 0.5·rank(RV30) ≥ threshold`) uses the raw score's *rank* against a
pooled historical distribution, never the calibrated value directly. Given how coarse a 4-to-
6-step calibrator is, using it directly as a gating threshold would be far less discriminating
than the rank-based approach the frozen system actually uses — the design choice now has a
concrete, artifact-level reason visible, not just a code-reading one.

---

## 5. How the six models are used together (confirmed against `shadow_engine.py`, unmodified)

`ShadowEngine.evaluate()` (`scripts/shadow_engine.py:75-110`, read-only, unchanged) is the only
code that ever combines these 12 objects:

1. For one `(anchor, minute)` decision point, `d_features.compute_block()` produces the 66
   micro + 90 cross features (per symbol), and a symbol index is appended — 157 columns,
   matching `n_features_in_ = 157` verified on every one of the six boosters (§3.1).
2. `self.boosters[minute].predict_proba(X)[:, 1]` → `raw`, using **only the one booster whose
   filename matches the current decision minute** — the six models are never combined,
   ensembled, or averaged with each other; exactly one is consulted per decision point,
   selected purely by `minute`.
3. `self.isotonics[minute].predict(raw)` → `cal` — same one-per-minute selection, computed and
   stored but not used in the entry gate (§4).
4. `rv30()` (a separate, non-ML realized-volatility calculation) and the two frozen reference
   distributions (`ref_model.npy`, `ref_rv30.npy` — already verified present and
   hash-authentic in every prior check this session) combine with `raw` to produce `comb`, which
   is compared against the single `PINNED_THRESHOLD` recorded in `artifact.json`.

There is no cross-horizon interaction in the frozen code: a decision at minute 480 never
consults the minute-10 booster, and vice versa. The six pairs are six independent, structurally
identical-by-configuration but individually-trained single-tree classifiers, each responsible
for exactly one of the six decision-minute checkpoints EXP-107 evaluates per hourly anchor.

---

## 6. What this analysis does and does not establish

**Established, directly from the files**: the artifact's structural shape (single-tree,
per-horizon classifiers with a narrow, coarse calibration layer), which feature dominates each
horizon's one tree, that the configured 300-tree budget was never used, and that validation
log-loss for all six sits statistically at the no-skill baseline **at the point early stopping
halted training**.

**Not established, and not claimed, by this analysis**: whether the frozen `PINNED_THRESHOLD`
gate (operating on the *rank* of `|raw-0.5|` combined with the *rank* of `RV30`, not on raw
log-loss) produces a real edge — that is a live-signal/backtest question, answered (to the
extent it can be, from already-recorded evidence) by `PRE_DECLARATION.md`,
`gate2.json`/`verification.json`, and `CODEBASE_MAP.md` §3, not by this document. A single
tree's near-baseline log-loss does not by itself prove or disprove that its *rank*-transformed,
volatility-conditioned, top-1%-gated signal has skill — those are different statistical claims,
and this analysis deliberately does not conflate them. Whether the artifact should be trusted as
a production signal source is exactly the question the rest of this session's integration and
replay work (still pending real bytes being imported) is designed to answer with data, not with
an inference from model internals alone.
