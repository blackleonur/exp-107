"""
EXP-107 -- the D LONG-ONLY shadow decision core.

RESEARCH ONLY. This module imports NO order-placing function and holds NO credential.
It cannot place a trade. NO PRODUCTION / PAPER STRATEGY CHANGES MADE.

ONE decision core, TWO drivers:
    * R2_gate2_replay.py  feeds it historical bars  -> must reproduce EXP-105 exactly
    * R3_shadow_run.py    feeds it live bars        -> emits shadow trades

The replay driver is not a test harness written alongside the engine; it drives the same
`ShadowEngine.evaluate()` the live run uses. That is deliberate -- EXP-096 built a separate
forward harness, it passed its own verification, and EXP-097 found it fired zero signals.

Everything the engine needs is LOADED from the frozen artifact. It recomputes no threshold and
refits nothing.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from d_features import (DECISION_MINUTES, LIVE_WARMUP_BARS, NS, SYMBOLS,  # noqa: F401
                        compute_block, rv30)

ARTIFACT = Path(__file__).resolve().parents[1] / "artifact"


@dataclass
class Decision:
    """One evaluated decision point. `fired` is the only thing that becomes a shadow trade."""
    anchor_ms: int
    minute: int
    symbol: str
    raw_score: float
    cal_score: float
    rv30_bp: float
    rank_model: float
    rank_rv30: float
    comb: float
    threshold: float
    side: float
    fired: bool
    reason: str = ""


@dataclass
class ShadowEngine:
    """Loads the frozen artifact once and evaluates decision points. Stateless per call."""
    boosters: dict = field(default_factory=dict)
    isotonics: dict = field(default_factory=dict)
    ref_model: np.ndarray = None
    ref_rv30: np.ndarray = None
    threshold: float = None
    meta: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path = ARTIFACT) -> "ShadowEngine":
        import joblib
        meta = json.loads((path / "artifact.json").read_text())
        e = cls(
            boosters={t: joblib.load(path / f"booster_{t}.pkl") for t in DECISION_MINUTES},
            isotonics={t: joblib.load(path / f"isotonic_{t}.pkl") for t in DECISION_MINUTES},
            ref_model=np.load(path / "ref_model.npy"),
            ref_rv30=np.load(path / "ref_rv30.npy"),
            threshold=float(meta["PINNED_THRESHOLD"]),
            meta=meta)
        assert e.meta["weights"] == [0.5, 0.5], "artifact weights are not the frozen 0.5/0.5"
        return e

    # ---- the decision, identical in replay and live ------------------------------
    def evaluate(self, C: np.ndarray, QV: np.ndarray, TB: np.ndarray,
                 apos: int, anchor_ms: int, minute: int) -> list[Decision]:
        """Evaluate all 10 symbols at one (anchor, decision minute).

        C/QV/TB are (NG, NS) trailing buffers on a shared minute grid. `apos` is the anchor's
        row. Nothing after row `apos + minute` is read -- asserted inside compute_block.
        """
        a = np.array([apos], dtype=np.int64)
        micro, cross = compute_block(C, QV, TB, a, minute)
        rv = rv30(C, a, minute)[0]
        X = np.hstack([micro[0], cross[0],
                       np.arange(NS, dtype=np.float32)[:, None]]).astype(np.float32)
        raw = np.asarray(self.boosters[minute].predict_proba(X))[:, 1]
        cal = self.isotonics[minute].predict(raw)

        conf = np.abs(raw - 0.5)
        r_m = np.searchsorted(self.ref_model, conf) / len(self.ref_model)
        r_v = np.searchsorted(self.ref_rv30, rv) / len(self.ref_rv30)
        comb = 0.5 * r_m + 0.5 * r_v

        out = []
        for j, s in enumerate(SYMBOLS):
            ok = np.isfinite(rv[j]) and np.isfinite(raw[j])
            side = 1.0 if raw[j] >= 0.5 else -1.0
            fired = bool(ok and comb[j] >= self.threshold and side > 0)
            reason = ("" if fired else
                      "nonfinite" if not ok else
                      "below_threshold" if comb[j] < self.threshold else
                      "short_dropped")
            out.append(Decision(
                anchor_ms=int(anchor_ms), minute=int(minute), symbol=s,
                raw_score=float(raw[j]), cal_score=float(cal[j]),
                rv30_bp=float(rv[j]) if np.isfinite(rv[j]) else float("nan"),
                rank_model=float(r_m[j]), rank_rv30=float(r_v[j]), comb=float(comb[j]),
                threshold=self.threshold, side=side, fired=fired, reason=reason))
        return out


def dedup_earliest(fired: list[Decision]) -> list[Decision]:
    """Keep the EARLIEST qualifying decision minute per (symbol, anchor), as every prior EXP."""
    best: dict[tuple, Decision] = {}
    for d in sorted(fired, key=lambda x: (x.symbol, x.anchor_ms, x.minute)):
        k = (d.symbol, d.anchor_ms)
        if k not in best:
            best[k] = d
    return list(best.values())
