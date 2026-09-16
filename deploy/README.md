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
