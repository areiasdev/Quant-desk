"""The 40 RADAR setups (research/radar/setups.md) as signal generators.

Each per-symbol setup is `fn(d, p, sym, ctx) -> (signals, exit_long, exit_short)` where `d` is the
symbol's bar frame on the setup's timeframe (index 0..n-1), `p` the parameter dict. Every value
used for a signal at bar t is computed from bars <= t (building blocks below shift where
setups.md says "previous n bars"). Cross-sectional setups (S26, S27, M08) return
{symbol: (signals, exit_long, exit_short)}.

DEFAULTS are RADAR's parameters exactly as written; GRIDS (<= 3 params, <= 4 values each,
always containing the default) are used for walk-forward and robustness only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from .engine import make_signals

# ------------------------------------------------------------------ building blocks
def HH(x: pd.Series, n: int) -> pd.Series:
    return x.shift(1).rolling(n, min_periods=n).max()


def LL(x: pd.Series, n: int) -> pd.Series:
    return x.shift(1).rolling(n, min_periods=n).min()


def ATR(d: pd.DataFrame, n: int) -> pd.Series:
    pc = d["close"].shift(1)
    tr = pd.concat([d["high"] - d["low"], (d["high"] - pc).abs(), (d["low"] - pc).abs()],
                   axis=1).max(axis=1)
    return tr.rolling(n, min_periods=n).mean()


def SMA(x: pd.Series, n: int) -> pd.Series:
    return x.rolling(n, min_periods=n).mean()


def QVavg(d: pd.DataFrame, n: int) -> pd.Series:
    return d["quote_volume"].shift(1).rolling(n, min_periods=n).mean()


def RET(x: pd.Series, n: int) -> pd.Series:
    return x / x.shift(n) - 1


def VWAP(d: pd.DataFrame, n: int) -> pd.Series:
    return d["quote_volume"].rolling(n, min_periods=n).sum() / \
        d["volume"].rolling(n, min_periods=n).sum()


def F8pct(h1: pd.DataFrame, q: float) -> pd.Series:
    """q-th percentile of the symbol's own settled F8 over the 90 days BEFORE the latest settlement
    (>= 30 settlements), as-of each 1h bar."""
    ns = h1["new_settle"].to_numpy()
    ev = pd.Series(h1.loc[ns, "F8"].to_numpy(),
                   index=pd.DatetimeIndex(h1.loc[ns, "funding_time"]))
    if len(ev) == 0:
        return pd.Series(np.nan, index=h1.index)
    pct = ev.rolling("90D", closed="left", min_periods=30).quantile(q / 100).to_numpy()
    idx = np.cumsum(ns) - 1
    out = np.where(idx >= 0, pct[np.clip(idx, 0, None)], np.nan)
    return pd.Series(out, index=h1.index)


def to4h(s1h: pd.Series, h1: pd.DataFrame, d4: pd.DataFrame) -> np.ndarray:
    """Value of a 1h series at the last 1h bar of each 4h bar (known at the 4h close)."""
    s = pd.Series(np.asarray(s1h), index=h1["open_time"])
    return s.reindex(d4["open_time"] + pd.Timedelta(hours=3)).to_numpy()


def next_idx(mask: np.ndarray, at: np.ndarray) -> np.ndarray:
    """For each position in `at`, the first index > it where mask is True (or n+10)."""
    pos = np.flatnonzero(mask)
    k = np.searchsorted(pos, at, side="right")
    return np.where(k < len(pos), pos[np.minimum(k, len(pos) - 1)], len(mask) + 10)


def local(d: pd.DataFrame, tz: str) -> pd.Series:
    return d["open_time"].dt.tz_convert(tz)


def _sig(mask_long, mask_short, d, stop_l=None, stop_s=None, **kw):
    """Combine long/short masks into a signal frame; per-side arrays via dict(l=.., s=..)."""
    parts = []
    for side, m, st in ((1, mask_long, stop_l), (-1, mask_short, stop_s)):
        if m is None:
            continue
        m = np.asarray(m, bool)
        ix = np.flatnonzero(m)
        if len(ix) == 0:
            continue
        cols = dict(idx=ix, side=np.full(len(ix), side))
        if st is not None:
            cols["stop"] = np.asarray(st, float)[ix]
        for k, v in kw.items():
            if isinstance(v, dict):
                v = v["l" if side == 1 else "s"]
            if v is None:
                continue
            v = np.asarray(v, float)
            cols[k] = v[ix] if v.ndim else np.full(len(ix), float(v))
        parts.append(make_signals(**cols))
    if not parts:
        return make_signals(idx=[], side=[])
    return pd.concat(parts, ignore_index=True).sort_values("idx", kind="stable")


def _ns(x: pd.Series) -> np.ndarray:
    return x.astype("datetime64[ns, UTC]").astype("int64").to_numpy()


def _ar(n):
    return np.arange(n, dtype=float)


# ------------------------------------------------------------------ A. breakouts
def s01(d, p, sym, ctx):
    hh, ll = HH(d["high"], 48), LL(d["low"], 48)
    ratio = (hh - ll) / d["close"].shift(1)
    thr = ratio.shift(1).rolling(1440, min_periods=1440).quantile(p["pct"])
    vol = d["quote_volume"] >= p["vol"] * QVavg(d, 48)
    narrow = ratio < thr
    L = narrow & vol & (d["close"] > hh)
    S = narrow & vol & (d["close"] < ll)
    mid = (hh + ll) / 2
    return _sig(L, S, d, stop_l=mid, stop_s=mid, target_R=p["tR"], tmax=_ar(len(d)) + 1 + 48), \
        None, None


def s02(d, p, sym, ctx):
    n = p["n"]
    vol = d["quote_volume"] >= p["vol"] * QVavg(d, n)
    L = (d["close"] > HH(d["high"], n)) & vol
    S = (d["close"] < LL(d["low"], n)) & vol
    atr = ATR(d, 14)
    xl = (d["close"] < LL(d["low"], p["exit_n"])).to_numpy()
    xs = (d["close"] > HH(d["high"], p["exit_n"])).to_numpy()
    return _sig(L, S, d, stop_dist=2 * atr, tmax=_ar(len(d)) + 1 + 30), xl, xs


def s03(d, p, sym, ctx):
    n = p["n"]
    oi = d["open_interest"]
    oiu = (oi / oi.shift(24) - 1) >= p["oi"]
    vol = d["quote_volume"] >= p["vol"] * QVavg(d, n)
    L = (d["close"] > HH(d["high"], n)) & vol & oiu
    S = (d["close"] < LL(d["low"], n)) & vol & oiu
    return _sig(L, S, d, stop_l=LL(d["low"], 6), stop_s=HH(d["high"], 6), target_R=2.0,
                tmax=_ar(len(d)) + 1 + 24), None, None


def s04(d, p, sym, ctx):
    t = d["open_time"]
    wk = (t - pd.to_timedelta(t.dt.weekday, unit="D")).dt.floor("D")
    g = d.groupby(wk)
    wh, wl = g["high"].max(), g["low"].min()
    prev = pd.DataFrame({"wh": wh, "wl": wl})
    prev.index = prev.index + pd.Timedelta(days=7)        # previous week's levels, keyed by week
    WH = wk.map(prev["wh"]).to_numpy(float)
    WL = wk.map(prev["wl"]).to_numpy(float)
    vol = (d["quote_volume"] >= p["vol"] * QVavg(d, 42)).to_numpy()
    c = d["close"].to_numpy()
    L = (c > WH) & vol
    S = (c < WL) & vol
    any_ = L | S
    first = any_ & (pd.Series(any_).groupby(wk.to_numpy()).cumsum().to_numpy() == 1)
    L, S = L & first, S & first & ~L
    tnext = np.searchsorted(_ns(t), _ns(wk + pd.Timedelta(days=7)))
    mid = (WH + WL) / 2
    return _sig(L, S, d, stop_l=mid, stop_s=mid, target_R=p["tR"], tmax=tnext), None, None


# ------------------------------------------------------------------ B. trend pullbacks
def s05(d, p, sym, ctx):
    c = d["close"]
    f, s = SMA(c, p["fast"]), SMA(c, p["slow"])
    atr = ATR(d, 14)
    L = (c > s) & (f > s) & (f > f.shift(6)) & (d["low"] <= f) & (c > f)
    S = (c < s) & (f < s) & (f < f.shift(6)) & (d["high"] >= f) & (c < f)
    stl = np.minimum(d["low"], LL(d["low"], 3)) - 0.5 * atr
    sts = np.maximum(d["high"], HH(d["high"], 3)) + 0.5 * atr
    return _sig(L, S, d, stop_l=stl, stop_s=sts, target_R=p["tR"],
                tmax=_ar(len(d)) + 1 + 30), (c < s).to_numpy(), (c > s).to_numpy()


def s06(d, p, sym, ctx):
    n, within = p["n"], p["within"]
    h, lo, c = d["high"].to_numpy(), d["low"].to_numpy(), d["close"].to_numpy()
    hh, ll = HH(d["high"], n).to_numpy(), LL(d["low"], n).to_numpy()
    atr = ATR(d, 14).to_numpy()
    idx, side, stop = [], [], []
    for sd in (1, -1):
        lvl = hh if sd == 1 else ll
        brk = (c > lvl) if sd == 1 else (c < lvl)
        b, B, used, broken = -10**9, np.nan, True, False
        for t in range(len(c)):
            if brk[t]:
                b, B, used, broken = t, lvl[t], False, False
                continue
            if t - b > within or used or not np.isfinite(B):
                continue
            if (sd == 1 and c[t] < B) or (sd == -1 and c[t] > B):
                broken = True
            if broken:
                continue
            ok = (lo[t] <= B + 0.25 * atr[t] and c[t] > B) if sd == 1 else \
                (h[t] >= B - 0.25 * atr[t] and c[t] < B)
            if ok:
                idx.append(t), side.append(sd), stop.append(B - sd * atr[t])
                used = True
    sig = make_signals(idx=idx, side=side, stop=stop, target_R=np.full(len(idx), p["tR"]),
                       tmax=np.asarray(idx, float) + 1 + 36)
    return sig.sort_values("idx", kind="stable"), None, None


def s07(d, p, sym, ctx):
    c = d["close"]
    s = SMA(c, p["sma"])
    day = d["open_time"].dt.floor("D")
    g = d.groupby(day)
    vw = (g["quote_volume"].sum() / g["volume"].sum())
    vw.index = vw.index + pd.Timedelta(days=1)            # yesterday's VWAP, keyed by today
    Vy = day.map(vw).to_numpy(float)
    atr = ATR(d, 14)
    L = ((c > s) & (s > s.shift(24))).to_numpy() & (d["low"].to_numpy() < Vy) & (c.to_numpy() > Vy)
    S = ((c < s) & (s < s.shift(24))).to_numpy() & (d["high"].to_numpy() > Vy) & \
        (c.to_numpy() < Vy)
    # max one trade (signal) per day
    for m in (L, S):
        first = pd.Series(m).groupby(day.to_numpy()).cumsum().to_numpy() == 1
        m &= first
    S &= ~L
    stl = d["low"] - p["buf"] * atr
    sts = d["high"] + p["buf"] * atr
    return _sig(L, S, d, stop_l=stl, stop_s=sts, target_R=p["tR"],
                tmax=_ar(len(d)) + 1 + 24), None, None


def s08(d, p, sym, ctx):
    c = d["close"]
    f, s = SMA(c, p["fast"]), SMA(c, 200)
    k = p["k"]
    down = np.ones(len(d), bool)
    up = np.ones(len(d), bool)
    for i in range(k):
        down &= (c.shift(i) < c.shift(i + 1)).to_numpy()
        up &= (c.shift(i) > c.shift(i + 1)).to_numpy()
    atr = ATR(d, 14)
    L = ((c > f) & (f > s)).to_numpy() & down
    S = ((c < f) & (f < s)).to_numpy() & up
    lo3 = d["low"].rolling(3).min() - 0.5 * atr
    hi3 = d["high"].rolling(3).max() + 0.5 * atr
    return _sig(L, S, d, stop_l=lo3, stop_s=hi3,
                close_exit=dict(l=d["high"].shift(2), s=d["low"].shift(2)),
                tmax=_ar(len(d)) + 1 + p["tmax"]), None, None


# ------------------------------------------------------------------ C. flushes
def _flush(d, p, side):
    oi = d["open_interest"]
    atr1 = ATR(d, 24).shift(1)
    body = (d["open"] - d["close"]) if side == 1 else (d["close"] - d["open"])
    return ((oi / oi.shift(2) - 1) <= -p["oi"]) & (body >= p["body"] * atr1) & \
        (d["quote_volume"] >= p["vol"] * QVavg(d, 24)), atr1


def s09(d, p, sym, ctx):
    m, atr1 = _flush(d, p, 1)
    return _sig(m, None, d, stop_l=d["low"] - 0.5 * atr1, target=(d["open"] + d["close"]) / 2,
                tmax=_ar(len(d)) + 1 + p["tmax"]), None, None


def s10(d, p, sym, ctx):
    m, atr1 = _flush(d, p, -1)
    return _sig(None, m, d, stop_s=d["high"] + 0.5 * atr1, target=(d["open"] + d["close"]) / 2,
                tmax=_ar(len(d)) + 1 + p["tmax"]), None, None


def s11(d, p, sym, ctx):
    oi = d["open_interest"]
    atr = ATR(d, 14)
    rng = d["high"] - d["low"]
    pos = (d["close"] - d["low"]) / rng.where(rng > 0)
    flush = ((oi / oi.shift(1) - 1) <= -p["oi"]) & (rng >= p["rng"] * atr.shift(1)) & (pos <= 0.3)
    L = flush.shift(1, fill_value=False) & (d["close"] > d["close"].shift(1))
    return _sig(L, None, d, stop_l=d["low"].shift(1), target=d["open"].shift(1),
                tmax=_ar(len(d)) + 1 + p["tmax"]), None, None


def s12(d, p, sym, ctx):
    m, _ = _flush(d, p, 1)
    lo, c, qv = d["low"].to_numpy(), d["close"].to_numpy(), d["quote_volume"].to_numpy()
    op = d["open"].to_numpy()
    atr = ATR(d, 24).to_numpy()
    idx, stop, tgt = [], [], []
    for f in np.flatnonzero(m.to_numpy()):
        for t in range(f + 1, min(f + 25, len(c))):
            if c[t] < lo[f]:
                break
            if t - f >= 4 and lo[t] <= lo[f] * 1.005 and c[t] > lo[f] and qv[t] < qv[f]:
                idx.append(t), stop.append(lo[f] - 0.5 * atr[t]), tgt.append(op[f])
                break
    sig = make_signals(idx=idx, side=np.ones(len(idx), int), stop=stop, target=tgt,
                       tmax=np.asarray(idx, float) + 1 + p["tmax"])
    return sig.drop_duplicates("idx").sort_values("idx"), None, None


# ------------------------------------------------------------------ D. funding
def _f8pct_tf(d, sym, q, tf):
    s = F8pct(sym.h1, q)
    return s.to_numpy() if tf == "1h" else to4h(s, sym.h1, d)


def s13(d, p, sym, ctx):
    pct = _f8pct_tf(d, sym, p["pct"], "4h")
    F8 = d["F8"].to_numpy()
    S = (F8 >= np.maximum(p["fmin"], pct)) & (d["close"] < d["low"].shift(1)).to_numpy()
    xs = (d["settle_min"] <= 0.0001).to_numpy()
    return _sig(None, S, d, stop_s=HH(d["high"], 6) + 0.5 * ATR(d, 14), target_R=p["tR"],
                tmax=_ar(len(d)) + 1 + 18), None, xs


def s14(d, p, sym, ctx):
    pct = _f8pct_tf(d, sym, 100 - p["pct"], "4h")
    F8 = d["F8"].to_numpy()
    L = (F8 <= np.minimum(-p["fmin"], pct)) & (d["close"] > d["high"].shift(1)).to_numpy()
    xl = (d["settle_max"] >= 0).to_numpy()
    return _sig(L, None, d, stop_l=LL(d["low"], 6) - 0.5 * ATR(d, 14), target_R=p["tR"],
                tmax=_ar(len(d)) + 1 + 18), xl, None


def s15(d, p, sym, ctx):
    n, w = p["n"], p["within"]
    hh, ll = HH(d["high"], n).to_numpy(), LL(d["low"], n).to_numpy()
    c, h, lo = d["close"].to_numpy(), d["high"].to_numpy(), d["low"].to_numpy()
    F8 = d["F8"].to_numpy()
    hi_p, lo_p = _f8pct_tf(d, sym, p["pct"], "1h"), _f8pct_tf(d, sym, 100 - p["pct"], "1h")
    atr = ATR(d, 14).to_numpy()
    N = len(c)
    out = []
    for sd in (-1, 1):
        brk = (c > hh) if sd == -1 else (c < ll)
        bi = pd.Series(np.where(brk, np.arange(N), np.nan)).ffill().shift(1).to_numpy()
        ok = np.isfinite(bi) & ((np.arange(N) - bi) <= w)
        b = np.where(ok, bi, 0).astype(int)
        lvl = hh[b] if sd == -1 else ll[b]
        cond = ok & ((c < lvl) if sd == -1 else (c > lvl)) & \
            ((F8 >= hi_p) if sd == -1 else (F8 <= lo_p))
        for t in np.flatnonzero(cond):
            bb = b[t]
            if sd == -1:
                st = h[bb:t + 1].max() + 0.25 * atr[t]
            else:
                st = lo[bb:t + 1].min() - 0.25 * atr[t]
            out.append((t, sd, st, (hh[bb] + ll[bb]) / 2))
    if not out:
        return make_signals(idx=[], side=[]), None, None
    a = np.array(out)
    sig = make_signals(idx=a[:, 0].astype(int), side=a[:, 1].astype(int), stop=a[:, 2],
                       target=a[:, 3], tmax=a[:, 0] + 1 + 24)
    return sig.sort_values("idx", kind="stable"), None, None


def s16(d, p, sym, ctx):
    c = d["close"]
    f = SMA(c, p["fast"])
    s = SMA(c, 200)
    cond = ((c > f) & (f > s) & (d["F8"] < 0)).to_numpy()
    below = (c < f).to_numpy()
    sig = np.zeros(len(d), bool)
    armed = True
    for t in range(len(d)):
        if below[t]:
            armed = True
        elif armed and cond[t]:
            sig[t], armed = True, False
    xl = below | (d["settle_max"] > p["fexit"]).to_numpy()
    return _sig(sig, None, d, stop_dist=p["atr"] * ATR(d, 14)), xl, None


def s17(d, p, sym, ctx):
    nxt = d["funding_time"] + pd.to_timedelta(d["funding_interval_hours"], unit="h")
    pre = (d["open_time"] + pd.Timedelta(hours=2)) == nxt
    F8 = d["F8"]
    L = pre & (F8 <= -p["f"])
    S = pre & (F8 >= p["f"])
    return _sig(L, S, d, stop_dist=p["atr"] * ATR(d, 24), tmax=_ar(len(d)) + 2), None, None


# ------------------------------------------------------------------ E. OI divergences
def s18(d, p, sym, ctx):
    c, oi = d["close"], d["open_interest"]
    S = (RET(c, 24) >= p["ret"]) & ((oi / oi.shift(24) - 1) <= -p["oi"]) & \
        (c < d["low"].shift(1))
    hh = HH(d["high"], 24)
    tgt = c.shift(24) + 0.5 * (hh - c.shift(24))
    return _sig(None, S, d, stop_s=hh + 0.5 * ATR(d, 14), target=tgt,
                tmax=_ar(len(d)) + 1 + p["tmax"]), None, None


def s19(d, p, sym, ctx):
    c, oi = d["close"], d["open_interest"]
    ll = LL(d["low"], 168)
    L = (RET(c, 24) <= -p["ret"]) & ((oi / oi.shift(24) - 1) >= p["oi"]) & (d["low"] < ll) & \
        (c > ll)
    return _sig(L, None, d, stop_l=d["low"] - 0.5 * ATR(d, 14), target_R=p["tR"],
                tmax=_ar(len(d)) + 1 + 24), None, None


def s20(d, p, sym, ctx):
    c, oi = d["close"], d["open_interest"]
    oiu = (oi / oi.shift(6) - 1) >= p["oi"]
    L = (RET(c, 6) >= p["ret"]) & oiu & (c > d["high"].shift(1))
    S = (RET(c, 6) <= -p["ret"]) & oiu & (c < d["low"].shift(1))
    return _sig(L, S, d, stop_l=LL(d["low"], 6), stop_s=HH(d["high"], 6), target_R=p["tR"],
                tmax=_ar(len(d)) + 1 + 12), None, None


def s21(d, p, sym, ctx):
    tp, gl = d["top_trader_position_ls_ratio"], d["global_account_ls_ratio"]
    dt_, dg = tp / tp.shift(24) - 1, gl / gl.shift(24) - 1
    L = (dt_ >= p["top"]) & (dg <= -p["glob"])
    S = (dt_ <= -p["top"]) & (dg >= p["glob"])
    return _sig(L, S, d, stop_dist=2 * ATR(d, 14), target_R=p["tR"],
                tmax=_ar(len(d)) + 1 + 24), None, None


# ------------------------------------------------------------------ F. sessions
def s22(d, p, sym, ctx):
    t = d["open_time"]
    day = t.dt.floor("D")
    asia = t.dt.hour <= 6
    g = d[asia].groupby(day[asia])
    a = pd.DataFrame({"hi": g["high"].max(), "lo": g["low"].min(), "n": g["high"].count()})
    a = a[a["n"] == 7]
    a["w"] = (a["hi"] - a["lo"]) / ((a["hi"] + a["lo"]) / 2)
    a["thr"] = a["w"].shift(1).rolling(20, min_periods=20).quantile(p["q"])
    hi, lo = day.map(a["hi"]).to_numpy(float), day.map(a["lo"]).to_numpy(float)
    ok = (day.map(a["w"]) < day.map(a["thr"])).to_numpy(bool)
    lon = local(d, "Europe/London")
    eu = ((lon.dt.hour == 8) & (lon.dt.minute == 0)).to_numpy()
    c = d["close"].to_numpy()
    L = eu & ok & (c > hi)
    S = eu & ok & (c < lo)
    ex = next_idx((lon.dt.hour == p["exit_h"]).to_numpy(), np.arange(len(d)))
    mid = (hi + lo) / 2
    return _sig(L, S, d, stop_l=mid, stop_s=mid, target_R=p["tR"], tmax=ex), None, None


def _us_days(d):
    ny = local(d, "America/New_York")
    wd = (ny.dt.weekday < 5).to_numpy()
    return ny, wd


def s23(d, p, sym, ctx):
    lon = local(d, "Europe/London")
    ny, wd = _us_days(d)
    day = d["open_time"].dt.floor("D").to_numpy()
    N = len(d)
    eu_i = pd.Series(np.where((lon.dt.hour == 8).to_numpy(), np.arange(N), np.nan)) \
        .groupby(day).transform("max").to_numpy()
    ny8 = pd.Series(np.where((ny.dt.hour == 8).to_numpy(), np.arange(N), np.nan)) \
        .groupby(day).transform("max").to_numpy()
    atr = ATR(d, 24).to_numpy()
    o, c, h, lo = (d[k].to_numpy() for k in ("open", "close", "high", "low"))
    us = wd & (ny.dt.hour == 9).to_numpy() & np.isfinite(eu_i) & np.isfinite(ny8)
    out = []
    for t in np.flatnonzero(us):
        e, n8 = int(eu_i[t]), int(ny8[t])
        if not (e < n8 < t):
            continue
        a = atr[e]
        mv = c[n8] - o[e]
        if mv >= p["eu"] * a and c[t] - o[t] <= -p["us"] * a:
            out.append((t, -1, h[e:t + 1].max()))
        elif mv <= -p["eu"] * a and c[t] - o[t] >= p["us"] * a:
            out.append((t, 1, lo[e:t + 1].min()))
    if not out:
        return make_signals(idx=[], side=[]), None, None
    x = np.array(out)
    ex = next_idx((ny.dt.hour == 16).to_numpy(), x[:, 0].astype(int))
    return make_signals(idx=x[:, 0].astype(int), side=x[:, 1].astype(int), stop=x[:, 2],
                        target_R=np.full(len(x), p["tR"]), tmax=ex), None, None


def s24(d, p, sym, ctx):
    ny, wd = _us_days(d)
    us = wd & (ny.dt.hour == 9).to_numpy()
    qv = d["quote_volume"].where(us)
    ref = qv.dropna().shift(1).rolling(10, min_periods=10).mean().reindex(d.index).to_numpy()
    body = (d["close"] - d["open"]).to_numpy()
    atr = ATR(d, 24).to_numpy()
    big = us & (np.abs(body) >= p["atr"] * atr) & (d["quote_volume"].to_numpy() >= p["vol"] * ref)
    L, S = big & (body > 0), big & (body < 0)
    ex = next_idx((ny.dt.hour == 15).to_numpy(), np.arange(len(d)))
    return _sig(L, S, d, stop_l=d["low"], stop_s=d["high"], target_R=p["tR"], tmax=ex), \
        None, None


def s25(d, p, sym, ctx):
    t = d["open_time"]
    day = t.dt.floor("D")
    g = d.groupby(day)
    dd = pd.DataFrame({"hi": g["high"].max(), "lo": g["low"].min(), "n": g["high"].count()})
    dd["rng"] = dd["hi"] - dd["lo"]
    rows = {}
    for D in dd.index[dd.index.weekday == 0]:
        sat, sun = D - pd.Timedelta(days=2), D - pd.Timedelta(days=1)
        prev = [D - pd.Timedelta(days=k) for k in (6, 5, 4, 3)]   # Tue..Fri before
        if not all(x in dd.index and dd.loc[x, "n"] == 24 for x in [sat, sun] + prev):
            continue
        hi, lo = max(dd.loc[sat, "hi"], dd.loc[sun, "hi"]), min(dd.loc[sat, "lo"], dd.loc[sun, "lo"])
        if hi - lo < p["w"] * dd.loc[prev, "rng"].mean():
            rows[D] = (hi, lo)
    N = len(d)
    hi = day.map({k: v[0] for k, v in rows.items()}).to_numpy(float)
    lo = day.map({k: v[1] for k, v in rows.items()}).to_numpy(float)
    win = (t.dt.weekday == 0).to_numpy() & (t.dt.hour < p["end_h"]).to_numpy()
    c = d["close"].to_numpy()
    L0, S0 = win & (c > hi), win & (c < lo)
    first = (pd.Series(L0 | S0).groupby(day.to_numpy()).cumsum().to_numpy() == 1) & (L0 | S0)
    L, S = L0 & first, S0 & first & ~L0
    tue = np.searchsorted(_ns(t), _ns(day + pd.Timedelta(days=1)))
    mid = (hi + lo) / 2
    return _sig(L, S, d, stop_l=mid, stop_s=mid, target_R=p["tR"], tmax=tue), None, None


# ------------------------------------------------------------------ G. alts vs BTC
def _btc1h(ctx):
    b = ctx.syms["BTCUSDT"].h1
    return pd.DataFrame({"open_time": b["open_time"], "b_close": b["close"],
                         "b_hh72": HH(b["high"], 72), "b_ll72": LL(b["low"], 72),
                         "b_ret24": RET(b["close"], 24)})


def _cross(ctx, symbols, p, side, eligible):
    """S26/S27/M08: BTC trigger + laggard alts, max 3 per signal bar (largest lag)."""
    btc = _btc1h(ctx)
    if side == 1:
        trig = (btc["b_close"] > btc["b_hh72"]) & (btc["b_ret24"] >= p["btc"])
    else:
        trig = (btc["b_close"] < btc["b_ll72"]) & (btc["b_ret24"] <= -p["btc"])
    btc = btc[trig]
    cands = []
    for s in symbols:
        if s == "BTCUSDT":
            continue
        d = ctx.syms[s].h1
        m = d[["open_time", "close", "corr30d"]].merge(btc, on="open_time", how="left")
        lag = side * (m["b_ret24"] - RET(d["close"], 24))     # >0 means the alt lags
        ok = m["b_ret24"].notna() & (m["corr30d"] >= p["corr"]) & (lag >= p["lag"])
        ok &= eligible(s, d)
        ix = np.flatnonzero(ok.to_numpy())
        if len(ix):
            cands.append(pd.DataFrame({"sym": s, "idx": ix, "time": d["open_time"].to_numpy()[ix],
                                       "lag": lag.to_numpy()[ix]}))
    out = {}
    if not cands:
        return out
    cd = pd.concat(cands)
    cd = cd.sort_values(["time", "lag"], ascending=[True, False]).groupby("time").head(3)
    for s, g in cd.groupby("sym"):
        d = ctx.syms[s].h1
        atr = ATR(d, 14).to_numpy()
        ix = g["idx"].to_numpy()
        stop = (LL(d["low"], 24).to_numpy() - 0.5 * atr) if side == 1 else \
            (HH(d["high"], 24).to_numpy() + 0.5 * atr)
        out[s] = (make_signals(idx=ix, side=np.full(len(ix), side), stop=stop[ix],
                               target_R=np.full(len(ix), 2.0), tmax=ix + 1.0 + 24), None, None)
    return out


def s26(ctx, p, eligible):
    return _cross(ctx, ctx.core, p, 1, eligible)


def s27(ctx, p, eligible):
    return _cross(ctx, ctx.core, p, -1, eligible)


def _btc4h(ctx):
    b = ctx.syms["BTCUSDT"].h4
    return pd.DataFrame({"open_time": b["open_time"], "b_close": b["close"],
                         "b_sma50": SMA(b["close"], 50), "b_hh180": HH(b["high"], 180),
                         "b_r1": b["close"].pct_change(), "b_ret6": RET(b["close"], 6)})


def s28(d, p, sym, ctx):
    if sym.name == "BTCUSDT":
        return make_signals(idx=[], side=[]), None, None
    m = d[["open_time", "close"]].merge(_btc4h(ctx), on="open_time", how="left")
    r1 = d["close"].pct_change()
    beta = r1.rolling(180, min_periods=180).cov(m["b_r1"]) / \
        m["b_r1"].rolling(180, min_periods=180).var()
    res = RET(d["close"], 6) - beta * m["b_ret6"]
    z = res / res.shift(1).rolling(180, min_periods=180).std()
    L = (m["b_close"] > m["b_sma50"]) & (z <= -p["z"])
    return _sig(L, None, d, stop_dist=p["atr"] * ATR(d, 14), tmax=_ar(len(d)) + 1 + p["tmax"]), \
        (z >= 0).to_numpy(), None


def s29(d, p, sym, ctx):
    if sym.name == "BTCUSDT":
        return make_signals(idx=[], side=[]), None, None
    m = d[["open_time"]].merge(_btc4h(ctx), on="open_time", how="left")
    hh = HH(d["high"], 180)
    c = d["close"]
    L = (m["b_close"] > m["b_hh180"]) & (c <= p["depth"] * hh) & (c > SMA(c, 50))
    return _sig(L, None, d, stop_l=LL(d["low"], 12), close_exit=dict(l=hh, s=None),
                target_R=p["tR"], tmax=_ar(len(d)) + 1 + p["tmax"]), None, None


# ------------------------------------------------------------------ M. memes
def m01(d, p, sym, ctx):
    c = d["close"]
    L = (d["hype_volume_z30d"] >= p["z"]) & (d["hype_oi_surge_7d"] >= p["oi"]) & \
        (c > HH(d["high"], 24)) & (d["days_since_listing"] >= 30)
    return _sig(L, None, d, stop_l=LL(d["low"], 6), stop_dist=1.5 * ATR(d, 14),
                tmax=_ar(len(d)) + 1 + p["tmax"]), (c < LL(d["low"], 6)).to_numpy(), None


def m02(d, p, sym, ctx):
    s = ((d["hype_move_vs_btc_24h"] >= p["move"]) & (d["hype_volume_z30d"] >= p["z"]) &
         (d["hype_funding_jump_bp"] <= 2)).to_numpy()
    vw = VWAP(d, 24).to_numpy()
    c = d["close"].to_numpy()
    inband = (c >= vw) & (c <= (1 + p["band"]) * vw)
    atr = ATR(d, 14).to_numpy()
    ts = set()
    for i in np.flatnonzero(s):
        w = np.flatnonzero(inband[i + 1:i + 13])
        if len(w):
            ts.add(i + 1 + int(w[0]))
    ix = np.array(sorted(ts), dtype=int)
    return make_signals(idx=ix, side=np.ones(len(ix), int), stop=vw[ix] - atr[ix],
                        target_R=np.full(len(ix), 2.0), tmax=ix + 1.0 + 24), None, None


def m03(d, p, sym, ctx):
    S = (d["F8"] >= p["f"]) & (d["hype_funding_jump_bp"] >= p["jump"]) & \
        (d["hype_move_vs_btc_24h"] >= p["move"]) & (d["close"] < d["low"].shift(1))
    xs = (d["settleF8"] <= 0.0001).to_numpy()
    return _sig(None, S, d, stop_s=HH(d["high"], 12) + 0.5 * ATR(d, 14), target=VWAP(d, 24),
                tmax=_ar(len(d)) + 1 + 36), None, xs


def m04(d, p, sym, ctx):
    c, oi = d["close"], d["open_interest"]
    L = ((oi / oi.shift(3) - 1) <= -p["oi"]) & (RET(c, 3) <= -p["drop"]) & \
        (d["quote_volume"].rolling(3).max() >= 3 * QVavg(d, 24).shift(2))
    tgt = c.shift(3) - 0.5 * (c.shift(3) - c)
    return _sig(L, None, d, stop_l=d["low"].rolling(3).min() - 0.5 * ATR(d, 24), target=tgt,
                tmax=_ar(len(d)) + 1 + p["tmax"]), None, None


def _first_vwap(d):
    if not len(d) or not (d["days_since_listing"].iloc[0] < 1):
        return np.nan
    f = d.iloc[:24]
    return f["quote_volume"].sum() / f["volume"].sum()


def m05(d, p, sym, ctx):
    ds = d["days_since_listing"]
    c = d["close"]
    S = ((ds >= 2) & (ds <= p["days"]) & (c < LL(d["low"], 24)) &
         (c < _first_vwap(d))).to_numpy().copy()
    f = np.flatnonzero(S)
    S[:] = False
    if len(f):
        S[f[0]] = True                                     # one trade per symbol
    return _sig(None, S, d, stop_s=HH(d["high"], 24), target_R=p["tR"],
                tmax=_ar(len(d)) + 1 + p["tmax"]), None, None


def m06(d, p, sym, ctx):
    ds = d["days_since_listing"]
    c = d["close"]
    since = d["quote_volume"].expanding().mean() * 24
    hot = (d["hype_volume_z30d"] >= 3) | ((ds < 20) & (d["qv24"] >= 3 * since))
    S = (ds <= p["days"]) & (RET(c, 24) >= p["ret"]) & hot & (c < d["low"].shift(1))
    return _sig(None, S, d, stop_s=HH(d["high"], 24) + 0.5 * ATR(d, 14), target_R=p["tR"],
                tmax=_ar(len(d)) + 1 + 24), None, None


def m07(d, p, sym, ctx):
    o, h, lo, c = d["open"], d["high"], d["low"], d["close"]
    wick = h - np.maximum(o, c)
    S = (d["hype_volume_z30d"] >= p["z"]) & (RET(c, 24) >= p["ret"]) & (c < o) & \
        (wick >= p["wick"] * (h - lo))
    return _sig(None, S, d, stop_s=h + 0.25 * ATR(d, 14), target_R=2.0,
                tmax=_ar(len(d)) + 1 + 24), None, None


def m08(ctx, p, eligible):
    return _cross(ctx, ctx.memes, p, 1, eligible)


def m09(d, p, sym, ctx):
    n = p["n"]
    c = d["close"]
    base = (d["hype_oi_surge_7d"] >= p["oi"]) & (RET(c, 24).abs() <= p["flat"])
    hh, ll = HH(d["high"], n), LL(d["low"], n)
    atr2 = 2 * ATR(d, 14)
    return _sig(base & (c > hh), base & (c < ll), d, stop_l=ll, stop_s=hh, stop_dist=atr2,
                target_R=2.0, tmax=_ar(len(d)) + 1 + 48), None, None


def m10(d, p, sym, ctx):
    L = (d["F8"] <= -p["f"]) & (d["hype_oi_surge_7d"] >= p["oi"]) & \
        (d["close"] > HH(d["high"], 12))
    xl = (d["settleF8"] >= 0).to_numpy()
    return _sig(L, None, d, stop_l=LL(d["low"], 12), target_R=p["tR"],
                tmax=_ar(len(d)) + 1 + 24), xl, None


def m11(d, p, sym, ctx):
    h1 = sym.h1
    burst = (h1["hype_volume_z30d"] >= p["z"]).to_numpy()
    n1 = len(h1)
    bi = pd.Series(np.where(burst, np.arange(n1), np.nan)).ffill().shift(72).to_numpy()
    age_ok = np.isfinite(bi) & ((np.arange(n1) - bi) <= 168)
    bclose = np.where(age_ok, h1["close"].to_numpy()[np.where(age_ok, bi, 0).astype(int)], np.nan)
    bc4 = to4h(bclose, h1, d)
    c = d["close"]
    S = (d["hype_volume_z30d"] <= 0).to_numpy() & (c.to_numpy() < bc4) & \
        (c < SMA(c, 18)).to_numpy() & (c < d["low"].shift(1)).to_numpy()
    return _sig(None, S, d, stop_s=HH(d["high"], 6) + 0.5 * ATR(d, 14), target_R=p["tR"],
                tmax=_ar(len(d)) + 1 + p["tmax"]), None, None


# ------------------------------------------------------------------ registry
@dataclass
class Setup:
    id: str
    name: str
    kind: str                  # core | meme
    tf: str                    # 1h | 4h
    fn: Callable
    defaults: dict
    grid: dict = field(default_factory=dict)
    cross: bool = False
    family: str = ""
    side: str = ""


def _S(id_, name, kind, tf, fn, defaults, grid, cross=False, family="", side=""):
    for k, v in grid.items():
        assert defaults[k] in v, (id_, k)
        assert len(v) <= 4
    assert len(grid) <= 3
    return Setup(id_, name, kind, tf, fn, defaults, grid, cross, family, side)


SETUPS = {s.id: s for s in [
    _S("S01", "Two-day range breakout on a volume burst", "core", "1h", s01,
       dict(vol=2.0, pct=0.25, tR=2.0), dict(vol=[1.5, 2.0, 3.0], pct=[0.15, 0.25, 0.4],
                                             tR=[1.5, 2.0, 3.0]), family="breakout", side="both"),
    _S("S02", "Five-day range breakout, trailing exit", "core", "4h", s02,
       dict(n=30, vol=1.5, exit_n=10), dict(n=[20, 30, 42], vol=[1.0, 1.5, 2.0],
                                            exit_n=[6, 10, 15]), family="breakout", side="both"),
    _S("S03", "Breakout with rising open interest", "core", "1h", s03,
       dict(n=24, oi=0.03, vol=1.5), dict(n=[12, 24, 48], oi=[0.01, 0.03, 0.06],
                                          vol=[1.0, 1.5, 2.0]), family="breakout", side="both"),
    _S("S04", "Prior-week high/low breakout", "core", "4h", s04,
       dict(vol=1.5, tR=2.0), dict(vol=[1.0, 1.5, 2.0], tR=[1.5, 2.0, 3.0]),
       family="breakout", side="both"),
    _S("S05", "Dip to SMA50 in a 4h uptrend", "core", "4h", s05,
       dict(fast=50, slow=200, tR=2.0), dict(fast=[30, 50, 80], slow=[150, 200, 250],
                                             tR=[1.5, 2.0, 3.0]), family="pullback", side="both"),
    _S("S06", "Retest of a broken range high", "core", "1h", s06,
       dict(n=48, within=24, tR=2.0), dict(n=[24, 48, 96], within=[12, 24, 36],
                                           tR=[1.5, 2.0, 3.0]), family="pullback", side="both"),
    _S("S07", "Dip below yesterday's VWAP in an uptrend", "core", "1h", s07,
       dict(sma=200, tR=2.0, buf=0.5), dict(sma=[100, 200, 300], tR=[1.5, 2.0, 3.0],
                                            buf=[0.25, 0.5, 1.0]), family="pullback", side="both"),
    _S("S08", "Three down bars inside a strong trend", "core", "4h", s08,
       dict(k=3, tmax=12, fast=50), dict(k=[2, 3, 4], tmax=[6, 12, 18], fast=[30, 50, 80]),
       family="pullback", side="both"),
    _S("S09", "Long-liquidation flush rebound", "core", "1h", s09,
       dict(oi=0.05, body=2.5, vol=3.0, tmax=12), dict(oi=[0.03, 0.05, 0.08],
                                                      body=[2.0, 2.5, 3.0], vol=[2.0, 3.0, 4.0]),
       family="flush", side="long"),
    _S("S10", "Short-squeeze candle fade", "core", "1h", s10,
       dict(oi=0.05, body=2.5, vol=3.0, tmax=12), dict(oi=[0.03, 0.05, 0.08],
                                                      body=[2.0, 2.5, 3.0], vol=[2.0, 3.0, 4.0]),
       family="flush", side="short"),
    _S("S11", "Confirmed 4h flush reversal", "core", "4h", s11,
       dict(oi=0.08, rng=3.0, tmax=6), dict(oi=[0.04, 0.06, 0.08, 0.12], rng=[2.0, 2.5, 3.0],
                                            tmax=[3, 6, 12]), family="flush", side="long"),
    _S("S12", "Retest of the liquidation low", "core", "1h", s12,
       dict(oi=0.05, body=2.5, vol=3.0, tmax=24), dict(oi=[0.03, 0.05, 0.08],
                                                      body=[2.0, 2.5, 3.0], tmax=[12, 24, 36]),
       family="flush", side="long"),
    _S("S13", "Crowded-long fade (high funding)", "core", "4h", s13,
       dict(fmin=0.0005, pct=95, tR=2.0), dict(fmin=[0.0003, 0.0005, 0.001], pct=[90, 95, 98],
                                               tR=[1.5, 2.0, 3.0]), family="funding", side="short"),
    _S("S14", "Crowded-short squeeze (negative funding)", "core", "4h", s14,
       dict(fmin=0.0003, pct=95, tR=2.0), dict(fmin=[0.0001, 0.0003, 0.0006], pct=[90, 95, 98],
                                               tR=[1.5, 2.0, 3.0]), family="funding", side="long"),
    _S("S15", "Failed breakout with crowded funding", "core", "1h", s15,
       dict(n=168, pct=90, within=6), dict(n=[72, 168, 336], pct=[80, 90, 95], within=[3, 6, 12]),
       family="funding", side="both"),
    _S("S16", "Paid to hold: negative funding in an uptrend", "core", "4h", s16,
       dict(fast=50, atr=2.0, fexit=0.0001), dict(fast=[30, 50, 80], atr=[1.5, 2.0, 3.0],
                                                  fexit=[0.0001, 0.0002, 0.0005]),
       family="funding", side="long"),
    _S("S17", "Pre-settlement drift with high funding", "core", "1h", s17,
       dict(f=0.0005, atr=1.0), dict(f=[0.0003, 0.0005, 0.001], atr=[0.5, 1.0, 2.0]),
       family="funding", side="both"),
    _S("S18", "Rally on falling OI (short covering) fade", "core", "1h", s18,
       dict(ret=0.05, oi=0.05, tmax=24), dict(ret=[0.03, 0.05, 0.08], oi=[0.03, 0.05, 0.08],
                                              tmax=[12, 24, 36]), family="oi", side="short"),
    _S("S19", "Sell-off into support on rising OI squeeze", "core", "1h", s19,
       dict(ret=0.05, oi=0.08, tR=2.0), dict(ret=[0.03, 0.05, 0.08], oi=[0.04, 0.08, 0.12],
                                             tR=[1.5, 2.0, 3.0]), family="oi", side="long"),
    _S("S20", "Price up + OI up continuation", "core", "4h", s20,
       dict(ret=0.03, oi=0.05, tR=2.0), dict(ret=[0.02, 0.03, 0.05], oi=[0.03, 0.05, 0.08],
                                             tR=[1.5, 2.0, 3.0]), family="oi", side="both"),
    _S("S21", "Top traders versus the crowd", "core", "1h", s21,
       dict(top=0.10, glob=0.10, tR=2.0), dict(top=[0.05, 0.10, 0.15], glob=[0.05, 0.10, 0.15],
                                               tR=[1.5, 2.0, 3.0]), family="oi", side="both"),
    _S("S22", "Asian range breakout at the EU open", "core", "1h", s22,
       dict(q=0.5, tR=2.0, exit_h=16), dict(q=[0.3, 0.5, 0.7], tR=[1.5, 2.0, 3.0],
                                            exit_h=[12, 16, 20]), family="session", side="both"),
    _S("S23", "US-open reversal of the European move", "core", "1h", s23,
       dict(eu=1.5, us=0.5, tR=2.0), dict(eu=[1.0, 1.5, 2.0], us=[0.25, 0.5, 1.0],
                                          tR=[1.5, 2.0, 3.0]), family="session", side="both"),
    _S("S24", "US-open momentum", "core", "1h", s24,
       dict(atr=1.0, vol=2.0, tR=2.0), dict(atr=[0.75, 1.0, 1.5], vol=[1.5, 2.0, 3.0],
                                            tR=[1.5, 2.0, 3.0]), family="session", side="both"),
    _S("S25", "Monday breakout of the weekend range", "core", "1h", s25,
       dict(w=0.5, tR=2.0, end_h=11), dict(w=[0.35, 0.5, 0.75], tR=[1.5, 2.0, 3.0],
                                           end_h=[6, 11, 16]), family="session", side="both"),
    _S("S26", "Alt catch-up after a BTC breakout", "core", "1h", s26,
       dict(btc=0.03, lag=0.03, corr=0.6), dict(btc=[0.02, 0.03, 0.05], lag=[0.02, 0.03, 0.05],
                                                corr=[0.5, 0.6, 0.7]), cross=True,
       family="alt-vs-btc", side="long"),
    _S("S27", "Alt catch-down after a BTC drop", "core", "1h", s27,
       dict(btc=0.03, lag=0.03, corr=0.6), dict(btc=[0.02, 0.03, 0.05], lag=[0.02, 0.03, 0.05],
                                                corr=[0.5, 0.6, 0.7]), cross=True,
       family="alt-vs-btc", side="short"),
    _S("S28", "Beta-adjusted laggard in a BTC uptrend", "core", "4h", s28,
       dict(z=2.0, tmax=18, atr=2.0), dict(z=[1.5, 2.0, 2.5], tmax=[12, 18, 30],
                                           atr=[1.5, 2.0, 3.0]), family="alt-vs-btc", side="long"),
    _S("S29", "Alt below its high when BTC makes a 30-day high", "core", "4h", s29,
       dict(depth=0.9, tR=2.0, tmax=42), dict(depth=[0.85, 0.9, 0.95], tR=[1.5, 2.0, 3.0],
                                              tmax=[24, 42, 60]), family="alt-vs-btc",
       side="long"),
    _S("M01", "Ride a fresh hype burst", "meme", "1h", m01,
       dict(z=3.0, oi=0.30, tmax=48), dict(z=[2.0, 3.0, 4.0], oi=[0.15, 0.30, 0.50],
                                           tmax=[24, 48, 72]), family="meme", side="long"),
    _S("M02", "Second leg of a not-yet-crowded pump", "meme", "1h", m02,
       dict(move=0.15, z=2.0, band=0.02), dict(move=[0.10, 0.15, 0.25], z=[1.0, 2.0, 3.0],
                                               band=[0.01, 0.02, 0.04]), family="meme",
       side="long"),
    _S("M03", "Fade extreme funding after a pump", "meme", "1h", m03,
       dict(f=0.001, jump=20.0, move=0.20), dict(f=[0.0005, 0.001, 0.002], jump=[10.0, 20.0, 40.0],
                                                 move=[0.10, 0.20, 0.30]), family="meme",
       side="short"),
    _S("M04", "Meme post-flush rebound", "meme", "1h", m04,
       dict(oi=0.15, drop=0.15, tmax=12), dict(oi=[0.10, 0.15, 0.25], drop=[0.10, 0.15, 0.25],
                                               tmax=[6, 12, 24]), family="meme", side="long"),
    _S("M05", "New-listing breakdown", "meme", "1h", m05,
       dict(days=14, tR=2.0, tmax=72), dict(days=[7, 14, 21], tR=[1.5, 2.0, 3.0],
                                            tmax=[48, 72, 120]), family="meme", side="short"),
    _S("M06", "Fade a first-week spike", "meme", "1h", m06,
       dict(ret=0.40, days=10, tR=2.0), dict(ret=[0.25, 0.40, 0.60], days=[5, 10, 20],
                                             tR=[1.5, 2.0, 3.0]), family="meme", side="short"),
    _S("M07", "Volume-climax reversal", "meme", "1h", m07,
       dict(z=5.0, ret=0.30, wick=0.6), dict(z=[3.0, 5.0, 7.0], ret=[0.15, 0.30, 0.45],
                                             wick=[0.4, 0.6, 0.75]), family="meme", side="short"),
    _S("M08", "Meme laggard after a BTC risk-on move", "meme", "1h", m08,
       dict(btc=0.03, lag=0.03, corr=0.5), dict(btc=[0.02, 0.03, 0.05], lag=[0.02, 0.03, 0.05],
                                                corr=[0.4, 0.5, 0.6]), cross=True,
       family="meme", side="long"),
    _S("M09", "Coiled OI break", "meme", "1h", m09,
       dict(oi=0.50, flat=0.05, n=48), dict(oi=[0.30, 0.50, 0.80], flat=[0.03, 0.05, 0.08],
                                            n=[24, 48, 96]), family="meme", side="both"),
    _S("M10", "Squeeze of a heavily shorted meme", "meme", "1h", m10,
       dict(f=0.001, oi=0.20, tR=2.0), dict(f=[0.0005, 0.001, 0.002], oi=[0.10, 0.20, 0.40],
                                            tR=[1.5, 2.0, 3.0]), family="meme", side="long"),
    _S("M11", "Hype decay short", "meme", "4h", m11,
       dict(z=4.0, tR=2.0, tmax=42), dict(z=[3.0, 4.0, 5.0], tR=[1.5, 2.0, 3.0],
                                          tmax=[24, 42, 60]), family="meme", side="short"),
]}
