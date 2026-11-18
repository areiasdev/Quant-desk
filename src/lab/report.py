"""Stage 3: write research/lab/results_all.csv, REPORT.md, research/cards/<id>.md and
research/lab/top_setup.json from the screen (screen.csv) and full-protocol results (full/*.json).
Every number printed here comes from those files. Run from src/: ../.venv/bin/python -m lab.report
"""
from __future__ import annotations

import json
import warnings

import numpy as np
import pandas as pd

from .data import OOS_END, OOS_START, load_ctx
from .run import btc_buy_hold, btc_regimes, meme_basket
from .screen import OUT
from .setups import SETUPS

warnings.simplefilter("ignore")
REPO = OUT.parents[1]
CARDS = REPO / "research" / "cards"

OFFICIAL = ["<50 OOS trades", "negative after double costs", "does not beat BTC buy-and-hold",
            "edge only at one parameter value"]
ORDER = ["<50 OOS trades", "negative OOS after base costs", "negative after double costs",
         "does not beat BTC buy-and-hold", "edge only at one parameter value",
         "walk-forward OOS not positive", "edge depends on one coin",
         "does not beat BTC buy-and-hold on EUR100-tradeable trades (ESTIMATE)",
         "UNTRADEABLE AT EUR100 (ESTIMATE min notional)", "does not beat equal-weight meme basket"]


def pct(x, nd=1):
    return "n/a" if x is None or (isinstance(x, float) and not np.isfinite(x)) else \
        f"{100 * x:+.{nd}f}%"


def pc(x, nd=1):  # unsigned percent
    return "n/a" if x is None or (isinstance(x, float) and not np.isfinite(x)) else \
        f"{100 * x:.{nd}f}%"


def f(x, nd=3):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "n/a"
    return f"{x:+.{nd}f}" if isinstance(x, float) else str(x)


def _norm(reason: str) -> str:
    return "edge depends on one coin" if reason.startswith("edge depends on one coin") else reason


def build_table():
    sc = pd.read_csv(OUT / "screen.csv")
    full = {p.stem: json.loads(p.read_text()) for p in sorted((OUT / "full").glob("*.json"))}
    rows = []
    for _, s in sc.iterrows():
        r = s.to_dict()
        sid = r["id"]
        sr = [x.strip() for x in str(r.get("kill_reasons") or "").split(";")
              if x.strip() and x.strip() != "nan"]
        r["screen_result"] = "KILLED: " + "; ".join(sr) if sr else "KEPT (passed screen)"
        r["full_protocol"] = sid in full
        if sid in full:
            j = full[sid]
            reasons = j["kill_rules_failed"] + j["lab_gates_failed"]
            r.update(robust_neighbour_pos_share=j["robust_neighbour_pos_share"],
                     robust_grid_pos_share=j["robust_grid_pos_share"],
                     wf_oos_trades=j["wf_oos"]["trades"], wf_oos_total_R=j["wf_oos"]["total_R"],
                     wf_oos_exp_R=j["wf_oos"].get("exp_R"), wf_oos_cagr=j["wf_oos"]["cagr"],
                     wf_oos_max_dd=j["wf_oos"]["max_dd"],
                     tradeable_only_cagr=j["oos_tradeable_only"]["cagr"],
                     tradeable_only_trades=j["oos_tradeable_only"]["trades"],
                     mc_bootstrap_dd_p95=j["mc"].get("bootstrap_dd_p95"),
                     mc_bootstrap_p_ruin=j["mc"].get("bootstrap_p_ruin"),
                     mc_shuffle_dd_p95=j["mc"].get("shuffle_dd_p95"),
                     mc_shuffle_p_ruin=j["mc"].get("shuffle_p_ruin"),
                     bear_exp_R=j["regimes"]["bear"]["exp_R"],
                     sideways_exp_R=j["regimes"]["sideways"]["exp_R"],
                     bull_exp_R=j["regimes"]["bull"]["exp_R"],
                     y1_exp_R=list(j["oos_by_year"].values())[0]["exp_R"],
                     y2_exp_R=list(j["oos_by_year"].values())[1]["exp_R"],
                     **{f"score_{k}": v for k, v in j["score"].items()})
            n_off = len(j["kill_rules_failed"])
            n_lab = len(j["lab_gates_failed"])
        else:
            reasons = sr
            n_off = sum(1 for x in sr if x in OFFICIAL or x == "negative OOS after base costs")
            n_lab = len(sr) - n_off
        reasons = [_norm(x) if not x.startswith("edge depends") else x for x in reasons]
        r["final_status"] = "KEPT" if not reasons else "KILLED"
        r["final_reasons"] = "; ".join(reasons) if reasons else "passed every gate"
        normed = [_norm(x) for x in reasons]
        prim = next((o for o in ORDER if o in normed), "")
        if prim == "negative OOS after base costs":
            prim = "negative after double costs"
        r["primary_kill_reason"] = prim
        r["_n_off"], r["_n_lab"] = n_off, n_lab
        rows.append(r)
    df = pd.DataFrame(rows)
    df["_score"] = df.get("score_total", pd.Series(np.nan, index=df.index)).fillna(-1)
    df["_kept"] = (df["final_status"] == "KEPT").astype(int)
    df["_full"] = df["full_protocol"].astype(int)
    df = df.sort_values(["_kept", "_full", "_n_off", "_n_lab", "_score", "oos_exp_R"],
                        ascending=[False, False, True, True, False, False], kind="stable")
    df.insert(0, "rank", range(1, len(df) + 1))
    return df, full


def write_csv(df):
    cols = ["rank", "id", "name", "track", "tf", "side", "final_status", "primary_kill_reason",
            "final_reasons", "screen_result", "full_protocol", "oos_trades", "oos_win", "oos_exp_R",
            "oos_total_R", "oos_pf", "oos_cagr", "oos_total_ret", "oos_max_dd", "oos_worst_month",
            "oos_streak", "oos_exposure", "oos_exp_R_fee2x", "oos_exp_R_slip2x",
            "oos_exp_R_both2x", "oos_cagr_both2x", "is_trades", "is_exp_R", "is_cagr",
            "bh_btc_perp_cagr", "bh_btc_spot_cagr", "oos_tradeable_share_est", "top_symbol",
            "top_symbol_share", "oos_exp_R_both2x_ex_top", "robust_neighbour_pos_share",
            "robust_grid_pos_share", "wf_oos_trades", "wf_oos_total_R", "wf_oos_exp_R",
            "wf_oos_cagr", "wf_oos_max_dd", "tradeable_only_trades", "tradeable_only_cagr",
            "mc_shuffle_dd_p95", "mc_shuffle_p_ruin", "mc_bootstrap_dd_p95", "mc_bootstrap_p_ruin",
            "bull_exp_R", "bear_exp_R", "sideways_exp_R", "y1_exp_R", "y2_exp_R",
            "score_profit", "score_drawdown", "score_trades", "score_streak",
            "score_bear_sideways", "score_total"]
    df[[c for c in cols if c in df]].to_csv(OUT / "results_all.csv", index=False,
                                            float_format="%.5g")


def kill_counts(df):
    k = df[df["final_status"] == "KILLED"]
    prim = k["primary_kill_reason"].value_counts()
    allr = {}
    for s in k["final_reasons"]:
        for x in s.split(";"):
            x = _norm(x.strip())
            x = "negative after double costs" if x == "negative OOS after base costs" else x
            allr[x] = allr.get(x, 0) + 1
    # each setup counted once per reason
    allr = {}
    for s in k["final_reasons"]:
        seen = set()
        for x in s.split(";"):
            x = _norm(x.strip())
            x = "negative after double costs" if x == "negative OOS after base costs" else x
            seen.add(x)
        for x in seen:
            allr[x] = allr.get(x, 0) + 1
    return len(k), prim, pd.Series(allr).sort_values(ascending=False)


# ------------------------------------------------------------------ narrative (LAB's words)
def _wf_stats(j):
    q = j["wf_picks"]
    neg = sum(1 for p in q if p["test_R"] < 0)
    best = max(q, key=lambda p: p["test_R"])
    return neg, len(q), best


def _yr(j, i):
    return list(j["oos_by_year"].values())[i]


def _s16_fail(j):
    neg, n, best = _wf_stats(j)
    y1, y2 = _yr(j, 0), _yr(j, 1)
    return [
        f"The OOS profit is front-loaded: year 1 {y1['exp_R']:+.3f}R/trade, year 2 "
        f"{y2['exp_R']:+.3f}R/trade. The walk-forward profit leans on ONE quarter: "
        f"{best['test']} made {best['test_R']:+.1f}R, the other {n - 1} test quarters together "
        f"{j['wf_all_total_R'] - best['test_R']:+.1f}R; {neg} of {n} test quarters lost money.",
        f"It is long-only. In bear months it lost ({j['regimes']['bear']['exp_R']:+.3f}R/trade). "
        f"Year 2 beat BTC only because BTC fell ({pct(y2['bh_btc_spot'])} spot-like); in year 1 "
        f"it made {pct(y1['cagr'])} while BTC made {pct(y1['bh_btc_spot'])}.",
        f"Win rate is {pc(j['oos_win_rate'], 0)}: profits rely on a few large trend legs (best "
        f"trade {j['best_trade_R']:+.1f}R). Missing a handful of them (downtime, a skipped "
        "signal) changes the result materially.",
        f"At EUR100, BTC signals cannot be placed (ESTIMATE $100 minimum notional > the position "
        f"size): {pc(1 - j['oos_tradeable_share_est'], 0)} of OOS trades are not placeable, so "
        "the live trade list differs from the backtest; the tradeable-only row is an "
        "approximation at fixed $110 equity.",
        "Funding history ends 2026-10-31; the behaviour of funding (the input that defines the "
        "signal) after that is untested.",
        "Minimum notional, lot size and leverage tiers are ESTIMATES (exchangeInfo blocked).",
    ]


def _s02_fail(j):
    y2 = _yr(j, 1)
    return [
        f"{pc(j['top_symbol_share'], 0)} of the OOS profit came from ONE coin ({j['top_symbol']}, "
        f"2025-09) and the best single trade made {j['best_trade_R']:+.1f}R, including a huge "
        f"funding credit. Without it, double-cost expectancy is "
        f"{j['oos_exp_R_both2x_ex_top']:+.3f}R.",
        f"OOS closed-trade drawdown was {pc(j['oos_max_dd'], 0)} at 1% risk per trade; with up to "
        f"{j['oos_max_concurrent']} positions open at once the account is effectively levered to "
        "the whole market. That is deeper than the owner's 50% ruin line, measured from the peak.",
        f"The second OOS year lost money: {y2['exp_R']:+.3f}R/trade, {pct(y2['cagr'])} at 1% "
        f"risk, max DD {pc(y2['max_dd'], 0)}.",
        f"Shorts lost OOS ({j['oos_short_exp_R']:+.3f}R/trade over {j['oos_short_trades']} "
        f"trades); only longs made money ({j['oos_long_exp_R']:+.3f}R).",
        f"Longest losing streak OOS: {j['oos_losing_streak']} trades (walk-forward "
        f"{j['wf_oos']['losing_streak']}): very hard to follow on a monthly check.",
        f"Monte Carlo bootstrap: 5% worst-case drawdown {pc(j['mc']['bootstrap_dd_p95'], 0)}, "
        f"P(ruin) {pc(j['mc']['bootstrap_p_ruin'], 1)}.",
    ]


def _s19_fail(j):
    return [
        f"KILLED by LAB's walk-forward gate: optimising on 12 months and trading the next 3 gave "
        f"{j['wf_oos']['total_R']:+.2f}R over the OOS window ({j['wf_oos']['trades']} trades).",
        f"{pc(j['top_symbol_share'], 0)} of OOS profit came from one coin ({j['top_symbol']}); "
        f"without it the double-cost expectancy is {j['oos_exp_R_both2x_ex_top']:+.3f}R.",
        f"In-sample (2022-11..2024-10) it lost money ({j['is_exp_R']:+.3f}R/trade over "
        f"{j['is_trades']} trades); the OOS gain may be luck.",
        f"Few trades (~{j['oos_trades_per_month']:.1f} per month; {j['oos_trades']} OOS trades), "
        f"and year 2 was near zero ({_yr(j, 1)['exp_R']:+.3f}R/trade).",
    ]

NARR = {
    "S16": dict(
        idea="Buy a top-10 coin in a 4h uptrend while shorts are still paying longs to hold "
             "(negative funding), and get out when the trend or the funding turns.",
        why="When price trends up but funding is negative, the crowd on the perp is net short "
            "against the trend (or hedged with stale shorts). Longs are paid funding while they "
            "wait, and each push higher forces some of those shorts to cover, adding buying. The "
            "people who lose are trend-fighting shorts who pay funding *and* get squeezed; "
            "secondarily, cash-and-carry desks whose basis trades flip negative pay the funding "
            "we receive.",
        rules=[
            "Universe: each calendar month, the 10 Binance USDT-M crypto perps with the highest "
            "quote volume over the 30 days before the month starts (TradFi/index/stablecoin perps "
            "excluded; must have traded in the last 24h). Only new entries need membership; an open "
            "position may run past month end.",
            "Bars: 4h, anchored 00/04/08/12/16/20 UTC, complete bars only. SMA = simple average of "
            "4h closes; ATR14 = simple mean of the last 14 true ranges.",
            "F8 = the latest SETTLED funding rate x 8 / its funding interval in hours, as known at "
            "the 4h close (no predicted funding).",
            "Entry (long only): at a 4h close, close > SMA50 > SMA200 and F8 < 0. Buy at market at "
            "the next 4h open. One entry per uptrend leg: after a signal, the setup re-arms only "
            "after a 4h close below SMA50 (a signal is consumed even if it was not taken). Never "
            "enter on a bar with zero trades. One position per symbol.",
            "Stop: entry - 2 x ATR14 (ATR of the signal bar), fixed, resting stop-market. If a bar "
            "opens below the stop, exit at that open.",
            "Exit: (a) the first 4h close below SMA50 -> sell at the next 4h open; or (b) the first "
            "funding settlement with F8 > +0.01% (seen at a 4h close) -> sell at the next 4h open. "
            "No profit target, no time stop.",
        ],
        kill=lambda j: "Long-only trend following: it loses in BTC bear months (OOS bear-month "
             f"expectancy {j['regimes']['bear']['exp_R']:+.3f}R). A sustained bear market, or a "
             "structural change that keeps funding positive in every uptrend (no signals, or "
             "signals only at tops), kills it. Stop paper-trading it if the drawdown passes 30% "
             "of peak equity or the rolling 100-trade expectancy after costs goes below zero.",
        weak=lambda j: "Rolling 12-month expectancy below +0.05R after costs, a losing streak "
             f"longer than {j['wf_oos']['losing_streak']} (walk-forward longest), or a drawdown "
             f"beyond {pc(j['mc']['bootstrap_dd_p95'], 0)} (Monte Carlo 95th percentile). The "
             f"second OOS year ({_yr(j, 1)['exp_R']:+.3f}R/trade) was already at the first "
             "threshold.",
        fail=_s16_fail),
    "S02": dict(
        idea="Trade a 4h close outside the last 5 days' range on rising volume, in the breakout "
             "direction, and trail out on a close back through the 10-bar extreme.",
        why="A multi-day range stores resting stops just beyond its edges; a 4h close outside it "
            "on rising volume fills those stops as market orders and attracts trend followers. "
            "The losers are range traders positioned for the range to continue, and late shorts "
            "who are forced to cover in a squeeze (on the long side, longs also COLLECT negative "
            "funding during squeezes, as in MYX in 2025-09).",
        rules=[
            "Universe: monthly top-10 USDT-M crypto perps by trailing-30-day quote volume (as S16).",
            "Bars: 4h, anchored 00/04/08/12/16/20 UTC. HH(30)/LL(30) = highest high / lowest low "
            "of the previous 30 bars (excluding the signal bar). QVavg(30) = mean quote volume of "
            "the previous 30 bars. ATR14 = simple mean of the last 14 true ranges.",
            "Entry long: 4h close > HH(30) and quote volume >= 1.5 x QVavg(30); short: close < "
            "LL(30) with the same volume rule. Market order at the next 4h open. One position per "
            "symbol.",
            "Stop: entry -/+ 2 x ATR14, fixed. Gap through the stop -> exit at that open.",
            "Exit: first 4h close below LL(10) (long) / above HH(10) (short) -> exit at the next "
            "open; time stop: the open 30 bars after entry.",
        ],
        kill=lambda j: "A choppy, range-bound market without big trend legs (second OOS year: "
             f"{_yr(j, 1)['exp_R']:+.3f}R/trade, {pct(_yr(j, 1)['cagr'])} at 1% risk) and the "
             "absence of one-off squeezes like MYX.",
        weak=lambda j: "Any quarter losing more than 20R, or the short side continuing to lose "
             f"(already {j['oos_short_exp_R']:+.3f}R/trade OOS).",
        fail=_s02_fail),
    "S19": dict(
        idea="Buy a top-10 coin that sold off 5%+ in a day into a new 7-day low while open "
             "interest rose 8%+, if the bar closes back above that low.",
        why="Rising open interest into a sell-off means new shorts are opening late, at support. "
            "If the low holds (the bar closes back above the 7-day low), those late shorts are "
            "trapped and their covering lifts price. The late shorts lose.",
        rules=[
            "Universe: monthly top-10 USDT-M crypto perps (as S16). Bars: 1h.",
            "OI = open interest in contracts (base units), as-of the 5-minute row inside the bar "
            "(row stamped at the bar close excluded).",
            "Entry (long only): ret(24) <= -5%, OI[t]/OI[t-24] - 1 >= +8%, low[t] < LL(168) and "
            "close[t] > LL(168) (LL excludes bar t). Market at the next 1h open.",
            "Stop: low[t] - 0.5 x ATR14. Target: 2R (resting limit, maker). Time stop: 24 bars.",
        ],
        kill=lambda j: "Walk-forward already says it: re-optimising the three parameters every "
             f"quarter gave {j['wf_oos']['total_R']:+.2f}R. Persistent downtrends where the 7-day "
             "low keeps breaking kill it.",
        weak="Rolling 50-trade expectancy below zero after costs.",
        fail=_s19_fail),
}


def card(sid, j, df, regm):
    st = SETUPS[sid]
    n = NARR[sid]
    row = df[df["id"] == sid].iloc[0]
    yr = list(j["oos_by_year"].items())
    hm = pd.DataFrame(j["heatmap"]["table"])
    keys = list(st.grid)
    passed = j["status"] == "PASS"
    L = []
    L.append(f"# {sid} - {st.name}")
    L.append("")
    status = "PASSED every LAB gate (paper only)" if passed else \
        "KILLED - " + "; ".join(j["kill_rules_failed"] + j["lab_gates_failed"])
    L.append(f"**Status:** {status}. **Rank:** {int(row['rank'])} of 40. **Score:** "
             f"{j['score']['total']}/25. Source: `research/lab/full/{sid}.json`, "
             f"`research/lab/results_all.csv`; code `src/lab/`.")
    L.append("")
    L.append(f"**Idea (one line):** {n['idea']}")
    L.append("")
    L.append("## Why the edge should exist, and who loses money to it")
    L.append(n["why"])
    L.append("")
    L.append("## Exact rules (as tested; RADAR's default parameters, no tuning)")
    for x in n["rules"]:
        L.append(f"- {x}")
    L.append("- Costs in every number below: taker 0.05%/side (maker 0.02% for resting "
             "targets), slippage " + ("1bp BTC/ETH, 3bp other core, 10bp memes" ) +
             ", real funding at every settlement held, delisted coins included (forced close at the "
             "last real trade -2%, ESTIMATE).")
    L.append(f"- Parameters: `{json.dumps(st.defaults)}`.")
    L.append("")
    L.append("## Size per trade at EUR100 (~$110)")
    L.append(f"- Risk per trade: 1% = about $1.10. Median stop distance OOS: "
             f"{j['oos_median_stop_pct']:.2f}% -> median position notional "
             f"${j['oos_median_notional_eur100']:.0f} (= $1.10 / stop distance), capped at 3x "
             f"equity ($330); total open notional is also capped at 3x equity.")
    L.append(f"- Minimum notional (ESTIMATE: $5 most perps, $20 ETHUSDT, $100 BTCUSDT): "
             f"{pc(j['oos_tradeable_share_est'], 0)} of OOS trades are placeable at EUR100. "
             f"Not placeable: {j['oos_untradeable_symbols']}. Verdict: "
             + (("tradeable at EUR100 (ESTIMATE)" +
                 (", except most BTCUSDT signals." if "BTCUSDT" in j["oos_untradeable_symbols"]
                  else "."))
                if j["oos_tradeable_share_est"] >= 0.5 else "UNTRADEABLE AT EUR100 (ESTIMATE)."))
    L.append(f"- Up to {j['oos_max_concurrent']} positions were open at once in the OOS window; "
             f"about {j['oos_trades_per_month']:.1f} trades per month; median hold "
             f"{j['oos_median_hold_h']:.0f} h.")
    L.append("")
    L.append("## Out-of-sample results (2024-11-01 .. 2026-10-31), after all costs")
    bh, bs = j["bh_btc_perp"], j["bh_btc_spot"]
    to = j["oos_tradeable_only"]
    wf = j["wf_oos"]
    L.append("| | trades | win rate | expectancy | profit factor | CAGR (1% risk) | max DD | worst month | longest losing streak | exposure |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    L.append(f"| **{sid} default params** | {j['oos_trades']} | {pc(j['oos_win_rate'])} | "
             f"{f(j['oos_exp_R'])}R | {j['oos_pf']:.2f} | {pct(j['oos_cagr'])} | "
             f"{pc(j['oos_max_dd'])} | {pct(j['oos_worst_month'])} ({j['oos_worst_month_id']}) | "
             f"{j['oos_losing_streak']} | {pc(j['oos_exposure'], 0)} |")
    L.append(f"| {sid}, only trades placeable at EUR100 (ESTIMATE) | {to['trades']} | | "
             f"{f(to['exp_R'])}R | | {pct(to['cagr'])} | {pc(to['max_dd'])} | "
             f"{pct(to['worst_month'])} | {to['losing_streak']} | |")
    L.append(f"| {sid} walk-forward (re-optimised quarterly) | {wf['trades']} | "
             f"{pc(wf.get('win_rate'))} | {f(wf.get('exp_R'))}R | {wf.get('pf', float('nan')):.2f} | "
             f"{pct(wf['cagr'])} | {pc(wf['max_dd'])} | {pct(wf.get('worst_month'))} | "
             f"{wf.get('losing_streak')} | {pc(wf.get('exposure'), 0)} |")
    L.append(f"| BTC buy-and-hold, perp (fees, slippage, funding) | 1 | | | | {pct(bh['cagr'])} | "
             f"{pc(bh['max_dd'])} | {pct(bh['worst_month'])} | | 100% |")
    L.append(f"| BTC buy-and-hold, spot-like (fees, slippage, no funding) | 1 | | | | "
             f"{pct(bs['cagr'])} | {pc(bs['max_dd'])} | {pct(bs['worst_month'])} | | 100% |")
    L.append("")
    L.append(f"In-sample (2022-11 .. 2024-10, secondary): {j['is_trades']} trades, expectancy "
             f"{f(j['is_exp_R'])}R, CAGR {pct(j['is_cagr'])}.")
    L.append("")
    L.append("**By OOS year** (CAGR at 1% risk vs BTC total return over the same 12 months):")
    L.append("")
    L.append("| period | trades | expectancy | setup return | max DD | BTC spot-like | BTC perp |")
    L.append("|---|---:|---:|---:|---:|---:|---:|")
    for k, v in yr:
        L.append(f"| {k} | {v['trades']} | {f(v['exp_R'])}R | {pct(v['cagr'])} | "
                 f"{pc(v['max_dd'])} | {pct(v['bh_btc_spot'])} | {pct(v['bh_btc_perp'])} |")
    L.append("")
    L.append("**Costs:**")
    L.append("")
    L.append("| scenario | expectancy | CAGR |")
    L.append("|---|---:|---:|")
    L.append(f"| base (taker 0.05%, slippage x1) | {f(j['oos_exp_R'])}R | {pct(j['oos_cagr'])} |")
    L.append(f"| taker 0.10% (fees x2) | {f(j['oos_exp_R_fee2x'])}R | {pct(j['oos_cagr_fee2x'])} |")
    L.append(f"| slippage x2 | {f(j['oos_exp_R_slip2x'])}R | {pct(j['oos_cagr_slip2x'])} |")
    L.append(f"| fees x2 AND slippage x2 | {f(j['oos_exp_R_both2x'])}R | "
             f"{pct(j['oos_cagr_both2x'])} |")
    L.append(f"| fees x2 AND slippage x2, best coin ({j['top_symbol']}) removed | "
             f"{f(j.get('oos_exp_R_both2x_ex_top'))}R | |")
    L.append("")
    L.append(f"Funding is inside every number: total OOS funding = {j['oos_funding_R_total']:+.1f}R "
             f"over {j['oos_trades']} trades. Best coin {j['top_symbol']} = "
             f"{pc(j.get('top_symbol_share'), 0)} of OOS R; best single trade "
             f"{j['best_trade_R']:+.1f}R.")
    L.append("")
    L.append("**Regimes** (OOS months labelled by BTC's calendar-month return: bull > +5%, bear < "
             f"-5%, else sideways; OOS had {regm['bull']} bull, {regm['bear']} bear, "
             f"{regm['sideways']} sideways months):")
    L.append("")
    L.append("| regime | trades | expectancy | total R |")
    L.append("|---|---:|---:|---:|")
    for k in ("bull", "bear", "sideways"):
        g = j["regimes"][k]
        L.append(f"| {k} | {g['trades']} | {f(g['exp_R'])}R | {g['total_R']:+.1f}R |")
    L.append("")
    mc = j["mc"]
    L.append("**Monte Carlo** (OOS per-trade returns on equity, 5,000 runs each; ruin = equity "
             "touching 50% of start):")
    L.append("")
    L.append("| method | median max DD | 95th-pct max DD (5% worst case) | P(ruin) | 5th-pct final equity |")
    L.append("|---|---:|---:|---:|---:|")
    L.append(f"| reshuffle trade order | {pc(mc['shuffle_dd_median'])} | "
             f"{pc(mc['shuffle_dd_p95'])} | {pc(mc['shuffle_p_ruin'], 2)} | "
             f"{mc['shuffle_final_p5']:.2f}x |")
    L.append(f"| bootstrap trades | {pc(mc['bootstrap_dd_median'])} | "
             f"{pc(mc['bootstrap_dd_p95'])} | {pc(mc['bootstrap_p_ruin'], 2)} | "
             f"{mc['bootstrap_final_p5']:.2f}x |")
    L.append("")
    L.append("Monte Carlo compounds trades one after another, so it understates the drawdown of "
             "overlapping positions; the historical max DD above includes the overlap.")
    L.append("")
    L.append("## Walk-forward (optimise on 12 months by total R after base costs, trade the next "
             "3 months, roll by 3)")
    L.append("")
    L.append(f"Grid: `{json.dumps(st.grid)}` ({len(j['grid_oos_exp_R'])} combinations). "
             f"OOS-window walk-forward: {wf['trades']} trades, {wf['total_R']:+.1f}R, "
             f"expectancy {f(wf.get('exp_R'))}R, CAGR {pct(wf['cagr'])}, max DD "
             f"{pc(wf['max_dd'])}. All test quarters {j['wf_all_span']}: "
             f"{j['wf_all_trades']} trades, {j['wf_all_total_R']:+.1f}R.")
    L.append("")
    L.append("| test quarter | params chosen | trades | R |")
    L.append("|---|---|---:|---:|")
    for p in j["wf_picks"]:
        L.append(f"| {p['test']} | {p['params']} | {p['test_trades']} | {p['test_R']:+.1f} |")
    L.append("")
    L.append("## Parameter robustness (OOS expectancy in R after base costs)")
    L.append("")
    fixed = j["heatmap"]["fixed"]
    L.append(f"Rows: `{keys[0]}`; columns: `{keys[1]}`" +
             (f"; `{list(fixed)[0]}` fixed at {list(fixed.values())[0]}" if fixed else "") + ".")
    L.append("")
    L.append("| " + " | ".join(hm.columns) + " |")
    L.append("|" + "---|" * len(hm.columns))
    for _, rr in hm.iterrows():
        lab = rr[keys[0]]
        lab = int(lab) if float(lab).is_integer() else lab
        L.append("| " + " | ".join(str(lab) if c == keys[0] else f(rr[c]) for c in hm.columns)
                 + " |")
    L.append("")
    L.append(f"One-step neighbours of the defaults with positive OOS expectancy: "
             f"{pc(j['robust_neighbour_pos_share'], 0)}; whole grid: "
             f"{pc(j['robust_grid_pos_share'], 0)}.")
    L.append("")
    L.append("## Worst month and longest losing streak")
    L.append(f"- Worst OOS month: {pct(j['oos_worst_month'])} of equity ({j['oos_worst_month_id']}).")
    L.append(f"- Longest losing streak OOS: {j['oos_losing_streak']} trades in a row "
             f"(walk-forward: {wf.get('losing_streak')}).")
    L.append("")
    L.append("## The condition that kills it")
    L.append(n["kill"](j) if callable(n["kill"]) else n["kill"])
    L.append("")
    L.append("## First sign it is weakening")
    L.append(n["weak"](j) if callable(n["weak"]) else n["weak"])
    L.append("")
    L.append("## Final check")
    pos_wf = wf["total_R"] > 0
    L.append(f"- Wins on unseen data? OOS default params: {f(j['oos_exp_R'])}R/trade; "
             f"walk-forward: {wf['total_R']:+.1f}R -> " +
             ("YES" if j["oos_exp_R"] > 0 and pos_wf else "NO / NOT RELIABLY") + ".")
    L.append(f"- After fees and funding? {'YES' if j['oos_exp_R'] > 0 else 'NO'} (every number "
             "includes fees, slippage and real funding).")
    L.append(f"- Over 50 trades? {'YES' if j['oos_trades'] >= 50 else 'NO'} "
             f"({j['oos_trades']} OOS trades).")
    L.append(f"- With double fees (and double slippage)? "
             f"{'YES' if j['oos_exp_R_both2x'] > 0 else 'NO'} ({f(j['oos_exp_R_both2x'])}R).")
    L.append(f"- Robust to nearby settings? "
             f"{'YES' if j['robust_neighbour_pos_share'] >= 0.5 else 'NO'} "
             f"({pc(j['robust_neighbour_pos_share'], 0)} of neighbours positive).")
    L.append("- Any look-ahead leak? None found. Signals use closed bars only (HH/LL exclude the "
             "signal bar), orders fill at the next bar's open, funding is the last SETTLED rate "
             "known at the bar close, OI is the 5-minute row inside the bar, the universe for "
             "month M uses only bars closed before M. Engine shift tests and same-bar stop/target "
             "tests pass (`tests/test_lab_engine.py`).")
    L.append("")
    L.append("## WHY THIS COULD FAIL")
    for x in (n["fail"](j) if callable(n["fail"]) else n["fail"]):
        L.append(f"- {x}")
    L.append("")
    return "\n".join(L)


def top_json(j, df, regm):
    sid = j["id"]
    st = SETUPS[sid]
    passed = j["status"] == "PASS"
    reasons = j["kill_rules_failed"] + j["lab_gates_failed"]
    yr = j["oos_by_year"]
    out = {
        "id": sid, "name": st.name,
        "status": "QUALIFIED" if passed else "NOT_QUALIFIED",
        "status_note": ("Passed every LAB gate on out-of-sample data. PAPER TRADING ONLY "
                        "(RULES.md): do not go live until the owner says 'go live'.")
        if passed else "Best candidate, but it failed: " + "; ".join(reasons),
        "failed_gates": reasons,
        "warnings": [
            f"OOS year 2 (2025-11..2026-10) expectancy only "
            f"{list(yr.values())[1]['exp_R']:+.3f}R vs {list(yr.values())[0]['exp_R']:+.3f}R in "
            "year 1: the edge was much weaker in the last 12 months.",
            f"Long-only: OOS bear-month expectancy {j['regimes']['bear']['exp_R']:+.3f}R.",
            "Walk-forward profit leans on one test quarter ({}: {:+.1f}R); {} of {} test quarters "
            "lost money.".format(_wf_stats(j)[2]["test"], _wf_stats(j)[2]["test_R"],
                                 _wf_stats(j)[0], _wf_stats(j)[1]),
            f"At EUR100 most BTCUSDT signals are below the ESTIMATE $100 minimum notional "
            f"({pc(1 - j['oos_tradeable_share_est'], 0)} of OOS trades not placeable).",
            "Minimum notional / lot size / leverage tiers are ESTIMATES: verify from "
            "exchangeInfo before any live order.",
        ],
        "venue": "Binance USDT-M perpetual futures",
        "universe": {
            "rule": "Each calendar month M (00:00 UTC on day 1): rank Binance USDT-M perpetuals by "
                    "the sum of 1h quote volume over [M - 30 days, M). Keep the top 10.",
            "include_only": "crypto underlyings (exclude TradFi stock/ETF/commodity, index and "
                            "stablecoin perps)",
            "must_be_trading": "the symbol's last bar with trades > 0 opened within the 24h "
                               "before M",
            "membership_needed_for": "new entries only (entry bar's month); open positions may "
                                     "run past month end",
            "renamed_contracts": "separate symbols (e.g. MATICUSDT and POLUSDT are not stitched)",
            "reference_implementation": "src/radar/universe.py; data/universe_by_month.csv",
        },
        "timeframe": "4h bars anchored at 00/04/08/12/16/20 UTC built from four complete 1h "
                     "bars; act only on CLOSED bars",
        "indicators": {
            "SMA_fast": f"simple mean of the last {st.defaults['fast']} 4h closes (incl. bar t)",
            "SMA_slow": "simple mean of the last 200 4h closes (incl. bar t)",
            "ATR14": "simple mean of the last 14 true ranges, TR = max(high-low, |high-prev "
                     "close|, |low-prev close|)",
            "F8": "latest SETTLED funding rate x 8 / funding_interval_hours of that settlement, "
                  "as known at the bar close (a settlement is usable from the first bar whose "
                  "close is after its calc time); no predicted funding",
        },
        "entry": {
            "side": "long only",
            "condition": "at 4h close t: close > SMA_fast > SMA_slow AND F8 < 0",
            "leg_rule": "one signal per uptrend leg: after a signal, disarm until a 4h close < "
                        "SMA_fast; a signal is consumed even if no order is placed",
            "order": "market (taker) at the open of bar t+1",
            "skip_if": ["symbol not in the universe for the entry bar's month",
                        "bar t or bar t+1 has trades == 0", "any input is NaN",
                        "a position in this symbol is already open",
                        "stop would already be at/above the entry price"],
        },
        "stop": {"type": "fixed stop-market", "price": "entry_fill_reference - "
                 f"{st.defaults['atr']} x ATR14 at bar t (entry reference = bar t+1 open)",
                 "gap_rule": "if a bar opens below the stop, exit at that open"},
        "exits": [
            "4h close < SMA_fast -> market exit at the next 4h open",
            f"first funding settlement with F8 > {st.defaults['fexit']} (= "
            f"{st.defaults['fexit'] * 100:.2f}% per 8h) seen at a 4h close -> market exit at the "
            "next 4h open",
            "no profit target, no time stop",
            "delisting announced/contract stops: close immediately (backtest: last real trade "
            "-2%, ESTIMATE)",
        ],
        "params": st.defaults,
        "param_grid_tested": st.grid,
        "sizing": {
            "risk_per_trade": 0.01, "account_eur": 100, "account_usd_approx": 110,
            "notional": "risk_per_trade x equity / (|entry - stop| / entry)",
            "max_total_open_notional_x_equity": 3.0,
            "min_notional_usdt_ESTIMATE": {"BTCUSDT": 100, "ETHUSDT": 20, "default": 5},
            "if_below_min_notional": "skip the trade (do not round up the risk)",
            "median_position_notional_usd_at_eur100": round(j["oos_median_notional_eur100"], 2),
            "median_stop_distance_pct": round(j["oos_median_stop_pct"], 2),
        },
        "cost_assumptions": {"taker": 0.0005, "maker": 0.0002,
                             "slippage_bp": {"BTCUSDT": 1, "ETHUSDT": 1, "other_core": 3,
                                             "memes": 10},
                             "funding": "real settlements while held (long pays positive rate)"},
        "backtest": {
            "data": "data.binance.vision archive (data/MANIFEST.csv); window 2022-11-01 .. "
                    "2026-10-31 23:59 UTC; delisted symbols included",
            "oos_window": "2024-11-01 .. 2026-10-31",
            "oos": {k: j[f"oos_{k}"] for k in ("trades", "win_rate", "exp_R", "pf", "cagr",
                                                "max_dd", "worst_month", "worst_month_id",
                                                "losing_streak", "exposure")},
            "oos_double_fees_and_slippage": {"exp_R": j["oos_exp_R_both2x"],
                                             "cagr": j["oos_cagr_both2x"]},
            "oos_tradeable_at_eur100_ESTIMATE": j["oos_tradeable_only"],
            "oos_by_year": yr,
            "walk_forward_oos": {k: j["wf_oos"][k] for k in ("trades", "total_R", "exp_R",
                                                              "cagr", "max_dd")},
            "in_sample": {"trades": j["is_trades"], "exp_R": j["is_exp_R"],
                          "cagr": j["is_cagr"]},
            "btc_buy_hold_oos": {"perp_cagr": j["bh_btc_perp"]["cagr"],
                                 "spot_like_cagr": j["bh_btc_spot"]["cagr"]},
            "monte_carlo": j["mc"],
            "regimes": j["regimes"],
            "robust_neighbour_positive_share": j["robust_neighbour_pos_share"],
            "score": j["score"],
        },
        "monitoring": {
            "weakening": "rolling 12-month expectancy < +0.05R after costs, or losing streak > "
                         f"{j['wf_oos']['losing_streak']}, or drawdown > "
                         f"{pc(j['mc']['bootstrap_dd_p95'], 0)} (Monte Carlo 95th pct)",
            "kill": "drawdown > 30% of peak equity, or rolling 100-trade expectancy < 0 after "
                    "costs",
        },
        "source": {"code": "src/lab/", "card": f"research/cards/{sid}.md",
                   "results": f"research/lab/full/{sid}.json"},
    }
    return out


def report(df, full, regm, bh, bs, basket):
    n_k, prim, allr = kill_counts(df)
    kept = df[df["final_status"] == "KEPT"]
    memes = df[df["track"] == "meme"]
    L = []
    L.append("# LAB report - night 1 (backtest of RADAR's 40 setups)")
    L.append("")
    L.append("Generated by `src/lab/report.py` from `research/lab/screen.csv` and "
             "`research/lab/full/*.json`. Every number comes from code in `src/lab/` run on the "
             "Binance public archive (`data/MANIFEST.csv`). **Headline numbers are out-of-sample "
             "(OOS, 2024-11-01 .. 2026-10-31)**; in-sample (2022-11 .. 2024-10) is secondary. "
             "Paper only.")
    L.append("")
    L.append("## Bottom line")
    L.append("")
    top = kept.iloc[0] if len(kept) else df.iloc[0]
    L.append(f"- **{len(kept)} of 40 setups pass every gate: "
             f"{', '.join(kept['id'])}.** {n_k} are killed.")
    if len(kept):
        j = full[top["id"]]
        y = list(j["oos_by_year"].values())
        L.append(f"- **#1 = {top['id']} ({top['name']})**, status QUALIFIED (paper only): "
                 f"{j['oos_trades']} OOS trades, {f(j['oos_exp_R'])}R per trade, CAGR "
                 f"{pct(j['oos_cagr'])} at 1% risk, max DD {pc(j['oos_max_dd'])}; double costs "
                 f"{f(j['oos_exp_R_both2x'])}R; walk-forward {j['wf_oos']['total_R']:+.1f}R. "
                 f"It is weaker than it looks: year 1 {f(y[0]['exp_R'])}R/trade, year 2 "
                 f"{f(y[1]['exp_R'])}R/trade, and it loses in bear months.")
    for sid in kept["id"].iloc[1:]:
        j2 = full[sid]
        y2 = list(j2["oos_by_year"].values())[1]
        L.append(f"- {sid} ({j2['name']}) also passes the gates but is NOT recommended: OOS max "
                 f"DD {pc(j2['oos_max_dd'], 0)}, {pc(j2.get('top_symbol_share'), 0)} of its OOS "
                 f"profit came from one coin ({j2.get('top_symbol')}), and its second OOS year "
                 f"returned {pct(y2['cagr'])} at 1% risk.")
    L.append(f"- BTC buy-and-hold over the OOS window: {pct(bs['cagr'])}/yr spot-like, "
             f"{pct(bh['cagr'])}/yr as a perp (funding cost {pct(bh['funding'])} of the "
             "position). The gate uses the stricter of the two.")
    L.append("- **Meme track: no hype-proxy setup survives** (details below).")
    L.append("- At EUR100 the edge left is small in money terms: 1% risk is about $1.10 a trade.")
    L.append("")
    L.append("## Ranked table (all 40)")
    L.append("")
    L.append("Rank: setups that pass every gate first, then the other full-protocol setups, then "
             "the screen-only ones; within each group by number of official kill rules failed, "
             "then LAB gates failed, then score, then OOS expectancy. OOS, default parameters, "
             "base costs. "
             "CAGR/DD are for a $110 account at 1% risk per trade (closed-trade equity). "
             "Score = 5 criteria x 1-5 (full protocol only).")
    L.append("")
    L.append("| # | id | setup | track | status | OOS trades | win | exp R | PF | CAGR | max DD | worst mo | streak | exposure | exp R 2x costs | WF OOS R | score | why killed (all reasons) |")
    L.append("|---:|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    for _, r in df.iterrows():
        sc = "" if not np.isfinite(r.get("score_total", np.nan)) else int(r["score_total"])
        wfr = "" if not np.isfinite(r.get("wf_oos_total_R", np.nan)) else \
            f"{r['wf_oos_total_R']:+.1f}"
        pf = r.get("oos_pf", np.nan)
        L.append(
            f"| {int(r['rank'])} | {r['id']} | {r['name']} | {r['track']} | "
            f"{r['final_status']} | {int(r['oos_trades']) if np.isfinite(r['oos_trades']) else 0} | "
            f"{pc(r.get('oos_win'), 0)} | {f(r.get('oos_exp_R'))} | "
            f"{'inf' if pf == np.inf else f'{pf:.2f}' if np.isfinite(pf) else 'n/a'} | "
            f"{pct(r.get('oos_cagr'))} | {pc(r.get('oos_max_dd'), 0)} | "
            f"{pct(r.get('oos_worst_month'), 0)} | "
            f"{int(r['oos_streak']) if np.isfinite(r.get('oos_streak', np.nan)) else ''} | "
            f"{pc(r.get('oos_exposure'), 0)} | {f(r.get('oos_exp_R_both2x'))} | {wfr} | {sc} | "
            f"{'' if r['final_status'] == 'KEPT' else r['final_reasons']} |")
    L.append("")
    L.append("Per-setup detail (all columns, screen result and kill reasons): "
             "`research/lab/results_all.csv`. Cards: " +
             ", ".join(f"`research/cards/{c}.md`" for c in CARD_IDS) + ".")
    L.append("")
    L.append("## Kills: 40 screened, counts by reason")
    L.append("")
    L.append(f"{n_k} of 40 killed. Primary reason (first failed gate, in protocol order):")
    L.append("")
    L.append("| primary reason | setups |")
    L.append("|---|---:|")
    for k, v in prim.items():
        L.append(f"| {k} | {v} |")
    L.append("")
    L.append("Every reason a killed setup failed (a setup can fail several):")
    L.append("")
    L.append("| reason | setups |")
    L.append("|---|---:|")
    for k, v in allr.items():
        L.append(f"| {k} | {v} |")
    L.append("")
    L.append("Note: setups killed at the screen were not run through walk-forward/robustness; "
             "their reasons are the screen gates only.")
    L.append("")
    # full protocol table
    L.append("## Full protocol on the best 10 (by OOS expectancy among setups with >= 50 OOS trades)")
    L.append("")
    L.append("| id | IS exp R | OOS exp R | fees x2 | slip x2 | both x2 | both x2, best coin removed (coin) | neighbours +ve | WF OOS trades / R / CAGR | EUR100-tradeable CAGR | MC 95% DD (boot) | P(ruin) boot | bull / bear / sideways exp R | year1 / year2 exp R | score |")
    L.append("|---|---:|---:|---:|---:|---:|---|---:|---|---:|---:|---:|---|---|---:|")
    for sid in [x for x in df["id"] if x in full]:
        j = full[sid]
        rg = j["regimes"]
        y = list(j["oos_by_year"].values())
        L.append(
            f"| {sid} | {f(j['is_exp_R'])} | {f(j['oos_exp_R'])} | {f(j['oos_exp_R_fee2x'])} | "
            f"{f(j['oos_exp_R_slip2x'])} | {f(j['oos_exp_R_both2x'])} | "
            f"{f(j.get('oos_exp_R_both2x_ex_top'))} ({j.get('top_symbol')}, "
            f"{pc(j.get('top_symbol_share'), 0)} of R) | "
            f"{pc(j['robust_neighbour_pos_share'], 0)} | {j['wf_oos']['trades']} / "
            f"{j['wf_oos']['total_R']:+.1f} / {pct(j['wf_oos']['cagr'])} | "
            f"{pct(j['oos_tradeable_only']['cagr'])} | {pc(j['mc']['bootstrap_dd_p95'], 0)} | "
            f"{pc(j['mc']['bootstrap_p_ruin'], 1)} | {f(rg['bull']['exp_R'], 2)} / "
            f"{f(rg['bear']['exp_R'], 2)} / {f(rg['sideways']['exp_R'], 2)} | "
            f"{f(y[0]['exp_R'], 2)} / {f(y[1]['exp_R'], 2)} | {j['score']['total']} |")
    L.append("")
    L.append("Robustness heatmaps (OOS expectancy for every pair of nearby parameter values) are "
             "in the cards and in `research/lab/full/<id>.json` (`heatmap`, `grid_oos_exp_R`).")
    L.append("")
    # meme
    L.append("## Meme track verdict")
    L.append("")
    L.append("**No hype-proxy setup survives. The data does not show a usable hype/news edge in "
             "meme coins for this account.**")
    L.append("")
    m11 = full.get("M11")
    L.append("- The 'ride the hype' ideas all lose after costs OOS: " + ", ".join(
        f"{r['id']} {f(r['oos_exp_R'])}R ({int(r['oos_trades'])} trades)"
        for _, r in memes.iterrows() if r["id"] in ("M01", "M02", "M08", "M10")) +
        ". Buying volume/open-interest bursts, second legs and laggards after BTC moves is "
        "exactly where memes take money from late buyers, and our proxies arrive late.")
    if m11:
        y = list(m11["oos_by_year"].values())
        L.append(f"- The closest survivor is **M11 (hype decay short)**, the *opposite* of riding "
                 f"hype: short a meme 3-7 days after a volume burst once attention has died. OOS "
                 f"{m11['oos_trades']} trades, {f(m11['oos_exp_R'])}R/trade, CAGR "
                 f"{pct(m11['oos_cagr'])}, max DD {pc(m11['oos_max_dd'])}. It passes the four "
                 f"official kill rules, but it is killed by a LAB gate: on the trades a EUR100 "
                 f"account can place (ESTIMATE minimum notional) CAGR is "
                 f"{pct(m11['oos_tradeable_only']['cagr'])}, below BTC buy-and-hold "
                 f"({pct(bs['cagr'])}). At double costs its CAGR is {pct(m11['oos_cagr_both2x'])}, "
                 f"also below buy-and-hold. Year 1 {f(y[0]['exp_R'])}R/trade, year 2 "
                 f"{f(y[1]['exp_R'])}R/trade (it lost money in the last 12 months).")
    L.append(f"- Equal-weight meme basket held through the OOS window (monthly rebalance, full "
             f"costs, funding, delisted coins included): {pct(basket['total_return'])} total, "
             f"{pct(basket['cagr'])}/yr, max DD {pc(basket['max_dd'])} (avg "
             f"{basket['avg_coins']:.0f} coins). Holding memes was a disaster; shorting faded "
             "hype was the only direction with any positive expectancy, and it is too thin to "
             "trade at this size.")
    L.append("- Caveat: our hype proxies are exchange data only (volume, OI, funding, move vs "
             "BTC, listing age). No historical social/news archive was available, so a true "
             "news-driven edge is untested, not disproved. What is shown: by the time the "
             "exchange data shows the hype, the edge for a taker at 10bp slippage is gone.")
    L.append("")
    # benchmarks
    L.append("## Benchmarks (OOS)")
    L.append("")
    L.append("| benchmark | total return | CAGR | max DD | worst month |")
    L.append("|---|---:|---:|---:|---:|")
    L.append(f"| BTC buy-and-hold perp, 1x (taker 0.05% + 1bp each side, real funding) | "
             f"{pct(bh['total_return'])} | {pct(bh['cagr'])} | {pc(bh['max_dd'])} | "
             f"{pct(bh['worst_month'])} |")
    L.append(f"| BTC buy-and-hold spot-like (same fees/slippage, no funding) | "
             f"{pct(bs['total_return'])} | {pct(bs['cagr'])} | {pc(bs['max_dd'])} | "
             f"{pct(bs['worst_month'])} |")
    L.append(f"| Equal-weight meme basket | {pct(basket['total_return'])} | "
             f"{pct(basket['cagr'])} | {pc(basket['max_dd'])} | {pct(basket['worst_month'])} |")
    L.append("")
    L.append(f"OOS regime months (BTC calendar-month return): {regm['bull']} bull, "
             f"{regm['bear']} bear, {regm['sideways']} sideways. BTC spot-like: year 1 "
             f"(2024-11..2025-10) "
             f"{pct(list(full[kept.iloc[0]['id']]['oos_by_year'].values())[0]['bh_btc_spot']) if len(kept) else ''}"
             f", year 2 (2025-11..2026-10) "
             f"{pct(list(full[kept.iloc[0]['id']]['oos_by_year'].values())[1]['bh_btc_spot']) if len(kept) else ''}.")
    L.append("")
    L.append(METHOD)
    return "\n".join(L)


METHOD = """## Methodology

**Data.** Binance USDT-M archive only (data.binance.vision, `data/MANIFEST.csv`): 1h klines, real
funding settlements, 5-minute open interest / long-short metrics, for 127 symbols (85 ever in the
point-in-time top-10 universe, 58 memes, 16 delisted). Window 2022-11-01 .. 2026-10-31 23:59 UTC,
ended there so funding is real throughout (owner decision; no November funding estimate).
Universe: `data/universe_by_month.csv` (crypto-only, TradFi perps excluded; MATIC and POL kept
separate). Meme eligibility: `is_meme` and last-24h quote volume >= 20M USDT at the signal bar.

**Engine** (`src/lab/engine.py`, tests in `tests/test_lab_engine.py`). Event-driven on closed bars.
Signal at bar t's close, market entry at bar t+1's open (limit entries supported: filled only if
price trades through the limit, maker fee). Stops and targets checked inside every bar from the
entry bar, pessimistic: if one bar touches both, the stop is taken; a bar opening beyond the stop
exits at that open; targets are resting limits filled only on a trade-through (maker 0.02%).
Close-based exits execute at the next open. Never fill on a zero-trade bar. One position per
symbol per setup. Delisted symbols: a position open at the last real bar is closed at that close
moved 2% against the position (ESTIMATE haircut). Funding: every settlement with entry < T <= exit
is booked at qty x price(T) x rate. Tests cover look-ahead (perturbing future bars leaves earlier
trades unchanged; shifting a signal moves the entry by exactly one bar), costs, same-bar
stop/target, gap stops, funding window/sign/valuation, delisting, limit fills, sizing.

**Costs.** Taker 0.05%/side (re-run at 0.10%), maker 0.02% for resting limits, slippage 1bp
BTC/ETH, 3bp other core, 10bp memes (re-run doubled), real funding. "Double costs" = fees x2 AND
slippage x2 together.

**Sizing / portfolio.** $110 (EUR100) account, 1% of current closed-trade equity at risk per trade
(notional = risk / stop distance), total open notional capped at 3x equity (a trade that finds no
room is skipped). CAGR, max drawdown (closed-trade equity, peak to trough) and worst month come
from this portfolio simulation. Expectancy is in R = net return / stop distance. Exposure = share of
OOS hours with at least one position open.

**Minimum notional (ESTIMATE, exchangeInfo blocked).** $5 most perps, $20 ETHUSDT, $100 BTCUSDT.
A trade is placeable at EUR100 if min($1.10 / stop distance, $330) >= the minimum. A setup is
UNTRADEABLE AT EUR100 if fewer than half its OOS trades are placeable. Results are still reported;
a "tradeable-only" re-run (fixed $110 equity) is shown for the full-protocol setups.

**Protocol.** (1) Screen: all 40 with RADAR's default parameters (as written in setups.md, no
tuning). (2) Full protocol on the 10 best by OOS expectancy among setups with >= 50 OOS trades:
IS 2022-11..2024-10 vs OOS 2024-11..2026-10; parameter grid (<= 3 params x <= 4 values incl. the
default); walk-forward: choose the combination with the highest total R (base costs, >= 10 trades,
else defaults) on 12 months, trade it for the next 3, roll by 3 (12 test quarters 2023-11..2026-10,
8 inside the OOS window; positions are attributed to the quarter they were entered in);
robustness = share of one-step neighbours of the defaults with positive OOS expectancy, plus a
heatmap; cost doubling; Monte Carlo (5,000 reshuffles of the OOS per-trade returns on equity and
5,000 bootstraps; 95th percentile of max drawdown = the 5% worst case; ruin = equity touching
50% of start); BTC buy-and-hold over the same window after the same costs (perp with funding,
and spot-like without funding; the stricter one is the gate); for memes also an equal-weight
meme basket; regimes from BTC's calendar-month return (bull > +5%, bear < -5%, else sideways).

**Kill rules (owner).** < 50 OOS trades; negative after double costs; does not beat BTC
buy-and-hold after costs (OOS CAGR); edge only at one parameter value (< 50% of one-step
neighbours positive). **Extra LAB gates (stricter, never looser):** walk-forward OOS total R must be
positive; the result must survive double costs with the single best coin removed (a one-coin edge
is not an edge); it must beat buy-and-hold on the trades a EUR100 account can actually place
(RULES.md #5 tradeability); meme setups must also beat the meme basket. These extra gates were
added during the run: the one-coin and tradeable-only gates after seeing S02 depend on MYXUSDT and
before the final pass; they only ever remove setups.

**Scoring (1-5 each, 25 max).** OOS profit (CAGR <= 0 / <= 5% / <= 15% / <= 30% / more), max
drawdown (> 50% / > 30% / > 20% / > 10% / less), trades (< 50 / < 100 / < 200 / < 400 / more),
longest losing streak (> 20 / 15-20 / 10-14 / 6-9 / <= 5), bear & sideways (both negative / one
negative / both >= 0 but one < 0.05R / both >= 0.05R / both >= 0.15R).

**Known limitations.** Equity is closed-trade (open-position drawdowns inside a trade are not
marked to market), so real drawdowns are somewhat larger. 4h setups see stops on 4h high/low with
the pessimistic same-bar rule. Funding is valued at the 1h open of the settlement hour. Metrics
(OI) exist only for universe months + the month before for non-meme symbols (setups that need OI
get no signals outside them, which matches eligibility). The walk-forward stitches trades from
different parameter sets without re-enforcing one-position-per-symbol across quarter boundaries.
US holidays are not removed from "US weekdays". Hype proxies are exchange-data proxies, not news.
"""

CARD_IDS: list[str] = []


def main():
    global CARD_IDS
    df, full = build_table()
    ctx = load_ctx(["BTCUSDT"] + [s for s in __import__("lab.data", fromlist=["x"])
                                  .research_symbols()])
    bh, bs = btc_buy_hold(ctx), btc_buy_hold(ctx, funding=False)
    basket = meme_basket(ctx)
    reg = btc_regimes(ctx)
    oos_months = pd.period_range(OOS_START.tz_localize(None), (OOS_END - pd.Timedelta(seconds=1))
                                 .tz_localize(None), freq="M").strftime("%Y-%m")
    regm = reg.reindex(oos_months).value_counts().to_dict()
    for k in ("bull", "bear", "sideways"):
        regm.setdefault(k, 0)
    # cards: top 3 core + best meme if one survives
    ranked_full = [x for x in df["id"] if x in full]
    core = [x for x in ranked_full if SETUPS[x].kind == "core"][:3]
    meme_ok = [x for x in df[(df["final_status"] == "KEPT")]["id"] if SETUPS[x].kind == "meme"]
    CARD_IDS = core + meme_ok[:1]
    write_csv(df)
    CARDS.mkdir(parents=True, exist_ok=True)
    for sid in CARD_IDS:
        if sid not in NARR:
            raise SystemExit(f"no narrative for card {sid}")
        (CARDS / f"{sid}.md").write_text(card(sid, full[sid], df, regm))
    (OUT / "REPORT.md").write_text(report(df, full, regm, bh, bs, basket))
    best = df.iloc[0]["id"]
    tj = top_json(full[best], df, regm) if best in full else None
    (OUT / "top_setup.json").write_text(json.dumps(tj, indent=2, default=str))
    print("cards", CARD_IDS, "top", best, tj["status"])
    print(df[["rank", "id", "final_status", "primary_kill_reason", "score_total"]].head(12))


if __name__ == "__main__":
    main()
