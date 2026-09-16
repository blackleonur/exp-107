# EXP-107 — PRE-DECLARATION
## Pre-Live Shadow Run — D LONG-ONLY

**Written and frozen BEFORE any shadow signal is generated.**

**Status: RESEARCH ONLY / EXECUTION REHEARSAL. This is not a profitability test.**

---

## 0. Absolute rules

**NO REAL ORDERS. NO REAL MONEY. NO TRADING PERMISSION. NO WITHDRAWAL PERMISSION.**
Any Binance API use is **READ-ONLY** market data. `src/tradebot` and the four existing paper
engines (exp020, exp028, exp048a, exp048b) are **FROZEN** — EXP-107 is a **new, separate**
module that imports from them read-only and modifies nothing. EXP-098 → EXP-106 results are
read-only. **D's model, its RV30 input, its LONG gate and its threshold are frozen. No filter
is added. No parameter is changed after seeing any result.**

---

## 1. Blockers identified before starting, reported not worked around

### 1.1 There is no live feature path for D

`src/tradebot/paper/live_features.py` computes **47** features for EXP-020's system.
**D requires EXP-092's 156 features** — 66 microstructure + 90 cross-sectional — including
Roll spread, Amihud, Kyle's lambda, VPIN, bipower variation, variance ratio, CVD slope, and
**leave-one-out cross-sectional aggregates that require all 10 symbols aligned at the same
minute, at 9 lags**. This has to be implemented.

> **Binding gate (§4): the live implementation must reproduce the frozen historical block
> bar-for-bar before any signal it emits is treated as a D signal.** EXP-096 built a forward
> harness that passed its verification lock and EXP-097 still found it fired **zero** signals
> for a reason nobody had checked. Verification is therefore a gate, not a formality.

### 1.2 The 48-hour window cannot validate the exit lifecycle

| | |
|---|---|
| D's signal rate (EXP-105) | 2.38/day → **~4.8 signals in 48h** |
| D's hold time | **1,403 min ≈ 23.4h** (exit at `anchor + 1440`) |
| **Shadow trades that would CLOSE inside 48h** | **~2.4** |

**No trade opened in the final 24 hours can close inside the window.** The §11 checklist items
that depend on exits — MFE, MAE, duration, exit price, gross/net PnL, market-neutral PnL —
would rest on roughly **two** observations.

**Declared consequence:** the 48-hour run can validate **signal generation, feature
correctness, execution capture and shadow-entry lifecycle**. It **cannot** validate exit
mechanics. Exit validation needs **≥ 5 days**. This is stated now so a 2-observation exit table
is not later presented as a passed check.

### 1.3 Deployment is outside what I can do

`BORSABOT_VPS_SSH_PASSWORD` is unset and `deploy/scripts/deploy_exp048.py` prompts
interactively. The VPS is a **shared** box running unrelated third-party production sites with
root SSH and no firewall. **I do not deploy, restart or touch it.** The user runs it.

---

## 2. The frozen strategy

**D = MODEL + RV30, LONG ONLY**, exactly as EXP-101 → EXP-106 define it:

- `comb = 0.5·rank_model + 0.5·rank_rv30`, ranks against the **frozen VALIDATION** distribution
- Gate: **99.0 percentile** of `comb` on that validation slice — **pinned to a fixed numeric
  threshold in the artifact**, never recomputed live
- Side: `sign(raw − 0.5)`; **only LONG is taken**
- Deduplication: earliest qualifying decision minute per `(symbol, anchor)`
- Decision minutes {10, 30, 60, 120, 240, 480}; exit at `anchor + 1440`
- **RV30**: realised volatility of 1-minute returns over the 30 minutes **ending at** the
  decision minute

The model artifact and the threshold are frozen **once**, before the run, and hashed.

---

## 3. Data and recording

Real-time klines. For every evaluated decision point: timestamp · symbol · model score ·
RV30 · `comb` · threshold · fired (yes/no). For every fired signal, a **SHADOW_ENTRY** with:
signal_timestamp · entry_timestamp · symbol · direction · model_score · RV30 · confidence ·
entry_mid · entry_bid · entry_ask · spread_bp · simulated_entry_price · simulated_cost ·
simulated_position_size · signal source.

**Execution realism (§5):** LONG entry priced at **ask**, exit at **bid**, when a real book
snapshot exists. When it does not, the row is marked **`mid_fallback`** and **a fallback is
never reported as real execution**. Both are reported side by side:
`THEORETICAL MID ENTRY` vs `EXECUTABLE ASK ENTRY`, with the difference as
**execution penalty**.

**Market neutralization:** EXP-103's frozen beta, **read from `exp103/betas.csv`**. No beta is
refitted. Raw and neutral are kept separate.

---

## 4. Verification gate — must pass before the run counts

1. **Feature parity:** the live path, replayed over a historical window, reproduces the frozen
   EXP-092 block. NaN pattern identical; per-feature relative difference at float noise.
2. **Signal parity:** replaying the live path over EXP-105's fresh window reproduces
   **the same 38 trades** EXP-105 selected.
3. **Threshold provenance:** the pinned threshold equals the frozen validation percentile.
4. **No order path:** a static check that the shadow module imports no order-placing function
   and holds no trading-permitted credential.

**If gate 1 or 2 fails, the shadow run does not start.**

---

## 5. Health monitoring (§8)

Every 5 minutes: process alive · data freshness · last signal · last trade · database write ·
API/websocket health. Failure raises an **ALERT**. **No automatic strategy or config change is
ever made.** On crash, **service restart only** — no code, config or strategy change — and the
restart event is logged (§9).

---

## 6. Deliverables

`results/exp107_shadow/` — `REPORT.md`, `TRADES.csv`, `DAILY.csv`, `EXECUTION.csv`, plus the
verification evidence and the health log.

The §11 checklist is reported in full, with **every exit-dependent line explicitly marked by
its observation count** so a 2-trade average is never read as a measurement.

---

## 7. Verdict — technical only

**SYSTEM READY FOR MICRO-LIVE** or **SYSTEM NOT READY**.

> **This verdict is about whether the system is technically fit to be connected to real money —
> data integrity, signal correctness, execution capture, lifecycle, persistence. It says
> NOTHING about profitability.** EXP-106 established that a 48-hour window has essentially zero
> power to say anything about edge, and no such claim may be made from this run.

**A `SYSTEM READY FOR MICRO-LIVE` verdict is not authorisation to trade. No real-money trading
is started by this experiment under any outcome.**

---

**NO PRODUCTION / PAPER STRATEGY CHANGES MADE. NO REAL ORDERS.**
