# EXP-124 — CODEBASE_MAP.md

**Phase 1 deliverable. Read-only audit. Nothing in this repository was modified to produce
this document.**

---

## 0. First and most important finding

**This repository is not the trading-bot monorepo the EXP-124 brief describes.** It is a
single, self-contained experiment export: the pre-live shadow-run rehearsal for **one frozen
model** ("D = MODEL + RV30, LONG ONLY"). It contains no `src/tradebot` package, no
`results/exp092_microstructure_direction`, no `results/exp105_long_only_validation`, no
`results/exp108…exp123` directories, and no trained model files. All of this is confirmed by
direct filesystem search — there is exactly one directory on this machine that looks like a
repo (`/home/user/exp-107`), it has one git commit ("borsa bot first commit"), and nothing
matching `tradebot`, `exp092`, `exp105` exists anywhere under `/home`.

Concretely, four of the eight scripts import modules or read directories that are **absent**:

| Script | Missing dependency | Effect |
|---|---|---|
| `scripts/R0_verify.py` | `from tradebot.features.loaders import scan_symbol`; `results/exp092_microstructure_direction/data/*.npz`; `results/exp105_long_only_validation/evaluation.json` | Cannot run — `ImportError` / `FileNotFoundError` on line 1 of execution |
| `scripts/R1_freeze_artifact.py` | same `results/exp092…` npz files; `tradebot.ml.classifiers.fit_lightgbm_classifier`; `tradebot.features.loaders.scan_symbol` | Cannot run |
| `scripts/R2_gate2_replay.py` | `tradebot.features.loaders.scan_symbol`; `results/exp105_long_only_validation/fresh_trades.csv` | Cannot run |
| `scripts/R3_shadow_run.py`, `R4_report.py`, `R5_dashboard.py`, `shadow_engine.py`, `d_features.py` | none of the above — but `shadow_engine.ShadowEngine.load()` requires `artifact/booster_{t}.pkl` and `artifact/isotonic_{t}.pkl` for t in {10,30,60,120,240,480} | **These 12 files are not in the repo.** `.gitignore` line 4 is `*.pkl`, so they were deliberately never committed. Only `artifact.json`, `ref_model.npy`, `ref_rv30.npy` are present. |

**Net effect: no script in this repository can currently execute end-to-end**, including the
shadow runner itself — `ShadowEngine.load()` (`scripts/shadow_engine.py:61-72`) will raise on
the first missing `booster_10.pkl`. What *is* present and self-contained is the **decision
logic** (`d_features.py`, `shadow_engine.py`), the **orchestration/ops code** (`R3`, `R4`,
`R5`), the **result artifacts of a past run** (the four CSV/JSON files at repo root — these are
outputs, already generated elsewhere, checked in as evidence) and the **deployment docs**.

This changes what Phase 1 can honestly conclude, and it is reported now rather than glossed
over, per the task's own rule: results are not made to look better than they are.

**Also absent, entirely:** any artifact, code, or data from EXP-108 through EXP-123. The only
information this session has about those experiments is the prose summary in the EXP-124
task instructions themselves — there is no underlying dataset, notebook, or result file to
re-derive, audit, or validate those claims against. Section 27 of the brief ("does MAG_LONG
HIGH actually add incremental information on top of EXP-107's own signals?") cannot be
answered from this repository as it stands; it needs at minimum the EXP-092/105 feature blocks
and the exp108–123 result sets, none of which exist here.

This is flagged as **Blocker #1** — see the end of this document.

---

## 1. Repository layout

```
exp-107/
├── PRE_DECLARATION.md              EXP-107's own pre-declaration (frozen before its shadow run)
├── .gitignore                      excludes .venv/, __pycache__/, *.pyc, *.pkl, *.log, *.db, .env
├── artifact/                       frozen model artifact (PARTIAL — see Blocker #2)
│   ├── artifact.json               metadata: fold dates, threshold, feature weights, file hashes
│   ├── ref_model.npy                sorted |raw-0.5| confidence distribution (129,600 pts)
│   └── ref_rv30.npy                 sorted RV30 distribution (129,600 pts)
│                                    (booster_{t}.pkl, isotonic_{t}.pkl — MISSING, gitignored)
├── scripts/
│   ├── d_features.py               THE feature block: shared by verification and live engine
│   ├── shadow_engine.py            THE decision core: ShadowEngine.evaluate()
│   ├── R0_verify.py                Gate 1/3: feature + threshold parity vs frozen EXP-092/105
│   ├── R1_freeze_artifact.py       freezes booster/isotonic/ref-distribution artifact (2-stage)
│   ├── R2_gate2_replay.py          Gate 2: signal parity vs EXP-105's 38 selected trades
│   ├── R3_shadow_run.py            the live shadow (paper) runner — polls Binance, no orders
│   ├── R4_report.py                builds REPORT.md / TRADES.csv / DAILY.csv / EXECUTION.csv
│   └── R5_dashboard.py             read-only HTTP dashboard, Turkish UI, token-gated
├── deploy/
│   ├── README.md, VPS_KURULUM.md   deployment instructions (systemd, VPS, Turkish + English)
│   ├── borsabot-exp107-shadow.service      runner unit (no port, outbound only)
│   └── borsabot-exp107-dashboard.service   dashboard unit (port 8440, token-gated)
├── gate2.json, gate2_all_decisions.csv, gate2_replay_trades.csv   Gate-2 run output (past run)
└── verification.json, verification.csv                            Gate-1 run output (past run)
```

No `src/`, no `tests/`, no `results/`, no `tradebot/` package anywhere in this repo.

---

## 2. What "EXP-107" actually is

EXP-107 is **not** a general signal-generation engine. It is the pre-live rehearsal harness
for a single, already-fitted, frozen classifier called **D**, defined entirely by:

> **D = 0.5 · rank(model confidence) + 0.5 · rank(RV30), gated at the 99.0th percentile of that
> combined rank, LONG side only.**

Everything in this repo exists to (a) prove a live-data implementation of D's feature pipeline
reproduces the historical (offline, EXP-092/105) one bar-for-bar, and (b) run D against live
Binance data and paper-record what it would have done, with **zero** order-placement
capability anywhere in the code path.

### 2.1 Core signal production point

`scripts/shadow_engine.py:75-110` — `ShadowEngine.evaluate(C, QV, TB, apos, anchor_ms, minute)`.
This is the **single** signal-generation function in the repository. It is called identically
by the historical-parity replay (`R2_gate2_replay.py:106`) and the live runner
(`R3_shadow_run.py:234-235`) — by design, so the two paths cannot silently diverge (this is
explicitly the failure mode the code's own comments say EXP-096/097 hit: a separately written
forward harness passed its own verification and then fired zero live signals for an unrelated
bug).

Per call it evaluates **all 10 symbols at once**, for **one (anchor, decision-minute) point**:

```
micro, cross = compute_block(C, QV, TB, apos, minute)      # d_features.py:83
rv           = rv30(C, apos, minute)                        # d_features.py:196
X            = hstack(micro, cross, symbol_index)
raw          = booster[minute].predict_proba(X)[:,1]        # LightGBM, frozen
cal          = isotonic[minute].predict(raw)                 # calibration, frozen, unused in gate
conf         = |raw - 0.5|
r_m          = searchsorted(ref_model, conf) / len(ref_model)     # percentile rank vs VALIDATION
r_v          = searchsorted(ref_rv30, rv)   / len(ref_rv30)
comb         = 0.5*r_m + 0.5*r_v
side         = LONG if raw >= 0.5 else SHORT
fired        = finite(rv, raw) AND comb >= PINNED_THRESHOLD AND side == LONG
```

`cal` (the isotonic-calibrated probability) is computed and stored on every decision and shadow
trade row, but **it does not gate anything** — the entry condition is on `comb`, built from the
raw score's *rank*, not from `cal`. This is worth flagging explicitly for the intelligence
layer: `cal_score` is logged as if it were a calibrated confidence, but it is presently inert
inside the decision itself.

### 2.2 LONG/SHORT logic

There is no SHORT execution path. `side = raw>=0.5 ? LONG : SHORT` is computed
(`shadow_engine.py:98`) purely to label *why* a candidate was rejected
(`reason="short_dropped"`, line 103) — a SHORT-leaning score can still cross the `comb`
threshold, but it is dropped at the last line of the gate (`side > 0`, line 99). No trade of
either direction is ever entered on a SHORT score. This is the system's own frozen design
choice (title: "D LONG-ONLY"), not an omission — but it means the phrase "LONG/SHORT koşulları"
from the brief only has a LONG side to inventory here.

### 2.3 Entry conditions

- Evaluated on a fixed schedule, **not** continuously: anchors are every hour on the hour
  (`R3_shadow_run.py:367-369`, `anchor % 3_600_000 == 0`), and at each anchor, decision points
  are checked at **anchor + {10, 30, 60, 120, 240, 480} minutes** (`d_features.py:29`).
- Gate: `comb >= PINNED_THRESHOLD` (0.964579822530864, frozen, `artifact/artifact.json`) **and**
  side is LONG **and** both raw score and RV30 are finite.
- **Deduplication**: only the *earliest* qualifying decision minute per `(symbol, anchor)` is
  kept (`shadow_engine.py:113-120`, `dedup_earliest`) — this is the system's only cooldown-like
  mechanism, and it operates within a single anchor hour, not across anchors.
- Expected fire rate, per the deploy docs: **~2.4 signals/day across all 10 symbols combined**
  (`deploy/README.md:114`) — an extremely sparse, high-bar gate (99th percentile by
  construction), not a frequent-trading system.

### 2.4 Exit conditions / stop-loss / take-profit

**There is no price-based exit of any kind.** Exit is purely time-based:
`close_due_ms = anchor_ms + HOLD_MIN * 60_000`, `HOLD_MIN = 1440` (24 hours), set at entry
(`R3_shadow_run.py:57,267`) and checked every poll cycle (`R3_shadow_run.py:290-291`). There is
no stop-loss, no take-profit, no trailing stop, no early-exit-on-weakening-signal logic
anywhere in the codebase. MFE/MAE and a "first target" (40bp) are **recorded for reporting**
(`R3_shadow_run.py:300-305`) but do not trigger anything — they are observational, not
decision-driving.

### 2.5 Position sizing / leverage

`POSITION_SIZE_USD = 1000.0` is a **fixed constant**, explicitly "notional, for reporting only
— no money moves" (`R3_shadow_run.py:59`). There is no sizing logic (no vol-scaling, no
Kelly, no risk-based sizing) and no leverage concept anywhere in the code — futures symbols are
used for their kline data only; no margin, leverage, or liquidation-distance calculation
exists.

### 2.6 Concurrent positions / portfolio logic

None. Every `(symbol, anchor)` pair is independent; the code has no concept of total open
exposure, correlated exposure, or a position limit. Nothing in this repo would stop 10/10
symbols from being simultaneously OPEN. There is no portfolio risk engine, no correlation
check, no capital-awareness of any kind — `Runner.open_shadow` (`R3_shadow_run.py:247-270`)
inserts a row unconditionally once a decision fires and no identical-key row already exists.

### 2.7 Cooldown

Only the within-anchor `dedup_earliest` described in §2.3. There is no cross-anchor cooldown
(e.g. "don't re-enter this symbol for N hours after a trade closes") — a symbol could in
principle fire again at the very next hourly anchor if the gate is crossed again.

### 2.8 Data refresh mechanism / websocket vs REST

**REST polling only, no websocket.** `R3_shadow_run.py`'s `Runner.run()` loop
(`R3_shadow_run.py:358-384`) polls every **20 seconds** (`time.sleep(20)`, line 384), calling
`Bars.poll()` which hits `GET /fapi/v1/klines` (limit 60) per symbol per cycle, plus
`GET /fapi/v1/ticker/bookTicker` once per trade open/close event (`book()`,
`R3_shadow_run.py:202-213`). These are the **only two Binance endpoints used anywhere in the
repository**, both public and unauthenticated (`FAPI = "https://fapi.binance.com"`). Health is
snapshotted every 300s (`HEALTH_EVERY_S`) and staleness is flagged past 240s
(`STALE_AFTER_S`).

### 2.9 Candle timeframe(s)

**One native timeframe: 1-minute closed bars** (`interval=1m` throughout). There is no
multi-timeframe candle concept anywhere in this repo — what look like "timeframes" in the
brief's sense (5m/15m/1H/…) do not exist here. Instead, D's features are computed as **rolling
windows over the 1-minute series**: micro-features at windows {30, 120, 480} minutes
(`d_features.py:31`), cross-sectional features at lags {1, 5, 15, 30, 60, 180, 360, 720, 1440}
minutes (`d_features.py:32`). This is a materially different structure from classic
OHLC-per-timeframe analysis and is worth being explicit about before building the "Multi-
Timeframe Engine" section of the brief — it has to be built from scratch by resampling this
1-minute base series; nothing to reuse exists.

### 2.10 Indicators / features used (the 156-wide D feature vector)

From `d_features.py:37-52`, per decision point, per symbol:

- **Micro-structure (66 = 3 windows × 11 families × 2 [value, cross-sectional rank])**,
  windows {30, 120, 480} min: `rskew`, `rkurt` (realised return skew/kurtosis), `roll`
  (Roll's effective spread proxy), `amihud` (illiquidity), `kyle` (Kyle's lambda proxy),
  `vpin` (order-flow toxicity proxy), `jump` (bipower-variation-based jump ratio), `vratio`
  (variance ratio, 5-min sub-bars), `cvd_slope` (cumulative signed volume flow's linear
  trend), `signac` (return autocorrelation / "signal-to-noise" proxy), `sflow` (signed
  volume / total volume).
- **Cross-sectional (90 = 9 lags × 10 families)**, lags {1,5,15,30,60,180,360,720,1440} min:
  own return, BTC return (broadcast), ETH return (broadcast), market (leave-one-out) return,
  breadth (leave-one-out), dispersion (leave-one-out std), relative strength (own − market),
  cross-sectional rank, BTC-minus-own, ETH-minus-own.
- Plus a bare **symbol index** feature appended for the model (`shadow_engine.py:86`,
  `R1_freeze_artifact.py:68`).

All feature computation is **causal by construction and asserted so**: `compute_block`
asserts `end.max() < C.shape[0]` and the `vratio`/`cvd` inner loops assert no read past minute
`t` (`d_features.py:104,136,146`). The verification gates (§3 below) exist specifically to
prove no leakage crept in between the historical build and the live path.

### 2.11 State management / where past decisions are kept

SQLite, one file: `shadow.db` (gitignored, not present in this checkout — it is created at
runtime by `R3_shadow_run.py`). Schema (`R3_shadow_run.py:93-120`):

- `decisions` — every evaluated (anchor, minute, symbol) point, fired or not, with full score
  breakdown. PK `(anchor_ms, minute, symbol)`.
- `shadow_trades` — one row per fired-and-deduped entry, full lifecycle (entry/exit
  price/type, gross/net/penalty bp, MFE/MAE, status OPEN/CLOSED). PK `trade_id`
  (`{symbol}_{anchor_ms}`).
- `path` — per-trade time series of mid-price excursion, for MFE/MAE reconstruction.
- `health` — heartbeat rows: data age, open-trade count, alerts.
- `restarts` — every process start, timestamped.

There is **no decision-memory-informed logic anywhere** — nothing in the repo reads
`shadow_trades` or `decisions` back into a future decision. It is write-only from the engine's
point of view; it exists for reporting (`R4_report.py`) and the dashboard (`R5_dashboard.py`),
not for adaptive behavior. This is the exact gap EXP-124 §21 ("geçmiş karar hafızası") asks to
be filled — currently there is none.

### 2.12 Dashboard

`scripts/R5_dashboard.py` — pure-stdlib (`http.server` + `sqlite3` + `json`, no Flask, no
pandas/numpy) read-only HTTP server. Opens `shadow.db` with `mode=ro` (cannot write, cannot
trade). Token-gated via `EXP107_TOKEN` env var (`?t=` query param, 403 otherwise). Serves:
`/` (Turkish-language HTML page: live/stale badge, decision-point/fired/trade counters, closed-
trade P&L stats explicitly labeled "yeterli değil" — not sufficient — below 5 observations,
recent trades table, recent decisions table, health history table), `/json` (same data as
JSON), `/health` (liveness probe). Auto-refreshes every 60s client-side. No auth beyond the
token; explicitly documented as the *only* privacy control since "the box has no firewall"
(`deploy/borsabot-exp107-dashboard.service` comments).

### 2.13 Logging

`log()` in `R3_shadow_run.py:69-73` — timestamped line to both stdout and `shadow.log`
(gitignored, not present in checkout). Used for: backfill progress, readiness, retry warnings,
poll/evaluate/update_open errors, SHADOW ENTRY / SHADOW EXIT lines, ALERT lines, health status.
Deploy units route stdout/stderr to journald (`StandardOutput=journal`).

### 2.14 Error handling

- HTTP fetch (`_get`, `R3_shadow_run.py:76-89`): up to 4 retries, 1.5s·(i+1) backoff, on
  `URLError`/`TimeoutError`/`JSONDecodeError`.
- Main loop (`Runner.run`, `R3_shadow_run.py:358-384`): `poll()`, `evaluate_point()`, and
  `update_open()` are each wrapped so one symbol/point/cycle failure doesn't kill the process;
  errors are logged, not raised.
- `book()` (`R3_shadow_run.py:202-213`): falls back from live bid/ask to last-close "mid",
  explicitly tagged `mid_fallback` so a degraded read is never silently reported as a real
  execution price.
- Health alerting: `STALE_DATA` if last bar age > 240s, `API_ERROR` if the last poll failed;
  written to the `health` table and logged, but **no automated remediation** — per
  `PRE_DECLARATION.md:114-116`, a crash gets a service restart only, never a code/config/
  strategy change, and that restart is logged to the `restarts` table.
- A hard runtime assertion (`R3_shadow_run.py:65-66`) checks `ccxt` and `binance.client` are
  not in `sys.modules` — a defense-in-depth guard against anything trade-capable being
  importable in this process.

### 2.15 API connections / exchange integration

Binance USDT-M **Futures** public REST API only:
- `GET /fapi/v1/klines` (1-minute bars, backfill + rolling poll)
- `GET /fapi/v1/ticker/bookTicker` (best bid/ask, at trade open/close only)

No API key, no secret, read anywhere (`Environment=BINANCE_API_KEY=` /
`BINANCE_API_SECRET=` are explicitly blanked in both systemd units). No order-placement
endpoint is called or importable from any file in this repository. No websocket client
exists in the repo.

### 2.16 Database / file persistence

- `shadow.db` (SQLite) — the only live-state store; gitignored, not checked in.
- `artifact/` — the frozen model (see Blocker #2: booster/isotonic files are gitignored and
  absent from this checkout; `ref_model.npy`, `ref_rv30.npy`, `artifact.json` are present).
- CSV/JSON report outputs (`REPORT.md`, `TRADES.csv`, `DAILY.csv`, `EXECUTION.csv`) — generated
  by `R4_report.py` from `shadow.db`; not checked in (would be produced by a run).
- The four files **that are** checked in at repo root (`verification.json/.csv`,
  `gate2.json`, `gate2_*_trades.csv` / `gate2_all_decisions.csv`) are **evidence of a past
  offline run** of `R0` and `R2` — snapshots, not live/regenerable in this checkout (their
  source scripts can't run here, per §0).

---

## 3. Verification gates (the discipline this repo is built around)

Three gates, all offline, all logged as **PASS** in the checked-in evidence files:

- **Gate 1** (`R0_verify.py`) — feature parity: the shared `compute_block` implementation,
  run over a long history (1a) and over only the trailing 2,600-bar live buffer a real engine
  would hold (1b), reproduces the frozen EXP-092 feature block. Checked-in result
  (`verification.json`): 0 NaN mismatches, worst relative difference 1.46e-05 (`rkurt_30`),
  median exactly 0.
- **Gate 2** (`R2_gate2_replay.py`) — signal parity: the live decision core, replayed bar-by-
  bar over EXP-105's fresh window, reproduces EXP-105's 38 selected trades exactly. Checked-in
  result (`gate2.json`): 38/38 intersection, 0 missing, 0 extra, 100% decision-minute
  agreement, max score difference 1.69e-08.
- **Gate 3** (`R0_verify.py`, tail) — threshold provenance: the pinned threshold in
  `artifact.json` must equal EXP-105's independently-computed validation-percentile threshold.
  Checked-in result: both equal `0.964579822530864` exactly.

These gates exist because of a documented prior failure (referenced repeatedly in comments):
EXP-096 built a separate forward-replay harness, it passed its own internal check, and EXP-097
found it fired **zero** live signals for an unrelated bug nobody had caught — hence "one
function, two callers" (§2.1) instead of two independent implementations.

**Implication for EXP-124**: any new engine this task adds on top of D must be held to the same
discipline — a parity/replay gate against a frozen reference — before its output is trusted.
This is a design precedent worth reusing, not just documentation of the past.

---

## 4. Relating the EXP-108→123 findings to what's actually here

The task instructions describe EXP-108→123 findings (D's tail-dependent, market-wide edge;
118/119/122's ~51–53% direction signal that mostly doesn't survive cost; 120's confidence-
filtered cells that failed to replicate in 121; 123's ~55–56% cells under HIGH prior-move /
HIGH volatility / HIGH cross-sectional agreement, still not cost-positive). None of the
underlying artifacts for those experiments exist in this repository — this map can only note
*where* each finding would plug into EXP-107's existing surface, not verify or reproduce any of
them:

| Finding | Where it would attach in EXP-107 |
|---|---|
| D's tail-dependent, market-wide LONG edge (116/117) | D **is** the core signal (§2.1) — this is a statement about D itself, not an addable layer. Relevant to how much weight any *confirmation* on top of D should be given, since D's own edge is already characterized as fragile/tail-driven. |
| 118/119/122 weak (~51–53%) direction signal, cost-eating | Would live in a new **Direction Confirmation Engine** (brief §12–13) — a *separate* probability estimate to compare against D's `raw`/`cal` scores, never a replacement for the gate in §2.1. |
| 120's confidence-filtered cells, unreplicated in 121 | Directly cautionary for **any** feature-weighting step (brief §14): the repo's own verification-gate culture (§3 above) argues any such filter needs an out-of-sample replication gate before being weighted above zero, exactly as 121 did to 120. |
| 123's MAG HIGH / VOL HIGH / cross-sectional agreement, ~55–56%, not cost-positive | Would live in three new engines the repo does not have at all today: **Previous-Move/Magnitude** (brief §9), **Volatility** (brief §8), **Cross-Sectional agreement** (brief §10) — none of these exist as engines; only the raw ingredients partially overlap (D's own cross-sectional *features*, §2.10, are a different, already-model-consumed thing, not a standalone agreement-ratio signal). |

None of this can be tested against real data from this repository alone — see Blocker #1.

---

## 5. Blockers (reported now, not worked around)

**Blocker #1 — EXP-108→123 artifacts are not in this repository.**
Section 27 of the brief ("does EXP-123's MAG_LONG=HIGH genuinely add incremental information on
top of EXP-107's own signal?") requires the underlying feature/label data and, ideally, the
prior experiments' code, to test properly. None of it is present or reachable from this
environment (confirmed by filesystem search, §0). The only information available about those
experiments is the prose summary embedded in the task instructions — which is a *claim*, not
data, and must not be treated as verified evidence per the task's own rules ("hiçbir bulguyu
kanıtlanmış edge olarak kabul etme").

**Blocker #2 — the D model itself cannot currently be loaded.**
`booster_{10,30,60,120,240,480}.pkl` and `isotonic_{10,30,60,120,240,480}.pkl` (12 files) are
required by `ShadowEngine.load()` and are gitignored / absent from this checkout. Without them,
neither the shadow runner nor any replay can execute, and no new engine that wraps
`ShadowEngine.evaluate()` can be run or tested end-to-end.

**Blocker #3 — the feature/data-loading dependency (`tradebot.features.loaders.scan_symbol`)
and the historical kline archive it reads from do not exist in this repository or elsewhere on
this machine.** `R0` and `R1`'s "slice" stage, and `R2`'s "bars" stage, cannot run. Only the
live path (`R3`, which reaches Binance directly via `urllib`) has an independent, working data
source — but it still needs Blocker #2 resolved to produce a decision.

**Blocker #4 — no historical price archive for backtest/replay (brief §26, 2020→2026) exists
in this environment.** `R3`'s live poller only ever holds a rolling `KEEP_BARS` (~3,480
minutes ≈ 2.4 days) window; there is no bulk historical kline store here to replay against.

None of these are fixed by writing more code inside this session — they are either files that
exist elsewhere and need to be supplied (Blockers #2, #3 partially), or genuinely do not exist
anywhere accessible (Blocker #1, #4, unless built up going forward from live data only).

---

## 6. What IS buildable right now, without any of the above

Independent of the blockers, this repository already has a working, well-tested pattern that
the EXP-124 layer can extend safely:

- A pure-stdlib, no-order-path, read-only Binance REST client (`R3_shadow_run.py`'s `Bars` /
  `book()`) that could be extended to also fetch the additional timeframes the brief asks for
  (by resampling the same klines feed, or additional `interval=` calls), still with zero
  order-placement capability.
- A SQLite persistence pattern (`R3_shadow_run.py`'s schema) that a Decision Memory layer
  (brief §21) can sit beside as new tables, without touching the existing ones.
- A verification-gate discipline (§3 above) to hold every new "engine" to the same standard
  D itself was held to, before any of its output is trusted or weighted.
- The dashboard's read-only, token-gated, separate-process pattern (`R5_dashboard.py`) as the
  template for an expanded EXP-124 dashboard (brief §29).

What is **not** buildable without external input: anything that depends on reproducing D's own
predictions (needs Blocker #2), anything that depends on the EXP-092/105 historical feature
archive (needs Blocker #3), anything claiming to validate the EXP-108→123 findings against real
data (needs Blocker #1), and any multi-year backtest (needs Blocker #4).
