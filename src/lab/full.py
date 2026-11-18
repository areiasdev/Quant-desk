"""Stage 2: full testing protocol on the best ~10 setups from the screen.

For each setup: default-parameter OOS headline (+ IS), double fees / double slippage, the full
parameter grid (<= 3 params x <= 4 values), walk-forward (optimise on rolling 12 months, test the
next 3, roll by 3), parameter robustness (one-step neighbours of the defaults + heatmap), Monte
Carlo (5,000 reshuffles and 5,000 bootstraps of the OOS trades), BTC buy-and-hold (perp and
spot-like) and an equal-weight meme basket, BTC regimes, metrics and 1-5 scores.
Writes research/lab/full/<id>.json. Run from src/: ../.venv/bin/python -m lab.full S02 S16 ...
"""
from __future__ import annotations

import itertools
import json
import multiprocessing as mp
import sys
import time
import warnings

import numpy as np
import pandas as pd

from .data import IS_END, IS_START, OOS_END, OOS_START, WINDOW_START, load_ctx
from .engine import BASE, metrics, monte_carlo, portfolio
from .run import (btc_buy_hold, btc_regimes, meme_basket, regime_split, run_setup,
                  tradeable_share, with_costs)
from .screen import OUT, SCEN, TRADES, concentration
from .setups import SETUPS

warnings.simplefilter("ignore")
FULL = OUT / "full"
CTX = None
TRAIN_M, TEST_M = 12, 3
MIN_TRAIN_TRADES = 10


def combos(st):
    keys = list(st.grid)
    return keys, [dict(zip(keys, v)) for v in itertools.product(*(st.grid[k] for k in keys))]


def _key(c):
    return "|".join(f"{k}={v}" for k, v in c.items())


def _work(args):
    sid, c = args
    raw = run_setup(CTX, SETUPS[sid], c)
    keep = ["symbol", "side", "entry_time", "exit_time", "entry", "exit", "entry_kind",
            "exit_kind", "funding", "stop_frac", "slip_bp", "min_notional_est"]
    return sid, _key(c), raw[keep] if len(raw) else raw


def wf_windows():
    out, m = [], WINDOW_START
    while True:
        tr0, te0 = m, m + pd.DateOffset(months=TRAIN_M)
        te1 = te0 + pd.DateOffset(months=TEST_M)
        if te1 > OOS_END:
            break
        out.append((tr0, te0, te1))
        m = m + pd.DateOffset(months=TEST_M)
    return out


def _in(tr, a, b):
    return tr[(tr["entry_time"] >= a.value) & (tr["entry_time"] < b.value)]


def walk_forward(grid_tr: dict, default_key: str):
    picks, test_parts = [], []
    for tr0, te0, te1 in wf_windows():
        best, best_v = default_key, -np.inf
        for k, tr in grid_tr.items():
            x = _in(tr, tr0, te0) if len(tr) else tr
            if len(x) < MIN_TRAIN_TRADES:
                continue
            v = x["R"].sum()
            if v > best_v or (v == best_v and k == default_key):
                best, best_v = k, v
        t = grid_tr[best]
        tt = _in(t, te0, te1) if len(t) else t
        picks.append(dict(train=f"{tr0:%Y-%m}..{te0 - pd.Timedelta(days=1):%Y-%m}",
                          test=f"{te0:%Y-%m}..{te1 - pd.Timedelta(days=1):%Y-%m}", params=best,
                          train_R=None if best_v == -np.inf else float(best_v),
                          test_trades=int(len(tt)), test_R=float(tt["R"].sum()) if len(tt) else 0))
        if len(tt):
            test_parts.append(tt)
    wf = pd.concat(test_parts) if test_parts else pd.DataFrame(columns=["entry_time", "R"])
    return picks, wf


def neighbours(st, default):
    keys = list(st.grid)
    out = []
    for k in keys:
        vals = st.grid[k]
        i = vals.index(default[k])
        for j in (i - 1, i + 1):
            if 0 <= j < len(vals):
                c = {kk: default[kk] for kk in keys}
                c[k] = vals[j]
                out.append(_key(c))
    return out


def heatmap(st, default, oos_exp):
    keys = list(st.grid)
    a, b = keys[0], keys[1] if len(keys) > 1 else None
    rows = []
    for va in st.grid[a]:
        row = {a: va}
        for vb in (st.grid[b] if b else [None]):
            c = {k: default[k] for k in keys}
            c[a] = va
            if b:
                c[b] = vb
            row[f"{b}={vb}" if b else "exp_R"] = oos_exp.get(_key(c), np.nan)
        rows.append(row)
    fixed = {k: default[k] for k in keys[2:]}
    return pd.DataFrame(rows), fixed


def score(r) -> dict:
    cagr, dd, n, ls = r["oos_cagr"], r["oos_max_dd"], r["oos_trades"], r["oos_losing_streak"]
    s_p = 1 if cagr <= 0 else 2 if cagr <= 0.05 else 3 if cagr <= 0.15 else 4 if cagr <= 0.30 else 5
    s_dd = 1 if dd > 0.5 else 2 if dd > 0.3 else 3 if dd > 0.2 else 4 if dd > 0.1 else 5
    s_n = 1 if n < 50 else 2 if n < 100 else 3 if n < 200 else 4 if n < 400 else 5
    s_ls = 1 if ls > 20 else 2 if ls >= 15 else 3 if ls >= 10 else 4 if ls >= 6 else 5
    be, sw = r["regimes"]["bear"]["exp_R"], r["regimes"]["sideways"]["exp_R"]
    be, sw = (-9 if not np.isfinite(be) else be), (-9 if not np.isfinite(sw) else sw)
    if be < 0 and sw < 0:
        s_r = 1
    elif be < 0 or sw < 0:
        s_r = 2
    elif min(be, sw) < 0.05:
        s_r = 3
    elif min(be, sw) < 0.15:
        s_r = 4
    else:
        s_r = 5
    return dict(profit=s_p, drawdown=s_dd, trades=s_n, streak=s_ls, bear_sideways=s_r,
                total=s_p + s_dd + s_n + s_ls + s_r)


def full_one(ctx, sid, grid_raw, bh, bh_spot, basket, reg):
    st = SETUPS[sid]
    raw = pd.read_parquet(TRADES / f"{sid}_default.parquet")
    tr = with_costs(raw, BASE)
    r = dict(id=sid, name=st.name, track=st.kind, tf=st.tf, defaults=st.defaults, grid=st.grid)
    mo = metrics(tr, OOS_START, OOS_END)
    mi = metrics(tr, IS_START, IS_END)
    monthly = mo.pop("monthly")
    mi.pop("monthly", None)
    r.update({f"oos_{k}": v for k, v in mo.items()})
    r.update({f"is_{k}": v for k, v in mi.items()})
    r["oos_monthly"] = {k: float(v) for k, v in monthly.items()}
    for k, c in SCEN.items():
        if k == "base":
            continue
        m = metrics(with_costs(raw, c), OOS_START, OOS_END)
        r[f"oos_exp_R_{k}"], r[f"oos_cagr_{k}"] = m["exp_R"], m["cagr"]
        r[f"oos_total_R_{k}"] = m["total_R"]
    w = _in(tr, OOS_START, OOS_END)
    r["oos_tradeable_share_est"] = tradeable_share(w)
    r["oos_median_stop_pct"] = float(w["stop_frac"].median() * 100)
    r["oos_median_notional_eur100"] = float(np.minimum(1.10 / w["stop_frac"], 330).median())
    r["oos_funding_R_total"] = float((w["funding"] / w["stop_frac"]).sum())
    r["oos_long_trades"] = int((w["side"] == 1).sum())
    r["oos_short_trades"] = int((w["side"] == -1).sum())
    r["oos_long_exp_R"] = float(w.loc[w["side"] == 1, "R"].mean()) if (w["side"] == 1).any() else None
    r["oos_short_exp_R"] = float(w.loc[w["side"] == -1, "R"].mean()) \
        if (w["side"] == -1).any() else None
    r["oos_exit_reasons"] = w["reason"].value_counts().to_dict()
    # what a EUR100 account can actually place (ESTIMATE min notional, 1% risk, <= 3x)
    ok = np.minimum(0.01 * 110 / w["stop_frac"], 330) >= w["min_notional_est"]
    mt = metrics(w[ok], OOS_START, OOS_END)
    mt.pop("monthly", None)
    r["oos_tradeable_only"] = {k: mt[k] for k in ("trades", "exp_R", "total_R", "cagr", "max_dd",
                                                  "losing_streak", "worst_month")}
    r["oos_untradeable_symbols"] = w.loc[~ok, "symbol"].value_counts().head(6).to_dict()
    ev = pd.Series(np.r_[np.ones(len(w)), -np.ones(len(w))],
                   index=np.r_[w["entry_time"].to_numpy(), w["exit_time"].to_numpy()])
    r["oos_max_concurrent"] = int(ev.sort_index(kind="stable").cumsum().max()) if len(w) else 0
    r["oos_trades_per_month"] = len(w) / 24
    r["oos_median_hold_h"] = float(((w["exit_time"] - w["entry_time"]) / 3.6e12).median())
    r["oos_symbols"] = int(w["symbol"].nunique())
    r.update(concentration(w, raw))
    # yearly split of OOS
    yrs = {}
    for a, b in ((OOS_START, pd.Timestamp("2025-11-01", tz="UTC")),
                 (pd.Timestamp("2025-11-01", tz="UTC"), OOS_END)):
        m = metrics(tr, a, b)
        yrs[f"{a:%Y-%m}..{b - pd.Timedelta(days=1):%Y-%m}"] = dict(
            trades=m["trades"], exp_R=m["exp_R"], cagr=m["cagr"], max_dd=m["max_dd"],
            bh_btc_spot=btc_buy_hold(ctx, a, b, funding=False)["total_return"],
            bh_btc_perp=btc_buy_hold(ctx, a, b)["total_return"])
    r["oos_by_year"] = yrs
    # Monte Carlo on the OOS portfolio's per-trade return on equity
    pt = portfolio(w)
    r["mc"] = monte_carlo(pt.loc[pt["taken"], "ret_on_equity"].to_numpy(), n=5000)
    # benchmarks and regimes
    r["bh_btc_perp"], r["bh_btc_spot"] = bh, bh_spot
    if st.kind == "meme":
        r["meme_basket"] = basket
    r["regimes"] = regime_split(w, reg)
    # grid: OOS expectancy per combo, walk-forward, robustness
    keys, cs = combos(st)
    dkey = _key({k: st.defaults[k] for k in keys})
    grid_tr = {k: (with_costs(v, BASE) if len(v) else v) for k, v in grid_raw.items()}
    oos_exp, oos_n, is_exp = {}, {}, {}
    for k, v in grid_tr.items():
        o = _in(v, OOS_START, OOS_END) if len(v) else v
        i_ = _in(v, IS_START, IS_END) if len(v) else v
        oos_exp[k] = float(o["R"].mean()) if len(o) else np.nan
        oos_n[k] = int(len(o))
        is_exp[k] = float(i_["R"].mean()) if len(i_) else np.nan
    r["grid_oos_exp_R"], r["grid_oos_trades"], r["grid_is_exp_R"] = oos_exp, oos_n, is_exp
    nb = neighbours(st, st.defaults)
    pos = [oos_exp[k] > 0 for k in nb if np.isfinite(oos_exp.get(k, np.nan))]
    r["robust_neighbours"] = nb
    r["robust_neighbour_pos_share"] = float(np.mean(pos)) if pos else 0.0
    allv = [v for v in oos_exp.values() if np.isfinite(v)]
    r["robust_grid_pos_share"] = float(np.mean([v > 0 for v in allv])) if allv else 0.0
    hm, fixed = heatmap(st, st.defaults, oos_exp)
    r["heatmap"] = dict(table=hm.to_dict(orient="records"), fixed=fixed)
    picks, wf = walk_forward(grid_tr, dkey)
    r["wf_picks"] = picks
    wfo = _in(wf, OOS_START, OOS_END) if len(wf) else wf
    if len(wfo):
        m = metrics(wfo, OOS_START, OOS_END)
        m.pop("monthly")
        r["wf_oos"] = m
    else:
        r["wf_oos"] = dict(trades=0, total_R=0.0, exp_R=np.nan, cagr=0.0, max_dd=0.0)
    r["wf_all_total_R"] = float(wf["R"].sum()) if len(wf) else 0.0
    r["wf_all_trades"] = int(len(wf))
    r["wf_all_span"] = f"{wf_windows()[0][1]:%Y-%m}..{OOS_END - pd.Timedelta(days=1):%Y-%m}"
    # scores and gates
    r["score"] = score(r)
    bench = max(bh["cagr"], bh_spot["cagr"])
    kill, extra = [], []
    if r["oos_trades"] < 50:
        kill.append("<50 OOS trades")
    if not (r["oos_exp_R_both2x"] > 0):
        kill.append("negative after double costs")
    if not (r["oos_cagr"] > bench):
        kill.append("does not beat BTC buy-and-hold")
    if r["robust_neighbour_pos_share"] < 0.5:
        kill.append("edge only at one parameter value")
    if not (r["wf_oos"]["total_R"] > 0):
        extra.append("walk-forward OOS not positive")
    if not (r.get("oos_exp_R_both2x_ex_top", -1) > 0):
        extra.append(f"edge depends on one coin ({r.get('top_symbol')})")
    if r["oos_tradeable_share_est"] < 0.5:
        extra.append("UNTRADEABLE AT EUR100 (ESTIMATE min notional)")
    if not (r["oos_tradeable_only"]["cagr"] > bench):
        extra.append("does not beat BTC buy-and-hold on EUR100-tradeable trades (ESTIMATE)")
    if st.kind == "meme" and not (r["oos_cagr"] > basket["cagr"]):
        extra.append("does not beat equal-weight meme basket")
    r["kill_rules_failed"], r["lab_gates_failed"] = kill, extra
    r["status"] = "PASS" if not kill and not extra else "KILLED"
    return r


def _clean(o):
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def main(ids):
    global CTX
    FULL.mkdir(parents=True, exist_ok=True)
    CTX = load_ctx()
    tasks = []
    for sid in ids:
        _, cs = combos(SETUPS[sid])
        tasks += [(sid, c) for c in cs]
    t0 = time.time()
    grid = {sid: {} for sid in ids}
    with mp.get_context("fork").Pool(4) as pool:
        for i, (sid, k, raw) in enumerate(pool.imap_unordered(_work, tasks, chunksize=1)):
            grid[sid][k] = raw
            if i % 20 == 0:
                print(f"grid {i + 1}/{len(tasks)} {time.time() - t0:.0f}s", flush=True)
    bh, bh_spot = btc_buy_hold(CTX), btc_buy_hold(CTX, funding=False)
    basket = meme_basket(CTX)
    reg = btc_regimes(CTX)
    for sid in ids:
        r = full_one(CTX, sid, grid[sid], bh, bh_spot, basket, reg)
        (FULL / f"{sid}.json").write_text(json.dumps(_clean(r), indent=1, default=str))
        print(sid, r["status"], r["kill_rules_failed"], r["lab_gates_failed"], r["score"],
              f"wf_oos_R={r['wf_oos']['total_R']:.1f}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
