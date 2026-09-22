"""
EXP-124 -- decision memory.

RESEARCH / PAPER ONLY. Records every decision this system makes, append-only, in its OWN
SQLite database (`intelligence.db` -- never `shadow.db`, isolation contract,
ARCHITECTURE_PLAN.md section 1). Per the original brief section 21 ("Sistem geçmiş kararlarını
unutmayacak") and the task instruction "Do not allow future decisions to overwrite historical
decisions": `write_decision()` only ever INSERTs, on a PRIMARY KEY that makes a duplicate
`decision_id` fail loudly (`sqlite3.IntegrityError`) rather than silently overwrite. The ONLY
function that may change an already-written row is `update_outcome()`, and it may only touch
the `outcome_*` columns (NULL until a position resolves) -- it can never alter what was decided
or why at the time.

`similar_setups()` exists to answer "the last N similar setups" (brief section 21) -- it is
explicitly advisory: the brief itself warns "geçmiş sonucu geleceğin garantisi olarak
kullanma," so nothing in this module or its callers may treat its output as a guarantee.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parents[2] / "intelligence.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
  decision_id TEXT PRIMARY KEY,
  open_time INTEGER NOT NULL,
  symbol TEXT NOT NULL,
  exp107_status TEXT, exp107_fired INTEGER, exp107_side TEXT, exp107_comb REAL,
  direction_estimate TEXT, direction_confidence REAL,
  confirmation_label TEXT, confirmation_score REAL,
  supporting_evidence TEXT,      -- JSON list of strings
  conflicting_evidence TEXT,      -- JSON list of strings
  opportunity_state TEXT,
  position_state TEXT,
  previous_decision_id TEXT,       -- links to the prior decision for this symbol, if any
  decision TEXT NOT NULL,            -- ENTER | HOLD | WAIT | EXIT | REDUCE | IGNORE
  reason TEXT,
  risk_note TEXT,
  confidence REAL,
  created_at TEXT NOT NULL,
  -- outcome columns: NULL/0 until resolved, filled ONLY by update_outcome()
  outcome_resolved INTEGER NOT NULL DEFAULT 0,
  outcome_correct INTEGER,
  outcome_pnl_bp REAL,
  outcome_mfe_bp REAL,
  outcome_mae_bp REAL,
  outcome_resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_decisions_symbol_time ON decisions(symbol, open_time);
CREATE INDEX IF NOT EXISTS idx_decisions_symbol_label ON decisions(symbol, confirmation_label);
"""

_COLUMNS = (
    "decision_id", "open_time", "symbol", "exp107_status", "exp107_fired", "exp107_side",
    "exp107_comb", "direction_estimate", "direction_confidence", "confirmation_label",
    "confirmation_score", "supporting_evidence", "conflicting_evidence", "opportunity_state",
    "position_state", "previous_decision_id", "decision", "reason", "risk_note", "confidence",
    "created_at")


@dataclass(frozen=True)
class DecisionRecord:
    decision_id: str
    open_time: int
    symbol: str
    exp107_status: str
    exp107_fired: bool
    exp107_side: str | None
    exp107_comb: float | None
    direction_estimate: str
    direction_confidence: float
    confirmation_label: str
    confirmation_score: float
    supporting_evidence: list[str] = field(default_factory=list)
    conflicting_evidence: list[str] = field(default_factory=list)
    opportunity_state: str = ""
    position_state: str = ""
    previous_decision_id: str | None = None
    decision: str = ""
    reason: str = ""
    risk_note: str = ""
    confidence: float = float("nan")
    created_at: str = ""


def connect(path: Path = DEFAULT_DB) -> sqlite3.Connection:
    con = sqlite3.connect(path, timeout=30)
    con.executescript(SCHEMA)
    return con


def write_decision(con: sqlite3.Connection, record: DecisionRecord) -> None:
    """INSERT only. A duplicate `decision_id` raises `sqlite3.IntegrityError` rather than
    silently overwriting -- the "never overwrite historical decisions" rule is enforced at the
    database layer, not left to caller discipline."""
    placeholders = ",".join("?" * len(_COLUMNS))
    values = (
        record.decision_id, record.open_time, record.symbol, record.exp107_status,
        int(record.exp107_fired), record.exp107_side, record.exp107_comb,
        record.direction_estimate, record.direction_confidence, record.confirmation_label,
        record.confirmation_score, json.dumps(record.supporting_evidence),
        json.dumps(record.conflicting_evidence), record.opportunity_state,
        record.position_state, record.previous_decision_id, record.decision, record.reason,
        record.risk_note, record.confidence, record.created_at)
    con.execute(f"INSERT INTO decisions ({','.join(_COLUMNS)}) VALUES ({placeholders})", values)
    con.commit()


def update_outcome(con: sqlite3.Connection, decision_id: str, correct: bool, pnl_bp: float,
                   mfe_bp: float, mae_bp: float, resolved_at: str) -> None:
    """The ONLY function permitted to change an already-written row -- and only its `outcome_*`
    columns. Raises ValueError (not a silent no-op) if `decision_id` doesn't exist, so a caller
    never mistakes "nothing happened" for "outcome recorded"."""
    cur = con.execute(
        "UPDATE decisions SET outcome_resolved=1, outcome_correct=?, outcome_pnl_bp=?, "
        "outcome_mfe_bp=?, outcome_mae_bp=?, outcome_resolved_at=? WHERE decision_id=?",
        (int(correct), pnl_bp, mfe_bp, mae_bp, resolved_at, decision_id))
    con.commit()
    if cur.rowcount == 0:
        raise ValueError(f"no decision with decision_id={decision_id!r} to update")


def get_decision(con: sqlite3.Connection, decision_id: str) -> dict | None:
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM decisions WHERE decision_id=?", (decision_id,)).fetchone()
    return dict(row) if row else None


def last_decision_for_symbol(con: sqlite3.Connection, symbol: str) -> dict | None:
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT * FROM decisions WHERE symbol=? ORDER BY open_time DESC LIMIT 1",
        (symbol,)).fetchone()
    return dict(row) if row else None


def similar_setups(con: sqlite3.Connection, symbol: str, confirmation_label: str,
                   limit: int = 20) -> list[dict]:
    """The last (up to) `limit` RESOLVED decisions for this symbol at this confirmation label,
    most recent first. Advisory input only -- see module docstring."""
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT * FROM decisions WHERE symbol=? AND confirmation_label=? "
        "AND outcome_resolved=1 ORDER BY open_time DESC LIMIT ?",
        (symbol, confirmation_label, limit)).fetchall()
    return [dict(r) for r in rows]


def all_decisions_for_symbol(con: sqlite3.Connection, symbol: str) -> list[dict]:
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT * FROM decisions WHERE symbol=? ORDER BY open_time ASC", (symbol,)).fetchall()
    return [dict(r) for r in rows]
