# EXP-124 — 107_INTEGRATION_REPORT.md
## EXP-107 trained model artifact recovery

**RESEARCH + PAPER ONLY. LIVE remains disabled throughout. No model was fabricated. No retraining
was attempted. Nothing under `scripts/` or `artifact/` was modified.**

**Result: the artifacts were NOT recovered. Per the task's own instruction ("If the artifacts
cannot be recovered: STOP and report exactly..."), this document is the stop-and-report
deliverable — the OFFLINE/SHADOW integration test and deterministic replay test sections of the
task are not attempted, since both are explicitly conditional on successful recovery.**

---

## 1. What was searched, and how

Every search below was run from a fresh, full fetch of the repository's remote state — not
just the branch this session started on.

### 1.1 Working tree
`find / -iname "*.pkl"` (whole filesystem, all mounts) and a targeted listing of `artifact/`.

### 1.2 Git history — this branch and every other ref
Started from `git branch -a` (2 branches visible: `main`, `claude/laughing-wright-qu0fkk`), then
ran `git fetch origin '+refs/*:refs/remotes/origin-all/*' --tags`, which is a full ref fetch, not
limited to the branch this session began on. That surfaced **two additional branches never
mentioned or visible before this fetch**:

- `claude/admiring-ritchie-stmgpg` (3 commits past the common base — a dashboard rewrite)
- `claude/borsa-bot-live-project-dbr1t3` (1 commit past the common base — adds a GitHub Actions
  deploy workflow)

Both were inspected directly:
```
git log --oneline origin-all/heads/claude/admiring-ritchie-stmgpg
git log --oneline origin-all/heads/claude/borsa-bot-live-project-dbr1t3
git ls-tree -r --name-only <each tip>
```
Neither branch's file tree contains any `artifact/*.pkl` file, or anything else not already
present in this session's own branch (`.github/workflows/deploy.yml` on the second branch is new
but irrelevant to artifact recovery — see §1.5). No other branches, and no tags, exist on the
remote (`git tag -l` → empty, confirmed again after the full fetch).

### 1.3 Every object ever committed, across every ref
```
git rev-list --objects --all | grep -iE "pkl|booster|isotonic"
```
run AFTER the full multi-branch fetch above, so it covers every blob ever introduced on every
branch this repository has ever had, not just the currently checked-out history. **Zero
matches.** A `.pkl` file matching `booster_*` or `isotonic_*` has never existed in any commit on
any branch of this repository, at any point. This is consistent with `.gitignore`'s `*.pkl` line
being present starting from the very first commit (`23ac8ca`, "borsa bot first commit") — the
files were, by design, never meant to be checked into version control at all.

### 1.4 Reflog, loose objects
`git reflog --all` was inspected for any dangling commit reference (none found beyond the
ordinary commit history already covered above — no evidence of a reset/rebase that dropped a
commit containing the artifact). `git rev-list --objects --all` above already covers every
reachable loose and packed object; there is no separate "unreachable but still on disk" object
to check without direct access to the remote's raw object store, which this session does not
have.

### 1.5 Other repositories this account can reach
`mcp__Claude_Code_Remote__list_repos` (no query filter, limit 200) returned **33 repositories**
across `blackleonur` and two other accounts. None is a trading/quant-related monorepo — they are
unrelated web/mobile apps (esnaf*, Atomaks, Kibris-al-sat, travel-app, reminder apps, etc.).
**No repository containing `tradebot`, `results/exp092_microstructure_direction`, or any other
path the frozen scripts reference exists among them.** This confirms `CODEBASE_MAP.md`
Blocker #3 (the `tradebot` package and its data) is not recoverable through any repository this
session can reach either.

The one genuinely new piece of information from this search is `.github/workflows/deploy.yml`
(on `claude/borsa-bot-live-project-dbr1t3`, never merged to `main`): it `scp`s `scripts/` and
`artifact/` to a VPS (`secrets.VPS_HOST`) and restarts two systemd services there. This confirms
where a **live, complete** copy of the artifact most plausibly exists — the VPS referenced
elsewhere in this repo's own `deploy/VPS_KURULUM.md` (IP `31.57.77.4`) — but:
- This session holds no SSH credential, no `VPS_HOST`/`VPS_USER`/`VPS_SSH_KEY` secret, and no
  network path to that host.
- `PRE_DECLARATION.md` (this repository's own, pre-existing, frozen document) explicitly states
  *"I do not deploy, restart or touch it. The user runs it"* about this exact VPS — reaching it
  is out of scope for this session even if credentials were somehow available.
- `mcp__github__list_releases`, `mcp__github__actions_list` (list_workflow_runs), and
  `mcp__github__list_issues` were all checked and are empty — the deploy workflow has **never
  actually run** (0 workflow runs total), so there is no GitHub Actions log or artifact upload
  to recover the files from that way either.

### 1.6 GitHub code search (cross-repository)
Not run against arbitrary public repositories — `booster_*.pkl`/`isotonic_*.pkl` are generic
LightGBM/scikit-learn artifact names with no identifying content visible from a filename search,
and this repository's scope is explicitly the accounts and repos already enumerated in §1.5, not
the entire public GitHub corpus. Searching public GitHub for a stranger's model file would not
be a legitimate way to "recover" this specific artifact even if a filename match turned up.

---

## 2. Exactly what was found

| Item | Status |
|---|---|
| `artifact/artifact.json` | **Present**, unchanged since Phase 1 (hash-verified by the standing `Test13_NoExp107Modification` test) |
| `artifact/ref_model.npy` | **Present**, unchanged, and its SHA-256 **matches** the value `artifact.json` itself recorded at freeze time (`12e8cd12f166ca45...`, prefix `12e8cd12f166ca45` — see §3) |
| `artifact/ref_rv30.npy` | **Present**, unchanged, hash **matches** its recorded value (`81d6de913de9ebf7...`) |
| `artifact/booster_{10,30,60,120,240,480}.pkl` (6 files) | **Absent.** Never found anywhere searched. |
| `artifact/isotonic_{10,30,60,120,240,480}.pkl` (6 files) | **Absent.** Never found anywhere searched. |
| Any other branch/tag/commit containing these files | **None exist** (full-history object scan across every ref, see §1.3) |
| Any other accessible repository containing them | **None** (§1.5) |
| Local filesystem, any mount | **None** — only unrelated `numpy`/`joblib` test-fixture `.pkl` files under `/usr/local/lib/python3.11/dist-packages/` (§1.1) |
| A plausible live location | The VPS at the IP recorded in `deploy/VPS_KURULUM.md`, reachable only by the user, per this repo's own existing rules (§1.5) |

---

## 3. Hash verification of what IS present

`artifact/artifact.json`'s own `sha256` field records the **expected** first-16-hex-characters
of each artifact file's SHA-256, written by `scripts/R1_freeze_artifact.py` at the moment the
artifact was originally frozen. This is the authoritative check for anything supplied later:

```json
"ref_model.npy": "12e8cd12f166ca45",
"ref_rv30.npy":  "81d6de913de9ebf7",
"booster_10.pkl":    "cce859ba4636286b",   "isotonic_10.pkl":  "5c179e68e0050363",
"booster_30.pkl":    "c335c66af7f8dd6d",   "isotonic_30.pkl":  "dbc5597f114b5d62",
"booster_60.pkl":    "f259b6f81320f358",   "isotonic_60.pkl":  "23d8fd6d8402507c",
"booster_120.pkl":   "0b50d501bcf007be",   "isotonic_120.pkl": "59c24f3325c47de0",
"booster_240.pkl":   "b041b1608e9675b6",   "isotonic_240.pkl": "23b2e49221a08af4",
"booster_480.pkl":   "12859ba779b36c4c",   "isotonic_480.pkl": "04231cff06db4212"
```

Computing SHA-256 fresh on the two `.npy` files present in this checkout and comparing the
first 16 hex characters:

- `ref_model.npy` → `12e8cd12f166ca45bcf70daa4a97dc33...` → prefix `12e8cd12f166ca45` — **matches**.
- `ref_rv30.npy` → `81d6de913de9ebf7b1f02293510851...` → prefix `81d6de913de9ebf7` — **matches**.

**Positive finding**: the two reference-distribution files that ARE present are verified
authentic against the artifact's own recorded provenance — they were not silently swapped or
corrupted. This does not help load `ShadowEngine` (it still requires all twelve `.pkl` files),
but it does mean that if the six booster and six isotonic files are ever supplied by the user,
this same 16-character-prefix comparison against the table above is the exact, sufficient check
to confirm they are the genuine, matching files before anything downstream trusts them — not a
new verification scheme invented for this report, but the one the artifact's own author already
built in.

---

## 4. What is missing and why `ENTER` remains unavailable

Twelve files: `booster_{10,30,60,120,240,480}.pkl`, `isotonic_{10,30,60,120,240,480}.pkl`.
Without them, `scripts/shadow_engine.py::ShadowEngine.load()` — called completely unmodified by
`intelligence/core/exp107_signal.py::Exp107SignalProvider` — fails on its first
`joblib.load(path / f"booster_{t}.pkl")` call, and the provider correctly, honestly reports
`status="UNAVAILABLE"` rather than substituting anything. `Exp107Signal.is_long_fire` is
therefore structurally `False` for every symbol, every cycle, always — which is the single gate
`intelligence/decision/decision_engine.py::decide()` requires before it can ever return `ENTER`
(`ARCHITECTURE_PLAN.md` §0's hard invariant, verified by
`test_decision_engine.py::TestHardInvariantEnterRequiresExp107Fire` and, against this
repository's real state with no mocking, by `test_exp107_signal.py::TestRealRepoState`).

This is not a bug in the new `intelligence/` layer. It is the correct, honest consequence of the
twelve files genuinely not existing anywhere this session can reach.

---

## 5. Integration result

**Not attempted, per the task's own conditional instruction.** "After successful artifact
recovery, create an OFFLINE/SHADOW integration test" — recovery was not successful, so no such
test was built for this report.

What already exists from Phase 14/15 (not new for this report, but the closest available
evidence of the pipeline's shape) is `test_cycle_runner.py::TestRunCycleForSymbolAgainstRealRepoState`,
which runs the REAL, unmocked `Exp107SignalProvider` against this repository's real (currently
incomplete) `artifact/` through the full chain — evidence collection → confirmation →
opportunity manager → risk → position monitor → decision engine → decision memory — and proves
it lands on `IGNORE`/`WATCHING`, never `ENTER`, without crashing. That is a demonstration of the
pipeline's *shape* and its *safe degradation*, not a demonstration of it working with a real
EXP-107 signal — the distinction this section exists to keep clear.

## 6. Signal parity result

**Not attempted, for the same reason.** Signal parity (this layer's read of EXP-107's output
matching `scripts/R2_gate2_replay.py`'s own gate) requires an `ShadowEngine.evaluate()` that
actually runs, which requires the twelve missing files. There is nothing to compare.

## 7. Replay result

**Not attempted, for the same reason**, and doubly blocked: even with the model artifact
supplied, a deterministic replay would still need either (a) the historical kline archive
`scripts/R0_verify.py`/`R2_gate2_replay.py` read from (`CODEBASE_MAP.md` Blocker #3, the
`tradebot` package and its data — also confirmed absent from every reachable repository in this
search, §1.5), or (b) real history accumulated going forward by
`intelligence/core/kline_store.py`, which currently holds zero rows (no `cycle_runner` process
has ever been run against live data in this session — see `FINAL_REPORT.md` §17).

## 8. Sample decision traces

One real trace is available — from the UNAVAILABLE path, already exercised by the existing test
suite, reproduced here for concreteness (not fabricated for this report; this is
`CycleRunner.run_cycle_for_symbol("BTCUSDT", ...)`'s actual `journal_text` output against this
repository's real state):

```
DECISION: BTCUSDT

EXP107: NO SIGNAL

CONFIRMATION: NO_SIGNAL (score=0.00)

OPPORTUNITY STATE: WATCHING

SUPPORT:
- (none)

CONTRADICTION:
- (none)

RISK: UNKNOWN

PORTFOLIO: (no note)

DECISION: IGNORE

REASON: EXP-107 has not fired a LONG signal for this symbol this cycle

ALTERNATIVE_ACTION: continue WATCHING next cycle
```

No trace of `ENTER`, `HOLD`, `REDUCE`, or `EXIT` exists anywhere in this repository, because none
has ever been produced — every one of those actions requires `exp107.is_long_fire`, which has
never been `True` in this session.

---

## 9. Unresolved limitations

- The twelve model files may exist only on the VPS the user runs, or only on whatever machine
  originally ran `scripts/R1_freeze_artifact.py` — neither is reachable from this session.
- Even if supplied, loading them requires `lightgbm` and `scikit-learn` in addition to the
  already-installed `joblib` (`intelligence/requirements.txt` documents `joblib` as needed for
  the OK path; `lightgbm`/`scikit-learn` were not installed in this session since there was
  nothing to load them for).
- The historical kline archive needed for a real replay (`tradebot` package + data) is absent
  from every repository this session can reach, independent of the model-artifact question.

## 10. Explicit LIVE blockers (unchanged, restated for this report)

Every item in `EXP-124/LIVE_GATE.md` §1 still applies, in full, and the first one is now
confirmed — not merely assumed — unmet by an exhaustive search rather than by the file simply
not being in the working tree:

- [ ] EXP-107's trained model artifact supplied and verified — **confirmed not recoverable by
  this session; see §1–2 above.**
- [ ] A multi-week PAPER run has actually happened — blocked by the item above.
- [ ] Every other precondition in `LIVE_GATE.md` §1 — unreachable while the first is unmet.

**LIVE remains disabled. No model was fabricated or retrained. No file under `scripts/` or
`artifact/` was modified by this investigation** (re-verified by
`Test13_NoExp107Modification`, which includes a check that no `.pkl` file has been added to
`artifact/`).
