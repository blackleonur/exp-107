"""
EXP-107 -- read-only web dashboard for the shadow run.

RESEARCH ONLY. This process opens `shadow.db` in SQLite READ-ONLY mode (`mode=ro`), serves
HTML/JSON, and can write nothing and trade nothing. It imports no exchange client, no
credential and no order path. It is a separate process from the runner.

    python R5_dashboard.py [--port 8440] [--host 0.0.0.0]

Access control: if the environment variable EXP107_TOKEN is set, every request must carry
`?t=<token>`. Set it. The box has no firewall, so without a token the page is readable by
anyone who finds the port.

Pure stdlib -- sqlite3 + http.server + json. No Flask, no numpy, no pandas. That keeps the
VPS install to nothing at all beyond python3.
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

DB = Path(__file__).resolve().parents[1] / "shadow.db"
TOKEN = os.environ.get("EXP107_TOKEN", "")
COST_BP = 14.38
# USD/TRY used only to translate the simulated (paper) bp PnL into TL for readability.
# No money moves and no live FX feed is called -- set this to today's rate yourself.
TRY_RATE = float(os.environ.get("EXP107_USDTRY", "42.0"))
POSITION_SIZE_USD_DISPLAY = 1000  # matches POSITION_SIZE_USD in R3_shadow_run.py; display only


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


def con() -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def snapshot() -> dict:
    if not DB.exists():
        return {"error": "shadow.db does not exist yet — has the runner started?"}
    with con() as c:
        g = lambda q, d=0: (c.execute(q).fetchone() or [d])[0]  # noqa: E731
        health = c.execute("SELECT * FROM health ORDER BY ts DESC LIMIT 1").fetchone()
        trades = [dict(r) for r in c.execute(
            "SELECT * FROM shadow_trades ORDER BY anchor_ms DESC LIMIT 60")]
        recent = [dict(r) for r in c.execute(
            "SELECT symbol,minute,anchor_ms,raw_score,rv30_bp,comb,threshold,fired,reason,"
            "evaluated_at FROM decisions ORDER BY anchor_ms DESC, comb DESC LIMIT 25")]
        closed = [dict(r) for r in c.execute(
            "SELECT * FROM shadow_trades WHERE status='CLOSED'")]
        top = c.execute(
            "SELECT symbol,minute,anchor_ms,comb,threshold FROM decisions "
            "ORDER BY comb DESC LIMIT 8").fetchall()
        hist = [dict(r) for r in c.execute(
            "SELECT ts,data_age_s,open_trades,alert FROM health ORDER BY ts DESC LIMIT 12")]
        open_ids = [t["trade_id"] for t in trades if t["status"] == "OPEN"]
        latest_path: dict[str, dict] = {}
        if open_ids:
            qmarks = ",".join("?" * len(open_ids))
            for r in c.execute(
                    f"SELECT trade_id, mid, ts_ms, excursion_bp FROM path "
                    f"WHERE trade_id IN ({qmarks}) AND ts_ms = "
                    f"(SELECT MAX(ts_ms) FROM path p2 WHERE p2.trade_id = path.trade_id)",
                    open_ids):
                latest_path[r["trade_id"]] = dict(r)
        d = dict(
            db=str(DB), now=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            decision_points=g("SELECT COUNT(*) FROM decisions"),
            fired=g("SELECT COUNT(*) FROM decisions WHERE fired=1"),
            trades_total=g("SELECT COUNT(*) FROM shadow_trades"),
            open_trades=g("SELECT COUNT(*) FROM shadow_trades WHERE status='OPEN'"),
            closed_trades=g("SELECT COUNT(*) FROM shadow_trades WHERE status='CLOSED'"),
            restarts=g("SELECT COUNT(*) FROM restarts"),
            first_decision=g("SELECT MIN(evaluated_at) FROM decisions", ""),
            last_decision=g("SELECT MAX(evaluated_at) FROM decisions", ""),
            health=dict(health) if health else None,
            trades=trades, recent=recent, top=[dict(r) for r in top], hist=hist)
    for t in d["trades"]:
        t["entry_price"] = t.get("simulated_entry_price")
        if t["status"] == "OPEN":
            lp = latest_path.get(t["trade_id"])
            t["current_price"] = lp["mid"] if lp else None
            t["price_asof_ms"] = lp["ts_ms"] if lp else None
            t["pnl_bp"] = lp["excursion_bp"] if lp else None
            t["pnl_is_final"] = False
        else:
            t["current_price"] = t.get("simulated_exit_price")
            t["price_asof_ms"] = None
            t["pnl_bp"] = t.get("net_bp")
            t["pnl_is_final"] = True
        t["pnl_usd"] = bp_to_usd(t["pnl_bp"], t.get("simulated_position_usd"))
        t["pnl_try"] = usd_to_try(t["pnl_usd"])
    if closed:
        n = len(closed)
        num = lambda k: [t[k] for t in closed if t.get(k) is not None]  # noqa: E731
        avg = lambda v: (sum(v) / len(v)) if v else None                # noqa: E731
        priced = [t for t in closed if t.get("net_bp") is not None]
        for t in priced:
            t["pnl_usd"] = bp_to_usd(t["net_bp"], t.get("simulated_position_usd"))
            t["pnl_try"] = usd_to_try(t["pnl_usd"])
        best = max(priced, key=lambda t: t["net_bp"]) if priced else None
        worst = min(priced, key=lambda t: t["net_bp"]) if priced else None
        total_usd = sum(t["pnl_usd"] for t in priced) if priced else None
        wins = [t for t in priced if t["net_bp"] > 0]
        d["closed_stats"] = dict(
            n=n, gross_mid=avg(num("gross_bp")), gross_exec=avg(num("gross_executable_bp")),
            net=avg(num("net_bp")), penalty=avg(num("execution_penalty_bp")),
            mfe=avg(num("mfe_bp")), mae=avg(num("mae_bp")),
            duration=avg(num("duration_min")),
            total_usd=total_usd, total_try=usd_to_try(total_usd),
            win_rate=(len(wins) / len(priced) * 100) if priced else None,
            best=best, worst=worst)
    else:
        d["closed_stats"] = dict(n=0, best=None, worst=None, total_usd=None, total_try=None,
                                 win_rate=None)
    sp = [t["spread_bp"] for t in d["trades"] if t.get("spread_bp") is not None]
    d["spread"] = dict(n=len(sp), avg=(sum(sp) / len(sp)) if sp else None,
                       med=(sorted(sp)[len(sp) // 2] if sp else None))
    return d


CSS = """
:root{--bg:#0f1115;--card:#171a21;--line:#252a34;--tx:#e6e9ef;--dim:#8b93a7;
--ok:#3fb950;--warn:#d29922;--bad:#f85149;--acc:#58a6ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);
font:14px/1.5 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
padding:16px;padding-block:20px}
h1{font-size:17px;margin:0 0 4px}h2{font-size:13px;text-transform:uppercase;
letter-spacing:.08em;color:var(--dim);margin:22px 0 8px;font-weight:600}
.sub{color:var(--dim);font-size:12px;margin-bottom:18px}
.grid{display:grid;gap:10px;grid-template-columns:repeat(auto-fit,minmax(130px,1fr))}
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
.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}.dim{color:var(--dim)}
.pill{display:inline-block;padding:2px 8px;border-radius:99px;font-size:11px;font-weight:600}
.p-ok{background:rgba(63,185,80,.14);color:var(--ok)}
.p-bad{background:rgba(248,81,73,.14);color:var(--bad)}
.p-dim{background:rgba(139,147,167,.14);color:var(--dim)}
.note{background:rgba(88,166,255,.07);border:1px solid rgba(88,166,255,.25);
border-radius:9px;padding:11px 13px;font-size:12.5px;color:#b9c6da;margin:14px 0}
.bar{height:5px;background:var(--line);border-radius:3px;overflow:hidden;margin-top:6px}
.bar>i{display:block;height:100%;background:var(--acc)}
@media(max-width:560px){.v{font-size:19px}body{padding:12px}}
"""


def esc(x) -> str:
    return html.escape("" if x is None else str(x))


def ts(ms) -> str:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).strftime("%m-%d %H:%M")
    except Exception:  # noqa: BLE001
        return "-"


def num(v, n=2, suf=""):
    return '<span class="dim">-</span>' if v is None else f"{v:.{n}f}{suf}"


def svg_cum_chart(points: list[float], width: int = 640, height: int = 170) -> str:
    """Cumulative net PnL (TL) across closed trades, in order. Inline SVG, no JS/CDN."""
    if len(points) < 2:
        return '<div class=note>Grafik için en az 2 kapanan işlem gerekiyor.</div>'
    pad_l, pad_r, pad_t, pad_b = 50, 14, 14, 10
    w, h = width - pad_l - pad_r, height - pad_t - pad_b
    lo, hi = min(0.0, *points), max(0.0, *points)
    span = (hi - lo) or 1.0

    def x(i: int) -> float:
        return pad_l + w * i / (len(points) - 1)

    def y(v: float) -> float:
        return pad_t + h - (v - lo) / span * h

    poly = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(points))
    last_up = points[-1] >= 0
    color = "var(--ok)" if last_up else "var(--bad)"
    labels = "".join(
        f'<text x="4" y="{y(v)+3:.1f}" font-size="10" style="fill:var(--dim)">{v:,.0f}</text>'
        for v in sorted({round(lo), 0, round(hi)}))
    return (f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
            f'style="display:block">'
            f'<line x1="{pad_l}" y1="{y(0):.1f}" x2="{width-pad_r}" y2="{y(0):.1f}" '
            f'style="stroke:var(--line)" stroke-dasharray="3,3"/>'
            f'{labels}'
            f'<polyline points="{poly}" fill="none" style="stroke:{color}" stroke-width="2"/>'
            f'<circle cx="{x(len(points)-1):.1f}" cy="{y(points[-1]):.1f}" r="3.5" style="fill:{color}"/>'
            f'</svg>')


def svg_bar_chart(items: list[tuple[str, float]], width: int = 640, height: int = 170) -> str:
    """Per-trade net result (bp), in order. Inline SVG, no JS/CDN."""
    if not items:
        return '<div class=note>Henüz kapanan işlem yok.</div>'
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
            f'<title>{esc(label)}: {v:+.1f}bp</title></rect>')
    return (f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
            f'style="display:block">'
            f'<line x1="{pad_l}" y1="{zero_y:.1f}" x2="{width-pad_r}" y2="{zero_y:.1f}" '
            f'style="stroke:var(--line)"/>' + "".join(bars) + '</svg>')


STATUS_TR = {"OPEN": "AÇIK", "CLOSED": "KAPANDI"}
REASON_TR = {"nonfinite": "veri yetersiz", "below_threshold": "eşik altında",
             "short_dropped": "düşüş sinyali (atlandı)"}
ENTRY_TR = {"ask": "satış fiyatı", "mid_fallback": "orta fiyat (yedek)", "mid": "orta fiyat"}


def page(d: dict) -> str:
    if "error" in d:
        return (f"<!doctype html><meta charset=utf-8><style>{CSS}</style>"
                f"<h1>Kripto Sinyal Sistemi (Deneme)</h1>"
                f"<div class=note>Veritabanı henüz oluşmadı — sistem başlamamış olabilir.</div>")
    h = d.get("health") or {}
    age = h.get("data_age_s")
    alert = h.get("alert") or ""
    live = age is not None and age < 300 and not alert
    cs, sp = d["closed_stats"], d["spread"]

    rows = []
    for t in d["trades"][:40]:
        st = t["status"]
        pill = "p-ok" if st == "OPEN" else "p-dim"
        pnl = t.get("pnl_bp")
        cls = "ok" if (pnl or 0) > 0 else ("bad" if pnl is not None else "dim")
        entry_label = ENTRY_TR.get(t.get("entry_price_type"), t.get("entry_price_type"))
        approx = "" if t.get("pnl_is_final") else "~"
        cur_price = price_fmt(t.get("current_price"))
        if st == "OPEN" and t.get("current_price") is None:
            cur_price = '<span class="dim">veri bekleniyor</span>'
        rows.append(
            f"<tr><td>{ts(t['anchor_ms'])}</td><td><b>{esc(t['symbol'])}</b></td>"
            f"<td><span class='pill {pill}'>{STATUS_TR.get(st, esc(st))}</span></td>"
            f"<td>+{esc(t['decision_minute'])} dk</td>"
            f"<td>{price_fmt(t.get('entry_price'))}<div class=sub style='margin:0'>{esc(entry_label)}</div></td>"
            f"<td>{cur_price}</td>"
            f"<td>{num(t.get('comb'),4)}</td>"
            f"<td>{num(t.get('spread_bp'),2,'bp')}</td>"
            f"<td class={cls}>{approx}{num(pnl,1,'bp')}</td>"
            f"<td class={cls}>{approx}{num(t.get('pnl_usd'),2,'$')}</td>"
            f"<td class={cls}>{approx}{num(t.get('pnl_try'),0,' TL')}</td></tr>")
    trades_tbl = ("".join(rows) or
                  "<tr><td colspan=11 class=dim>henüz deneme işlemi yok — sistem 10 coin "
                  "üzerinde günde ortalama ~2,4 kez sinyal üretiyor</td></tr>")

    def trade_card(label: str, t: dict | None) -> str:
        if not t:
            return (f'<div class=card><div class=k>{label}</div>'
                     f'<div class=v dim>henüz yok</div></div>')
        net = t.get("net_bp")
        cls = "ok" if (net or 0) > 0 else "bad"
        return (f'<div class=card><div class=k>{label}</div>'
                f'<div class="v {cls}">{num(net,1,"bp")}</div>'
                f'<div class=sub style="margin:2px 0 0"><b>{esc(t["symbol"])}</b> · '
                f'{ts(t["anchor_ms"])} · {num(t.get("pnl_usd"),2,"$")} · '
                f'{num(t.get("pnl_try"),0," TL")}</div></div>')

    dec = []
    for r in d["recent"]:
        result = ("<span class='pill p-ok'>SİNYAL VERİLDİ</span>" if r["fired"]
                  else "<span class=dim>" + esc(REASON_TR.get(r["reason"], r["reason"])) + "</span>")
        dec.append(
            f"<tr><td>{ts(r['anchor_ms'])}</td><td><b>{esc(r['symbol'])}</b></td>"
            f"<td>+{esc(r['minute'])} dk</td><td>{num(r['raw_score'],4)}</td>"
            f"<td>{num(r['rv30_bp'],1)}</td><td>{num(r['comb'],4)}</td>"
            f"<td class=dim>{num(r['threshold'],4)}</td>"
            f"<td>{result}</td></tr>")

    hh = "".join(
        f"<tr><td>{esc(x['ts'])[5:16]}</td><td>{num(x['data_age_s'],0,'s')}</td>"
        f"<td>{esc(x['open_trades'])}</td>"
        f"<td>{'<span class=bad>'+esc(x['alert'])+'</span>' if x['alert'] else '<span class=ok>sorun yok</span>'}</td></tr>"
        for x in d["hist"])

    closed_sorted = sorted(
        (t for t in d["trades"] if t["status"] == "CLOSED" and t.get("pnl_bp") is not None),
        key=lambda t: t["anchor_ms"])
    cum, running = [], 0.0
    for t in closed_sorted:
        running += t.get("pnl_try") or 0.0
        cum.append(running)
    bar_items = [(t["symbol"], t["pnl_bp"]) for t in closed_sorted[-24:]]
    chart_cum = svg_cum_chart(cum)
    chart_bar = svg_bar_chart(bar_items)

    tot_cls = "ok" if (cs.get("total_try") or 0) >= 0 else "bad"
    prog = min(100, round(100 * d["decision_points"] / max(60, 1))) if d["decision_points"] < 60 else 100
    thr = num(d['recent'][0]['threshold'],6) if d['recent'] else '0.964580'
    return f"""<!doctype html><html lang=tr><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<meta http-equiv=refresh content=60><title>Kripto Sinyal Sistemi (Deneme)</title><style>{CSS}</style></head><body>
<h1>Kripto Alım Sinyali Sistemi — Deneme Aşaması
<span class="pill {'p-ok' if live else 'p-bad'}">{'ÇALIŞIYOR' if live else 'VERİ ESKİ'}</span></h1>
<div class=sub>sadece test amaçlı · gerçek para kullanılmıyor · izleme sayfası (salt okunur) · {esc(d['now'])} UTC ·
sayfa 60 saniyede bir kendini yeniler</div>

<div class=note><b>Bu sayfa ne anlatıyor?</b> Bir bilgisayar programı kripto para fiyatlarını sürekli izliyor ve
geçmiş verilere bakarak "şimdi almak mantıklı mı?" diye tahmin ediyor. Aşağıdaki her şey bir <b>deneme</b> —
sistem gerçekten para yatırmıyor, sadece "gerçekten alsaydım ne olurdu?" sorusunu simüle edip kaydediyor.
Amaç, gerçek parayla çalıştırılmadan önce sistemin güvenilir olup olmadığını görmek.</div>

<div class=grid>
<div class=card><div class=k>kontrol sayısı</div><div class=v>{d['decision_points']:,}</div>
<div class=bar><i style="width:{prog}%"></i></div></div>
<div class=card><div class=k>verilen sinyal</div><div class=v>{d['fired']}</div></div>
<div class=card><div class=k>deneme işlemi</div><div class=v>{d['trades_total']}</div></div>
<div class=card><div class=k>açık işlem</div><div class=v>{d['open_trades']}</div></div>
<div class=card><div class=k>kapanan işlem</div><div class=v>{d['closed_trades']}</div></div>
<div class=card><div class=k>veri yaşı</div><div class="v {'ok' if live else 'bad'}">
{num(age,0,'s')}</div></div>
<div class=card><div class=k>yeniden başlama</div><div class=v>{d['restarts']}</div></div>
<div class=card><div class=k>ort. fiyat farkı</div><div class=v>{num(sp['avg'],2,'bp')}</div></div>
</div>
<div class=sub style="margin-top:-8px">kontrol sayısı: sistem piyasayı kaç kez taradı · verilen sinyal: sistemin "şimdi al" dediği an sayısı ·
veri yaşı: en son fiyat verisi kaç saniye önce geldi · fiyat farkı: alış ve satış fiyatı arasındaki küçük maliyet (bp = baz puan, 100 bp = %1)</div>

{'<div class=note><b>UYARI:</b> '+esc(alert)+'</div>' if alert else ''}

<h2>İşlem kalitesi — gerçekte ne kadar kazanılır? ({cs['n']} kapanan işlem)</h2>
{'<div class=note>Henüz kapanan işlem yok. Bu sistem bir işlemi ortalama ~23,4 saat açık tutuyor, yani ilk sonuç ilk sinyalden yaklaşık bir gün sonra görünür.</div>' if cs['n']==0 else ''}
<div class=grid>
<div class=card><div class=k>kağıt üzerinde kâr</div><div class=v>{num(cs.get('gross_mid'),1,'bp')}</div></div>
<div class=card><div class=k>gerçekte uygulanabilir kâr</div><div class=v>{num(cs.get('gross_exec'),1,'bp')}</div></div>
<div class=card><div class=k>işlem maliyeti</div><div class=v>{num(cs.get('penalty'),1,'bp')}</div></div>
<div class=card><div class=k>maliyet sonrası net sonuç</div><div class=v>{num(cs.get('net'),1,'bp')}</div></div>
<div class=card><div class=k>ort. en iyi an (MFE)</div><div class=v>{num(cs.get('mfe'),1,'bp')}</div></div>
<div class=card><div class=k>ort. en kötü an (MAE)</div><div class=v>{num(cs.get('mae'),1,'bp')}</div></div>
<div class=card><div class=k>kazanma oranı</div><div class=v>{num(cs.get('win_rate'),0,'%')}</div></div>
<div class=card><div class=k>toplam net kâr/zarar</div><div class="v {tot_cls}">{num(cs.get('total_usd'),2,'$')}</div></div>
<div class=card><div class=k>toplam net kâr/zarar (TL)</div><div class="v {tot_cls}">{num(cs.get('total_try'),0,' TL')}</div></div>
</div>
<div class=sub style="margin-top:-8px">"maliyet sonrası net sonuç" asıl önemli sayıdır — {COST_BP}bp'lik tahmini işlem maliyeti düşüldükten sonra
gerçekte cepte kalan kısmı gösterir. TL karşılığı, işlem başına ${POSITION_SIZE_USD_DISPLAY} sanal pozisyon büyüklüğü ve
sabit {TRY_RATE:.2f} USD/TL kuruyla hesaplanır — gerçek para hareket etmez, sadece "olsaydı ne olurdu" simülasyonudur.</div>
{'<div class=note>Bu sayılar sadece <b>'+str(cs['n'])+'</b> işleme dayanıyor — güvenilir bir sonuç çıkarmak için yeterli değil. Sadece sistemin kayıtları doğru tuttuğunu gösterir, kârlı olduğunu değil.</div>' if 0 < cs['n'] < 5 else ''}

<h2>En iyi ve en kötü işlem</h2>
<div class=grid>
{trade_card('en yüksek kârlı işlem', cs.get('best'))}
{trade_card('en düşük kârlı (en zararlı) işlem', cs.get('worst'))}
</div>

<h2>Grafikler</h2>
<div class=card><div class=k>kümülatif net kâr/zarar (TL) — kapanan işlemler sırasıyla</div>
<div style="margin-top:8px">{chart_cum}</div></div>
<div class=card style="margin-top:10px"><div class=k>işlem başına net sonuç (bp) — son {len(bar_items)} kapanan işlem</div>
<div style="margin-top:8px">{chart_bar}</div></div>
<div class=sub style="margin-top:6px">yeşil = kârlı, kırmızı = zararlı · çubuğun üzerine gelince (mouse) coin adı ve bp değeri görünür</div>

<h2>Deneme işlemleri (simülasyon)</h2>
<div class=sub style="margin-top:-4px">"~" işaretli sayılar <b>açık</b> işlemler için anlık/tahmini değerdir (henüz kapanmadı,
işlem maliyeti düşülmemiş) · işaretsiz sayılar kapanmış, kesinleşmiş sonuçtur</div>
<div class=tw><table><thead><tr><th>zaman</th><th>coin</th><th>durum</th><th>geçen süre</th>
<th>giriş fiyatı</th><th>güncel/çıkış fiyatı</th><th>puan</th><th>fiyat farkı</th>
<th>kâr/zarar (bp)</th><th>kâr/zarar ($)</th><th>kâr/zarar (TL)</th></tr></thead>
<tbody>{trades_tbl}</tbody></table></div>

<h2>Son kontroller (sistem ne gördü?)</h2>
<div class=tw><table><thead><tr><th>zaman</th><th>coin</th><th>geçen süre</th><th>ham puan</th>
<th>oynaklık</th><th>birleşik puan</th><th>eşik</th><th>sonuç</th></tr></thead>
<tbody>{''.join(dec) or '<tr><td colspan=8 class=dim>henüz veri yok</td></tr>'}</tbody></table></div>

<h2>Sistem sağlığı</h2>
<div class=tw><table><thead><tr><th>zaman</th><th>veri yaşı</th><th>açık işlem</th><th>durum</th>
</tr></thead><tbody>{hh or '<tr><td colspan=4 class=dim>henüz veri yok</td></tr>'}</tbody></table></div>

<div class=note>Sistem, "birleşik puan" belirli bir çizgiyi (şu an <b>{thr}</b>) geçtiğinde alım sinyali veriyor.
Bu çizgi geçmiş verilerin en seçici diliminden (en üst %1) belirlendi — yani sistem bilerek az ve seçici sinyal
üretiyor, sık işlem yapmak amaçlanmıyor. Saatlerce hiç sinyal gelmemesi normaldir, arıza değildir. Sistem
çalıştırılmadan önce test edildi ve geçmiş sonuçlarla birebir eşleştiği doğrulandı.</div>
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
