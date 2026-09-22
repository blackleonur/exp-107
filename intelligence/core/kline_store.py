"""
EXP-124 -- append-only local kline archive.

RESEARCH / PAPER ONLY. This is NOT a substitute for the 2020-2026 historical archive the
original brief asked for (EXP-124/CODEBASE_MAP.md Blocker #4 -- that archive does not exist
anywhere on this machine and is not fabricated here). It is instead an honestly-labeled,
growing record that starts accumulating real 1-minute bars from the moment this system first
runs. Every consumer of this store (replay tests, feature-registry stability checks, the
eventual REPORT.md) must read and report `coverage()` -- the true observed date range and
sample size -- rather than assume any particular depth of history exists.

Separate database file from EXP-107's own shadow.db (isolation contract, ARCHITECTURE_PLAN.md
section 1) -- this module never opens, reads or writes shadow.db.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parents[2] / "intelligence.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS klines_1m (
  symbol TEXT, open_time INTEGER, open REAL, high REAL, low REAL, close REAL,
  volume REAL, quote_volume REAL, taker_buy_quote_volume REAL,
  PRIMARY KEY (symbol, open_time)
);
CREATE INDEX IF NOT EXISTS idx_klines_1m_time ON klines_1m(open_time);
"""


@dataclass(frozen=True)
class Coverage:
    symbol: str
    n_bars: int
    first_open_time: int | None
    last_open_time: int | None

    @property
    def is_empty(self) -> bool:
        return self.n_bars == 0


def connect(path: Path = DEFAULT_DB) -> sqlite3.Connection:
    con = sqlite3.connect(path, timeout=30)
    con.executescript(SCHEMA)
    return con


def append_bars(con: sqlite3.Connection, symbol: str, rows: list[list]) -> int:
    """rows: raw Binance kline rows (as returned by BinanceClient.klines / MarketBuffer's
    internal per-symbol lists). Only bars whose close time has passed are ever handed here by
    convention (MarketBuffer already enforces that) -- this function does not re-check it, so
    callers must not pass an in-progress bar.

    Returns the number of NEW rows inserted (existing (symbol, open_time) rows are left
    untouched -- an archive entry, once written, is never silently overwritten)."""
    before = con.total_changes
    con.executemany(
        "INSERT OR IGNORE INTO klines_1m "
        "(symbol, open_time, open, high, low, close, volume, quote_volume, "
        " taker_buy_quote_volume) VALUES (?,?,?,?,?,?,?,?,?)",
        [(symbol, int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]),
          float(r[5]), float(r[7]), float(r[10])) for r in rows])
    con.commit()
    return con.total_changes - before


def coverage(con: sqlite3.Connection, symbol: str) -> Coverage:
    row = con.execute(
        "SELECT COUNT(*), MIN(open_time), MAX(open_time) FROM klines_1m WHERE symbol=?",
        (symbol,)).fetchone()
    n, lo, hi = row
    return Coverage(symbol=symbol, n_bars=int(n or 0), first_open_time=lo, last_open_time=hi)


def load_range(con: sqlite3.Connection, symbol: str, start_ms: int | None = None,
               end_ms: int | None = None) -> list[tuple]:
    """Returns rows ordered by open_time: (open_time, open, high, low, close, volume,
    quote_volume, taker_buy_quote_volume)."""
    q = "SELECT open_time, open, high, low, close, volume, quote_volume, " \
        "taker_buy_quote_volume FROM klines_1m WHERE symbol=?"
    params: list = [symbol]
    if start_ms is not None:
        q += " AND open_time >= ?"
        params.append(start_ms)
    if end_ms is not None:
        q += " AND open_time <= ?"
        params.append(end_ms)
    q += " ORDER BY open_time"
    return con.execute(q, params).fetchall()
