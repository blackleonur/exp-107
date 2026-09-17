# EXP-107 Shadow Runner — deployment

**RESEARCH ONLY. This service cannot place an order.** It imports no exchange client, no
signing code and no credential, and calls exactly two Binance **public, unauthenticated,
read-only** endpoints (`/fapi/v1/klines`, `/fapi/v1/ticker/bookTicker`). No API key, no
secret, no trading permission, no withdrawal permission — none are needed and none are read.

It touches **nothing** that already exists: its own directory, its own SQLite file, its own
systemd unit. It does not modify exp020, exp028, exp048a, exp048b, nginx, the firewall or any
other service.

---

## Option A — run it on this machine (simplest, nothing to deploy)

```
cd results/exp107_shadow/scripts
../../../.venv/Scripts/python.exe R3_shadow_run.py run
```

Leave it running. Check progress any time from another shell:

```
cd results/exp107_shadow/scripts
../../../.venv/Scripts/python.exe R3_shadow_run.py status
```

Build the report whenever you want one:

```
../../../.venv/Scripts/python.exe R4_report.py
```

Stop it with Ctrl+C. Nothing is lost — state lives in `results/exp107_shadow/shadow.db`.

---

## Option B — run it on the VPS

**I cannot do this step**: outbound SSH is blocked in my environment. Run these yourself.

### 1. Copy the package

From this repo root:

```
scp -r results/exp107_shadow root@<VPS>:/opt/projects/borsabot-exp107/
```

The artifact (`artifact/`, 2.2 MB) must go with it — the runner loads the frozen boosters,
calibrators, reference distributions and pinned threshold from there and refits nothing.

### 2. Python environment on the VPS

```
ssh root@<VPS>
cd /opt/projects/borsabot-exp107
python3 -m venv .venv
.venv/bin/pip install numpy scikit-learn lightgbm joblib
```

`pandas` and `polars` are deliberately **not** required by the runner, and installing them
does not matter — the runner never imports them (see the note in `R3_shadow_run.py`).
`R4_report.py` does use pandas; install it only if you want to generate reports on the VPS
rather than locally.

### 3. Install the service

```
cp deploy/borsabot-exp107-shadow.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now borsabot-exp107-shadow
systemctl status borsabot-exp107-shadow
```

### 4. Watch it

```
journalctl -u borsabot-exp107-shadow -f
tail -f /opt/projects/borsabot-exp107/shadow.log
.venv/bin/python scripts/R3_shadow_run.py status
```

### 5. Stop it

```
systemctl stop borsabot-exp107-shadow
systemctl disable borsabot-exp107-shadow
```

### Removing it completely

```
systemctl disable --now borsabot-exp107-shadow
rm /etc/systemd/system/borsabot-exp107-shadow.service
systemctl daemon-reload
rm -rf /opt/projects/borsabot-exp107
```

Nothing else on the box is affected.

---

## Ports

**This service opens no port.** It is outbound-only — it polls Binance and writes SQLite. There
is no dashboard and nothing to expose, so there is no port to firewall and no new attack
surface on a shared machine. If you later want a dashboard, that is a separate decision.

---

## What to expect

D fires roughly **2.4 signals/day** across the 10 symbols and holds each ~**23.4 hours**. So:

| Elapsed | Signals (approx) | Closed (approx) |
|---|---|---|
| 24h | ~2 | ~0 |
| 48h | ~5 | ~2 |
| 5 days | ~12 | ~9 |
| 7 days | ~17 | ~14 |

Long quiet stretches are normal and are not a fault. `status` showing `fired 0` after a few
hours is the expected behaviour of a 99th-percentile gate, not a broken system — the
`decisions` table fills continuously and is how you confirm it is alive and evaluating.

---

## Restart policy

The unit restarts on failure. **Per the pre-declaration, a crash permits a service restart
only — no code, config or strategy change.** Every process start is logged to the `restarts`
table.

---

## Continuous deploy via GitHub Actions

`.github/workflows/deploy.yml` copies `scripts/` and `artifact/` to the VPS and restarts both
units on every push to `main` that touches those paths (or on a manual
`workflow_dispatch` run). It authenticates with an SSH key, never a password, and I cannot set
this up end-to-end myself — outbound SSH from my environment is blocked and I cannot open your
GitHub repo settings. Do these one-time steps yourself:

### 1. Generate a dedicated deploy keypair (on your own machine, not the VPS)

```
ssh-keygen -t ed25519 -f deploy_key -C "github-actions-borsabot-exp107" -N ""
```

This gives you `deploy_key` (private) and `deploy_key.pub` (public).

### 2. Authorize the public key on the VPS

```
ssh root@31.57.77.4
mkdir -p ~/.ssh && chmod 700 ~/.ssh
echo "<contents of deploy_key.pub>" >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

Prefer not to hand a CI system root over SSH long-term? Create a scoped deploy user instead
(`adduser deployer`), give it `authorized_keys` the same way, and grant it passwordless sudo
for only the two restart commands via `visudo`:

```
deployer ALL=(root) NOPASSWD: /bin/systemctl restart borsabot-exp107-shadow, /bin/systemctl restart borsabot-exp107-dashboard, /bin/systemctl status borsabot-exp107-shadow, /bin/systemctl status borsabot-exp107-dashboard
```

then prefix the two `systemctl` lines in the workflow's `script:` with `sudo`, and also chown
`/opt/projects/borsabot-exp107` to `deployer` so the scp step can write there.

### 3. Add the three GitHub secrets

Repo → Settings → Secrets and variables → Actions → New repository secret:

| Secret | Value |
|---|---|
| `VPS_HOST` | `31.57.77.4` |
| `VPS_USER` | `root` (or `deployer` if you made a scoped user) |
| `VPS_SSH_KEY` | full contents of the **private** key `deploy_key` |

### 4. Rotate the root password

The password shared earlier in this chat is compromised (it went into a chat transcript in
plaintext) — run `passwd` on the VPS regardless of which deploy user you choose.

### 5. Test it

Push a change under `scripts/` or `artifact/` to `main`, or trigger the workflow manually from
the Actions tab. Watch the run logs; if it succeeds, `journalctl -u borsabot-exp107-shadow -f`
on the VPS should show the service restart.

Note: this workflow only pushes `scripts/` and `artifact/` (the runtime payload) — it does not
re-copy `deploy/*.service` files or re-run `systemctl daemon-reload`. If you change a unit
file, apply that one manually as in Step 5/6 of `VPS_KURULUM.md`.
