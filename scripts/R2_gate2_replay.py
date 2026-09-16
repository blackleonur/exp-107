"""
EXP-107 verification GATE 2 -- signal parity.  Three stages, run in order:

    python R2_gate2_replay.py bars      # polars: klines -> .npy        (no LightGBM)
    python R2_gate2_replay.py decide    # numpy + LightGBM only         (no pandas/polars)
    python R2_gate2_replay.py compare   # pandas: compare to EXP-105    (no LightGBM)

RESEARCH ONLY. Places no orders. NO PRODUCTION / PAPER STRATEGY CHANGES MADE.

Drives the LIVE decision core (`ShadowEngine.evaluate`) over EXP-105's fresh window, bar by
bar, holding only the trailing buffer a live engine would actually have, and checks that it
reproduces EXP-105's 38 selected trades EXACTLY.

WHY THREE STAGES -- and why this constrains the live engine too:
LightGBM 4.7.0 in this environment dies with `access violation reading 0x0` inside
LGBM_BoosterPredictForMat in any process that has also imported pandas or polars (bisected in
EXP-094 section 5.9; hit again here on the first single-process attempt). The decide stage is
therefore pure numpy + LightGBM.

**The live engine inherits this constraint**: its market-data path must reach numpy without
importing pandas or polars, or prediction must run in a separate process. R3 uses stdlib
json + urllib -> numpy for exactly this reason.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
REPO = HERE.parents[2]
ROOT = REPO / "results" / "exp107_shadow"
E105 = REPO / "results" / "exp105_long_only_validation"
TMP = ROOT / "gate2_tmp"
TMP.mkdir(parents=True, exist_ok=True)

from d_features import DECISION_MINUTES, LIVE_WARMUP_BARS, NS, SYMBOLS  # noqa: E402

WIN_FROM_ISO, WIN_TO_ISO = "2026-08-27T00:00:00", "2026-09-11T23:00:00"


def _ms(iso: str) -> int:
    from datetime import datetime, timezone
    return int(datetime.fromisoformat(iso).replace(tzinfo=timezone.utc).timestamp() * 1000)


WIN_FROM, WIN_TO = _ms(WIN_FROM_ISO), _ms(WIN_TO_ISO)


def stage_bars() -> None:
    """polars only -- never touches LightGBM."""
    import polars as pl
    from tradebot.features.loaders import scan_symbol
    slice_from = WIN_FROM - (LIVE_WARMUP_BARS + 600) * 60_000
    raw = {}
    for s in SYMBOLS:
        k = (scan_symbol("futures_klines", s)
             .select("open_time", "close", "quote_asset_volume",
                     "taker_buy_quote_asset_volume")
             .filter(pl.col("open_time") >= slice_from).sort("open_time").collect())
        raw[s] = {c: k[c].to_numpy() for c in k.columns}
    grid = np.unique(np.concatenate(
        [raw[s]["open_time"].astype(np.int64) for s in SYMBOLS]))
    gpos = {int(t): i for i, t in enumerate(grid)}
    NG = len(grid)

    def lay(f):
        M = np.full((NG, NS), np.nan)
        for j, s in enumerate(SYMBOLS):
            ix = np.array([gpos[int(t)] for t in raw[s]["open_time"].astype(np.int64)])
            M[ix, j] = raw[s][f].astype(np.float64)
        return M

    np.save(TMP / "grid.npy", grid)
    np.save(TMP / "C.npy", lay("close"))
    np.save(TMP / "QV.npy", lay("quote_asset_volume"))
    np.save(TMP / "TB.npy", lay("taker_buy_quote_asset_volume"))
    print(f"bars: {NG:,} minutes x {NS} symbols -> {TMP}")


def stage_decide() -> None:
    """numpy + LightGBM only -- no pandas, no polars, or LightGBM segfaults."""
    from shadow_engine import ShadowEngine, dedup_earliest
    grid = np.load(TMP / "grid.npy")
    C, QV, TB = (np.load(TMP / f"{n}.npy") for n in ("C", "QV", "TB"))
    gpos = {int(t): i for i, t in enumerate(grid)}
    NG = len(grid)
    eng = ShadowEngine.load()
    print(f"artifact threshold {eng.threshold:.15f}  refs {len(eng.ref_model):,}")

    anchors = np.arange(WIN_FROM, WIN_TO + 1, 3_600_000, dtype=np.int64)
    print(f"replaying {len(anchors)} anchors x {len(DECISION_MINUTES)} minutes = "
          f"{len(anchors)*len(DECISION_MINUTES):,} decision points", flush=True)
    rows, fired = [], []
    for i, a in enumerate(anchors):
        ap = gpos.get(int(a))
        if ap is None:
            continue
        for t in DECISION_MINUTES:
            lo, hi = ap + t - LIVE_WARMUP_BARS + 1, ap + t + 1
            if lo < 0 or hi > NG:
                continue
            for d in eng.evaluate(C[lo:hi], QV[lo:hi], TB[lo:hi], ap - lo, int(a), t):
                rows.append((SYMBOLS.index(d.symbol), d.anchor_ms, d.minute, d.raw_score,
                             d.cal_score, d.rv30_bp, d.comb, d.side, float(d.fired)))
                if d.fired:
                    fired.append(d)
        if (i + 1) % 96 == 0:
            print(f"  {i+1}/{len(anchors)} anchors, {len(fired)} raw fires", flush=True)
    sel = dedup_earliest(fired)
    print(f"\nraw fires {len(fired)} -> dedup earliest minute, LONG only: {len(sel)}")
    np.save(TMP / "all_decisions.npy", np.array(rows, dtype=np.float64))
    np.save(TMP / "selected.npy", np.array(
        [(SYMBOLS.index(d.symbol), d.anchor_ms, d.minute, d.raw_score, d.rv30_bp, d.comb)
         for d in sel], dtype=np.float64))


def stage_compare() -> None:
    """pandas only -- no LightGBM."""
    import pandas as pd
    sel = np.load(TMP / "selected.npy")
    allc = np.load(TMP / "all_decisions.npy")
    D = pd.DataFrame(sel, columns=["sym_i", "anchor", "minute", "raw_score", "rv30", "comb"])
    D["symbol"] = [SYMBOLS[int(i)] for i in D.sym_i]
    D["anchor"] = D.anchor.astype(np.int64)
    D["minute"] = D.minute.astype(int)
    D = D.sort_values(["anchor", "symbol"]).reset_index(drop=True)
    D.to_csv(ROOT / "gate2_replay_trades.csv", index=False)
    A = pd.DataFrame(allc, columns=["sym_i", "anchor", "minute", "raw_score", "cal_score",
                                    "rv30", "comb", "side", "fired"])
    A["symbol"] = [SYMBOLS[int(i)] for i in A.sym_i]
    A.to_csv(ROOT / "gate2_all_decisions.csv", index=False)

    ref = pd.read_csv(E105 / "fresh_trades.csv")
    k_new = set(zip(D.symbol, D.anchor))
    k_ref = set(zip(ref.sym, ref.anchor))
    inter, union = k_new & k_ref, k_new | k_ref
    missing, extra = k_ref - k_new, k_new - k_ref
    print("\n" + "=" * 96)
    print("GATE 2 -- SIGNAL PARITY vs EXP-105")
    print("=" * 96)
    print(f"  decision points evaluated : {len(A):,}")
    print(f"  EXP-105 selected          : {len(k_ref)}")
    print(f"  replay selected           : {len(k_new)}")
    print(f"  intersection              : {len(inter)}  "
          f"({100*len(inter)/max(len(union),1):.2f}% of union)")
    print(f"  missing (in 105, not replay): {len(missing)}")
    print(f"  extra   (in replay, not 105): {len(extra)}")
    minute_ok, sc = float("nan"), float("nan")
    if inter:
        m = D.merge(ref, left_on=["symbol", "anchor"], right_on=["sym", "anchor"],
                    suffixes=("_new", "_ref"))
        minute_ok = float((m.minute_new == m.minute_ref).mean())
        sc = float(np.max(np.abs(m.raw_score.to_numpy() - m.raw.to_numpy())))
        print(f"  decision-minute agreement : {100*minute_ok:.2f}%")
        print(f"  max |raw score| difference: {sc:.3e}")
    GATE2 = (len(missing) == 0 and len(extra) == 0 and minute_ok == 1.0)
    print(f"\n  GATE 2: {'PASS' if GATE2 else 'FAIL'}")
    for k in sorted(missing)[:6]:
        print(f"    MISSING {k[0]} {pd.to_datetime(k[1], unit='ms')}")
    for k in sorted(extra)[:6]:
        print(f"    EXTRA   {k[0]} {pd.to_datetime(k[1], unit='ms')}")
    (ROOT / "gate2.json").write_text(json.dumps(dict(
        gate2=bool(GATE2), exp105=len(k_ref), replay=len(k_new), intersection=len(inter),
        missing=len(missing), extra=len(extra), minute_agreement=minute_ok,
        max_score_diff=sc, decision_points=int(len(A)),
        raw_fires=int(A.fired.sum())), indent=2))
    if not GATE2:
        print("\n  GATE 2 FAILED -- the shadow run does not start.")
        sys.exit(1)
    print("\nGate 2 passed. The live decision path reproduces EXP-105's selection exactly.")


if __name__ == "__main__":
    {"bars": stage_bars, "decide": stage_decide, "compare": stage_compare}[sys.argv[1]]()
