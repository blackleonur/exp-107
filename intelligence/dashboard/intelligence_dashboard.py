"""
EXP-124 -- read-only web dashboard for the intelligence layer.

RESEARCH / PAPER ONLY. Opens `intelligence.db` (intelligence/memory/decision_memory.py's
schema) with SQLite `mode=ro`, serves HTML/JSON, and can write NOTHING and trade NOTHING. It
imports no exchange client, no credential and no order path -- same pattern as
scripts/R5_dashboard.py, deliberately: separate file, separate process, separate database,
separate port (8441, not EXP-107's 8440), so the two dashboards can run side by side without
conflict. It never opens `shadow.db`.

Access control: if INTELLIGENCE_TOKEN is set, every request must carry `?t=<token>`. Set it --
the same "no firewall" caveat R5_dashboard.py documents applies here too.

Pure stdlib -- sqlite3 + http.server + json. No Flask, no numpy, no pandas.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sqlite3
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

DB = Path(__file__).resolve().parents[2] / "intelligence.db"
TOKEN = os.environ.get("INTELLIGENCE_TOKEN", "")


def con() -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def snapshot() -> dict:
    if not DB.exists():
        return {"error": "intelligence.db does not exist yet -- has the cycle runner started?"}
    with con() as c:
        # one row per symbol: its MOST RECENT decision -- the live "opportunity queue"
        latest = c.execute(
            "SELECT d.* FROM decisions d "
            "INNER JOIN (SELECT symbol, MAX(open_time) mx FROM decisions GROUP BY symbol) m "
            "ON d.symbol = m.symbol AND d.open_time = m.mx "
            "ORDER BY d.confirmation_score DESC").fetchall()
        recent = c.execute(
            "SELECT * FROM decisions ORDER BY open_time DESC, created_at DESC LIMIT 40"
        ).fetchall()
        counts_row = c.execute(
            "SELECT decision, COUNT(*) n FROM decisions GROUP BY decision").fetchall()
        contradiction_counts = c.execute(
            "SELECT symbol, conflicting_evidence FROM decisions "
            "ORDER BY open_time DESC LIMIT 200").fetchall()
        n_resolved = c.execute(
            "SELECT COUNT(*) FROM decisions WHERE outcome_resolved=1").fetchone()[0]
        n_total = c.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]

        contradiction_total = 0
        for r in contradiction_counts:
            try:
                contradiction_total += len(json.loads(r["conflicting_evidence"] or "[]"))
            except (TypeError, ValueError):
                pass

        return dict(
            db=str(DB), now=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            n_total=n_total, n_resolved=n_resolved,
            decision_counts={r["decision"]: r["n"] for r in counts_row},
            contradiction_total=contradiction_total,
            opportunities=[dict(r) for r in latest], recent=[dict(r) for r in recent])


CSS = """
:root{--bg:#0f1115;--card:#171a21;--line:#252a34;--tx:#e6e9ef;--dim:#8b93a7;
--ok:#3fb950;--warn:#d29922;--bad:#f85149;--acc:#58a6ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);
font:14px/1.5 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif;padding:16px}
h1{font-size:17px;margin:0 0 4px}h2{font-size:13px;text-transform:uppercase;
letter-spacing:.08em;color:var(--dim);margin:22px 0 8px;font-weight:600}
.sub{color:var(--dim);font-size:12px;margin-bottom:18px}
.grid{display:grid;gap:10px;grid-template-columns:repeat(auto-fit,minmax(130px,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:9px;padding:12px}
.k{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.05em}
.v{font-size:22px;font-weight:600;margin-top:3px}
.tw{overflow-x:auto;border:1px solid var(--line);border-radius:9px;background:var(--card)}
table{border-collapse:collapse;width:100%;font-size:12.5px}
th{text-align:left;color:var(--dim);font-weight:600;padding:8px 10px;
border-bottom:1px solid var(--line);white-space:nowrap;font-size:11px;text-transform:uppercase}
td{padding:7px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
tr:last-child td{border-bottom:0}
.pill{display:inline-block;padding:2px 8px;border-radius:99px;font-size:11px;font-weight:600}
.p-enter{background:rgba(63,185,80,.14);color:var(--ok)}
.p-wait{background:rgba(210,153,34,.14);color:var(--warn)}
.p-ignore{background:rgba(139,147,167,.14);color:var(--dim)}
.p-exit{background:rgba(248,81,73,.14);color:var(--bad)}
.note{background:rgba(88,166,255,.07);border:1px solid rgba(88,166,255,.25);
border-radius:9px;padding:11px 13px;font-size:12.5px;color:#b9c6da;margin:14px 0}
"""

_PILL = {"ENTER": "p-enter", "HOLD": "p-enter", "WAIT": "p-wait", "REDUCE": "p-wait",
        "EXIT": "p-exit", "IGNORE": "p-ignore"}


def esc(x) -> str:
    return html.escape("" if x is None else str(x))


def ts(ms) -> str:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime("%m-%d %H:%M")
    except Exception:  # noqa: BLE001
        return "-"


def page(d: dict) -> str:
    if "error" in d:
        return (f"<!doctype html><meta charset=utf-8><style>{CSS}</style>"
                f"<h1>EXP-124 Intelligence Layer</h1>"
                f"<div class=note>{esc(d['error'])}</div>")

    opp_rows = "".join(
        f"<tr><td>{esc(r['symbol'])}</td>"
        f"<td>{esc(r['exp107_status'])}{' fired' if r['exp107_fired'] else ''}</td>"
        f"<td>{esc(r['exp107_side'] or '-')}</td>"
        f"<td>{esc(r['confirmation_label'])}</td>"
        f"<td>{r['confirmation_score']:.2f}</td>"
        f"<td>{esc(r['risk_note'])}</td>"
        f"<td>{esc(r['opportunity_state'])}</td>"
        f"<td><span class='pill {_PILL.get(r['decision'], 'p-ignore')}'>{esc(r['decision'])}</span></td>"
        f"<td class=dim>{esc(r['reason'])[:60]}</td></tr>"
        for r in d["opportunities"]) or "<tr><td colspan=9 class=dim>no decisions yet</td></tr>"

    hist_rows = "".join(
        f"<tr><td>{ts(r['open_time'])}</td><td>{esc(r['symbol'])}</td>"
        f"<td>{esc(r['confirmation_label'])}</td>"
        f"<td><span class='pill {_PILL.get(r['decision'], 'p-ignore')}'>{esc(r['decision'])}</span></td>"
        f"<td class=dim>{esc(r['reason'])[:70]}</td></tr>"
        for r in d["recent"]) or "<tr><td colspan=5 class=dim>no history yet</td></tr>"

    counts = d["decision_counts"]
    count_cards = "".join(
        f"<div class=card><div class=k>{esc(k)}</div><div class=v>{v}</div></div>"
        for k, v in sorted(counts.items())) or "<div class=card><div class=k>no decisions</div></div>"

    return f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<meta http-equiv=refresh content=15><title>EXP-124 Intelligence Layer</title>
<style>{CSS}</style></head><body>
<h1>EXP-124 -- Adaptive Intelligence Layer (RESEARCH / PAPER ONLY)</h1>
<div class=sub>read-only · no orders possible from this process · {esc(d['now'])} UTC ·
refreshes every 15s</div>
<div class=note><b>What this is not:</b> this dashboard cannot place a trade, and nothing it
shows is a claim of proven edge. ENTER only ever appears once EXP-107's own model artifact is
supplied and actually fires -- see EXP-124/CODEBASE_MAP.md.</div>

<h2>Decision counts (all time)</h2>
<div class=grid>{count_cards}
<div class=card><div class=k>total decisions</div><div class=v>{d['n_total']}</div></div>
<div class=card><div class=k>outcomes resolved</div><div class=v>{d['n_resolved']}</div></div>
<div class=card><div class=k>contradiction count (last 200)</div><div class=v>{d['contradiction_total']}</div></div>
</div>

<h2>Opportunity queue (latest read per symbol, ranked by confirmation score)</h2>
<div class=tw><table><thead><tr><th>symbol</th><th>exp107</th><th>side</th>
<th>confirmation</th><th>score</th><th>risk</th><th>state</th><th>decision</th><th>reason</th>
</tr></thead><tbody>{opp_rows}</tbody></table></div>

<h2>Decision history (most recent 40)</h2>
<div class=tw><table><thead><tr><th>time</th><th>symbol</th><th>confirmation</th>
<th>decision</th><th>reason</th></tr></thead><tbody>{hist_rows}</tbody></table></div>
</body></html>"""


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _deny(self) -> None:
        b = b"forbidden"
        self.send_response(403)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        if TOKEN and parse_qs(u.query).get("t", [""])[0] != TOKEN:
            return self._deny()
        if u.path in ("/health", "/healthz"):
            body, ctype = b'{"ok":true}', "application/json"
        elif u.path == "/json":
            body = json.dumps(snapshot(), default=str).encode()
            ctype = "application/json"
        elif u.path in ("/", "/index.html"):
            body = page(snapshot()).encode("utf-8")
            ctype = "text/html; charset=utf-8"
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a) -> None:
        pass


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8441)
    p.add_argument("--host", default="0.0.0.0")
    a = p.parse_args()
    print(f"EXP-124 intelligence dashboard  http://{a.host}:{a.port}/   db={DB}")
    print("token: " + ("REQUIRED (?t=...)" if TOKEN else
                       "NOT SET -- anyone who finds the port can read this page"))
    ThreadingHTTPServer((a.host, a.port), H).serve_forever()
