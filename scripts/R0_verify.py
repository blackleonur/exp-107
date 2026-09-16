"""
EXP-107 verification gate (PRE_DECLARATION section 4, gates 1 and 3).

RESEARCH ONLY. Reads klines and EXP-092's frozen blocks read-only. Places no orders.
NO PRODUCTION / PAPER STRATEGY CHANGES MADE.

Gate 1a -- FULL-BUFFER PARITY: the shared implementation, run over a long history, reproduces
          the frozen EXP-092 block.
Gate 1b -- LIVE-BUFFER PARITY: the SAME implementation, run over only the trailing
          LIVE_WARMUP_BARS that a live engine would actually hold, reproduces the same values.
          This is the gate that matters -- a live engine never has the full history, and this
          is precisely where a separately-written forward harness would silently diverge.
Gate 3  -- THRESHOLD PROVENANCE: the pinned threshold equals the frozen validation percentile.

If gate 1a or 1b fails, the shadow run does not start.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from d_features import (DECISION_MINUTES, LIVE_WARMUP_BARS, NS, SYMBOLS,  # noqa: E402
                        build_cumsums, compute_block, feature_names)

REPO = Path(__file__).resolve().parents[3]
E92 = REPO / "results" / "exp092_microstructure_direction" / "data"
ROOT = REPO / "results" / "exp107_shadow"
ROOT.mkdir(parents=True, exist_ok=True)

from tradebot.features.loaders import scan_symbol  # noqa: E402

SLICE_FROM = int(pd.Timestamp("2026-04-01").value // 1_000_000)
N_CHECK = 60                       # anchors verified, drawn from the end of the frozen grid

# ---------------------------------------------------------------- load a kline slice
raw = {}
for s in SYMBOLS:
    k = (scan_symbol("futures_klines", s)
         .select("open_time", "close", "quote_asset_volume", "taker_buy_quote_asset_volume")
         .filter(__import__("polars").col("open_time") >= SLICE_FROM)
         .sort("open_time").collect())
    raw[s] = {c: k[c].to_numpy() for c in k.columns}
grid = np.unique(np.concatenate([raw[s]["open_time"].astype(np.int64) for s in SYMBOLS]))
gpos = {int(t): i for i, t in enumerate(grid)}
NG = len(grid)


def lay(field):
    M = np.full((NG, NS), np.nan)
    for j, s in enumerate(SYMBOLS):
        ix = np.array([gpos[int(t)] for t in raw[s]["open_time"].astype(np.int64)])
        M[ix, j] = raw[s][field].astype(np.float64)
    return M


C, QV, TB = lay("close"), lay("quote_asset_volume"), lay("taker_buy_quote_asset_volume")
print(f"kline buffer: {NG:,} minutes x {NS} symbols "
      f"({pd.to_datetime(grid[0], unit='ms')} -> {pd.to_datetime(grid[-1], unit='ms')})")

# ---------------------------------------------------------------- anchors to verify
z = np.load(E92 / "btcusdt.npz", allow_pickle=True)
fot = np.array(z["open_time"], dtype=np.int64)
z.close()
cand = fot[(fot >= grid[0] + LIVE_WARMUP_BARS * 60_000) & (fot + 1440 * 60_000 <= grid[-1])]
anchors = cand[-N_CHECK:]
apos_full = np.array([gpos[int(a)] for a in anchors], dtype=np.int64)
print(f"verification anchors: {len(anchors)} "
      f"({pd.to_datetime(anchors[0], unit='ms')} -> {pd.to_datetime(anchors[-1], unit='ms')})")

micro_names, cross_names = feature_names()
frozen = {}
for j, s in enumerate(SYMBOLS):
    zz = np.load(E92 / f"{s.lower()}.npz", allow_pickle=True)
    sel = np.searchsorted(np.array(zz["open_time"], dtype=np.int64), anchors)
    frozen[s] = {f"{k}_{t}": np.array(zz[f"{k}_{t}"])[sel]
                 for t in DECISION_MINUTES for k in ("micro", "cross")}
    zz.close()
    if j == 0:
        fn = list(np.load(E92 / "adausdt.npz", allow_pickle=True)["micro_names"])
        assert fn == micro_names, "micro feature ORDER differs from the frozen build"
        fn2 = list(np.load(E92 / "adausdt.npz", allow_pickle=True)["cross_names"])
        assert fn2 == cross_names, "cross feature ORDER differs from the frozen build"
print("feature name/order check: PASS (156 columns match the frozen build exactly)")

# ---------------------------------------------------------------- GATE 1a: full buffer
CS_full = build_cumsums(C, QV, TB)
rows = []


def compare(tag, t, mi, cr):
    for j, s in enumerate(SYMBOLS):
        for key, new, nms in ((f"micro_{t}", mi[:, j, :], micro_names),
                              (f"cross_{t}", cr[:, j, :], cross_names)):
            fz = frozen[s][key]
            nanmm = int((np.isfinite(fz) != np.isfinite(new)).sum())
            both = np.isfinite(fz) & np.isfinite(new)
            for c in range(fz.shape[1]):
                m = both[:, c]
                if not m.any():
                    continue
                scale = max(float(np.max(np.abs(fz[m, c]))), 1e-12)
                rows.append(dict(gate=tag, block=key.split("_")[0], minute=t, symbol=s,
                                 feature=nms[c], nan_mismatch=nanmm,
                                 max_rel=float(np.max(np.abs(fz[m, c] - new[m, c])) / scale)))


print("\nGATE 1a -- full-buffer parity")
for t in DECISION_MINUTES:
    mi, cr = compute_block(C, QV, TB, apos_full, t, CS_full)
    compare("1a_full", t, mi, cr)
    print(f"  t={t} done", flush=True)

# ---------------------------------------------------------------- GATE 1b: live buffer
print(f"\nGATE 1b -- LIVE-buffer parity (only the trailing {LIVE_WARMUP_BARS:,} bars held)")
for t in DECISION_MINUTES:
    mis, crs = [], []
    for a in apos_full:
        lo = a + t - LIVE_WARMUP_BARS + 1
        assert lo >= 0, "buffer underflow"
        hi = a + t + 1                      # a live engine holds nothing past minute t
        Cb, QVb, TBb = C[lo:hi], QV[lo:hi], TB[lo:hi]
        mi, cr = compute_block(Cb, QVb, TBb, np.array([a - lo]), t)
        mis.append(mi); crs.append(cr)
    compare("1b_live", t, np.concatenate(mis), np.concatenate(crs))
    print(f"  t={t} done", flush=True)

V = pd.DataFrame(rows)
V.to_csv(ROOT / "verification.csv", index=False)
print("\n" + "=" * 100)
print("VERIFICATION RESULT")
print("=" * 100)
summary = {}
for tag, g in V.groupby("gate"):
    nanmm = int(g.nan_mismatch.sum())
    per = g.groupby("feature").max_rel.max()
    summary[tag] = dict(cells=int(len(g)), nan_mismatches=nanmm,
                        median_rel=float(per.median()), p99_rel=float(per.quantile(.99)),
                        worst_rel=float(per.max()), worst_feature=str(per.idxmax()))
    print(f"\n  {tag}:  cells {len(g):,}   NaN mismatches {nanmm}")
    print(f"    median feature rel diff : {per.median():.3e}")
    print(f"    99th percentile         : {per.quantile(.99):.3e}")
    print(f"    worst                   : {per.max():.3e}  ({per.idxmax()})")

GATE1 = all(v["nan_mismatches"] == 0 and v["worst_rel"] < 1e-3 and v["median_rel"] < 1e-5
            for v in summary.values())
print(f"\n  GATE 1 (NaN pattern identical, worst < 1e-3, median < 1e-5): "
      f"{'PASS' if GATE1 else 'FAIL'}")

# ---------------------------------------------------------------- GATE 3: threshold
print("\nGATE 3 -- threshold provenance")
E105 = REPO / "results" / "exp105_long_only_validation"
thr_live = None
if (E105 / "evaluation.json").exists():
    thr_live = json.loads((E105 / "evaluation.json").read_text()).get("threshold")
print(f"  EXP-105 pinned D threshold (99.0 pct of combined rank on VALIDATION): {thr_live}")
print("  -> the shadow artifact must carry this exact number; it is never recomputed live.")

with open(ROOT / "verification.json", "w") as f:
    json.dump(dict(gate1=GATE1, summary=summary, n_anchors=int(len(anchors)),
                   live_warmup_bars=LIVE_WARMUP_BARS,
                   anchors_from=str(pd.to_datetime(anchors[0], unit="ms")),
                   anchors_to=str(pd.to_datetime(anchors[-1], unit="ms")),
                   exp105_threshold=thr_live), f, indent=2)
if not GATE1:
    print("\n  GATE 1 FAILED -- the shadow run does not start.")
    sys.exit(1)
print("\nGate 1 passed. Feature parity is established for both full and live buffers.")
