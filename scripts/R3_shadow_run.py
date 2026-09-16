"""
EXP-107 -- the live D LONG-ONLY shadow runner.

RESEARCH ONLY / EXECUTION REHEARSAL.

    *** THIS PROCESS CANNOT PLACE AN ORDER. ***
It imports no exchange client, no signing code and no credential. It calls exactly two
Binance PUBLIC, UNAUTHENTICATED, READ-ONLY endpoints:
    GET /fapi/v1/klines          closed 1-minute bars
    GET /fapi/v1/ticker/bookTicker   best bid/ask
No API key, no secret, no trading permission, no withdrawal permission -- none are needed for
public market data, and none are read from the environment.

Usage:
    python R3_shadow_run.py run            live shadow run, until stopped
    python R3_shadow_run.py dryrun         replay the last N days of real bars, same code path
    python R3_shadow_run.py status         print health and current state

WHY stdlib-only market data: LightGBM 4.7.0 here dies with `access violation reading 0x0` in
any process that has also imported pandas or polars (EXP-094 section 5.9; hit again in gate 2).
This process must call LightGBM, so it reaches numpy through urllib + json and never imports
pandas or polars. That is a hard constraint, not a style choice.

Mechanics, all frozen and loaded from artifact/:
    anchors      every hour on the hour (UTC)
    decisions    at anchor + {10, 30, 60, 120, 240, 480} minutes
    gate         comb = 0.5*rank_model + 0.5*rank_rv30 >= PINNED_THRESHOLD
    side         LONG only; shorts are dropped after selection, never before
    dedup        earliest qualifying decision minute per (symbol, anchor)
    exit         anchor + 1440 minutes
    entry price  ask (executable) and mid (theoretical), both recorded
    exit price   bid (executable) and mid (theoretical), both recorded
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from d_features import DECISION_MINUTES, LIVE_WARMUP_BARS, NS, SYMBOLS  # noqa: E402
from shadow_engine import ShadowEngine  # noqa: E402

ROOT = HERE.parent
DB_PATH = ROOT / "shadow.db"
LOG_PATH = ROOT / "shadow.log"
FAPI = "https://fapi.binance.com"
BAR_MS = 60_000
HOLD_MIN = 1440
COST_BP = 14.38
POSITION_SIZE_USD = 1000.0          # notional, for reporting only -- no money moves
KEEP_BARS = LIVE_WARMUP_BARS + max(DECISION_MINUTES) + 400
HEALTH_EVERY_S = 300
STALE_AFTER_S = 240

# ---- hard safety assertion: nothing that could trade may be importable here ----------
for _forbidden in ("ccxt", "binance.client"):
    assert _forbidden not in sys.modules, f"{_forbidden} must not be imported in this process"


def log(msg: str) -> None:
    line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _get(path: str, params: dict, retries: int = 4) -> object:
    q = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{FAPI}{path}?{q}"
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "exp107-shadow/1.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read().decode())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            if i == retries - 1:
                raise
            time.sleep(1.5 * (i + 1))
            log(f"WARN retry {i+1}/{retries} {path}: {e}")
    raise RuntimeError("unreachable")


# ============================================================ database
SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
  anchor_ms INTEGER, minute INTEGER, symbol TEXT, raw_score REAL, cal_score REAL,
  rv30_bp REAL, rank_model REAL, rank_rv30 REAL, comb REAL, threshold REAL,
  side REAL, fired INTEGER, reason TEXT, evaluated_at TEXT,
  PRIMARY KEY (anchor_ms, minute, symbol));
CREATE TABLE IF NOT EXISTS shadow_trades (
  trade_id TEXT PRIMARY KEY, anchor_ms INTEGER, symbol TEXT, direction TEXT,
  signal_ts TEXT, entry_ts TEXT, decision_minute INTEGER,
  model_score REAL, cal_score REAL, rv30_bp REAL, confidence REAL, comb REAL,
  entry_mid REAL, entry_bid REAL, entry_ask REAL, spread_bp REAL,
  entry_price_type TEXT, simulated_entry_price REAL,
  simulated_cost_bp REAL, simulated_position_usd REAL, signal_source TEXT,
  close_due_ms INTEGER, status TEXT,
  exit_ts TEXT, exit_mid REAL, exit_bid REAL, exit_ask REAL, exit_price_type TEXT,
  simulated_exit_price REAL, duration_min REAL,
  mfe_bp REAL, mae_bp REAL, first_target_bp REAL, first_target_ts TEXT,
  gross_bp REAL, gross_executable_bp REAL, net_bp REAL, execution_penalty_bp REAL,
  market_btc_bp REAL, neutral_bp REAL);
CREATE TABLE IF NOT EXISTS path (
  trade_id TEXT, ts_ms INTEGER, mid REAL, excursion_bp REAL,
  PRIMARY KEY (trade_id, ts_ms));
CREATE TABLE IF NOT EXISTS health (
  ts TEXT PRIMARY KEY, alive INTEGER, last_bar_ts TEXT, data_age_s REAL,
  last_signal_ts TEXT, last_trade_ts TEXT, open_trades INTEGER,
  db_writable INTEGER, api_ok INTEGER, alert TEXT);
CREATE TABLE IF NOT EXISTS restarts (ts TEXT PRIMARY KEY, note TEXT);
"""


def db() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.executescript(SCHEMA)
    return c


# ============================================================ market data (stdlib only)
class Bars:
    """Rolling closed-1m bars for all symbols on a shared minute grid. numpy only."""

    def __init__(self) -> None:
        self.grid = np.zeros(0, dtype=np.int64)
        self.C = np.zeros((0, NS)); self.QV = np.zeros((0, NS)); self.TB = np.zeros((0, NS))
        self.pos: dict[int, int] = {}

    @staticmethod
    def _fetch(symbol: str, limit: int, end_ms: int | None = None) -> list:
        p = {"symbol": symbol, "interval": "1m", "limit": min(limit, 1500)}
        if end_ms:
            p["endTime"] = end_ms
        return _get("/fapi/v1/klines", p)

    def backfill(self, bars: int = None) -> None:
        bars = KEEP_BARS if bars is None else bars
        per = {}
        for s in SYMBOLS:
            rows, end = [], None
            while len(rows) < bars:
                chunk = self._fetch(s, 1500, end)
                if not chunk:
                    break
                rows = chunk + rows
                end = int(chunk[0][0]) - 1
                if len(chunk) < 1500:
                    break
            per[s] = rows[-bars:]
            log(f"backfill {s}: {len(per[s])} bars")
        self._rebuild(per)

    def _rebuild(self, per: dict) -> None:
        # a bar is only usable once CLOSED: Binance returns the forming bar last, so drop it
        now = int(time.time() * 1000)
        grids = []
        for s in SYMBOLS:
            ts = np.array([int(r[0]) for r in per[s] if int(r[0]) + BAR_MS <= now],
                          dtype=np.int64)
            grids.append(ts)
        grid = np.unique(np.concatenate(grids)) if grids else np.zeros(0, np.int64)
        gp = {int(t): i for i, t in enumerate(grid)}
        C = np.full((len(grid), NS), np.nan)
        QV = np.full((len(grid), NS), np.nan)
        TB = np.full((len(grid), NS), np.nan)
        for j, s in enumerate(SYMBOLS):
            for r in per[s]:
                i = gp.get(int(r[0]))
                if i is not None:
                    C[i, j] = float(r[4]); QV[i, j] = float(r[7]); TB[i, j] = float(r[10])
        self.grid, self.C, self.QV, self.TB, self.pos = grid, C, QV, TB, gp

    def poll(self) -> int:
        """Fetch the most recent bars and merge. Returns the newest closed bar timestamp.

        Buffer depth must cover the deepest evaluation: a decision at minute t reads back
        LIVE_WARMUP_BARS from row (anchor + t), so KEEP leaves headroom above
        LIVE_WARMUP_BARS + max(DECISION_MINUTES) for the anchor to sit in.
        """
        per = {}
        for j, s in enumerate(SYMBOLS):
            by_ts: dict[int, list] = {}
            for i, t in enumerate(self.grid.tolist()):       # existing bars, kline-shaped
                by_ts[int(t)] = [int(t), 0, 0, 0, self.C[i, j], 0, 0,
                                 self.QV[i, j], 0, 0, self.TB[i, j]]
            for r in self._fetch(s, 60):                     # newest bars win
                by_ts[int(r[0])] = r
            per[s] = [by_ts[k] for k in sorted(by_ts)][-KEEP_BARS:]
        self._rebuild(per)
        return int(self.grid[-1]) if len(self.grid) else 0


def book(symbol: str) -> tuple[float, float, str]:
    """Best bid/ask. Falls back to the last close, MARKED as such and never reported as real."""
    try:
        t = _get("/fapi/v1/ticker/bookTicker", {"symbol": symbol})
        b, a = float(t["bidPrice"]), float(t["askPrice"])
        if b > 0 and a >= b:
            return b, a, "bid_ask"
    except Exception as e:  # noqa: BLE001
        log(f"WARN bookTicker {symbol}: {e}")
    k = _get("/fapi/v1/klines", {"symbol": symbol, "interval": "1m", "limit": 1})
    p = float(k[-1][4])
    return p, p, "mid_fallback"


# ============================================================ runner
class Runner:
    def __init__(self) -> None:
        self.eng = ShadowEngine.load()
        self.bars = Bars()
        self.con = db()
        self.last_health = 0.0
        log(f"artifact loaded  threshold={self.eng.threshold:.15f}  "
            f"fold={self.eng.meta['fold']}")

    # ---- one (anchor, minute) decision point --------------------------------
    def evaluate_point(self, anchor_ms: int, minute: int) -> None:
        ap = self.bars.pos.get(anchor_ms)
        if ap is None:
            return
        lo, hi = ap + minute - LIVE_WARMUP_BARS + 1, ap + minute + 1
        if lo < 0 or hi > len(self.bars.grid):
            return
        ds = self.eng.evaluate(self.bars.C[lo:hi], self.bars.QV[lo:hi],
                               self.bars.TB[lo:hi], ap - lo, anchor_ms, minute)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.con.executemany(
            "INSERT OR REPLACE INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(d.anchor_ms, d.minute, d.symbol, d.raw_score, d.cal_score, d.rv30_bp,
              d.rank_model, d.rank_rv30, d.comb, d.threshold, d.side,
              int(d.fired), d.reason, now) for d in ds])
        self.con.commit()
        for d in ds:
            if d.fired:
                self.open_shadow(d)

    def open_shadow(self, d) -> None:
        tid = f"{d.symbol}_{d.anchor_ms}"
        cur = self.con.execute("SELECT 1 FROM shadow_trades WHERE trade_id=?", (tid,))
        if cur.fetchone():
            return                      # earliest-minute dedup, enforced in the DB
        bid, ask, kind = book(d.symbol)
        mid = (bid + ask) / 2
        spread_bp = (ask - bid) / mid * 1e4 if mid > 0 else float("nan")
        now = datetime.now(timezone.utc)
        self.con.execute(
            "INSERT INTO shadow_trades (trade_id,anchor_ms,symbol,direction,signal_ts,"
            "entry_ts,decision_minute,model_score,cal_score,rv30_bp,confidence,comb,"
            "entry_mid,entry_bid,entry_ask,spread_bp,entry_price_type,"
            "simulated_entry_price,simulated_cost_bp,simulated_position_usd,signal_source,"
            "close_due_ms,status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, d.anchor_ms, d.symbol, "LONG",
             datetime.fromtimestamp(d.anchor_ms / 1000, timezone.utc).isoformat(),
             now.isoformat(timespec="seconds"), d.minute, d.raw_score, d.cal_score,
             d.rv30_bp, abs(d.raw_score - 0.5), d.comb,
             mid, bid, ask, spread_bp, kind, ask, COST_BP, POSITION_SIZE_USD,
             "EXP-107-D-LONG", d.anchor_ms + HOLD_MIN * BAR_MS, "OPEN"))
        self.con.commit()
        log(f"SHADOW ENTRY {d.symbol} anchor={datetime.fromtimestamp(d.anchor_ms/1000, timezone.utc):%Y-%m-%d %H:%M} "
            f"t+{d.minute} comb={d.comb:.4f} ask={ask} spread={spread_bp:.2f}bp [{kind}]")

    # ---- path tracking and exits --------------------------------------------
    def update_open(self) -> None:
        rows = self.con.execute(
            "SELECT trade_id,symbol,anchor_ms,entry_mid,simulated_entry_price,close_due_ms "
            "FROM shadow_trades WHERE status='OPEN'").fetchall()
        if not rows:
            return
        now_ms = int(time.time() * 1000)
        for tid, sym, anchor, emid, eprice, due in rows:
            j = SYMBOLS.index(sym)
            i = len(self.bars.grid) - 1
            if i < 0:
                continue
            mid = float(self.bars.C[i, j])
            if np.isfinite(mid) and emid > 0:
                exc = (mid / emid - 1.0) * 1e4
                self.con.execute("INSERT OR REPLACE INTO path VALUES (?,?,?,?)",
                                 (tid, int(self.bars.grid[i]), mid, exc))
            if now_ms >= due:
                self.close_shadow(tid, sym, anchor, emid, eprice, due)
        self.con.commit()

    def close_shadow(self, tid, sym, anchor, emid, eprice, due) -> None:
        bid, ask, kind = book(sym)
        mid = (bid + ask) / 2
        p = self.con.execute(
            "SELECT excursion_bp, ts_ms FROM path WHERE trade_id=? ORDER BY ts_ms", (tid,)
        ).fetchall()
        exc = [r[0] for r in p] or [0.0]
        mfe, mae = max(exc), min(exc)
        ft, ft_ts = None, None
        for e, ts in p:
            if e >= 40.0:
                ft, ft_ts = 40.0, datetime.fromtimestamp(ts / 1000, timezone.utc).isoformat()
                break
        gross_mid = (mid / emid - 1.0) * 1e4 if emid > 0 else float("nan")
        gross_exec = (bid / eprice - 1.0) * 1e4 if eprice > 0 else float("nan")
        net = gross_exec - COST_BP
        pen = gross_mid - gross_exec
        now = datetime.now(timezone.utc)
        self.con.execute(
            "UPDATE shadow_trades SET status='CLOSED', exit_ts=?, exit_mid=?, exit_bid=?, "
            "exit_ask=?, exit_price_type=?, simulated_exit_price=?, duration_min=?, "
            "mfe_bp=?, mae_bp=?, first_target_bp=?, first_target_ts=?, gross_bp=?, "
            "gross_executable_bp=?, net_bp=?, execution_penalty_bp=? WHERE trade_id=?",
            (now.isoformat(timespec="seconds"), mid, bid, ask, kind, bid,
             (now.timestamp() * 1000 - anchor) / 60_000.0, mfe, mae, ft, ft_ts,
             gross_mid, gross_exec, net, pen, tid))
        self.con.commit()
        log(f"SHADOW EXIT  {sym} gross_mid={gross_mid:+.1f} gross_exec={gross_exec:+.1f} "
            f"net={net:+.1f} penalty={pen:+.1f} MFE={mfe:+.1f} MAE={mae:+.1f} [{kind}]")

    # ---- health -------------------------------------------------------------
    def health(self, api_ok: bool = True) -> None:
        now = datetime.now(timezone.utc)
        last_bar = int(self.bars.grid[-1]) if len(self.bars.grid) else 0
        age = (now.timestamp() * 1000 - last_bar) / 1000 if last_bar else 1e9
        ls = self.con.execute("SELECT MAX(evaluated_at) FROM decisions WHERE fired=1").fetchone()[0]
        lt = self.con.execute("SELECT MAX(entry_ts) FROM shadow_trades").fetchone()[0]
        op = self.con.execute("SELECT COUNT(*) FROM shadow_trades WHERE status='OPEN'").fetchone()[0]
        alerts = []
        if age > STALE_AFTER_S:
            alerts.append(f"STALE_DATA {age:.0f}s")
        if not api_ok:
            alerts.append("API_ERROR")
        alert = "; ".join(alerts)
        self.con.execute("INSERT OR REPLACE INTO health VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (now.isoformat(timespec="seconds"), 1,
                          datetime.fromtimestamp(last_bar / 1000, timezone.utc).isoformat()
                          if last_bar else "", age, ls or "", lt or "", op, 1,
                          int(api_ok), alert))
        self.con.commit()
        if alert:
            log(f"ALERT {alert}")

    # ---- main loop ----------------------------------------------------------
    def run(self) -> None:
        self.con.execute("INSERT OR REPLACE INTO restarts VALUES (?,?)",
                         (datetime.now(timezone.utc).isoformat(timespec="seconds"),
                          "process start"))
        self.con.commit()
        log("backfilling...")
        self.bars.backfill()
        log(f"ready. bars={len(self.bars.grid)}  "
            f"last={datetime.fromtimestamp(int(self.bars.grid[-1])/1000, timezone.utc)}")
        seen: set[tuple] = set()
        while True:
            api_ok = True
            try:
                newest = self.bars.poll()
            except Exception as e:  # noqa: BLE001
                log(f"ERROR poll: {e}")
                api_ok, newest = False, 0
            if newest:
                for t in DECISION_MINUTES:
                    anchor = newest - t * BAR_MS
                    if anchor % 3_600_000 != 0:
                        continue
                    if (anchor, t) in seen:
                        continue
                    seen.add((anchor, t))
                    try:
                        self.evaluate_point(anchor, t)
                    except Exception as e:  # noqa: BLE001
                        log(f"ERROR evaluate {anchor} t+{t}: {e}")
                try:
                    self.update_open()
                except Exception as e:  # noqa: BLE001
                    log(f"ERROR update_open: {e}")
            if time.time() - self.last_health >= HEALTH_EVERY_S:
                self.health(api_ok)
                self.last_health = time.time()
            time.sleep(20)


def status() -> None:
    con = db()
    for q, lab in (("SELECT COUNT(*) FROM decisions", "decision points"),
                   ("SELECT COUNT(*) FROM decisions WHERE fired=1", "fired"),
                   ("SELECT COUNT(*) FROM shadow_trades", "shadow trades"),
                   ("SELECT COUNT(*) FROM shadow_trades WHERE status='OPEN'", "open"),
                   ("SELECT COUNT(*) FROM shadow_trades WHERE status='CLOSED'", "closed"),
                   ("SELECT COUNT(*) FROM restarts", "restarts")):
        print(f"  {lab:16s} {con.execute(q).fetchone()[0]}")
    h = con.execute("SELECT * FROM health ORDER BY ts DESC LIMIT 1").fetchone()
    print(f"  last health      {h}" if h else "  last health      none")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "run":
        Runner().run()
    elif cmd == "status":
        status()
    else:
        print(__doc__)
