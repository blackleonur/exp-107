"""
EXP-107 -- the D feature block, ONE implementation used by both the historical replay and the
live shadow engine.

RESEARCH ONLY. Imports nothing from the paper engines and places no orders.
NO PRODUCTION / PAPER STRATEGY CHANGES MADE.

This is EXP-092's `C0_build.py` inner computation extracted verbatim into a function over a
trailing buffer. The batch verification and the live engine call THE SAME function, so they
cannot drift apart -- which is the failure mode EXP-096/097 exposed, where a separately-written
forward harness passed its own lock and still behaved differently from the historical build.

    compute_block(C, QV, TB, apos, t) -> (micro[NA, NS, 66], cross[NA, NS, 90])

C, QV, TB are (NG, NS) arrays of close, quote volume and taker-buy quote volume on a shared
minute grid, NS = 10 symbols. `apos` are anchor row positions; `t` is the decision minute.
Every window ends at row `apos + t` and reads no later bar.

Buffer requirement: the longest lookback is a 1440-minute cross lag plus a 480-minute decision
offset, so the buffer must hold at least 1,920 bars before the anchor. LIVE_WARMUP_BARS adds
margin.
"""
from __future__ import annotations

import numpy as np

SYMBOLS = ["ADAUSDT", "AVAXUSDT", "BNBUSDT", "BTCUSDT", "DOGEUSDT",
           "DOTUSDT", "ETHUSDT", "LINKUSDT", "SOLUSDT", "XRPUSDT"]
DECISION_MINUTES = (10, 30, 60, 120, 240, 480)
HORIZON = 1440
WINDOWS = (30, 120, 480)
CROSS_LAGS = (1, 5, 15, 30, 60, 180, 360, 720, 1440)
NS = len(SYMBOLS)
BTC_I, ETH_I = SYMBOLS.index("BTCUSDT"), SYMBOLS.index("ETHUSDT")
LIVE_WARMUP_BARS = 2_600          # 1440 cross lag + 480 decision offset + margin

MICRO_FAMILIES = ("rskew", "rkurt", "roll", "amihud", "kyle", "vpin",
                  "jump", "vratio", "cvd_slope", "signac", "sflow")
CROSS_FAMILIES = ("own_ret", "btc_ret", "eth_ret", "mkt_ret", "breadth", "disp",
                  "relstr", "rank", "btc_minus_own", "eth_minus_own")


def feature_names() -> tuple[list[str], list[str]]:
    micro = [f"{nm}_{W}{sfx}" for W in WINDOWS for nm in MICRO_FAMILIES
             for sfx in ("", "_xrank")]
    # the batch build emits value then xrank per family, family-major within each window
    micro = []
    for W in WINDOWS:
        for nm in MICRO_FAMILIES:
            micro.append(f"{nm}_{W}")
            micro.append(f"{nm}_{W}_xrank")
    cross = [f"{nm}_{L}" for L in CROSS_LAGS for nm in CROSS_FAMILIES]
    return micro, cross


def _cums(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    ng = x.shape[0]
    v = ~np.isnan(x)
    z = np.where(v, x, 0.0)
    cs = np.zeros((ng + 1, x.shape[1]))
    cv = np.zeros((ng + 1, x.shape[1]), dtype=np.int64)
    np.cumsum(z, axis=0, out=cs[1:])
    np.cumsum(v, axis=0, out=cv[1:])
    return cs, cv


def build_cumsums(C: np.ndarray, QV: np.ndarray, TB: np.ndarray) -> dict:
    ng = C.shape[0]
    R = np.full((ng, NS), np.nan)
    R[1:] = C[1:] / C[:-1] - 1.0
    SIGNED = 2.0 * TB - QV
    lag = np.vstack([np.full((1, NS), np.nan), R[:-1]])
    out = {}
    for nm, arr in (("r", R), ("r2", R * R), ("r3", R ** 3), ("r4", R ** 4),
                    ("absr", np.abs(R)), ("qv", QV), ("sv", SIGNED),
                    ("absimb", np.abs(SIGNED)), ("rsv", R * SIGNED),
                    ("sv2", SIGNED * SIGNED), ("rlag", R * lag),
                    ("absrlag", np.abs(R) * np.abs(lag))):
        out[nm] = _cums(arr)
    return out


def compute_block(C: np.ndarray, QV: np.ndarray, TB: np.ndarray,
                  apos: np.ndarray, t: int,
                  CS: dict | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Micro (NA, NS, 66) and cross (NA, NS, 90) at decision minute `t`."""
    apos = np.asarray(apos, dtype=np.int64)
    NA = len(apos)
    if CS is None:
        CS = build_cumsums(C, QV, TB)

    def wsum(nm, b, a):
        cs, cv = CS[nm]
        W = (b - a)[:, None]
        good = a >= 0
        v = np.full((NA, NS), np.nan)
        n = np.zeros((NA, NS), dtype=np.int64)
        v[good] = cs[b[good]] - cs[a[good]]
        n[good] = cv[b[good]] - cv[a[good]]
        v[n < W] = np.nan
        return v

    end = apos + t
    assert end.max() < C.shape[0], "decision minute is beyond the buffer"
    feats = []
    for W in WINDOWS:
        b, a = end + 1, end + 1 - W
        s1 = wsum("r", b, a); s2 = wsum("r2", b, a)
        s3 = wsum("r3", b, a); s4 = wsum("r4", b, a)
        mu = s1 / W
        var = np.maximum(s2 / W - mu ** 2, 1e-18)
        sd = np.sqrt(var)
        with np.errstate(invalid="ignore", divide="ignore"):
            skew = (s3 / W - 3 * mu * var - mu ** 3) / sd ** 3
            kurt = (s4 / W - 4 * mu * (s3 / W) + 6 * mu ** 2 * (s2 / W) - 3 * mu ** 4) / var ** 2
            cov1 = wsum("rlag", b, a) / W - mu ** 2
            roll = np.where(cov1 < 0, 2.0 * np.sqrt(np.maximum(-cov1, 0)) * 1e4, 0.0)
            qv = wsum("qv", b, a)
            amihud = (wsum("absr", b, a) / W) / np.maximum(qv / W, 1e-9) * 1e10
            sv = wsum("sv", b, a); rsv = wsum("rsv", b, a); sv2 = wsum("sv2", b, a)
            msv = sv / W
            kyle = (rsv / W - mu * msv) / np.maximum(sv2 / W - msv ** 2, 1e-9) * 1e10
            vpin = (wsum("absimb", b, a) / W) / np.maximum(qv / W, 1e-9)
            bpv = (np.pi / 2.0) * (wsum("absrlag", b, a) / W)
            jump = 1.0 - bpv / np.maximum(s2 / W, 1e-18)
            sflow = sv / np.maximum(qv, 1e-9)
            signac = cov1 / var
            k5 = 5
            r5 = np.full((NA, NS), np.nan)
            good5 = (end - W) >= 0
            if good5.any():
                idx = np.where(good5)[0]
                m = W // k5
                if m >= 4:
                    pts = (end[idx] - W)[:, None] + k5 * np.arange(m + 1)[None, :]
                    assert pts.max() <= end[idx].max(), "vratio would read past minute t"
                    seg = C[pts]
                    rr = seg[:, 1:, :] / seg[:, :-1, :] - 1.0
                    r5[idx] = rr.var(axis=1)
            with np.errstate(invalid="ignore", divide="ignore"):
                vratio = r5 / np.maximum(k5 * var, 1e-18)
            cvd = np.full((NA, NS), np.nan)
            goodc = (end + 1 - W) >= 0
            if goodc.any():
                idx = np.where(goodc)[0]
                pts = (end[idx] + 1 - W)[:, None] + np.arange(W + 1)[None, :]
                cs_sv = CS["sv"][0]
                cc = cs_sv[pts] - cs_sv[pts[:, [0]]]
                x = np.arange(W + 1, dtype=np.float64)
                xc = x - x.mean()
                cvd[idx] = ((cc - cc.mean(axis=1, keepdims=True))
                            * xc[None, :, None]).sum(axis=1) / (xc ** 2).sum()
                cvd[idx] = cvd[idx] / np.maximum(np.abs(cc[:, -1, :]) + 1e-9, 1e-9)
        for arr in (skew, kurt, roll, amihud, kyle, vpin, jump, vratio, cvd, signac, sflow):
            feats.append(arr.astype(np.float32))
            rk = np.argsort(np.argsort(np.where(np.isnan(arr), -np.inf, arr), axis=1), axis=1)
            cnt = np.sum(~np.isnan(arr), axis=1, keepdims=True)
            rkn = (rk / np.maximum(cnt - 1, 1)).astype(np.float32)
            rkn[np.isnan(arr)] = np.nan
            feats.append(rkn)
    micro = np.stack(feats, axis=2)

    cf = []
    RET = {}
    for L in CROSS_LAGS:
        e2, a2 = end, end - L
        good = a2 >= 0
        r = np.full((NA, NS), np.nan)
        r[good] = C[e2[good]] / C[a2[good]] - 1.0
        RET[L] = r
    for L in CROSS_LAGS:
        r = RET[L]
        valid = ~np.isnan(r)
        tot = np.nansum(r, axis=1, keepdims=True)
        cnt = np.sum(valid, axis=1, keepdims=True)
        den = np.where(cnt - valid > 0, cnt - valid, np.nan)
        mkt = (tot - np.nan_to_num(r)) / den
        pos = np.where(valid, (r > 0).astype(float), 0.0)
        breadth = (np.nansum(pos, axis=1, keepdims=True) - pos) / den
        disp = np.full_like(r, np.nan)
        for j in range(NS):
            with np.errstate(invalid="ignore"):
                disp[:, j] = np.nanstd(np.delete(r, j, axis=1), axis=1)
        rk = np.argsort(np.argsort(np.where(np.isnan(r), -np.inf, r), axis=1), axis=1)
        rank = (rk / np.maximum(cnt - 1, 1))
        rank[np.isnan(r)] = np.nan
        B = np.broadcast_to
        for arr in (r, B(r[:, [BTC_I]], r.shape), B(r[:, [ETH_I]], r.shape), mkt,
                    breadth, disp, r - mkt, rank,
                    r[:, [BTC_I]] - r, r[:, [ETH_I]] - r):
            cf.append(np.asarray(arr, dtype=np.float32))
    cross = np.stack(cf, axis=2)
    return micro, cross


def rv30(C: np.ndarray, apos: np.ndarray, t: int, window: int = 30) -> np.ndarray:
    """Realised vol of 1-min returns over the `window` minutes ENDING at minute t, in bp."""
    apos = np.asarray(apos, dtype=np.int64)
    end = apos + t
    R = np.full_like(C, np.nan)
    R[1:] = C[1:] / C[:-1] - 1.0
    cs2 = np.concatenate([np.zeros((1, NS)), np.nancumsum(R * R, axis=0)])
    ok = end >= window
    out = np.full((len(apos), NS), np.nan)
    e = end[ok]
    out[ok] = np.sqrt((cs2[e + 1] - cs2[e + 1 - window]) / window) * 1e4
    return out
