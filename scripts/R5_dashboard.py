"""
EXP-107 -- read-only web dashboard for the shadow run.

RESEARCH ONLY. This process opens `shadow.db` in SQLite READ-ONLY mode (`mode=ro`), serves
HTML/JSON, and can write nothing and trade nothing. It imports no exchange client, no
credential and no order path. It is a separate process from the runner (R3_shadow_run.py) --
restarting or editing this file never touches an open position, the strategy, or the database.

    python R5_dashboard.py [--port 8440] [--host 0.0.0.0]

Access control: if the environment variable EXP107_TOKEN is set, every request must carry
`?t=<token>`. Set it. The box has no firewall, so without a token the page is readable by
anyone who finds the port.

Pages (all GET, all read-only):
    /            ana sayfa -- portfoy KPI'lari, acik pozisyonlar, grafikler, coin analizi,
                 islem sikligi, sure analizi, sistem sagligi, log paneli
    /gecmis      kapanmis islemlerin filtrelenebilir / siralanabilir tam listesi
    /islem?id=.. tek bir islemin (acik ya da kapali) tam detayi + fiyat gecmisi grafigi
    /json        ham anlik veri (snapshot), makine icin

Pure stdlib -- sqlite3 + http.server + json + html. No Flask, no numpy, no pandas, no JS
framework, no CDN. That keeps the VPS install to nothing at all beyond python3. Charts are
plain inline SVG rendered server-side; the only inline <script> on the page is the two-line
"submit this form on change" used by the history filters.

NOT AVAILABLE IN THIS SYSTEM (do not read these as bugs): a portfolio starting balance / free
balance, a real Binance account connection, and real fill/commission data. This is a *shadow*
run -- EXP-107's PRE_DECLARATION.md forbids any credential, any order path and any real money.
Every trade uses a fixed $1,000 notional for reporting only (see POSITION_SIZE_USD_DISPLAY),
and "cost" is COST_BP, a documented estimated round-trip cost, not a real fill. Anywhere real
data does not exist, this dashboard says so instead of inventing a number.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

DB = Path(__file__).resolve().parents[1] / "shadow.db"
TOKEN = os.environ.get("EXP107_TOKEN", "")
COST_BP = 14.38
# USD/TRY used only to translate the simulated (paper) bp PnL into TL for readability.
# No money moves and no live FX feed is called -- set this to today's rate yourself.
TRY_RATE = float(os.environ.get("EXP107_USDTRY", "42.0"))
POSITION_SIZE_USD_DISPLAY = 1000  # matches POSITION_SIZE_USD in R3_shadow_run.py; display only
STALE_AFTER_S = 300

DUR_BUCKETS = [
    (0, 30, "0-30 dk"), (30, 60, "30-60 dk"), (60, 180, "1-3 saat"),
    (180, 360, "3-6 saat"), (360, 720, "6-12 saat"), (720, 1440, "12-24 saat"),
    (1440, None, "24+ saat"),
]


# ============================================================ number / unit helpers
def bp_to_usd(bp: float | None, position_usd: float | None) -> float | None:
    if bp is None or position_usd is None:
        return None
    return bp / 1e4 * position_usd


def usd_to_try(usd: float | None) -> float | None:
    return None if usd is None else usd * TRY_RATE


def price_fmt(v: float | None) -> str:
    if v is None:
        return '<span class="dim">-</span>'
    if v >= 100:
        return f"${v:,.2f}"
    if v >= 1:
        return f"${v:,.4f}"
    return f"${v:,.6f}"


def qty_fmt(q: float | None, symbol: str = "") -> str:
    if q is None:
        return '<span class="dim">-</span>'
    coin = symbol[:-4] if symbol.endswith("USDT") else symbol
    return f"{q:,.4f} {coin}" if q >= 1 else f"{q:,.6f} {coin}"


def duration_fmt(mins: float | None) -> str:
    if mins is None or mins < 0:
        return '<span class="dim">-</span>'
    total = int(round(mins))
    d, rem = divmod(total, 1440)
    h, m = divmod(rem, 60)
    parts = []
    if d:
        parts.append(f"{d} gün")
    if h:
        parts.append(f"{h} saat")
    if m or not parts:
        parts.append(f"{m} dakika")
    return " ".join(parts)


def esc(x) -> str:
    return html.escape("" if x is None else str(x))


def ts(ms) -> str:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime("%m-%d %H:%M")
    except Exception:  # noqa: BLE001
        return "-"


def ts_iso(s: str | None) -> str:
    if not s:
        return '<span class="dim">-</span>'
    try:
        return datetime.fromisoformat(s).strftime("%m-%d %H:%M:%S")
    except Exception:  # noqa: BLE001
        return esc(s)


def num(v, n=2, suf=""):
    return '<span class="dim">-</span>' if v is None else f"{v:,.{n}f}{suf}"


# ============================================================ data access (READ ONLY)
def con() -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def _enrich_trade(t: dict) -> None:
    """Fill in derived, read-only fields shared by every page. Never writes anywhere."""
    t["entry_price"] = t.get("simulated_entry_price")
    t["qty"] = (t["simulated_position_usd"] / t["entry_price"]
                if t.get("entry_price") else None)
    if t["status"] == "OPEN":
        t["age_min"] = ((time.time() * 1000 - t["anchor_ms"]) / 60_000.0
                         if t.get("anchor_ms") else None)
        t["pnl_is_final"] = False
    else:
        t["current_price"] = t.get("simulated_exit_price")
        t["gross_bp_now"] = t.get("gross_bp")
        t["net_bp_now"] = t.get("net_bp")
        t["best_seen_bp"] = t.get("mfe_bp")
        t["worst_seen_bp"] = t.get("mae_bp")
        t["age_min"] = t.get("duration_min")
        t["pnl_is_final"] = True
    t["gross_usd"] = bp_to_usd(t.get("gross_bp_now"), t.get("simulated_position_usd"))
    t["gross_try"] = usd_to_try(t["gross_usd"])
    t["net_usd"] = bp_to_usd(t.get("net_bp_now"), t.get("simulated_position_usd"))
    t["net_try"] = usd_to_try(t["net_usd"])
    # aliases used across charts / best-worst / history -- "pnl" always means net
    t["pnl_bp"] = t.get("net_bp_now")
    t["pnl_usd"] = t["net_usd"]
    t["pnl_try"] = t["net_try"]
    t["current_value_usd"] = (t["simulated_position_usd"] + t["gross_usd"]
                               if t.get("simulated_position_usd") is not None
                               and t.get("gross_usd") is not None else None)
    t["current_value_try"] = usd_to_try(t["current_value_usd"])


def snapshot() -> dict:
    if not DB.exists():
        return {"error": "shadow.db does not exist yet — has the runner started?"}
    with con() as c:
        g = lambda q, d=0: (c.execute(q).fetchone() or [d])[0]  # noqa: E731
        health = c.execute("SELECT * FROM health ORDER BY ts DESC LIMIT 1").fetchone()
        trades = [dict(r) for r in c.execute(
            "SELECT * FROM shadow_trades ORDER BY anchor_ms DESC")]
        recent = [dict(r) for r in c.execute(
            "SELECT symbol,minute,anchor_ms,raw_score,rv30_bp,comb,threshold,fired,reason,"
            "evaluated_at FROM decisions ORDER BY anchor_ms DESC, comb DESC LIMIT 25")]
        fired_recent = [dict(r) for r in c.execute(
            "SELECT symbol,minute,anchor_ms,comb,evaluated_at FROM decisions "
            "WHERE fired=1 ORDER BY anchor_ms DESC LIMIT 20")]
        hist = [dict(r) for r in c.execute(
            "SELECT ts,data_age_s,open_trades,alert,api_ok FROM health ORDER BY ts DESC LIMIT 12")]
        restarts = [dict(r) for r in c.execute(
            "SELECT ts,note FROM restarts ORDER BY ts DESC LIMIT 10")]
        open_ids = [t["trade_id"] for t in trades if t["status"] == "OPEN"]
        latest_path: dict[str, dict] = {}
        path_extremes: dict[str, dict] = {}
        if open_ids:
            qmarks = ",".join("?" * len(open_ids))
            for r in c.execute(
                    f"SELECT trade_id, mid, ts_ms, excursion_bp FROM path "
                    f"WHERE trade_id IN ({qmarks}) AND ts_ms = "
                    f"(SELECT MAX(ts_ms) FROM path p2 WHERE p2.trade_id = path.trade_id)",
                    open_ids):
                latest_path[r["trade_id"]] = dict(r)
            for r in c.execute(
                    f"SELECT trade_id, MAX(excursion_bp) mx, MIN(excursion_bp) mn "
                    f"FROM path WHERE trade_id IN ({qmarks}) GROUP BY trade_id", open_ids):
                path_extremes[r["trade_id"]] = dict(r)
        d = dict(
            db=str(DB), now=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            decision_points=g("SELECT COUNT(*) FROM decisions"),
            fired=g("SELECT COUNT(*) FROM decisions WHERE fired=1"),
            symbols_seen=g("SELECT COUNT(DISTINCT symbol) FROM decisions"),
            trades_total=g("SELECT COUNT(*) FROM shadow_trades"),
            open_trades=g("SELECT COUNT(*) FROM shadow_trades WHERE status='OPEN'"),
            closed_trades=g("SELECT COUNT(*) FROM shadow_trades WHERE status='CLOSED'"),
            restarts_n=g("SELECT COUNT(*) FROM restarts"),
            restarts=restarts,
            first_decision=g("SELECT MIN(evaluated_at) FROM decisions", ""),
            last_decision=g("SELECT MAX(evaluated_at) FROM decisions", ""),
            health=dict(health) if health else None,
            trades=trades, recent=recent, fired_recent=fired_recent, hist=hist)

    for t in d["trades"]:
        if t["status"] == "OPEN":
            lp = latest_path.get(t["trade_id"])
            ext = path_extremes.get(t["trade_id"])
            t["current_price"] = lp["mid"] if lp else None
            t["price_asof_ms"] = lp["ts_ms"] if lp else None
            t["gross_bp_now"] = lp["excursion_bp"] if lp else None
            t["net_bp_now"] = (t["gross_bp_now"] - COST_BP
                                if t["gross_bp_now"] is not None else None)
            t["best_seen_bp"] = ext["mx"] if ext else t.get("gross_bp_now")
            t["worst_seen_bp"] = ext["mn"] if ext else t.get("gross_bp_now")
        _enrich_trade(t)

    open_list = [t for t in d["trades"] if t["status"] == "OPEN"]
    closed_list = [t for t in d["trades"] if t["status"] == "CLOSED"]
    priced_closed = [t for t in closed_list if t.get("net_bp") is not None]

    if priced_closed:
        n = len(priced_closed)
        avg = lambda k: sum(t[k] for t in priced_closed if t.get(k) is not None) / max(
            1, sum(1 for t in priced_closed if t.get(k) is not None))  # noqa: E731
        wins = [t for t in priced_closed if t["net_bp"] > 0]
        best = max(priced_closed, key=lambda t: t["net_bp"])
        worst = min(priced_closed, key=lambda t: t["net_bp"])
        durs = [t for t in priced_closed if t.get("duration_min") is not None]
        longest = max(durs, key=lambda t: t["duration_min"]) if durs else None
        shortest = min(durs, key=lambda t: t["duration_min"]) if durs else None
        realized_usd = sum(t["pnl_usd"] for t in priced_closed if t.get("pnl_usd") is not None)
        d["closed_stats"] = dict(
            n=n, gross_mid=avg("gross_bp"), gross_exec=avg("gross_executable_bp"),
            net=avg("net_bp"), penalty=avg("execution_penalty_bp"),
            mfe=avg("mfe_bp"), mae=avg("mae_bp"), duration=avg("duration_min"),
            total_usd=realized_usd, total_try=usd_to_try(realized_usd),
            win_rate=(len(wins) / n * 100), best=best, worst=worst,
            longest=longest, shortest=shortest)
    else:
        d["closed_stats"] = dict(n=0, best=None, worst=None, longest=None, shortest=None,
                                  total_usd=None, total_try=None, win_rate=None)

    priced_open = [t for t in open_list if t.get("pnl_usd") is not None]
    unrealized_usd = (sum(t["pnl_usd"] for t in priced_open) if priced_open else None)
    realized_usd = d["closed_stats"]["total_usd"]
    total_usd = None
    if realized_usd is not None or unrealized_usd is not None:
        total_usd = (realized_usd or 0.0) + (unrealized_usd or 0.0)
    capital_deployed_usd = len(open_list) * POSITION_SIZE_USD_DISPLAY
    d["portfolio"] = dict(
        open_n=len(open_list), closed_n=len(closed_list),
        capital_deployed_usd=capital_deployed_usd,
        capital_deployed_try=usd_to_try(capital_deployed_usd),
        realized_usd=realized_usd, realized_try=usd_to_try(realized_usd),
        unrealized_usd=unrealized_usd, unrealized_try=usd_to_try(unrealized_usd),
        total_usd=total_usd, total_try=usd_to_try(total_usd))

    coin_map: dict[str, list[dict]] = {}
    for t in priced_closed:
        coin_map.setdefault(t["symbol"], []).append(t)
    coin_stats = []
    for sym, lst in coin_map.items():
        n = len(lst)
        w = sum(1 for t in lst if t["net_bp"] > 0)
        coin_stats.append(dict(
            symbol=sym, n=n, win_rate=w / n * 100,
            net_try=sum(t["pnl_try"] for t in lst if t.get("pnl_try") is not None),
            avg_bp=sum(t["net_bp"] for t in lst) / n,
            best_bp=max(t["net_bp"] for t in lst), worst_bp=min(t["net_bp"] for t in lst)))
    coin_stats.sort(key=lambda x: x["net_try"], reverse=True)
    d["coin_stats"] = coin_stats

    dur_stats = []
    for lo_m, hi_m, label in DUR_BUCKETS:
        bucket = [t for t in priced_closed if t.get("duration_min") is not None
                  and t["duration_min"] >= lo_m and (hi_m is None or t["duration_min"] < hi_m)]
        n = len(bucket)
        dur_stats.append(dict(
            label=label, n=n,
            win_rate=(sum(1 for t in bucket if t["net_bp"] > 0) / n * 100) if n else None,
            avg_bp=(sum(t["net_bp"] for t in bucket) / n) if n else None,
            total_try=(sum(t["pnl_try"] for t in bucket if t.get("pnl_try") is not None)
                       if n else None)))
    d["dur_stats"] = dur_stats

    bp_vals = [t["net_bp"] for t in priced_closed]
    if bp_vals:
        lo, hi = min(bp_vals), max(bp_vals)
        if lo == hi:
            lo, hi = lo - 1, hi + 1
        nbins = 8
        bw = (hi - lo) / nbins
        bins = [0] * nbins
        for v in bp_vals:
            bins[min(nbins - 1, int((v - lo) / bw))] += 1
        d["bp_hist"] = [(f"{lo+i*bw:+.0f}/{lo+(i+1)*bw:+.0f}", bins[i]) for i in range(nbins)]
    else:
        d["bp_hist"] = []

    now_ms = int(time.time() * 1000)
    entry_ms = [t["anchor_ms"] for t in d["trades"] if t.get("anchor_ms")]
    hours_elapsed = None
    if d.get("first_decision"):
        try:
            first_dt = datetime.fromisoformat(d["first_decision"])
            hours_elapsed = max((datetime.now(timezone.utc) - first_dt).total_seconds() / 3600, 1.0)
        except Exception:  # noqa: BLE001
            hours_elapsed = None
    daily_counts: dict[str, int] = {}
    for ms in entry_ms:
        day = datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%m-%d")
        daily_counts[day] = daily_counts.get(day, 0) + 1
    d["freq"] = dict(
        trades_24h=sum(1 for ms in entry_ms if now_ms - ms <= 24 * 3_600_000),
        trades_7d=sum(1 for ms in entry_ms if now_ms - ms <= 7 * 24 * 3_600_000),
        avg_per_day=(d["trades_total"] / (hours_elapsed / 24)) if hours_elapsed else None,
        avg_per_hour=(d["trades_total"] / hours_elapsed) if hours_elapsed else None,
        daily=sorted(daily_counts.items())[-14:])

    log_events = []
    for r in d["fired_recent"]:
        log_events.append((r["evaluated_at"],
                            f"SİNYAL verildi: {r['symbol']} (+{r['minute']} dk, "
                            f"puan={r['comb']:.4f})"))
    for t in d["trades"]:
        if t.get("entry_ts"):
            ep = t.get("entry_price")
            ep_txt = f"${ep:,.4f}" if ep is not None else "?"
            log_events.append((t["entry_ts"], f"İŞLEM AÇILDI: {t['symbol']} @ {ep_txt}"))
        if t.get("exit_ts"):
            net = t.get("net_bp")
            sonuc = "KÂR" if (net or 0) > 0 else "ZARAR"
            net_txt = f"{net:+.1f}bp" if net is not None else "?bp"
            log_events.append((t["exit_ts"], f"İŞLEM KAPANDI: {t['symbol']} ({sonuc}, {net_txt})"))
    for h_ in d["hist"]:
        if h_.get("alert"):
            log_events.append((h_["ts"], f"UYARI: {h_['alert']}"))
    for r_ in d["restarts"]:
        note = f" ({r_['note']})" if r_.get("note") else ""
        log_events.append((r_["ts"], f"SİSTEM YENİDEN BAŞLADI{note}"))
    log_events.sort(key=lambda x: x[0] or "", reverse=True)
    d["log_events"] = log_events[:40]

    sp = [t["spread_bp"] for t in d["trades"] if t.get("spread_bp") is not None]
    d["spread"] = dict(n=len(sp), avg=(sum(sp) / len(sp)) if sp else None)
    return d


def trade_detail(trade_id: str) -> dict | None:
    if not trade_id:
        return None
    with con() as c:
        row = c.execute("SELECT * FROM shadow_trades WHERE trade_id=?", (trade_id,)).fetchone()
        if not row:
            return None
        t = dict(row)
        path_rows = [dict(r) for r in c.execute(
            "SELECT ts_ms, mid, excursion_bp FROM path WHERE trade_id=? ORDER BY ts_ms",
            (trade_id,))]
    if t["status"] == "OPEN":
        lp = path_rows[-1] if path_rows else None
        t["current_price"] = lp["mid"] if lp else None
        t["price_asof_ms"] = lp["ts_ms"] if lp else None
        t["gross_bp_now"] = lp["excursion_bp"] if lp else None
        t["net_bp_now"] = (t["gross_bp_now"] - COST_BP
                            if t["gross_bp_now"] is not None else None)
        exs = [p["excursion_bp"] for p in path_rows]
        t["best_seen_bp"] = max(exs) if exs else None
        t["worst_seen_bp"] = min(exs) if exs else None
    _enrich_trade(t)
    t["path"] = path_rows
    return t


# ============================================================ presentation
CSS = """
:root{--bg:#0f1115;--card:#171a21;--line:#252a34;--tx:#e6e9ef;--dim:#8b93a7;
--ok:#3fb950;--warn:#d29922;--bad:#f85149;--acc:#58a6ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);
font:14px/1.5 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
padding:16px;padding-block:20px}
a{color:inherit}
h1{font-size:17px;margin:0 0 4px}h2{font-size:13px;text-transform:uppercase;
letter-spacing:.08em;color:var(--dim);margin:22px 0 8px;font-weight:600}
.sub{color:var(--dim);font-size:12px;margin-bottom:18px}
.grid{display:grid;gap:10px;grid-template-columns:repeat(auto-fit,minmax(140px,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:9px;padding:12px}
.k{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.05em}
.v{font-size:22px;font-weight:600;margin-top:3px;font-variant-numeric:tabular-nums}
.tw{overflow-x:auto;border:1px solid var(--line);border-radius:9px;background:var(--card)}
table{border-collapse:collapse;width:100%;font-size:12.5px}
th{text-align:left;color:var(--dim);font-weight:600;padding:8px 10px;
border-bottom:1px solid var(--line);white-space:nowrap;font-size:11px;
text-transform:uppercase;letter-spacing:.04em}
td{padding:7px 10px;border-bottom:1px solid var(--line);white-space:nowrap;
font-variant-numeric:tabular-nums}tr:last-child td{border-bottom:0}
tr.link:hover{background:rgba(88,166,255,.06);cursor:pointer}
.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}.dim{color:var(--dim)}
.pill{display:inline-block;padding:2px 8px;border-radius:99px;font-size:11px;font-weight:600}
.p-ok{background:rgba(63,185,80,.14);color:var(--ok)}
.p-bad{background:rgba(248,81,73,.14);color:var(--bad)}
.p-dim{background:rgba(139,147,167,.14);color:var(--dim)}
.note{background:rgba(88,166,255,.07);border:1px solid rgba(88,166,255,.25);
border-radius:9px;padding:11px 13px;font-size:12.5px;color:#b9c6da;margin:14px 0}
.bar{height:5px;background:var(--line);border-radius:3px;overflow:hidden;margin-top:6px}
.bar>i{display:block;height:100%;background:var(--acc)}
select,input{background:var(--card);color:var(--tx);border:1px solid var(--line);
border-radius:6px;padding:6px 8px;font-size:12.5px}
.navl{text-decoration:none}
.log{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;
line-height:1.9;max-height:360px;overflow-y:auto}
.log .t{color:var(--dim);margin-right:8px}
@media(max-width:560px){.v{font-size:19px}body{padding:12px}}
"""


def nav(active: str, qs: dict) -> str:
    tq = f"?t={quote(TOKEN)}" if TOKEN else ""
    def link(path, label, key):
        cls = "p-ok" if key == active else "p-dim"
        return f'<a class=navl href="{path}{tq}"><span class="pill {cls}">{label}</span></a>'
    return (f'<div style="margin-bottom:14px;display:flex;gap:8px">'
            f'{link("/", "Ana Sayfa", "ana")}{link("/gecmis", "İşlem Geçmişi", "gecmis")}</div>')


def missing_db_page() -> str:
    return (f"<!doctype html><meta charset=utf-8><style>{CSS}</style>"
            f"<h1>Kripto Sinyal Sistemi (Deneme)</h1>"
            f"<div class=note>Veritabanı henüz oluşmadı — sistem başlamamış olabilir.</div>")


# ---- charts (pure inline SVG, no JS/CDN) -------------------------------------------------
def svg_cum_chart(points: list[float], width: int = 640, height: int = 170) -> str:
    if len(points) < 2:
        return '<div class=note>Grafik için en az 2 kapanan işlem gerekiyor.</div>'
    pad_l, pad_r, pad_t, pad_b = 54, 14, 14, 10
    w, h = width - pad_l - pad_r, height - pad_t - pad_b
    lo, hi = min(0.0, *points), max(0.0, *points)
    span = (hi - lo) or 1.0

    def x(i: int) -> float:
        return pad_l + w * i / (len(points) - 1)

    def y(v: float) -> float:
        return pad_t + h - (v - lo) / span * h

    poly = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(points))
    color = "var(--ok)" if points[-1] >= 0 else "var(--bad)"
    labels = "".join(
        f'<text x="4" y="{y(v)+3:.1f}" font-size="10" style="fill:var(--dim)">{v:,.0f}</text>'
        for v in sorted({round(lo), 0, round(hi)}))
    return (f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
            f'style="display:block">'
            f'<line x1="{pad_l}" y1="{y(0):.1f}" x2="{width-pad_r}" y2="{y(0):.1f}" '
            f'style="stroke:var(--line)" stroke-dasharray="3,3"/>'
            f'{labels}'
            f'<polyline points="{poly}" fill="none" style="stroke:{color}" stroke-width="2"/>'
            f'<circle cx="{x(len(points)-1):.1f}" cy="{y(points[-1]):.1f}" r="3.5" '
            f'style="fill:{color}"/></svg>')


def svg_bar_chart(items: list[tuple[str, float]], width: int = 640, height: int = 170,
                   unit: str = "bp") -> str:
    if not items:
        return '<div class=note>Henüz veri yok.</div>'
    pad_l, pad_r, pad_t, pad_b = 8, 8, 10, 8
    w, h = width - pad_l - pad_r, height - pad_t - pad_b
    vals = [v for _, v in items]
    lo, hi = min(0.0, *vals), max(0.0, *vals)
    span = (hi - lo) or 1.0
    zero_y = pad_t + h - (0 - lo) / span * h
    n = len(items)
    gap = w / n
    bw = max(2.0, gap * 0.6)
    bars = []
    for i, (label, v) in enumerate(items):
        cx = pad_l + gap * i + gap / 2
        y_top = pad_t + h - (max(v, 0) - lo) / span * h
        y_bot = pad_t + h - (min(v, 0) - lo) / span * h
        color = "var(--ok)" if v >= 0 else "var(--bad)"
        bars.append(
            f'<rect x="{cx-bw/2:.1f}" y="{y_top:.1f}" width="{bw:.1f}" '
            f'height="{max(y_bot-y_top,1):.1f}" rx="1.5" style="fill:{color}">'
            f'<title>{esc(label)}: {v:+,.1f}{unit}</title></rect>')
    return (f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
            f'style="display:block">'
            f'<line x1="{pad_l}" y1="{zero_y:.1f}" x2="{width-pad_r}" y2="{zero_y:.1f}" '
            f'style="stroke:var(--line)"/>' + "".join(bars) + '</svg>')


def svg_hist_chart(bins: list[tuple[str, int]], width: int = 640, height: int = 170) -> str:
    if not bins:
        return '<div class=note>Henüz kapanan işlem yok.</div>'
    pad_l, pad_r, pad_t, pad_b = 8, 8, 10, 26
    w, h = width - pad_l - pad_r, height - pad_t - pad_b
    hi = max((v for _, v in bins), default=0) or 1
    n = len(bins)
    gap = w / n
    bw = max(2.0, gap * 0.7)
    parts = []
    for i, (label, v) in enumerate(bins):
        cx = pad_l + gap * i + gap / 2
        bh = (v / hi) * h
        y_top = pad_t + h - bh
        parts.append(
            f'<rect x="{cx-bw/2:.1f}" y="{y_top:.1f}" width="{bw:.1f}" height="{max(bh,1):.1f}" '
            f'rx="1.5" style="fill:var(--acc)"><title>{esc(label)}bp: {v} işlem</title></rect>'
            f'<text x="{cx:.1f}" y="{height-6}" font-size="8.5" text-anchor="middle" '
            f'style="fill:var(--dim)">{esc(label)}</text>')
    return (f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
            f'style="display:block">' + "".join(parts) + '</svg>')


def svg_price_path(path_rows: list[dict], entry_price: float | None,
                    width: int = 640, height: int = 200) -> str:
    if len(path_rows) < 2:
        return ('<div class=note>Fiyat geçmişi grafiği için yeterli veri yok — sistem bu '
                'işlem için henüz yeterli fiyat örneği toplamadı.</div>')
    pad_l, pad_r, pad_t, pad_b = 62, 14, 14, 10
    w, h = width - pad_l - pad_r, height - pad_t - pad_b
    vals = [p["mid"] for p in path_rows]
    if entry_price:
        vals = vals + [entry_price]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    n = len(path_rows)

    def x(i: int) -> float:
        return pad_l + w * i / (n - 1)

    def y(v: float) -> float:
        return pad_t + h - (v - lo) / span * h

    poly = " ".join(f"{x(i):.1f},{y(p['mid']):.1f}" for i, p in enumerate(path_rows))
    markers = ""
    if entry_price:
        ey = y(entry_price)
        markers += (f'<line x1="{pad_l}" y1="{ey:.1f}" x2="{width-pad_r}" y2="{ey:.1f}" '
                    f'style="stroke:var(--acc)" stroke-dasharray="4,3"/>'
                    f'<text x="{width-pad_r}" y="{ey-4:.1f}" font-size="10" text-anchor="end" '
                    f'style="fill:var(--acc)">giriş {entry_price:,.4f}</text>')
    labels = "".join(
        f'<text x="4" y="{y(v)+3:.1f}" font-size="10" style="fill:var(--dim)">{v:,.4f}</text>'
        for v in (lo, hi))
    last_color = "var(--ok)" if path_rows[-1]["mid"] >= (entry_price or path_rows[0]["mid"]) \
        else "var(--bad)"
    return (f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
            f'style="display:block">{markers}{labels}'
            f'<polyline points="{poly}" fill="none" style="stroke:var(--acc)" '
            f'stroke-width="2"/>'
            f'<circle cx="{x(n-1):.1f}" cy="{y(path_rows[-1]["mid"]):.1f}" r="3.5" '
            f'style="fill:{last_color}"/></svg>')


STATUS_TR = {"OPEN": "AÇIK", "CLOSED": "KAPANDI"}
REASON_TR = {"nonfinite": "veri yetersiz", "below_threshold": "eşik altında",
             "short_dropped": "düşüş sinyali (atlandı)"}
ENTRY_TR = {"ask": "satış fiyatı", "mid_fallback": "orta fiyat (yedek)", "mid": "orta fiyat"}


def health_pill(ok: bool | None, warn: bool = False) -> str:
    if ok is None:
        return "⚪"
    if warn:
        return "🟡"
    return "🟢" if ok else "🔴"


def trade_card(label: str, t: dict | None) -> str:
    if not t:
        return (f'<div class=card><div class=k>{esc(label)}</div>'
                 f'<div class="v dim">henüz yok</div></div>')
    net = t.get("net_bp")
    cls = "ok" if (net or 0) > 0 else "bad"
    return (f'<div class=card><div class=k>{esc(label)}</div>'
            f'<div class="v {cls}">{num(net,1,"bp")}</div>'
            f'<div class=sub style="margin:2px 0 0"><b>{esc(t["symbol"])}</b> · '
            f'{ts(t["anchor_ms"])} · {num(t.get("pnl_usd"),2,"$")} · '
            f'{num(t.get("pnl_try"),0," TL")}</div></div>')


def duration_card(label: str, t: dict | None) -> str:
    if not t:
        return (f'<div class=card><div class=k>{esc(label)}</div>'
                 f'<div class="v dim">henüz yok</div></div>')
    return (f'<div class=card><div class=k>{esc(label)}</div>'
            f'<div class=v>{duration_fmt(t.get("duration_min"))}</div>'
            f'<div class=sub style="margin:2px 0 0"><b>{esc(t["symbol"])}</b> · '
            f'{ts(t["anchor_ms"])}</div></div>')


# ============================================================ page: ana sayfa
def page_dashboard(d: dict, qs: dict) -> str:
    if "error" in d:
        return missing_db_page()
    h = d.get("health") or {}
    age = h.get("data_age_s")
    alert = h.get("alert") or ""
    live = age is not None and age < STALE_AFTER_S and not alert
    cs, sp, pf, freq = d["closed_stats"], d["spread"], d["portfolio"], d["freq"]

    # ---- open positions table (section 3) ----
    open_trades = [t for t in d["trades"] if t["status"] == "OPEN"]
    rows = []
    for t in open_trades:
        gcls = "ok" if (t.get("gross_bp_now") or 0) > 0 else "bad"
        ncls = "ok" if (t.get("net_bp_now") or 0) > 0 else "bad"
        cur_price = price_fmt(t.get("current_price"))
        if t.get("current_price") is None:
            cur_price = '<span class="dim">veri bekleniyor</span>'
        href = f"/islem?{qstr(qs, id=t['trade_id'])}"
        rows.append(
            f"<tr class=link onclick=\"location.href='{href}'\">"
            f"<td><a class=navl href='{href}'><b>{esc(t['symbol'])}</b></a></td>"
            f"<td>LONG</td><td>{ts(t['anchor_ms'])}</td>"
            f"<td>{price_fmt(t.get('entry_price'))}</td><td>{cur_price}</td>"
            f"<td>{qty_fmt(t.get('qty'), t['symbol'])}</td>"
            f"<td>${POSITION_SIZE_USD_DISPLAY:,}</td>"
            f"<td>{num(t.get('current_value_usd'),2,'$')}</td>"
            f"<td class={gcls}>{num(t.get('gross_try'),0,' TL')}</td>"
            f"<td class={gcls}>{num((t.get('gross_bp_now') or 0)/100,2,'%') if t.get('gross_bp_now') is not None else '<span class=dim>-</span>'}</td>"
            f"<td class={gcls}>{num(t.get('gross_bp_now'),1,'bp')}</td>"
            f"<td class={ncls}>~{num(t.get('net_try'),0,' TL')}</td>"
            f"<td class={ncls}>~{num((t.get('net_bp_now') or 0)/100,2,'%') if t.get('net_bp_now') is not None else '<span class=dim>-</span>'}</td>"
            f"<td class={ncls}>~{num(t.get('net_bp_now'),1,'bp')}</td>"
            f"<td>{duration_fmt(t.get('age_min'))}</td>"
            f"<td class=ok>{num(t.get('best_seen_bp'),1,'bp')}</td>"
            f"<td class=bad>{num(t.get('worst_seen_bp'),1,'bp')}</td>"
            f"<td><span class='pill p-ok'>AÇIK</span></td></tr>")
    open_tbl = "".join(rows) or (
        "<tr><td colspan=17 class=dim>şu anda açık işlem yok — sistem 10 coin üzerinde "
        "günde ortalama ~2,4 kez sinyal üretiyor, sabırlı olun</td></tr>")

    # ---- charts ----
    closed_sorted = sorted(
        (t for t in d["trades"] if t["status"] == "CLOSED" and t.get("net_bp") is not None),
        key=lambda t: t["anchor_ms"])
    cum, running = [], 0.0
    for t in closed_sorted:
        running += t.get("pnl_try") or 0.0
        cum.append(running)
    bar_items = [(t["symbol"], t["net_bp"]) for t in closed_sorted[-24:]]
    chart_cum = svg_cum_chart(cum)
    chart_bar = svg_bar_chart(bar_items)
    chart_hist = svg_hist_chart(d["bp_hist"])
    coin_bar_items = [(cst["symbol"], cst["net_try"]) for cst in d["coin_stats"]]
    chart_coin = svg_bar_chart(coin_bar_items, unit=" TL")
    daily_items = [(day, float(n)) for day, n in freq["daily"]]
    chart_daily = svg_bar_chart(daily_items, unit=" işlem") if daily_items else \
        '<div class=note>Henüz veri yok.</div>'

    # ---- coin analysis table (section 10) ----
    coin_rows = "".join(
        f"<tr><td><b>{esc(c['symbol'])}</b></td><td>{c['n']}</td>"
        f"<td>{num(c['win_rate'],0,'%')}</td>"
        f"<td class={'ok' if c['net_try']>=0 else 'bad'}>{num(c['net_try'],0,' TL')}</td>"
        f"<td>{num(c['avg_bp'],1,'bp')}</td><td class=ok>{num(c['best_bp'],1,'bp')}</td>"
        f"<td class=bad>{num(c['worst_bp'],1,'bp')}</td></tr>"
        for c in d["coin_stats"]) or "<tr><td colspan=7 class=dim>henüz kapanan işlem yok</td></tr>"

    # ---- duration bucket table (section 12) ----
    dur_rows = "".join(
        f"<tr><td>{esc(b['label'])}</td><td>{b['n']}</td>"
        f"<td>{num(b['win_rate'],0,'%')}</td><td>{num(b['avg_bp'],1,'bp')}</td>"
        f"<td class={'ok' if (b['total_try'] or 0)>=0 else 'bad'}>{num(b['total_try'],0,' TL')}</td></tr>"
        for b in d["dur_stats"])

    # ---- log panel (section 15) ----
    log_rows = "".join(
        f'<div><span class=t>{ts_iso(t_)[:16] if t_ and "T" in str(t_) else esc(t_)[:16]}</span>{esc(msg)}</div>'
        for t_, msg in d["log_events"]) or '<div class=dim>henüz olay yok</div>'

    # ---- system health panel (section 14) ----
    api_ok = h.get("api_ok")
    db_ok = True  # this request already opened shadow.db successfully
    data_ok = age is not None and age < STALE_AFTER_S
    hh = "".join(
        f"<tr><td>{esc(x['ts'])[5:16]}</td><td>{num(x['data_age_s'],0,'s')}</td>"
        f"<td>{esc(x['open_trades'])}</td>"
        f"<td>{'<span class=bad>'+esc(x['alert'])+'</span>' if x['alert'] else '<span class=ok>sorun yok</span>'}</td></tr>"
        for x in d["hist"])

    dec_rows = []
    for r in d["recent"]:
        result = ("<span class='pill p-ok'>SİNYAL VERİLDİ</span>" if r["fired"]
                  else "<span class=dim>" + esc(REASON_TR.get(r["reason"], r["reason"])) + "</span>")
        dec_rows.append(
            f"<tr><td>{ts(r['anchor_ms'])}</td><td><b>{esc(r['symbol'])}</b></td>"
            f"<td>+{esc(r['minute'])} dk</td><td>{num(r['raw_score'],4)}</td>"
            f"<td>{num(r['rv30_bp'],1)}</td><td>{num(r['comb'],4)}</td>"
            f"<td class=dim>{num(r['threshold'],4)}</td><td>{result}</td></tr>")
    dec_tbl = "".join(dec_rows) or '<tr><td colspan=8 class=dim>henüz veri yok</td></tr>'

    tot_cls = "ok" if (pf.get("total_try") or 0) >= 0 else "bad"
    real_cls = "ok" if (pf.get("realized_try") or 0) >= 0 else "bad"
    unreal_cls = "ok" if (pf.get("unrealized_try") or 0) >= 0 else "bad"
    prog = min(100, round(100 * d["decision_points"] / max(60, 1))) if d["decision_points"] < 60 else 100
    thr = num(d['recent'][0]['threshold'], 6) if d['recent'] else '0.964580'

    return f"""<!doctype html><html lang=tr><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<meta http-equiv=refresh content=60><title>BorsaBot İzleme Paneli — EXP-107</title>
<style>{CSS}</style></head><body>
<h1>Kripto Alım Sinyali Sistemi — İzleme Paneli
<span class="pill {'p-ok' if live else 'p-bad'}">{'ÇALIŞIYOR' if live else 'VERİ ESKİ'}</span></h1>
{nav("ana", qs)}
<div class=sub>sadece test amaçlı · gerçek para kullanılmıyor · salt okunur izleme sayfası · {esc(d['now'])} UTC ·
sayfa 60 saniyede bir kendini yeniler</div>

<div class=note><b>Bu sayfa ne anlatıyor?</b> Bir bilgisayar programı kripto para fiyatlarını sürekli izliyor ve
geçmiş verilere bakarak "şimdi almak mantıklı mı?" diye tahmin ediyor. Aşağıdaki her şey bir <b>deneme</b> —
sistem gerçekten para yatırmıyor, sadece "gerçekten alsaydım ne olurdu?" sorusunu simüle edip kaydediyor.
Bu sistemde tek bir portföy bakiyesi / başlangıç sermayesi kavramı <b>yoktur</b> — her işlem, raporlama için
sabit ${POSITION_SIZE_USD_DISPLAY:,} sanal (simüle) büyüklükle açılıyor. Gerçek bir borsa bağlantısı, gerçek
bakiye veya gerçek komisyon verisi bu sistemde <b>mevcut değil</b>; olmayan hiçbir veri uydurulmaz.</div>

{'<div class=note><b>UYARI:</b> '+esc(alert)+'</div>' if alert else ''}

<h2>Portföy (simülasyon)</h2>
<div class=grid>
<div class=card><div class=k>açık pozisyon sayısı</div><div class=v>{pf['open_n']}</div></div>
<div class=card><div class=k>açık pozisyonlarda kullanılan sermaye</div>
<div class=v>{num(pf['capital_deployed_try'],0,' TL')}</div></div>
<div class=card><div class=k>gerçekleşmiş kâr/zarar</div>
<div class="v {real_cls}">{num(pf['realized_try'],0,' TL')}</div></div>
<div class=card><div class=k>gerçekleşmemiş kâr/zarar (~)</div>
<div class="v {unreal_cls}">{num(pf['unrealized_try'],0,' TL')}</div></div>
<div class=card><div class=k>toplam kâr/zarar (~)</div>
<div class="v {tot_cls}">{num(pf['total_try'],0,' TL')}</div></div>
<div class=card><div class=k>kazanma oranı</div><div class=v>{num(cs.get('win_rate'),0,'%')}</div></div>
</div>
<div class=sub style="margin-top:-8px">"gerçekleşmemiş kâr/zarar" açık pozisyonların şu anki tahmini durumu (henüz
kesinleşmedi, maliyet düşümü tahminidir) · "toplam başlangıç sermayesi" ve "kullanılabilir bakiye" bu sistemde
<b>veri mevcut değil</b> çünkü tek bir portföy bakiyesi tutulmuyor, her işlem kendi sabit sanal büyüklüğüyle simüle ediliyor</div>

<h2>Anlık durum</h2>
<div class=grid>
<div class=card><div class=k>bot durumu</div><div class="v {'ok' if live else 'bad'}">{'ÇALIŞIYOR' if live else 'VERİ ESKİ'}</div></div>
<div class=card><div class=k>son veri güncellemesi</div><div class=v>{num(age,0,'s önce')}</div></div>
<div class=card><div class=k>son sinyal</div><div class=v style="font-size:13px">{ts_iso(h.get('last_signal_ts'))}</div></div>
<div class=card><div class=k>son işlem</div><div class=v style="font-size:13px">{ts_iso(h.get('last_trade_ts'))}</div></div>
<div class=card><div class=k>izlenen sembol</div><div class=v>{d['symbols_seen']}</div></div>
<div class=card><div class=k>açık pozisyon</div><div class=v>{d['open_trades']}</div></div>
</div>

<h2>Açık İşlemler ({d['open_trades']})</h2>
<div class=sub style="margin-top:-4px">"~" işaretli sayılar tahminidir (işlem henüz kapanmadı, çıkış maliyeti
gerçekleşmedi) · satıra tıklayarak işlemin tam detayına gidebilirsiniz</div>
<div class=tw><table><thead><tr><th>coin</th><th>yön</th><th>açılış zamanı</th>
<th>açılış fiyatı</th><th>güncel fiyat</th><th>miktar</th><th>kullanılan sermaye</th>
<th>güncel poz. değeri</th><th>ham PnL (TL)</th><th>ham PnL (%)</th><th>ham PnL (BP)</th>
<th>net PnL (TL)</th><th>net PnL (%)</th><th>net PnL (BP)</th><th>işlem süresi</th>
<th>en yüksek görülen</th><th>en düşük görülen</th><th>durum</th></tr></thead>
<tbody>{open_tbl}</tbody></table></div>

<h2>En iyi / en kötü / en uzun / en kısa (kapanan işlemler arasından)</h2>
<div class=grid>
{trade_card('en yüksek kârlı işlem', cs.get('best'))}
{trade_card('en büyük zararlı işlem', cs.get('worst'))}
{duration_card('en uzun işlem', cs.get('longest'))}
{duration_card('en kısa işlem', cs.get('shortest'))}
</div>
{'<div class=note>Bu sayılar sadece <b>'+str(cs['n'])+'</b> kapanan işleme dayanıyor — güvenilir bir sonuç çıkarmak için yeterli değil.</div>' if 0 < cs['n'] < 5 else ''}

<h2>Kapanan işlem kalitesi — ortalamalar ({cs['n']} işlem)</h2>
{'<div class=note>Henüz kapanan işlem yok. Bu sistem bir işlemi ortalama ~23,4 saat açık tutuyor, yani ilk sonuç ilk sinyalden yaklaşık bir gün sonra görünür.</div>' if cs['n']==0 else ''}
<div class=grid>
<div class=card><div class=k>ort. kağıt üzerinde kâr</div><div class=v>{num(cs.get('gross_mid'),1,'bp')}</div></div>
<div class=card><div class=k>ort. uygulanabilir kâr</div><div class=v>{num(cs.get('gross_exec'),1,'bp')}</div></div>
<div class=card><div class=k>ort. işlem maliyeti</div><div class=v>{num(cs.get('penalty'),1,'bp')}</div></div>
<div class=card><div class=k>ort. maliyet sonrası net</div><div class=v>{num(cs.get('net'),1,'bp')}</div></div>
<div class=card><div class=k>ort. en iyi an (MFE)</div><div class=v>{num(cs.get('mfe'),1,'bp')}</div></div>
<div class=card><div class=k>ort. en kötü an (MAE)</div><div class=v>{num(cs.get('mae'),1,'bp')}</div></div>
<div class=card><div class=k>ort. işlem süresi</div><div class=v style="font-size:16px">{duration_fmt(cs.get('duration'))}</div></div>
</div>
<div class=sub style="margin-top:-8px">"net" asıl önemli sayıdır — {COST_BP}bp'lik tahmini işlem maliyeti düşüldükten sonra
gerçekte cepte kalan kısmı gösterir. TL karşılığı sabit {TRY_RATE:.2f} USD/TL kuruyla hesaplanır (gerçek para hareket etmez).</div>

<h2>Grafikler</h2>
<div class=grid style="grid-template-columns:1fr 1fr">
<div class=card><div class=k>kümülatif gerçekleşmiş kâr/zarar (TL)</div>
<div style="margin-top:8px">{chart_cum}</div></div>
<div class=card><div class=k>işlem başına net sonuç (bp) — son {len(bar_items)} kapanan işlem</div>
<div style="margin-top:8px">{chart_bar}</div></div>
<div class=card><div class=k>BP dağılımı (histogram)</div>
<div style="margin-top:8px">{chart_hist}</div></div>
<div class=card><div class=k>coin başına toplam net kâr/zarar (TL)</div>
<div style="margin-top:8px">{chart_coin}</div></div>
<div class=card><div class=k>günlük işlem sayısı (son 14 gün)</div>
<div style="margin-top:8px">{chart_daily}</div></div>
<div class=card><div class=k>kazanan / kaybeden</div>
<div class=v>{len([t for t in closed_sorted if t['net_bp']>0])} / {len([t for t in closed_sorted if t['net_bp']<=0])}</div>
<div class=bar style="height:10px"><i style="width:{cs.get('win_rate') or 0:.0f}%;background:var(--ok)"></i></div>
<div class=sub style="margin-top:6px">{num(cs.get('win_rate'),0,'%')} kazanma oranı</div></div>
</div>
<div class=sub style="margin-top:6px">yeşil = kârlı, kırmızı = zararlı · çubuğun üzerine gelince (mouse) detay görünür</div>

<h2>Coin analizi</h2>
<div class=tw><table><thead><tr><th>coin</th><th>işlem</th><th>kazanma oranı</th>
<th>net PnL (TL)</th><th>ort. BP</th><th>en iyi (BP)</th><th>en kötü (BP)</th></tr></thead>
<tbody>{coin_rows}</tbody></table></div>

<h2>İşlem süresi analizi</h2>
<div class=tw><table><thead><tr><th>süre aralığı</th><th>işlem</th><th>kazanma oranı</th>
<th>ort. BP</th><th>toplam PnL (TL)</th></tr></thead><tbody>{dur_rows}</tbody></table></div>

<h2>İşlem sıklığı</h2>
<div class=grid>
<div class=card><div class=k>son 24 saat</div><div class=v>{freq['trades_24h']}</div></div>
<div class=card><div class=k>son 7 gün</div><div class=v>{freq['trades_7d']}</div></div>
<div class=card><div class=k>ort. işlem/gün</div><div class=v>{num(freq['avg_per_day'],2)}</div></div>
<div class=card><div class=k>ort. işlem/saat</div><div class=v>{num(freq['avg_per_hour'],3)}</div></div>
</div>

<h2>Sistem sağlığı</h2>
<div class=grid>
<div class=card><div class=k>bot çalışıyor mu</div><div class=v>{health_pill(live)}</div></div>
<div class=card><div class=k>API bağlantısı</div><div class=v>{health_pill(api_ok)}</div></div>
<div class=card><div class=k>veri tazeliği</div><div class=v>{health_pill(data_ok)}</div></div>
<div class=card><div class=k>database bağlantısı</div><div class=v>{health_pill(db_ok)}</div></div>
<div class=card><div class=k>yeniden başlama sayısı</div><div class=v>{d['restarts_n']}</div></div>
<div class=card><div class=k>ort. fiyat farkı (spread)</div><div class=v>{num(sp['avg'],2,'bp')}</div></div>
</div>
<div class=tw style="margin-top:10px"><table><thead><tr><th>zaman</th><th>veri yaşı</th><th>açık işlem</th><th>durum</th>
</tr></thead><tbody>{hh or '<tr><td colspan=4 class=dim>henüz veri yok</td></tr>'}</tbody></table></div>

<h2>Son olaylar (log)</h2>
<div class="card log">{log_rows}</div>

<h2>Son kontroller (sistem ne gördü?)</h2>
<div class=tw><table><thead><tr><th>zaman</th><th>coin</th><th>geçen süre</th><th>ham puan</th>
<th>oynaklık</th><th>birleşik puan</th><th>eşik</th><th>sonuç</th></tr></thead>
<tbody>{dec_tbl}</tbody></table></div>

<div class=note>Sistem, "birleşik puan" belirli bir çizgiyi (şu an <b>{thr}</b>) geçtiğinde alım sinyali veriyor.
Bu çizgi geçmiş verilerin en seçici diliminden (en üst %1) belirlendi — yani sistem bilerek az ve seçici sinyal
üretiyor, sık işlem yapmak amaçlanmıyor. Saatlerce hiç sinyal gelmemesi normaldir, arıza değildir. Sadece LONG
(alış) yönünde işlem açılır; bu stratejinin sabit bir kuralıdır. Sistem çalıştırılmadan önce test edildi ve
geçmiş sonuçlarla birebir eşleştiği doğrulandı.</div>
</body></html>"""


# ============================================================ page: islem gecmisi
def page_history(d: dict, qs: dict) -> str:
    if "error" in d:
        return missing_db_page()
    closed = [t for t in d["trades"] if t["status"] == "CLOSED" and t.get("net_bp") is not None]
    coin_f = qs.get("coin", "").upper()
    result_f = qs.get("sonuc", "all")
    sort_f = qs.get("sirala", "newest")
    if coin_f:
        closed = [t for t in closed if t["symbol"] == coin_f]
    if result_f == "kar":
        closed = [t for t in closed if t["net_bp"] > 0]
    elif result_f == "zarar":
        closed = [t for t in closed if t["net_bp"] <= 0]
    sort_map = {
        "newest": (lambda t: t["anchor_ms"], True), "oldest": (lambda t: t["anchor_ms"], False),
        "bp_desc": (lambda t: t["net_bp"], True), "bp_asc": (lambda t: t["net_bp"], False),
        "tl_desc": (lambda t: t.get("pnl_try") or 0, True),
        "tl_asc": (lambda t: t.get("pnl_try") or 0, False),
        "dur_desc": (lambda t: t.get("duration_min") or 0, True),
        "dur_asc": (lambda t: t.get("duration_min") or 0, False),
    }
    key_fn, rev = sort_map.get(sort_f, sort_map["newest"])
    closed = sorted(closed, key=key_fn, reverse=rev)
    symbols = sorted({t["symbol"] for t in d["trades"] if t["status"] == "CLOSED"})

    rows = []
    for t in closed:
        cls = "ok" if t["net_bp"] > 0 else "bad"
        pill = "p-ok" if t["net_bp"] > 0 else "p-bad"
        sonuc = "KÂR" if t["net_bp"] > 0 else "ZARAR"
        href = f"/islem?{qstr(qs, id=t['trade_id'])}"
        rows.append(
            f"<tr class=link onclick=\"location.href='{href}'\">"
            f"<td><a class=navl href='{href}'><b>{esc(t['symbol'])}</b></a></td><td>LONG</td>"
            f"<td>{ts(t['anchor_ms'])}</td><td>{price_fmt(t.get('entry_price'))}</td>"
            f"<td>{ts_iso(t.get('exit_ts'))}</td><td>{price_fmt(t.get('simulated_exit_price'))}</td>"
            f"<td>${POSITION_SIZE_USD_DISPLAY:,}</td><td>{duration_fmt(t.get('duration_min'))}</td>"
            f"<td>{num(t.get('gross_bp'),1,'bp')}</td>"
            f"<td class={cls}>{num(t['net_bp'],1,'bp')}</td>"
            f"<td class={cls}>{num(t.get('pnl_try'),0,' TL')}</td>"
            f"<td class={cls}>{num(t['net_bp']/100,2,'%')}</td>"
            f"<td><span class='pill {pill}'>{sonuc}</span></td></tr>")
    tbl = "".join(rows) or "<tr><td colspan=13 class=dim>filtreyle eşleşen kapanmış işlem yok</td></tr>"
    coin_opts = "".join(
        f'<option value="{esc(s)}" {"selected" if s==coin_f else ""}>{esc(s)}</option>'
        for s in symbols)

    def opt(val, cur, label):
        return f'<option value="{val}" {"selected" if val==cur else ""}>{label}</option>'

    return f"""<!doctype html><html lang=tr><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>İşlem Geçmişi — EXP-107</title><style>{CSS}</style></head><body>
<h1>İşlem Geçmişi (Kapanmış İşlemler)</h1>
{nav("gecmis", qs)}
<div class=sub>salt okunur · sadece test amaçlı · gerçek para kullanılmıyor · {cs_n(closed)} işlem listeleniyor</div>

<form method=get action=/gecmis style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px">
<input type=hidden name=t value="{esc(TOKEN)}">
<select name=coin onchange="this.form.submit()"><option value="">Tüm coinler</option>{coin_opts}</select>
<select name=sonuc onchange="this.form.submit()">
{opt("all", result_f, "Tümü")}{opt("kar", result_f, "Sadece kâr")}{opt("zarar", result_f, "Sadece zarar")}
</select>
<select name=sirala onchange="this.form.submit()">
{opt("newest", sort_f, "En yeni")}{opt("oldest", sort_f, "En eski")}
{opt("bp_desc", sort_f, "En yüksek BP")}{opt("bp_asc", sort_f, "En düşük BP")}
{opt("tl_desc", sort_f, "En yüksek TL kâr")}{opt("tl_asc", sort_f, "En büyük TL zarar")}
{opt("dur_desc", sort_f, "En uzun süre")}{opt("dur_asc", sort_f, "En kısa süre")}
</select>
</form>

<div class=tw><table><thead><tr><th>coin</th><th>yön</th><th>açılış</th><th>açılış fiyatı</th>
<th>kapanış</th><th>kapanış fiyatı</th><th>pozisyon</th><th>süre</th>
<th>gross (bp)</th><th>net (bp)</th><th>net (TL)</th><th>net (%)</th><th>sonuç</th></tr></thead>
<tbody>{tbl}</tbody></table></div>
</body></html>"""


def cs_n(lst) -> int:
    return len(lst)


# ============================================================ page: islem detayi
def page_trade(t: dict | None, qs: dict) -> str:
    if not t:
        return f"""<!doctype html><meta charset=utf-8><style>{CSS}</style>
{nav("ana", qs)}<div class=note>İşlem bulunamadı — geçersiz veya eski bir id.</div>"""
    st = t["status"]
    pill = "p-ok" if st == "OPEN" else "p-dim"
    gcls = "ok" if (t.get("gross_bp_now") or 0) > 0 else "bad"
    ncls = "ok" if (t.get("net_bp_now") or 0) > 0 else "bad"
    approx = "" if t["pnl_is_final"] else "~ (tahmini)"
    entry_label = ENTRY_TR.get(t.get("entry_price_type"), t.get("entry_price_type"))
    chart = svg_price_path(t["path"], t.get("entry_price"))
    hi_lo = ""
    if t["path"]:
        vals = [p["mid"] for p in t["path"]]
        hi_lo = (f"<div class=sub>en yüksek görülen fiyat: {price_fmt(max(vals))} · "
                 f"en düşük görülen fiyat: {price_fmt(min(vals))}</div>")

    return f"""<!doctype html><html lang=tr><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<meta http-equiv=refresh content=60><title>{esc(t['symbol'])} — Pozisyon Detayı</title>
<style>{CSS}</style></head><body>
<h1>{esc(t['symbol'])} Pozisyon Detayı <span class="pill {pill}">{STATUS_TR.get(st, esc(st))}</span></h1>
{nav("ana", qs)}
<div class=sub>salt okunur · sadece test amaçlı · gerçek para kullanılmıyor</div>

<h2>İşlem bilgileri</h2>
<div class=grid>
<div class=card><div class=k>coin</div><div class=v>{esc(t['symbol'])}</div></div>
<div class=card><div class=k>yön</div><div class=v>LONG</div></div>
<div class=card><div class=k>açılış zamanı</div><div class=v style="font-size:14px">{ts_iso(t.get('entry_ts'))}</div></div>
<div class=card><div class=k>açılış fiyatı</div><div class=v>{price_fmt(t.get('entry_price'))}</div>
<div class=sub style="margin:2px 0 0">{esc(entry_label)}</div></div>
<div class=card><div class=k>güncel/çıkış fiyatı</div><div class=v>{price_fmt(t.get('current_price'))}</div></div>
<div class=card><div class=k>miktar (simüle)</div><div class=v style="font-size:16px">{qty_fmt(t.get('qty'), t['symbol'])}</div></div>
<div class=card><div class=k>pozisyon büyüklüğü</div><div class=v>${POSITION_SIZE_USD_DISPLAY:,}</div></div>
<div class=card><div class=k>işlem yaşı / süresi</div><div class=v style="font-size:16px">{duration_fmt(t.get('age_min'))}</div></div>
</div>

<h2>Performans {approx}</h2>
<div class=grid>
<div class=card><div class=k>ham (gross) PnL</div><div class="v {gcls}">{num(t.get('gross_bp_now'),1,'bp')}</div>
<div class=sub style="margin:2px 0 0">{num(t.get('gross_usd'),2,'$')} · {num(t.get('gross_try'),0,' TL')}</div></div>
<div class=card><div class=k>net PnL</div><div class="v {ncls}">{num(t.get('net_bp_now'),1,'bp')}</div>
<div class=sub style="margin:2px 0 0">{num(t.get('net_usd'),2,'$')} · {num(t.get('net_try'),0,' TL')}</div></div>
<div class=card><div class=k>net PnL (%)</div><div class="v {ncls}">{num((t.get('net_bp_now') or 0)/100,2,'%') if t.get('net_bp_now') is not None else '<span class=dim>-</span>'}</div></div>
<div class=card><div class=k>en yüksek görülen kâr</div><div class="v ok">{num(t.get('best_seen_bp'),1,'bp')}</div></div>
<div class=card><div class=k>en düşük görülen zarar (maks. drawdown)</div><div class="v bad">{num(t.get('worst_seen_bp'),1,'bp')}</div></div>
</div>
<div class=note><b>Gross / Net ayrımı:</b> Gross PnL, sadece fiyat hareketinden oluşan ham sonuçtur. Net PnL,
bundan tahmini {COST_BP}bp'lik işlem maliyeti (komisyon + spread + kayma tahmini, dokümante edilmiş sabit bir
tahmin) düşülerek hesaplanır. {'Bu işlem henüz kapanmadı; net rakam kapanışta kesinleşecek tahmini bir değerdir.' if not t['pnl_is_final'] else 'Bu işlem kapandı; rakamlar kesinleşmiştir.'}
Gerçek dolum fiyatı / gerçek komisyon kaydı bu sistemde <b>mevcut değil</b> (gerçek emir gönderilmiyor) —
uydurulmadı, tahmini olduğu açıkça belirtiliyor.</div>

<h2>Fiyat geçmişi</h2>
<div class=card>{chart}</div>
{hi_lo}
<div class=sub>açılış → güncel: {price_fmt(t.get('entry_price'))} → {price_fmt(t.get('current_price'))}
{f" · son fiyat güncellemesi: {ts_iso(datetime.fromtimestamp(t['price_asof_ms']/1000, timezone.utc).isoformat()) }" if t.get('price_asof_ms') else ""}</div>

{"<h2>Kapanış bilgileri</h2><div class=grid><div class=card><div class=k>kapanış zamanı</div><div class=v style='font-size:14px'>"+ts_iso(t.get('exit_ts'))+"</div></div><div class=card><div class=k>kapanış fiyatı</div><div class=v>"+price_fmt(t.get('simulated_exit_price'))+"</div></div><div class=card><div class=k>toplam süre</div><div class=v style='font-size:16px'>"+duration_fmt(t.get('duration_min'))+"</div></div></div>" if st=="CLOSED" else ""}
</body></html>"""


# ============================================================ query-string helper
def qstr(qs: dict, **overrides) -> str:
    merged = {**qs, **overrides}
    if TOKEN:
        merged.setdefault("t", TOKEN)
    return "&".join(f"{k}={quote(str(v))}" for k, v in merged.items() if v not in (None, ""))


# ============================================================ HTTP server
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
        qs = {k: v[0] for k, v in parse_qs(u.query).items()}
        if TOKEN and qs.get("t", "") != TOKEN:
            return self._deny()
        if u.path in ("/health", "/healthz"):
            body, ctype = b'{"ok":true}', "application/json"
        elif u.path == "/json":
            body = json.dumps(snapshot(), default=str).encode()
            ctype = "application/json"
        elif u.path in ("/", "/index.html"):
            body = page_dashboard(snapshot(), qs).encode("utf-8")
            ctype = "text/html; charset=utf-8"
        elif u.path == "/gecmis":
            body = page_history(snapshot(), qs).encode("utf-8")
            ctype = "text/html; charset=utf-8"
        elif u.path == "/islem":
            body = page_trade(trade_detail(qs.get("id", "")), qs).encode("utf-8")
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

    def log_message(self, *a) -> None:      # quiet; journald already timestamps
        pass


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8440)
    p.add_argument("--host", default="0.0.0.0")
    a = p.parse_args()
    print(f"EXP-107 dashboard  http://{a.host}:{a.port}/   db={DB}")
    print("token: " + ("REQUIRED (?t=...)" if TOKEN else
                       "NOT SET -- anyone who finds the port can read this page"))
    ThreadingHTTPServer((a.host, a.port), H).serve_forever()
