# EXP-124 — LIVE_GATE.md

**This document is a blocking placeholder. It authorizes nothing. Reaching every checkbox below
still requires a separate, explicit decision by the user before any LIVE code is written — this
file's existence is not that decision.**

---

## 0. Current mode

**RESEARCH + PAPER only.** No LIVE execution code exists anywhere in `intelligence/`. No file
in this session has ever imported an order-placing function, held a trading-capable credential,
or called an authenticated Binance endpoint. This is verified mechanically, not just claimed:

- Every long-running process this layer defines (`intelligence/loop/cycle_runner.py`) repeats
  the same `assert "ccxt" not in sys.modules and "binance.client" not in sys.modules` check
  `scripts/R3_shadow_run.py` already carries for EXP-107 itself.
- `intelligence/tests/adversarial/test_adversarial_suite.py::Test13_NoExp107Modification`
  hashes every file EXP-107 owns and fails the test suite if any of them changes.
- `intelligence/core/binance_client.py` calls only public, unauthenticated REST endpoints
  (`klines`, `bookTicker`, `depth`, `premiumIndex`, `openInterest`) — the same trust tier
  `scripts/R3_shadow_run.py` already uses for the first two.

## 1. Absolute preconditions before LIVE is even discussed

All of the following must be true, not merely planned:

- [ ] **EXP-107's own trained model artifact is supplied and verified.** `artifact/booster_{10,30,60,120,240,480}.pkl` and `artifact/isotonic_{10,30,60,120,240,480}.pkl` exist, `scripts/R0_verify.py` and `scripts/R2_gate2_replay.py` both pass their gates against them, and `intelligence/core/exp107_signal.py`'s `Exp107SignalProvider.status` reports `"OK"` — not assumed, checked.
- [ ] **A multi-week PAPER run has actually happened**, using `intelligence/loop/cycle_runner.py` continuously, writing to `intelligence.db`, with `outcome_resolved=1` on enough decisions across enough symbols and market regimes for `intelligence/confirmation/feature_registry.py` to have promoted at least the features actually used in any live-bound decision path above `UNSET`/`LOW`.
- [ ] **A REPORT.md-equivalent for that paper run exists** and answers the same 18 questions `EXP-124/FINAL_REPORT.md` §35 lists, with THAT run's real numbers — not the numbers in this document, which reflect zero live paper-run hours (the artifact was never available during this session).
- [ ] **Every "not production-ready" item in `FINAL_REPORT.md` §16 has been individually resolved or explicitly accepted in writing by the user**, not silently dropped.
- [ ] **Transaction cost and slippage,** measured from that paper run's own executable-vs-theoretical entry/exit prices (the same `entry_ask`/`exit_bid` discipline `scripts/R3_shadow_run.py` already uses for EXP-107 itself), still leave a net-positive expectancy for whatever the intelligence layer would have done differently from EXP-107 alone. A result of "no measurable improvement" or "negative after cost" is an acceptable, expected, and sufficient reason to stop here — per the original brief's own rule, no result is picked to make the system look ready.
- [ ] **A named person (the user) has explicitly said, in writing, "authorize LIVE."** No verdict this system produces — PAPER or otherwise — is self-authorizing. This mirrors the existing `PRE_DECLARATION.md`'s own rule for EXP-107: *"A `SYSTEM READY FOR MICRO-LIVE` verdict is not authorisation to trade."*

## 2. What LIVE would still need even after every box above is checked

None of this exists yet, and none of it is scoped by this document — it is listed so a future
session doesn't discover it late:

- An actual order-placement module, written from scratch, reviewed specifically for OWASP-style
  and exchange-specific risk (idempotency keys, rate limits, partial fills, position
  reconciliation on reconnect) — nothing in `intelligence/` today can be repurposed into this
  by adding a few lines; it would be new, security-reviewed code.
- Real credential handling (secrets manager or equivalent), with the same "never logged, never
  printed, never committed" discipline `scripts/R3_shadow_run.py`'s systemd units already
  demonstrate for the (currently unused) `BINANCE_API_KEY`/`BINANCE_API_SECRET` fields.
- A real circuit breaker independent of the strategy logic (kill switch, max-daily-loss halt,
  max-position-count halt) that cannot be overridden by anything in `decision_engine.py`.
- Explicit position-sizing and leverage decisions — this repository currently models **no
  leverage anywhere** (`intelligence/risk/portfolio_state.py`, matching EXP-107's own design),
  and that would need to be a deliberate choice, not an accidental side effect of skipping this
  step.
- A live monitoring/alerting path independent of `intelligence/dashboard/intelligence_dashboard.py`
  (which is explicitly read-only and has no alerting capability).

## 3. What this document is not

It is not a timeline, not a recommendation to proceed, and not evidence that any of the above
is close to being satisfied. As of this session, the FIRST item in Section 1 — a loadable
EXP-107 model artifact — is not satisfied, which makes every subsequent item moot. This file
exists so that fact is written down in one place a future reader will actually check, per the
task's own instruction: *"Gerçek emir gönderme KESİNLİKLE YOK."*
