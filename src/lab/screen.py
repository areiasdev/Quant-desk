"""Stage 1: screen all 40 setups with RADAR's default parameters.

Writes research/lab/screen.csv (one row per setup, OOS headline + IS secondary) and caches the
raw trade lists in data/clean/lab_cache/trades/ for stage 2.
Run from src/:  ../.venv/bin/python -m lab.screen
"""
from __future__ import annotations

import time
import warnings

import numpy as np
import pandas as pd

from .data import CACHE, IS_END, IS_START, OOS_END, OOS_START, load_ctx
from .engine import BASE, metrics
from .run import btc_buy_hold, run_setup, tradeable_share, with_costs
from .setups import SETUPS

warnings.simplefilter("ignore")
OUT = __import__("pathlib").Path(__file__).resolve().parents[2] / "research" / "lab"
TRADES = CACHE / "trades"

SCEN = {"base": BASE, "fee2x": BASE.scaled(2.0, 1.0), "slip2x": BASE.scaled(1.0, 2.0),
        "both2x": BASE.scaled(2.0, 2.0)}


def concentration(w: pd.DataFrame, raw: pd.DataFrame) -> dict:
    """How much of the OOS result comes from the single best symbol (by total R)."""
    if len(w) == 0:
        return {}
    by = w.groupby("symbol")["R"].sum().sort_values()
    top = by.index[-1]
    tot = by.sum()
    rest = raw[(raw["symbol"] != top)]
    rest = rest[(rest["entry_time"] >= OOS_START.value) & (rest["entry_time"] < OOS_END.value)]
    out = dict(top_symbol=top, top_symbol_R=float(by.iloc[-1]),
               top_symbol_share=float(by.iloc[-1] / tot) if tot > 0 else np.nan,
               best_trade_R=float(w["R"].max()))
    if len(rest):
        out["oos_exp_R_ex_top"] = float(with_costs(rest, BASE)["R"].mean())
        out["oos_exp_R_both2x_ex_top"] = float(with_costs(rest, SCEN["both2x"])["R"].mean())
    return out


def screen_row(sid, raw, bh, bh_spot) -> dict:
    st = SETUPS[sid]
    row = dict(id=sid, name=st.name, track=st.kind, tf=st.tf, side=st.side, family=st.family)
    if len(raw) == 0:
        row.update(oos_trades=0, kill_reasons="no trades")
        return row
    for k, c in SCEN.items():
        tr = with_costs(raw, c)
        m = metrics(tr, OOS_START, OOS_END)
        if k == "base":
            mi = metrics(tr, IS_START, IS_END)
            row.update(is_trades=mi["trades"], is_exp_R=mi["exp_R"], is_cagr=mi["cagr"],
                       oos_trades=m["trades"], oos_win=m["win_rate"], oos_exp_R=m["exp_R"],
                       oos_total_R=m["total_R"], oos_pf=m["pf"], oos_cagr=m["cagr"],
                       oos_total_ret=m["total_return"], oos_max_dd=m["max_dd"],
                       oos_worst_month=m["worst_month"], oos_streak=m["losing_streak"],
                       oos_exposure=m["exposure"], oos_avg_hold_h=m["avg_hold_h"])
            w = tr[(tr["entry_time"] >= OOS_START.value) & (tr["entry_time"] < OOS_END.value)]
            row["oos_tradeable_share_est"] = tradeable_share(w)
            row["oos_funding_R"] = float((w["funding"] / w["stop_frac"]).mean()) if len(w) else 0
            row.update(concentration(w, raw))
        else:
            row[f"oos_exp_R_{k}"] = m["exp_R"]
            row[f"oos_cagr_{k}"] = m["cagr"]
    row["bh_btc_perp_cagr"] = bh["cagr"]
    row["bh_btc_spot_cagr"] = bh_spot["cagr"]
    bench = max(bh["cagr"], bh_spot["cagr"])
    reasons = []
    if row["oos_trades"] < 50:
        reasons.append("<50 OOS trades")
    if not (row["oos_exp_R"] > 0):
        reasons.append("negative OOS after base costs")
    if not (row.get("oos_exp_R_both2x", -1) > 0):
        reasons.append("negative after double costs")
    if not (row["oos_cagr"] > bench):
        reasons.append("does not beat BTC buy-and-hold")
    if row["oos_trades"] and row["oos_tradeable_share_est"] < 0.5:
        reasons.append("UNTRADEABLE AT EUR100 (ESTIMATE min notional)")
    row["kill_reasons"] = "; ".join(reasons)
    return row


def main():
    TRADES.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    ctx = load_ctx()
    bh = btc_buy_hold(ctx)
    bh_spot = btc_buy_hold(ctx, funding=False)
    rows = []
    for sid, st in SETUPS.items():
        t0 = time.time()
        raw = run_setup(ctx, st)
        raw.to_parquet(TRADES / f"{sid}_default.parquet")
        rows.append(screen_row(sid, raw, bh, bh_spot))
        print(sid, rows[-1].get("oos_trades"), rows[-1].get("oos_exp_R"),
              rows[-1]["kill_reasons"], f"{time.time() - t0:.1f}s", flush=True)
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "screen.csv", index=False, float_format="%.5g")
    print("perp", bh)
    print("spot", bh_spot)
    return df


if __name__ == "__main__":
    main()
