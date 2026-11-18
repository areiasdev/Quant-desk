"""Run setups through the engine; benchmarks; regimes."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .data import (OOS_END, OOS_START, WINDOW_START, Ctx, min_notional)
from .engine import BASE, NS_H, Costs, add_net, max_dd, simulate, slip_bp
from .setups import SETUPS, Setup

MEME_MIN_QV24 = 20e6     # setups.md liquidity floor for meme setups (starting parameter)
_ELIG: dict = {}


def eligible_mask(ctx: Ctx, s: str, d: pd.DataFrame, kind: str, tf: str) -> np.ndarray:
    """Eligibility of a signal on bar idx: core = in the universe of the ENTRY bar's month;
    meme = meme symbol with last-24h quote volume >= 20M USDT at the signal bar."""
    key = (s, kind, tf)
    if key in _ELIG:
        return _ELIG[key]
    if kind == "core":
        nxt = d["open_time"].shift(-1)
        mon = nxt.dt.strftime("%Y-%m")
        m = np.array([isinstance(x, str) and s in ctx.universe.get(x, ()) for x in mon])
    else:
        m = (d["qv24"] >= MEME_MIN_QV24).to_numpy() & ctx.syms[s].is_meme
    _ELIG[key] = m
    return m


def _symbols(ctx: Ctx, st: Setup) -> list[str]:
    return ctx.core if st.kind == "core" else ctx.memes


def run_setup(ctx: Ctx, st: Setup, params: dict | None = None) -> pd.DataFrame:
    p = dict(st.defaults, **(params or {}))
    bar_ns = NS_H * (1 if st.tf == "1h" else 4)
    if st.cross:
        def elig(s, d):
            return eligible_mask(ctx, s, d, st.kind, st.tf)
        per = st.fn(ctx, p, elig)
    else:
        per = {}
        for s in _symbols(ctx, st):
            d = ctx.tf(s, st.tf)
            if len(d) < 3:
                continue
            per[s] = st.fn(d, p, ctx.syms[s], ctx)
    rows = []
    for s, (sig, xl, xs) in per.items():
        if sig is None or len(sig) == 0:
            continue
        d = ctx.tf(s, st.tf)
        m = eligible_mask(ctx, s, d, st.kind, st.tf)
        sig = sig[(sig["idx"] >= 0) & (sig["idx"] < len(d))]
        sig = sig[m[sig["idx"].to_numpy()]]
        if len(sig) == 0:
            continue
        sy = ctx.syms[s]
        h1 = sy.h1
        tr = simulate(d, sig, bar_ns=bar_ns, delisted=sy.delisted, fund_t=sy.fund_t,
                      fund_r=sy.fund_r,
                      px_t=h1["open_time"].astype("datetime64[ns, UTC]").astype("int64").to_numpy(),
                      px_o=h1["open"].to_numpy(float), exit_long=xl, exit_short=xs)
        for x in tr:
            x["symbol"] = s
        rows.extend(tr)
    df = pd.DataFrame(rows)
    if len(df) == 0:
        return df
    df["slip_bp"] = [slip_bp(s, ctx.syms[s].is_meme) for s in df["symbol"]]
    df["min_notional_est"] = df["symbol"].map(min_notional)
    return df.sort_values(["entry_time", "symbol"]).reset_index(drop=True)


def tradeable_share(tr: pd.DataFrame, equity: float = 110.0, risk: float = 0.01,
                    lev: float = 3.0) -> float:
    """Share of trades whose EUR100-account position (1% risk to stop, capped at 3x) is at or
    above the ESTIMATE minimum notional."""
    if len(tr) == 0:
        return np.nan
    notional = np.minimum(risk * equity / tr["stop_frac"], lev * equity)
    return float((notional >= tr["min_notional_est"]).mean())


# ------------------------------------------------------------------ benchmarks
def btc_buy_hold(ctx: Ctx, start=OOS_START, end=OOS_END, costs: Costs = BASE,
                 funding: bool = True) -> dict:
    """1x long BTCUSDT perp: buy at the first open, sell at the last close, taker fee + 1bp
    slippage each side, real funding paid/received at every settlement (funding=False gives a
    spot-like buy-and-hold with the same fees and slippage)."""
    b = ctx.syms["BTCUSDT"].h1
    w = b[(b["open_time"] >= start) & (b["open_time"] < end)].reset_index(drop=True)
    slip = 1e-4 * costs.slip_mult
    entry = w["open"].iat[0] * (1 + slip)
    sy = ctx.syms["BTCUSDT"]
    ft = sy.fund_t
    sel = (ft > w["open_time"].iat[0].value) & (ft < end.value)
    t_ns = w["open_time"].astype("datetime64[ns, UTC]").astype("int64").to_numpy()
    pi = np.clip(np.searchsorted(t_ns, ft[sel], side="right") - 1, 0, len(w) - 1)
    # equity path per unit: price move + cumulative funding (valued at the settlement price)
    fund_cash = np.zeros(len(w))
    np.add.at(fund_cash, pi, -sy.fund_r[sel] * w["open"].to_numpy()[pi] / entry)
    if not funding:
        fund_cash[:] = 0.0
    eq = (1 - costs.taker) * w["close"].to_numpy() / entry + np.cumsum(fund_cash)
    final = (1 - costs.taker) * (w["close"].iat[-1] * (1 - slip) / entry) * (1 - costs.taker) \
        + fund_cash.sum()
    years = (end - start) / pd.Timedelta(days=365.25)
    mser = pd.Series(eq, index=w["open_time"]).resample("ME").last()
    mret = mser.pct_change()
    mret.iloc[0] = mser.iloc[0] / 1.0 - 1
    return dict(total_return=float(final - 1), cagr=float(final ** (1 / years) - 1),
                max_dd=max_dd(eq, 1.0), worst_month=float(mret.min()),
                funding=float(fund_cash.sum()))


def meme_basket(ctx: Ctx, start=OOS_START, end=OOS_END, costs: Costs = BASE) -> dict:
    """Equal-weight basket of every meme perp trading at each month start (delisted included),
    rebalanced monthly; every month pays a full round trip (taker + 10bp slippage each side) and
    real funding. A coin delisted inside a month exits at its last close minus 2% (ESTIMATE)."""
    months = pd.date_range(start, end, freq="MS", inclusive="left")
    slip = 10e-4 * costs.slip_mult
    eq, path = 1.0, []
    for m0 in months:
        m1 = m0 + pd.offsets.MonthBegin(1)
        rets = []
        for s in ctx.memes:
            sy = ctx.syms[s]
            h = sy.h1
            w = h[(h["open_time"] >= m0) & (h["open_time"] < m1)]
            if len(w) == 0 or w["open_time"].iat[0] != m0 or w["trades"].iat[0] == 0:
                continue
            ent = w["open"].iat[0] * (1 + slip)
            last = w["close"].iat[-1]
            if sy.delisted and w["open_time"].iat[-1] == h["open_time"].iat[-1]:
                last *= 0.98
            ex = last * (1 - slip)
            ft = sy.fund_t
            sel = (ft > m0.value) & (ft <= w["open_time"].iat[-1].value + NS_H)
            fund = -np.sum(sy.fund_r[sel]) * (w["close"].mean() / ent)
            rets.append(ex / ent - 1 - costs.taker * (1 + ex / ent) + fund)
        r = float(np.mean(rets)) if rets else 0.0
        eq *= 1 + r
        path.append((m0.strftime("%Y-%m"), r, eq, len(rets)))
    p = pd.DataFrame(path, columns=["month", "ret", "equity", "n"])
    years = (end - start) / pd.Timedelta(days=365.25)
    return dict(total_return=eq - 1, cagr=eq ** (1 / years) - 1 if eq > 0 else -1.0,
                max_dd=max_dd(p["equity"].to_numpy(), 1.0), worst_month=float(p["ret"].min()),
                avg_coins=float(p["n"].mean()))


# ------------------------------------------------------------------ regimes
def btc_regimes(ctx: Ctx, thr: float = 0.05) -> pd.Series:
    """Month label from BTC's 30-day (calendar-month) return: bull > +5%, bear < -5%, else
    sideways. Uses the month's own return - a descriptive split, not a trading signal."""
    b = ctx.syms["BTCUSDT"].h1.set_index("open_time")["close"]
    mc = b.resample("ME").last()
    r = mc.pct_change()
    first = b[b.index < WINDOW_START + pd.offsets.MonthBegin(1)]
    r.iloc[0] = first.iloc[-1] / b.iloc[0] - 1
    lab = pd.Series(np.where(r > thr, "bull", np.where(r < -thr, "bear", "sideways")),
                    index=mc.index.strftime("%Y-%m"))
    return lab


def regime_split(tr: pd.DataFrame, reg: pd.Series) -> dict:
    m = pd.to_datetime(tr["entry_time"], utc=True).dt.strftime("%Y-%m")
    out = {}
    for k in ("bull", "bear", "sideways"):
        x = tr[m.map(reg).eq(k).to_numpy()]
        out[k] = dict(trades=int(len(x)), exp_R=float(x["R"].mean()) if len(x) else np.nan,
                      total_R=float(x["R"].sum()), months=int((reg.loc[sorted(set(m))] == k)
                                                             .sum()) if len(m) else 0)
    return out


def with_costs(tr: pd.DataFrame, costs: Costs = BASE) -> pd.DataFrame:
    return add_net(tr, costs) if len(tr) else tr


def get(sid: str) -> Setup:
    return SETUPS[sid]
