"""Helpers shared by the replay tests: LAB-equivalent config, LAB backtest of the same window,
and trade matching between the paper bot (trades.jsonl) and lab.engine."""
from __future__ import annotations

import copy

import numpy as np
import pandas as pd

from bot import config
from bot.journal import read_jsonl

NS_MS = 1_000_000


def lab_equivalent_config(root):
    """The real config.yaml, with Shield's position/halt limits and the minimum notional
    switched off, i.e. what LAB's portfolio() assumes. Only possible from Python (strict=False);
    config.yaml can never loosen Shield."""
    d = copy.deepcopy(config.raw())
    d["shield"].update(daily_loss_halt=1.0, consecutive_losses_halt=10_000,
                       max_open_positions=10_000, fetch_failures_halt=3, stale_bars=2,
                       max_leverage=3.0)
    d["exchange"]["min_notional_usdt_ESTIMATE"] = {"default": 0.0}
    return config.from_dict(d, strict=False).with_paths(root)


def default_config(root):
    return config.load().with_paths(root)


def universe_symbols(months):
    u = pd.read_csv(config.REPO / "data" / "universe_by_month.csv")
    return u[u["month"].isin(months)]


def lab_trades(start, end):
    """LAB's S16 backtest (default params, base costs) on the same universe; trades entered in
    [start, end). Returns (trades, portfolio) with LAB's sizing at 110 USDT."""
    from lab.data import load_ctx
    from lab.engine import portfolio
    from lab.run import run_setup, with_costs
    from lab.setups import SETUPS

    months = pd.period_range(start, pd.Timestamp(end) - pd.Timedelta(seconds=1),
                             freq="M").strftime("%Y-%m").tolist()
    syms = sorted(set(universe_symbols(months)["symbol"]) | {"BTCUSDT"})
    ctx = load_ctx(syms)
    tr = with_costs(run_setup(ctx, SETUPS["S16"]))
    s, e = pd.Timestamp(start, tz="UTC").value, pd.Timestamp(end, tz="UTC").value
    w = tr[(tr["entry_time"] >= s) & (tr["entry_time"] < e)].copy()
    w["closed_in_window"] = w["exit_time"] < e
    pt = portfolio(w, equity0=110.0)
    return w.reset_index(drop=True), pt


def bot_trades(logs_dir):
    recs = read_jsonl(logs_dir / "trades.jsonl")
    opens = {r["id"]: r for r in recs if r["event"] == "open"}
    closes = {r["id"]: r for r in recs if r["event"] == "close"}
    rows = []
    for i, o in opens.items():
        c = closes.get(i)
        rows.append(dict(id=i, symbol=o["symbol"],
                         entry_time=pd.Timestamp(o["entry_time"]).value, entry=o["entry"],
                         stop=o["stop"], notional=o["notional"],
                         exit_time=pd.Timestamp(c["exit_time"]).value if c else None,
                         exit=c["exit"] if c else None, reason=c["reason"] if c else None,
                         funding=c["funding"] if c else None, net=c["net"] if c else None,
                         pnl=c["pnl"] if c else None, late=c["late"] if c else False))
    return pd.DataFrame(rows)


def match(bot: pd.DataFrame, lab: pd.DataFrame, rel=1e-9):
    """Pair trades by (symbol, entry_time). Returns (pairs, only_bot, only_lab, diffs)."""
    lab = lab.copy()
    key_l = {(s, int(t)): i for i, (s, t) in enumerate(zip(lab["symbol"], lab["entry_time"]))}
    pairs, only_bot, diffs = [], [], []
    used = set()
    for _, b in bot.iterrows():
        k = (b["symbol"], int(b["entry_time"]))
        if k not in key_l:
            only_bot.append(b.to_dict())
            continue
        li = key_l[k]
        used.add(li)
        L = lab.iloc[li]
        pairs.append((b, L))
        d = {}
        if not np.isclose(b["entry"], L["entry"], rtol=rel, atol=0):
            d["entry"] = (b["entry"], L["entry"])
        if not np.isclose(b["stop"], L["stop"], rtol=1e-9, atol=0):
            d["stop"] = (b["stop"], L["stop"])
        if b["exit_time"] is not None and not pd.isna(b["exit_time"]):
            if int(b["exit_time"]) != int(L["exit_time"]):
                d["exit_time"] = (b["exit_time"], L["exit_time"])
            if not np.isclose(b["exit"], L["exit"], rtol=rel, atol=0):
                d["exit"] = (b["exit"], L["exit"])
            if b["reason"] != L["reason"]:
                d["reason"] = (b["reason"], L["reason"])
            if not np.isclose(b["funding"], L["funding"], rtol=1e-9, atol=1e-12):
                d["funding"] = (b["funding"], L["funding"])
            if not np.isclose(b["net"], L["net"], rtol=1e-9, atol=1e-12):
                d["net"] = (b["net"], L["net"])
        elif L["closed_in_window"]:
            d["still_open_in_bot"] = (None, L["exit_time"])
        if d:
            diffs.append(((b["symbol"], pd.Timestamp(int(b["entry_time"]), tz="UTC")), d))
    only_lab = lab[[i not in used for i in range(len(lab))]]
    return pairs, only_bot, only_lab, diffs
