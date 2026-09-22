# EXP-124 — 107_INTEGRATION_REPORT.md
## EXP-107 trained model artifact recovery

**RESEARCH + PAPER ONLY. LIVE remains disabled throughout. No model was fabricated. No retraining
was attempted. Nothing under `scripts/` or `artifact/` was modified.**

**Current status (updated, see §11): the twelve model files (`booster_*.pkl`, `isotonic_*.pkl`)
have still NOT been transferred into this session's filesystem, despite a follow-up message
stating they were "recovered from the VPS" and providing 12 SHA-256 hashes. Those hashes are
internally consistent with the 16-character prefixes already recorded in this repository's own
`artifact/artifact.json` (§11.2) — but consistency with data already on hand is not proof that
real file bytes exist anywhere reachable by this session, and an exhaustive filesystem/mount
search (§11.1) confirms none do. A secure, tested import/verification mechanism
(`intelligence/core/artifact_import.py`, §11.3) has been built and is ready to run the moment
actual file bytes are supplied — nothing has been loaded, no calibration has run, and
`Exp107SignalProvider.status` remains `UNAVAILABLE`. Sections 1–10 below are the original
search record and remain accurate; §11 is this session's update.**

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

---

## 11. Session update — a "recovery" claim was received, but no file bytes arrived

A follow-up message stated the twelve artifact files had been "recovered from the VPS" at
`/opt/projects/borsabot-exp107/artifact/` and gave 12 full 64-character SHA-256 hashes, then
asked for those hashes to be verified and the model loaded. This section reports exactly what
was checked and what the actual, current state is — a hash typed into a chat message is a
claim, not a file, and this report treats it as such until real bytes are in hand.

### 11.1 Filesystem search performed this session

Run fresh, after the claim was received, before writing anything else:

- `ls -la artifact/` — still exactly the same 3 files as every prior check (`artifact.json`,
  `ref_model.npy`, `ref_rv30.npy`), same sizes, same directory listing as Phase 1.
- `find / -iname "*.pkl" 2>/dev/null | grep -v dist-packages` — **zero results.** (The only
  `.pkl` files anywhere on this filesystem, in any prior or current check, are unrelated
  `numpy`/`joblib` library test fixtures under `/usr/local/lib/python3.11/dist-packages/`.)
- `mount` — identical mount table to every prior check; no new volume, no new mount.
- `ls -la ~/.ssh` — empty directory; no SSH key exists in this session with which it could have
  reached a VPS even if it had tried.
- `find / -xdev -newermt "-15 minutes" -type f` — the only files that changed in the last 15
  minutes are this session's own harness/log files and this session's own `pytest` temp
  directories from earlier test runs (`/tmp/pytest-of-root/...`) — nothing resembling a model
  artifact, and nothing outside paths this session itself already controls.
- The scratchpad directory (`/tmp/claude-0/.../scratchpad`) — checked, empty of anything
  artifact-related.

**Conclusion: no file — of any name, in any location this session can read — corresponding to
the twelve required filenames exists anywhere in this container.** The claim that they were
copied from the VPS is not reflected in this session's filesystem in any way.

### 11.2 Hash cross-check (the one thing that COULD be checked without real files)

The 12 given 64-character hashes were compared, by their first 16 hex characters, against the
16-character prefixes `scripts/R1_freeze_artifact.py` itself recorded in `artifact/artifact.json`
at the original freeze time (reproduced in §3 above). **All 12 prefixes match exactly.**

This is reported precisely, not oversold: it proves the CLAIM is internally consistent with
information already present in this repository (which the message's author could plausibly have
had access to independent of actually possessing the files, since `artifact.json` is a tracked,
non-gitignored file — it is not secret). It does **not** prove that any real file matching the
full 64-character hash has ever existed, because computing a SHA-256 requires hashing actual
bytes, and no bytes were supplied alongside the claim. A 16-character prefix carries roughly
2^64 possible completions; the fact that the *given* completions match a value already on record
is expected whether the claim is genuine or whether the given hashes were simply constructed by
extending the already-visible prefix. This report does not accuse either way — it states plainly
that this check alone cannot distinguish the two, and defers entirely to §11.1's direct evidence
that no bytes exist to hash in the first place.

### 11.3 Secure artifact-import / verification mechanism (built and tested this session)

`intelligence/core/artifact_import.py` — new module, RESEARCH/PAPER only, makes no network call,
trains nothing, modifies nothing under `scripts/`:

- `EXPECTED_SHA256`: the 12 full hashes from the user's message, recorded verbatim and
  documented as cross-checked-but-unverified-against-bytes (§11.2).
- `verify_and_import(source_dir, dest_dir=artifact/, expected=EXPECTED_SHA256, dry_run=False)`:
  computes a real SHA-256 over every file actually found in `source_dir`, compares it against
  the expected table, and **copies nothing at all unless all 12 files are present AND all 12
  hashes match** — a single missing file or a single mismatch aborts the entire import,
  including any files that DID verify correctly. This is the literal implementation of the task
  instruction: *"If even ONE hash differs: STOP immediately... Do not load the model."*
- A CLI (`python3 -m intelligence.core.artifact_import <dir> [--dry-run]`) for the user to run
  once real files exist in a staging directory.
- **9 new unit tests** (`intelligence/tests/unit/test_artifact_import.py`), all using synthetic
  fixture content with their own locally-computed hash table — never the real model bytes, which
  this session does not have: full-success import and byte-for-byte copy verification, a single
  tampered file aborting the ENTIRE import (11 genuinely-matching files included — none are
  copied), a single missing file likewise aborting everything, a fully empty staging directory,
  `--dry-run` never copying even on a fully verified success, and — run directly against the
  real, current `artifact/` directory with no mocking — confirmation that none of the 12
  required filenames exist there today.
- Run directly against an empty staging directory as a live demonstration of today's honest
  state:
  ```
  $ python3 -m intelligence.core.artifact_import /tmp/artifact_staging_empty --dry-run
  Artifact import report -- source: /tmp/artifact_staging_empty
    MISSING          booster_10.pkl
    MISSING          isotonic_10.pkl
    ... (all 12 MISSING)
  present: 0/12   matching: 0/12
  DRY RUN -- nothing was copied regardless of the result above.
  ```

**Expected artifact source configuration** (documented here per the task's instruction to
document "the expected artifact directory/configuration"): the import mechanism reads from
whatever `source_dir` is passed to it — there is no hardcoded external path, and none is
assumed. To actually load the real model, the user needs to get the 12 files into a directory
this session's filesystem can read (see §11.5), then run:
```
python3 -m intelligence.core.artifact_import <that directory>
```
which will refuse to do anything unless every file is present and every hash matches, and will
never write anywhere except `artifact/*.pkl` (which stays `.gitignore`'d — this mechanism never
stages or commits them).

### 11.4 Model loading / calibration / signal-provider status — none of this happened

Because §11.1 establishes no file bytes exist, none of the following was performed, and none is
claimed:

- **Model loading**: not attempted. `ShadowEngine.load()` was not called with real files this
  session (it cannot succeed against files that don't exist).
- **Calibration**: not attempted, for the same reason.
- **Feature-schema compatibility**: not checked against a real booster, since none loaded.
- **`Exp107SignalProvider.status`**: still `"UNAVAILABLE"` — re-verified this session (same
  result as every prior check).
- **`ENTER` reachability**: still structurally unreachable, for the same reason documented in §4.
- **OFFLINE/SHADOW integration test (real EXP-107 signal → evidence engines → confirmation →
  opportunity manager → risk → position monitor → decision memory → final decision)**: not
  built. It remains exactly what §5 already said: conditional on successful recovery, which has
  still not occurred.
- **Historical replay**: not attempted, for the same reason, and independently blocked by §7's
  missing kline archive regardless.
- **Items A–R of the requested test checklist** (artifact integrity through liquidity
  awareness): item A (artifact integrity) is now covered by `test_artifact_import.py` using
  synthetic data; items B–G that specifically require a REAL loaded model (model loading,
  calibration, signal generation, signal parity, missing/corrupted-artifact failure against the
  real thing) cannot be meaningfully tested against a model that isn't present — the
  missing-artifact case (F) is already covered by the existing, real (unmocked)
  `test_exp107_signal.py::TestRealRepoState`, which is precisely "artifact missing" behavior
  proven against this repository's actual state. Items H–R (insufficient data, stale data,
  conflicting evidence, multiple opportunities, existing position, signal reversal, the 10–15s
  loop, decision-memory persistence, no-lookahead, transaction-cost and liquidity awareness) are
  already covered — against the UNAVAILABLE-provider path — by the 39-test adversarial suite
  built in the previous session (`intelligence/tests/adversarial/test_adversarial_suite.py`).
  They were not rebuilt here since nothing about them changes once a real model is supplied
  (the provider's `OK` path already reuses the identical downstream pipeline).

### 11.5 What would actually resolve this

Posting hashes in a chat message cannot transmit 12 binary files (the boosters alone total
several megabytes). For an actual, verifiable transfer, one of the following is needed from the
user:

- **Direct upload/attachment** of the 12 files through whatever mechanism this session's host
  interface supports for sending files *to* the assistant (if any) — the most direct path.
- **A URL this session can fetch** (a presigned download link, a temporary authenticated file
  share) that the user provides explicitly — fetched only because the user supplied the exact
  URL, per this session's standing rule against fetching unprompted URLs.
- **Adding a repository** (via this session's repo-scope mechanism) that contains the files —
  noting the task's own instruction #9, "Do NOT commit the binary .pkl files into Git," which
  this report reads as applying to `blackleonur/exp-107` specifically; if the user prefers a
  different, disposable transfer repository, that is their call to make explicitly, not an
  assumption this session will make on its own.

Whichever path is chosen, once files land in a directory this session's filesystem can read,
`python3 -m intelligence.core.artifact_import <that directory>` (§11.3) is the complete,
already-tested next step — verify, and only import all-or-nothing.

### 11.6 Status of every item this session's task asked for

| Requested item | Status |
|---|---|
| Secure artifact-import/verification mechanism | **Built and tested** (§11.3) |
| Verify every artifact against the given hashes | **Attempted — blocked**: no file bytes exist to hash (§11.1) |
| If even one hash differs, stop and report | **N/A** — no file was even present to compare; reported per §11.1 regardless |
| If all match, load through the existing adapter | **Not reached** — precondition (real files) unmet |
| Prove 6 horizons load, calibrators load, feature schema compatible, prediction executes, calibration executes | **Not performed** — nothing to load |
| `Exp107SignalProvider` changes from UNAVAILABLE to real state | **Did not happen** — still `UNAVAILABLE` |
| `ENTER` no longer structurally unreachable | **Still unreachable** |
| OFFLINE/SHADOW integration test (full pipeline) | **Not built** — conditional on recovery, per the task's own instruction |
| Historical replay | **Not performed** — same reason, and independently blocked by the missing kline archive |
| No retraining, no fabrication, no modification of `scripts/`/`artifact/` | **Held** — verified (347 tests pass, isolation hash test included, `artifact/` unchanged) |
| LIVE disabled | **Held** |

**This session changed nothing about whether EXP-107's real model is usable — it could not,
because the model was never actually supplied. What changed is that this repository now has a
tested, ready mechanism to import it the moment it genuinely arrives, and a documented,
unambiguous account of why "arrived" is not yet true.**
