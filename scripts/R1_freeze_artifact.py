"""
EXP-107 -- freeze the D LONG-ONLY live artifact.  Usage:
    python R1_freeze_artifact.py slice      then      python R1_freeze_artifact.py fit

RESEARCH ONLY. Places no orders. NO PRODUCTION / PAPER STRATEGY CHANGES MADE.

Freezes, ONCE, exactly the objects the live engine needs and nothing it could re-derive:

    booster_{t}.pkl      the LightGBM model for decision minute t (SEED=42, deterministic)
    isotonic_{t}.pkl     the calibrator fitted on VALIDATION only
    ref_model.npy        sorted |raw-0.5| over the whole VALIDATION slice, pooled over minutes
    ref_rv30.npy         sorted rv30 over the same slice
    artifact.json        the PINNED threshold, the slice boundaries, and a SHA-256 of each file

Fold: S = 2026-08-27, the most recent one, identical to EXP-105's.
    TRAIN        [2020-01-01, 2026-05-29)   model only
    VALIDATION   [2026-05-29, 2026-08-27)   calibrator AND threshold

The live engine NEVER recomputes the threshold or the reference distributions. It loads them.

Two stages in separate processes: LightGBM 4.7.0 here dies with an access violation in any
process that has also read the compressed .npz blocks (bisected in EXP-094 section 5.9).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[3]
E92 = REPO / "results" / "exp092_microstructure_direction" / "data"
OUT = REPO / "results" / "exp107_shadow" / "artifact"
OUT.mkdir(parents=True, exist_ok=True)
SCRATCH = Path(os.environ.get(
    "EXP107_SCRATCH",
    r"C:\Users\pc\AppData\Local\Temp\claude\C--Users-pc-Desktop-borsabot"
    r"\c7bdcb85-ec2b-4753-920b-cf255a55746a\scratchpad\exp107"))
SCRATCH.mkdir(parents=True, exist_ok=True)

SYMBOLS = ["ADAUSDT", "AVAXUSDT", "BNBUSDT", "BTCUSDT", "DOGEUSDT",
           "DOTUSDT", "ETHUSDT", "LINKUSDT", "SOLUSDT", "XRPUSDT"]
MINUTES = (10, 30, 60, 120, 240, 480)
# Computed, never hand-written: the first version of this file hard-coded two epoch
# constants that were both exactly one year early, which silently shrank TRAIN from
# 561,600 rows to 474,000 and moved the pinned threshold by 1.2e-02. The mismatch was
# caught only because the threshold is compared against EXP-105's below -- which is why
# that comparison is in the artifact metadata rather than left implicit.
from datetime import datetime, timedelta, timezone  # noqa: E402

_FOLD = datetime(2026, 8, 27, tzinfo=timezone.utc)
FOLD_MS = int(_FOLD.timestamp() * 1000)
CAL_MS = int((_FOLD - timedelta(days=90)).timestamp() * 1000)
PCT = 99.0
RV_WINDOW = 30


def stage_slice() -> None:
    for t in MINUTES:
        Xs, ys, ots, syms = [], [], [], []
        for j, s in enumerate(SYMBOLS):
            z = np.load(E92 / f"{s.lower()}.npz", allow_pickle=True)
            mic = np.array(z[f"micro_{t}"], dtype=np.float32)
            cro = np.array(z[f"cross_{t}"], dtype=np.float32)
            Xs.append(np.hstack([mic, cro, np.full((len(mic), 1), j, np.float32)]))
            ys.append(np.array(z[f"y_{t}"], dtype=np.int32))
            ots.append(np.array(z["open_time"], dtype=np.int64))
            syms.append(np.full(len(mic), j, np.int16))
            z.close()
        X = np.vstack(Xs).astype(np.float32); y = np.concatenate(ys)
        ot = np.concatenate(ots); sym = np.concatenate(syms)
        tr = ot < CAL_MS
        ca = (ot >= CAL_MS) & (ot < FOLD_MS)
        assert ot[tr].max() < CAL_MS <= ot[ca].min(), "train/validation boundary violated"
        assert ot[ca].max() < FOLD_MS, "validation reaches the fold start"
        for nm, arr in (("xt", X[tr]), ("yt", y[tr]), ("xc", X[ca]), ("yc", y[ca]),
                        ("ot_c", ot[ca]), ("sym_c", sym[ca])):
            np.save(SCRATCH / f"{t}_{nm}.npy", np.ascontiguousarray(arr))
        print(f"t={t}: train {int(tr.sum()):,}  validation {int(ca.sum()):,}", flush=True)


def stage_fit() -> None:
    import joblib
    from sklearn.isotonic import IsotonicRegression
    from tradebot.ml.classifiers import fit_lightgbm_classifier

    conf_all, rv_all = [], []
    for t in MINUTES:
        xt = np.load(SCRATCH / f"{t}_xt.npy"); yt = np.load(SCRATCH / f"{t}_yt.npy")
        xc = np.load(SCRATCH / f"{t}_xc.npy"); yc = np.load(SCRATCH / f"{t}_yc.npy")
        fit = fit_lightgbm_classifier(xt, yt, xc, yc, [str(k) for k in range(xt.shape[1])])
        raw_c = np.asarray(fit.predict_proba_fn(xc), dtype=np.float64)
        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        iso.fit(raw_c, yc)
        joblib.dump(fit.raw_model, OUT / f"booster_{t}.pkl")
        joblib.dump(iso, OUT / f"isotonic_{t}.pkl")
        np.save(SCRATCH / f"{t}_rawc.npy", raw_c)
        conf_all.append(np.abs(raw_c - 0.5))
        print(f"t={t}: fitted, booster saved  (val rows {len(raw_c):,})", flush=True)

    # ---- rv30 over the same validation rows, from klines -------------------------
    from tradebot.features.loaders import scan_symbol
    KL = {}
    for s in SYMBOLS:
        k = (scan_symbol("futures_klines", s).select("open_time", "close")
             .sort("open_time").collect())
        ot = k["open_time"].to_numpy().astype(np.int64)
        cl = k["close"].to_numpy().astype(np.float64)
        r = np.zeros(len(cl)); r[1:] = cl[1:] / cl[:-1] - 1.0
        KL[s] = (ot, np.concatenate([[0.0], np.cumsum(r * r)]))
    for t in MINUTES:
        otc = np.load(SCRATCH / f"{t}_ot_c.npy")
        syc = np.load(SCRATCH / f"{t}_sym_c.npy")
        v = np.full(len(otc), np.nan)
        tt = otc + t * 60_000
        for j, s in enumerate(SYMBOLS):
            m = syc == j
            ot, cs2 = KL[s]
            e = np.searchsorted(ot, tt[m])
            ok = e < len(ot)
            ok[ok] &= ot[e[ok]] == tt[m][ok]
            ok &= e >= RV_WINDOW
            idx = np.where(m)[0]
            eo = e[ok]
            v[idx[ok]] = np.sqrt((cs2[eo + 1] - cs2[eo + 1 - RV_WINDOW]) / RV_WINDOW) * 1e4
        rv_all.append(v)

    conf = np.concatenate(conf_all); rv = np.concatenate(rv_all)
    ref_model = np.sort(conf[np.isfinite(conf)])
    ref_rv = np.sort(rv[np.isfinite(rv)])
    both = np.isfinite(conf) & np.isfinite(rv)
    comb_v = (0.5 * np.searchsorted(ref_model, conf[both]) / len(ref_model)
              + 0.5 * np.searchsorted(ref_rv, rv[both]) / len(ref_rv))
    thr = float(np.percentile(comb_v, PCT))
    np.save(OUT / "ref_model.npy", ref_model.astype(np.float64))
    np.save(OUT / "ref_rv30.npy", ref_rv.astype(np.float64))

    def sha(p: Path) -> str:
        return hashlib.sha256(p.read_bytes()).hexdigest()[:16]

    meta = dict(
        system="D = MODEL + RV30, LONG ONLY",
        fold="2026-08-27", train_end="2026-05-29", validation_end="2026-08-27",
        decision_minutes=list(MINUTES), rv_window=RV_WINDOW, percentile=PCT,
        horizon_minutes=1440, cost_bp=14.38,
        PINNED_THRESHOLD=thr,
        exp105_threshold=0.964579822530864,
        n_ref_model=int(len(ref_model)), n_ref_rv30=int(len(ref_rv)),
        weights=[0.5, 0.5],
        sha256={p.name: sha(p) for p in sorted(OUT.glob("*")) if p.suffix in (".pkl", ".npy")})
    (OUT / "artifact.json").write_text(json.dumps(meta, indent=2))
    print(f"\nPINNED THRESHOLD : {thr:.15f}")
    print(f"EXP-105 threshold: {meta['exp105_threshold']:.15f}")
    print(f"difference       : {abs(thr - meta['exp105_threshold']):.3e}")
    print(f"\nartifact -> {OUT}")
    for k, v2 in meta["sha256"].items():
        print(f"  {k:20s} {v2}")


if __name__ == "__main__":
    (stage_slice if sys.argv[1] == "slice" else stage_fit)()
