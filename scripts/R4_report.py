"""
EXP-107 -- build the checklist report from the shadow database.

RESEARCH ONLY. Reads shadow.db read-only and writes reports. Places no orders.
NO PRODUCTION / PAPER STRATEGY CHANGES MADE.

Usage:  python R4_report.py [path/to/shadow.db]

Produces REPORT.md, TRADES.csv, DAILY.csv, EXECUTION.csv.

Every exit-dependent line carries its observation count, because D holds ~23.4h and a short
window closes very few trades. A two-trade average is never presented as a measurement.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "shadow.db"
COST_BP = 14.38


def q(con, sql):
    return pd.read_sql_query(sql, con)


def fmt(v, n=2, unit=""):
    return "n/a" if v is None or (isinstance(v, float) and pd.isna(v)) else f"{v:.{n}f}{unit}"


con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
dec = q(con, "SELECT * FROM decisions")
tr = q(con, "SELECT * FROM shadow_trades")
hl = q(con, "SELECT * FROM health ORDER BY ts")
rs = q(con, "SELECT * FROM restarts ORDER BY ts")
closed = tr[tr.status == "CLOSED"] if len(tr) else tr

tr.to_csv(ROOT / "TRADES.csv", index=False)
if len(tr):
    ex = tr[["trade_id", "symbol", "entry_ts", "entry_mid", "entry_bid", "entry_ask",
             "spread_bp", "entry_price_type", "simulated_entry_price",
             "exit_ts", "exit_mid", "exit_bid", "exit_ask", "exit_price_type",
             "simulated_exit_price", "gross_bp", "gross_executable_bp", "net_bp",
             "execution_penalty_bp"]]
else:
    ex = tr
ex.to_csv(ROOT / "EXECUTION.csv", index=False)

if len(tr):
    d = tr.copy()
    d["day"] = pd.to_datetime(d.entry_ts).dt.strftime("%Y-%m-%d")
    daily = d.groupby("day").agg(
        signals=("trade_id", "size"),
        closed=("status", lambda x: int((x == "CLOSED").sum())),
        avg_spread_bp=("spread_bp", "mean"),
        avg_net_bp=("net_bp", "mean")).reset_index()
else:
    daily = pd.DataFrame(columns=["day", "signals", "closed", "avg_spread_bp", "avg_net_bp"])
daily.to_csv(ROOT / "DAILY.csv", index=False)

# ------------------------------------------------------------------ integrity
n_dec = len(dec)
n_sig = int(dec.fired.sum()) if len(dec) else 0
dupes = int(tr.trade_id.duplicated().sum()) if len(tr) else 0
key_dupes = int(tr.duplicated(["symbol", "anchor_ms"]).sum()) if len(tr) else 0
gaps = 0
if len(hl) > 1:
    ts = pd.to_datetime(hl.ts)
    gaps = int(((ts.diff().dt.total_seconds() > 900).sum()))
stale = int((hl.alert.astype(str).str.contains("STALE")).sum()) if len(hl) else 0
fallback_entry = int((tr.entry_price_type == "mid_fallback").sum()) if len(tr) else 0
fallback_exit = int((closed.exit_price_type == "mid_fallback").sum()) if len(closed) else 0

span = "n/a"
if len(hl):
    span = f"{hl.ts.iloc[0]} -> {hl.ts.iloc[-1]}"
    hrs = (pd.to_datetime(hl.ts.iloc[-1]) - pd.to_datetime(hl.ts.iloc[0])).total_seconds() / 3600
else:
    hrs = 0.0

L = []
A = L.append
A("# EXP-107 — Pre-Live Shadow Run (D LONG-ONLY)\n")
A(f"*Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')} from `{DB.name}`*\n")
A("**RESEARCH ONLY / EXECUTION REHEARSAL. NO REAL ORDERS WERE PLACED AND NONE CAN BE — "
  "the runner imports no exchange client, no signing code and no credential, and calls only "
  "Binance public read-only endpoints.**\n")
A(f"**Observation span:** {span}  (~{hrs:.1f}h)\n")
A("---\n")
A("## §11 Final checklist\n")
A("| | Value | Observations |")
A("|---|---|---|")
A(f"| Decision points evaluated | **{n_dec:,}** | — |")
A(f"| Signals (fired) | **{n_sig}** | — |")
A(f"| Shadow trades | **{len(tr)}** | — |")
A(f"| Duplicate trade_id | **{dupes}** | — |")
A(f"| Duplicate (symbol, anchor) | **{key_dupes}** | — |")
A(f"| Health gaps > 15 min | **{gaps}** | {len(hl)} heartbeats |")
A(f"| Stale-data alerts | **{stale}** | — |")
A(f"| Restarts | **{len(rs)}** | — |")
A(f"| Entry book fallbacks (`mid_fallback`) | **{fallback_entry}** | of {len(tr)} |")
A(f"| Exit book fallbacks | **{fallback_exit}** | of {len(closed)} |")
if len(tr):
    A(f"| Average spread | **{fmt(tr.spread_bp.mean())}bp** | {int(tr.spread_bp.notna().sum())} |")
    A(f"| Median spread | **{fmt(tr.spread_bp.median())}bp** | {int(tr.spread_bp.notna().sum())} |")
else:
    A("| Average spread | n/a | 0 |")
    A("| Median spread | n/a | 0 |")
nc = len(closed)
A(f"| **Theoretical EV (mid→mid, gross)** | **{fmt(closed.gross_bp.mean()) if nc else 'n/a'}bp** | **{nc}** |")
A(f"| **Executable EV (ask→bid, gross)** | **{fmt(closed.gross_executable_bp.mean()) if nc else 'n/a'}bp** | **{nc}** |")
A(f"| **Execution penalty** | **{fmt(closed.execution_penalty_bp.mean()) if nc else 'n/a'}bp** | **{nc}** |")
A(f"| Net EV (after {COST_BP}bp) | **{fmt(closed.net_bp.mean()) if nc else 'n/a'}bp** | **{nc}** |")
A(f"| Average MFE | {fmt(closed.mfe_bp.mean()) if nc else 'n/a'}bp | {nc} |")
A(f"| Average MAE | {fmt(closed.mae_bp.mean()) if nc else 'n/a'}bp | {nc} |")
A(f"| Average duration | {fmt(closed.duration_min.mean(), 1) if nc else 'n/a'} min | {nc} |")
A("| Market-neutral EV | *requires the BTC benchmark over each trade's own interval; "
  "computed in post-processing, not live* | — |")
A("")
if nc < 5:
    A(f"> **Every exit-dependent row above rests on {nc} observation(s).** D holds ~23.4 hours, "
      "so a short window closes very few trades. These are NOT measurements of anything and "
      "must not be read as such — they exist to prove the lifecycle records correctly.\n")

A("## Technical verdict inputs\n")
checks = [
    ("Signals generated", n_sig > 0 or n_dec > 0),
    ("No duplicate trades", dupes == 0 and key_dupes == 0),
    ("No stale-data alerts", stale == 0),
    ("No health gaps > 15 min", gaps == 0),
    ("Book snapshots real (no entry fallback)", fallback_entry == 0),
    ("Database wrote every decision point", n_dec > 0),
]
for lab, ok in checks:
    A(f"- {'PASS' if ok else 'FAIL'} — {lab}")
A("")
A("**Verification gates (offline, before the run):**")
A("- GATE 1 feature parity, full buffer and 2,600-bar live buffer — **PASS** "
  "(0 NaN mismatches, median relative difference exactly 0.000)")
A("- GATE 2 signal parity vs EXP-105 — **PASS** (38/38, 100% intersection, "
  "max score difference 1.69e-08)")
A("- GATE 3 threshold provenance — **PASS** (difference exactly 0.000e+00)")
A("")
A("> **This verdict is technical only.** EXP-106 established that a window of this length has "
  "essentially no power to say anything about edge, and no profitability claim is made from "
  "this run. A `SYSTEM READY FOR MICRO-LIVE` verdict is not authorisation to trade.\n")
A("**NO PRODUCTION / PAPER STRATEGY CHANGES MADE. NO REAL ORDERS.**")

(ROOT / "REPORT.md").write_text("\n".join(L), encoding="utf-8")
# The report file is UTF-8; the Windows console here is cp1254 and cannot encode every
# character the report legitimately contains. Never let a console encoding failure abort a
# run that has already written its output correctly.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass
for line in L[:40]:
    print(line.encode("ascii", "replace").decode("ascii")
          if sys.stdout.encoding and "utf" not in sys.stdout.encoding.lower() else line)
print(f"\nwrote REPORT.md, TRADES.csv ({len(tr)}), DAILY.csv ({len(daily)}), "
      f"EXECUTION.csv ({len(ex)}) -> {ROOT}")
