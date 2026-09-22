"""
EXP-124 -- read-only adapter for EXP-107's own signal.

RESEARCH / PAPER ONLY. This is the single seam between the new intelligence/ package and
EXP-107 (ARCHITECTURE_PLAN.md section 3.1). It:

  1. Attempts scripts/shadow_engine.ShadowEngine.load() UNCHANGED -- no copy, no
     reimplementation. If loading fails for ANY reason (the trained booster_*.pkl /
     isotonic_*.pkl artifact files are currently absent from this repository --
     CODEBASE_MAP.md Blocker #2 -- but this also covers a missing dependency, a corrupted
     file, or any other future failure), status becomes UNAVAILABLE and the error is recorded.
     NO SUBSTITUTE SCORE IS EVER FABRICATED. Loading is attempted ONCE per process lifetime --
     it does not retry every call pretending the files might appear, matching the "a crash
     gets a service restart only" discipline already established in the repository's own
     PRE_DECLARATION.md. A process restart is what picks up newly supplied artifact files.

  2. If loaded, calls ShadowEngine.evaluate() VERBATIM -- exactly as scripts/R2_gate2_replay.py
     and scripts/R3_shadow_run.py already do -- and wraps each returned Decision in a read-only
     Exp107Signal record. EXP-107's own decision logic is not touched, copied, or altered in
     any way.

  3. Separately, and always available regardless of (1)/(2): a read-only tail of EXP-107's own
     shadow_trades table from shadow.db, in mode=ro, exactly like scripts/R5_dashboard.py's
     access pattern. This never requires the model artifact -- only a shadow.db that a running
     R3_shadow_run.py has produced.

Nothing in this module writes to scripts/, artifact/, or shadow.db. Nothing in this module can
place, modify, or cancel an order -- it holds no exchange credential and imports no exchange
client.
"""
from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"


@dataclass(frozen=True)
class Exp107Signal:
    """One symbol's read at one (anchor, minute) decision point. `status` governs everything
    else: when it is "UNAVAILABLE", every other field except `symbol` and `error` is None --
    never a fabricated number standing in for a real score."""
    status: str                      # "OK" | "UNAVAILABLE"
    symbol: str | None = None
    anchor_ms: int | None = None
    minute: int | None = None
    raw_score: float | None = None
    cal_score: float | None = None
    rv30_bp: float | None = None
    comb: float | None = None
    threshold: float | None = None
    side: str | None = None            # "LONG" | "SHORT"
    fired: bool | None = None
    reason: str | None = None
    error: str | None = None             # populated only when status == "UNAVAILABLE"

    @property
    def is_long_fire(self) -> bool:
        """True only for a genuine, loaded, fired LONG signal. This is the ONLY function in the
        entire intelligence/ package permitted to answer "did EXP-107 say ENTER" -- every other
        module must go through an Exp107Signal, never re-derive this condition itself."""
        return self.status == "OK" and bool(self.fired) and self.side == "LONG"


class Exp107SignalProvider:
    """Loads scripts/shadow_engine.ShadowEngine at most once. Safe to hold for a process's
    entire lifetime; safe to construct many times (each attempts its own single load)."""

    def __init__(self, scripts_dir: Path = SCRIPTS_DIR) -> None:
        self._scripts_dir = scripts_dir
        self._engine = None
        self._status = "UNTRIED"
        self._error: str | None = None

    def _ensure_loaded(self) -> None:
        if self._status != "UNTRIED":
            return
        added = str(self._scripts_dir) not in sys.path
        if added:
            sys.path.insert(0, str(self._scripts_dir))
        try:
            from shadow_engine import ShadowEngine  # unmodified import of EXP-107's own module
            self._engine = ShadowEngine.load()
            self._status = "OK"
        except Exception as e:  # noqa: BLE001 -- any failure is reported, never papered over
            self._engine = None
            self._status = "UNAVAILABLE"
            self._error = f"{type(e).__name__}: {e}"

    @property
    def status(self) -> str:
        self._ensure_loaded()
        return self._status

    @property
    def error(self) -> str | None:
        self._ensure_loaded()
        return self._error

    def evaluate(self, C, QV, TB, apos: int, anchor_ms: int, minute: int) -> list[Exp107Signal]:
        """Wraps ShadowEngine.evaluate() UNCHANGED. On UNAVAILABLE, returns one
        Exp107Signal(status="UNAVAILABLE", ...) per symbol -- never a fabricated OK result."""
        self._ensure_loaded()
        if self._status != "OK":
            from intelligence.core.market_buffer import SYMBOLS
            return [Exp107Signal(status="UNAVAILABLE", symbol=s, error=self._error)
                    for s in SYMBOLS]
        decisions = self._engine.evaluate(C, QV, TB, apos, anchor_ms, minute)
        return [Exp107Signal(
            status="OK", symbol=d.symbol, anchor_ms=d.anchor_ms, minute=d.minute,
            raw_score=d.raw_score, cal_score=d.cal_score, rv30_bp=d.rv30_bp, comb=d.comb,
            threshold=d.threshold, side=("LONG" if d.side > 0 else "SHORT"), fired=d.fired,
            reason=d.reason) for d in decisions]


def recent_shadow_trades(shadow_db_path: Path, limit: int = 50) -> list[dict]:
    """Read-only tail of EXP-107's own shadow_trades table, mode=ro (same pattern as
    scripts/R5_dashboard.py). Returns [] if shadow.db does not exist yet -- never fabricated
    rows. Works independently of whether the model artifact is loadable, since it only reads
    what a running R3_shadow_run.py has already recorded."""
    if not Path(shadow_db_path).exists():
        return []
    con = sqlite3.connect(f"file:{shadow_db_path}?mode=ro", uri=True, timeout=10)
    try:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM shadow_trades ORDER BY anchor_ms DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []  # e.g. the table doesn't exist yet -- an empty, not-yet-initialized db
    finally:
        con.close()
