"""LAB meme rotation: five pre-registered rotation strategies on Binance USDT-M meme perps.

Rules and grids are frozen in research/lab/meme_rotation/DESIGN.md (committed before any
backtest). This module re-uses the LAB engine (`engine.simulate` for fills, stops and real funding,
`engine.add_net` for costs) and adds what a rotation needs:

* a point-in-time DAILY panel (00:00 UTC days built from 1h bars; the decision for day d is taken
  at the close of its 23:00 bar and executed at the next 00:00 open);
* cross-sectional rankings (risk-adjusted momentum) that only use closed days <= d;
* an edge monitor that admits a coin only while its last N CLOSED paper trades have positive
  expectancy (trades closed strictly before the decision instant);
* a portfolio simulator for a EUR100 (~110 USDT) account: 1% risk per position, <= 3 positions,
  <= 3x leverage, one position per symbol, positions below the minimum notional are SKIPPED and
  counted;
* the protocol: development window 2022-11..2026-04 (IS 2022-11..2024-10, OOS 2024-11..2026-04,
  walk-forward 12m train / 3m test), and a FINAL HOLDOUT 2026-05..2026-10 that the code runs
  exactly once (it refuses to run again once holdout.json exists).

Run from src/:
    ../.venv/bin/python -m lab.rotation dev        # development protocol -> dev_results.json
    ../.venv/bin/python -m lab.rotation holdout    # ONE holdout run per strategy -> holdout.json
    ../.venv/bin/python -m lab.rotation report     # REPORT.md + top.json from the two files
"""
from __future__ import annotations

import heapq
import itertools
import json
import sys
import time
import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .data import WINDOW_END, WINDOW_START, Ctx, Sym, load_ctx, research_symbols, resample_4h
from .engine import (BASE, NS_H, Costs, add_net, exposure, longest_losing_streak, make_signals,
                     max_dd, monte_carlo, simulate, slip_bp)
from .run import btc_buy_hold, btc_regimes, meme_basket, regime_split
from .setups import SETUPS

warnings.simplefilter("ignore")

OUT = Path(__file__).resolve().parents[2] / "research" / "lab" / "meme_rotation"

# ------------------------------------------------------------------ windows (frozen)
DEV_START = WINDOW_START                                   # 2022-11-01
DEV_END = pd.Timestamp("2026-05-01", tz="UTC")             # exclusive
DEV_IS_START, DEV_IS_END = DEV_START, pd.Timestamp("2024-11-01", tz="UTC")
DEV_OOS_START, DEV_OOS_END = DEV_IS_END, DEV_END
HOLDOUT_START, HOLDOUT_END = DEV_END, WINDOW_END           # 2026-05-01 .. 2026-11-01 (excl.)
TRAIN_M, TEST_M, MIN_TRAIN_TRADES = 12, 3, 10

# ------------------------------------------------------------------ account / sizing (frozen)
EQUITY0 = 110.0          # EUR100 ~ 110 USDT
RISK = 0.01              # 1% of closed-trade equity at risk per position
LEV_CAP = 3.0            # total open notional <= 3x equity
MAX_POS = 3              # at most 3 open positions
# Owner, 2026-11-18, from Binance exchangeInfo: BTC 50 USDT (min qty 0.001), ETH 20, default 5.
# The memes' own filters were not exported: they use the default 5 USDT (ESTIMATE for memes).
MIN_NOTIONAL = {"BTCUSDT": 50.0, "ETHUSDT": 20.0}
MIN_NOTIONAL_DEFAULT = 5.0
MIN_QTY = {"BTCUSDT": 0.001}

# ------------------------------------------------------------------ universe (frozen)
MIN_QV30 = 20e6          # mean daily quote volume over the last 30 days >= 20M USDT
MIN_AGE_D = 30           # listed >= 30 days at the decision
ATR_N, STOP_ATR = 14, 2.0  # protective stop for the daily strategies: 2 x ATR14 (daily)
SIGMA_N = 30             # daily-return volatility window (R4 sizing)

SCEN = {"base": BASE, "both2x": BASE.scaled(2.0, 2.0)}


def min_notional(sym: str, price: float | None = None) -> float:
    m = MIN_NOTIONAL.get(sym, MIN_NOTIONAL_DEFAULT)
    if price is not None and sym in MIN_QTY:
        m = max(m, MIN_QTY[sym] * price)
    return m


# ------------------------------------------------------------------ data
def meme_symbols() -> list[str]:
    from radar.config import DATA
    cls = pd.read_csv(DATA / "symbol_classes.csv")
    return sorted(set(cls.loc[cls["is_meme"].astype(bool), "symbol"]) & set(research_symbols()))


def truncate_ctx(ctx: Ctx, end: pd.Timestamp) -> Ctx:
    """A copy of ctx with every bar opening at or after `end` removed (and funding settlements at
    or after `end`). A symbol still trading at `end` is NOT delisted in the copy (positions are
    closed at the last bar's close, as at the end of any backtest)."""
    out = {}
    for s, sy in ctx.syms.items():
        h1 = sy.h1[sy.h1["open_time"] < end].reset_index(drop=True)
        if len(h1) == 0:
            continue
        keep = sy.fund_t < end.value
        out[s] = Sym(name=s, h1=h1, h4=resample_4h(h1), fund_t=sy.fund_t[keep],
                     fund_r=sy.fund_r[keep],
                     delisted=sy.delisted and sy.h1["open_time"].iloc[-1] < end - pd.Timedelta(hours=1),
                     is_meme=sy.is_meme, last_close=float(h1["close"].iloc[-1]))
    return Ctx(syms=out, universe=ctx.universe, memes=[m for m in ctx.memes if m in out],
               core=[c for c in ctx.core if c in out])


def daily_of(h1: pd.DataFrame) -> pd.DataFrame:
    """Daily (00:00 UTC) bars from 1h bars. A day is COMPLETE only if its 23:00 bar exists; the
    close / decision bar index (`idx23`) of an incomplete day are NaN / -1."""
    day = h1["open_time"].dt.floor("D")
    x = h1.assign(_i=np.arange(len(h1)))
    g = x.groupby(day, sort=True)
    out = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(),
                        "low": g["low"].min(), "close": g["close"].last(),
                        "qv": g["quote_volume"].sum(), "idx23": g["_i"].last(),
                        "trades23": g["trades"].last(),
                        "age": g["days_since_listing"].last() if "days_since_listing" in x
                        else np.nan})
    complete = g["open_time"].last() == out.index + pd.Timedelta(hours=23)
    out.loc[~complete, "close"] = np.nan
    out.loc[~complete, "idx23"] = -1
    out.loc[~complete, "trades23"] = 0
    out.index.name = "day"
    return out


@dataclass
class Panel:
    """Point-in-time daily panel, date x symbol. Every value at day d is known at d+1 00:00."""
    dates: pd.DatetimeIndex
    memes: list[str]
    close: pd.DataFrame
    close_ffill: pd.DataFrame
    lr: pd.DataFrame
    atr: pd.DataFrame
    sigma: pd.DataFrame
    qv30: pd.DataFrame
    age: pd.DataFrame
    idx23: pd.DataFrame
    elig: pd.DataFrame
    btc_close: pd.Series
    btc_sma50: pd.Series

    def score(self, L: int) -> pd.DataFrame:
        """Risk-adjusted L-day momentum: ln(C_d / C_{d-L}) / (sd of the last L daily log returns
        x sqrt(L)). Needs every one of the L+1 daily closes."""
        sd = self.lr.rolling(L, min_periods=L).std()
        mom = np.log(self.close) - np.log(self.close.shift(L))
        return mom / (sd.where(sd > 0) * np.sqrt(L))

    @property
    def btc_up(self) -> pd.Series:
        return (self.btc_close > self.btc_sma50) & self.btc_sma50.notna()

    @property
    def btc_down(self) -> pd.Series:
        return (self.btc_close < self.btc_sma50) & self.btc_sma50.notna()


def build_panel(ctx: Ctx, memes: list[str] | None = None) -> Panel:
    memes = [m for m in (memes or ctx.memes) if m in ctx.syms]
    per = {s: daily_of(ctx.syms[s].h1) for s in memes}
    b = daily_of(ctx.syms["BTCUSDT"].h1)
    lo = min(min(f.index.min() for f in per.values()), b.index.min())
    hi = max(max(f.index.max() for f in per.values()), b.index.max())
    dates = pd.date_range(lo, hi, freq="D")

    def piv(col):
        return pd.DataFrame({s: f[col] for s, f in per.items()}).reindex(dates)

    close, high, low = piv("close"), piv("high"), piv("low")
    pc = close.shift(1)
    tr = np.maximum(high - low, np.maximum((high - pc).abs(), (low - pc).abs()))
    lr = np.log(close).diff()
    qv30 = piv("qv").rolling(30, min_periods=30).mean()
    age = piv("age")
    idx23 = piv("idx23").fillna(-1)
    trades23 = piv("trades23").fillna(0)
    atr = tr.rolling(ATR_N, min_periods=ATR_N).mean()
    elig = (age >= MIN_AGE_D) & (qv30 >= MIN_QV30) & (trades23 > 0) & close.notna() & \
        atr.notna() & (idx23 >= 0)
    bc = b["close"].reindex(dates)
    return Panel(dates=dates, memes=memes, close=close, close_ffill=close.ffill(), lr=lr, atr=atr,
                 sigma=lr.rolling(SIGMA_N, min_periods=20).std(), qv30=qv30, age=age,
                 idx23=idx23, elig=elig.fillna(False).astype(bool), btc_close=bc,
                 btc_sma50=bc.rolling(50, min_periods=50).mean())


# ------------------------------------------------------------------ rankings
def select(score: pd.DataFrame, elig: pd.DataFrame, k: int, side: int,
           gate: pd.Series | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Target set per day: the k eligible coins with the strongest score (side=+1, score > 0) or
    the weakest (side=-1, score < 0). Rank 1 = best. `gate` False on a day -> empty target.
    Pure row-wise function: the target on day d depends only on row d of its inputs."""
    s = (score * side).where(elig.reindex_like(score).fillna(False).astype(bool))
    s = s.where(s > 0)
    rank = s.rank(axis=1, ascending=False, method="first")
    if gate is not None:
        g = gate.reindex(rank.index).fillna(False).astype(bool).to_numpy()
        rank = rank.where(np.repeat(g[:, None], rank.shape[1], axis=1))
    return (rank <= k), rank


def decision_days(P: Panel, reb: str) -> pd.DatetimeIndex:
    if reb == "daily":
        return P.dates
    if reb == "weekly":                      # Sunday's close -> Monday 00:00 entry
        return P.dates[P.dates.weekday == 6]
    raise ValueError(reb)


def _sim_args(sy: Sym) -> dict:
    h1 = sy.h1
    return dict(delisted=sy.delisted, fund_t=sy.fund_t, fund_r=sy.fund_r,
                px_t=h1["open_time"].astype("datetime64[ns, UTC]").astype("int64").to_numpy(),
                px_o=h1["open"].to_numpy(float))


def _frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if len(df) == 0:
        return df
    df["slip_bp"] = [slip_bp(s, True) for s in df["symbol"]]
    return df.sort_values(["entry_time", "symbol"], kind="stable").reset_index(drop=True)


def xs_candidates(ctx: Ctx, P: Panel, L: int, K: int, reb: str, side: int,
                  gate: pd.Series | None, exclude=(), leg: str = "main") -> pd.DataFrame:
    """Cross-sectional rotation as per-symbol trades on 1h bars. At each decision day d a coin in
    the target set gets an entry signal on its 23:00 bar (filled at the next 00:00 open, stop
    2 x ATR14d away); a held coin that is NOT in the target set at a decision gets a close-based
    exit (next 00:00 open). Held coins are always a subset of the last target set, so the number
    of open positions never exceeds K."""
    elig = P.elig.copy()
    for s in exclude:
        if s in elig:
            elig[s] = False
    target, rank = select(P.score(L), elig, K, side, gate)
    dec = decision_days(P, reb)
    rows = []
    for s in P.memes:
        if s in exclude:
            continue
        sy = ctx.syms[s]
        n = len(sy.h1)
        idx = P.idx23[s].reindex(dec).to_numpy()
        tg = target[s].reindex(dec).fillna(False).to_numpy(bool)
        ok = idx >= 0
        ent, ext = ok & tg, ok & ~tg
        if not ent.any():
            continue
        ei = idx[ent].astype(np.int64)
        days = dec[ent]
        sig = make_signals(idx=ei, side=np.full(len(ei), side),
                           stop_dist=STOP_ATR * P.atr[s].reindex(days).to_numpy())
        xm = np.zeros(n, bool)
        xm[idx[ext].astype(np.int64)] = True
        trs = simulate(sy.h1, sig, bar_ns=NS_H, exit_long=xm if side == 1 else None,
                       exit_short=xm if side == -1 else None, **_sim_args(sy))
        dmap = dict(zip(ei.tolist(), days))
        for x in trs:
            d = dmap[x["sig_idx"]]
            x.update(symbol=s, leg=leg, decision_day=d, prio=float(rank.at[d, s]),
                     sigma=float(P.sigma.at[d, s]))
        rows.extend(trs)
    return _frame(rows)


def _day_of_4h(d4: pd.DataFrame) -> pd.DatetimeIndex:
    """Last COMPLETE day at a 4h bar's close: day label = floor(close instant, D) - 1 day."""
    return pd.DatetimeIndex((d4["open_time"] + pd.Timedelta(hours=4)).dt.floor("D")
                            - pd.Timedelta(days=1))


def setup_candidates(ctx: Ctx, P: Panel, sid: str, params: dict | None = None, exclude=(),
                     leg: str = "main", prio_sign: float = -1.0) -> pd.DataFrame:
    """Paper trades of a LAB setup (setups.py) on every eligible meme (eligibility = the panel's
    liquidity/age filter as of the last complete day before the signal bar's close)."""
    st = SETUPS[sid]
    p = dict(st.defaults, **(params or {}))
    rows = []
    for s in P.memes:
        if s in exclude:
            continue
        sy = ctx.syms[s]
        d = sy.h4
        if len(d) < 3:
            continue
        sig, xl, xs = st.fn(d, p, sy, ctx)
        if sig is None or len(sig) == 0:
            continue
        day = _day_of_4h(d)
        el = P.elig[s].reindex(day).fillna(False).to_numpy(bool)
        sig = sig[(sig["idx"] >= 0) & (sig["idx"] < len(d))]
        sig = sig[el[sig["idx"].to_numpy()]]
        if len(sig) == 0:
            continue
        trs = simulate(d, sig, bar_ns=4 * NS_H, exit_long=xl, exit_short=xs, **_sim_args(sy))
        hz = d["hype_volume_z30d"].to_numpy(float)
        sg = P.sigma[s].reindex(day).to_numpy(float)
        for x in trs:
            i = x["sig_idx"]
            z = hz[i]
            x.update(symbol=s, leg=leg, decision_day=day[i],
                     prio=prio_sign * z if np.isfinite(z) else np.inf, hype_z=z,
                     sigma=sg[i])
        rows.extend(trs)
    return _frame(rows)


# ------------------------------------------------------------------ edge monitor
def edge_admit(symbol, entry_time, exit_time, R, N: int, theta: float,
               min_hist: int = 5) -> np.ndarray:
    """For each paper trade i: admitted iff the same coin's last N paper trades that CLOSED
    STRICTLY BEFORE trade i's decision instant (= its market entry time, the close of the signal
    bar) number at least `min_hist` and have mean net R > theta. Uses nothing at/after that
    instant (an exit filled at the same open as the new entry is NOT yet known)."""
    df = pd.DataFrame({"s": np.asarray(symbol), "et": np.asarray(entry_time, np.int64),
                       "xt": np.asarray(exit_time, np.int64), "R": np.asarray(R, float)})
    out = np.zeros(len(df), bool)
    for _, g in df.groupby("s", sort=False):
        o = np.argsort(g["xt"].to_numpy(), kind="stable")
        xt = g["xt"].to_numpy()[o]
        r = g["R"].to_numpy()[o]
        cs = np.r_[0.0, np.cumsum(r)]
        k = np.searchsorted(xt, g["et"].to_numpy(), side="left")   # trades with exit < entry
        lo = np.maximum(k - N, 0)
        cnt = k - lo
        mean = np.where(cnt > 0, (cs[k] - cs[lo]) / np.maximum(cnt, 1), np.nan)
        out[g.index.to_numpy()] = (cnt >= min_hist) & (mean > theta)
    return out


# ------------------------------------------------------------------ portfolio
def run_portfolio(c: pd.DataFrame, costs: Costs = BASE, start: pd.Timestamp = DEV_OOS_START,
                  end: pd.Timestamp = DEV_OOS_END, *, equity0: float = EQUITY0,
                  risk: float = RISK, lev: float = LEV_CAP, max_pos: int = MAX_POS,
                  leg_caps: dict | None = None, vol_budget: float | None = None
                  ) -> tuple[pd.DataFrame, dict]:
    """EUR100 account over candidates ENTERED in [start, end). Sequential in entry time (ties by
    `prio`, lower first). A candidate is skipped (and counted) if its symbol is already held, no
    slot is free (max_pos / leg caps), the 1%-risk notional is below the minimum notional, or the
    3x leverage room left is below the minimum notional. Sizing uses closed-trade equity."""
    skips = dict(symbol_open=0, slots=0, min_notional=0, leverage=0, ruined=0)
    if len(c) == 0:
        return pd.DataFrame(), skips
    c = c[(c["entry_time"] >= start.value) & (c["entry_time"] < end.value)]
    if len(c) == 0:
        return pd.DataFrame(), skips
    c = add_net(c, costs).sort_values(["entry_time", "prio", "symbol"], kind="stable")
    c = c.reset_index(drop=True)
    eq = equity0
    heap: list = []
    held: set = set()
    legs: dict = {}
    open_n = 0.0
    n = len(c)
    notl, pnl, eqb, taken, why = (np.zeros(n), np.zeros(n), np.zeros(n), np.zeros(n, bool),
                                  np.array([""] * n, dtype=object))
    et_a, xt_a = c["entry_time"].to_numpy(), c["exit_time"].to_numpy()
    sf_a, net_a = c["stop_frac"].to_numpy(float), c["net"].to_numpy(float)
    sym_a, leg_a = c["symbol"].to_numpy(), c["leg"].to_numpy()
    sig_a = c["sigma"].to_numpy(float) if "sigma" in c else np.full(n, np.nan)
    ent_a = c["entry"].to_numpy(float)
    for i in range(n):
        while heap and heap[0][0] <= et_a[i]:
            _, _, nn, pp, ss, lg = heapq.heappop(heap)
            eq += pp
            open_n -= nn
            held.discard(ss)
            legs[lg] -= 1
        eqb[i] = eq
        s, lg = sym_a[i], leg_a[i]
        if eq <= 0:
            why[i] = "ruined"
        elif s in held:
            why[i] = "symbol_open"
        elif len(heap) >= max_pos or (leg_caps is not None
                                       and legs.get(lg, 0) >= leg_caps.get(lg, max_pos)):
            why[i] = "slots"
        else:
            want = risk * eq / sf_a[i]
            if vol_budget is not None and np.isfinite(sig_a[i]) and sig_a[i] > 0:
                want = min(want, vol_budget * eq / sig_a[i])
            mn = min_notional(s, ent_a[i])
            room = lev * eq - open_n
            if want < mn:
                why[i] = "min_notional"
            elif min(want, room) < mn:
                why[i] = "leverage"
            else:
                nt = min(want, room)
                p = nt * net_a[i]
                heapq.heappush(heap, (int(xt_a[i]), i, nt, p, s, lg))
                held.add(s)
                legs[lg] = legs.get(lg, 0) + 1
                open_n += nt
                notl[i], pnl[i], taken[i] = nt, p, True
        if why[i]:
            skips[why[i]] += 1
    c["notional"], c["pnl"], c["equity_before"], c["taken"], c["skip"] = notl, pnl, eqb, taken, why
    c["ret_on_equity"] = np.where(taken, pnl / np.where(eqb > 0, eqb, 1.0), 0.0)
    return c, skips


def mtm_equity(pt: pd.DataFrame, P: Panel, start: pd.Timestamp, end: pd.Timestamp,
               equity0: float = EQUITY0) -> pd.Series:
    """Mark-to-market equity at every day end (d+1 00:00) in [start, end): realised P&L of closed
    trades + open positions valued at the day's close (exit costs and unpaid funding of open
    positions ignored). Indexed by day label d."""
    days = pd.date_range(start, end - pd.Timedelta(days=1), freq="D")
    inst = (days + pd.Timedelta(days=1)).as_unit("ns").asi8
    eq = np.full(len(days), equity0, dtype=float)
    if len(pt):
        tk = pt[pt["taken"]]
        for r in tk.itertuples(index=False):
            k = np.searchsorted(inst, r.exit_time, side="left")
            eq[k:] += r.pnl
            a = np.searchsorted(inst, r.entry_time, side="left")
            if k > a and r.symbol in P.close_ffill:
                px = P.close_ffill[r.symbol].reindex(days[a:k]).to_numpy(float)
                u = r.notional * r.side * (px / r.entry - 1)
                eq[a:k] += np.nan_to_num(u)
    return pd.Series(eq, index=days)


def summarize(pt: pd.DataFrame, skips: dict, P: Panel, start: pd.Timestamp, end: pd.Timestamp,
              equity0: float = EQUITY0) -> dict:
    years = (end - start) / pd.Timedelta(days=365.25)
    mtm = mtm_equity(pt, P, start, end, equity0)
    me = mtm.groupby(mtm.index.strftime("%Y-%m")).last()
    mret = me / me.shift(1, fill_value=equity0) - 1
    tk = pt[pt["taken"]] if len(pt) else pt
    n_mn = skips.get("min_notional", 0)
    base = dict(trades=int(len(tk)), skipped=dict(skips),
                skipped_min_notional=int(n_mn),
                min_notional_skip_share=float(n_mn / (len(tk) + n_mn)) if len(tk) + n_mn else 0.0,
                max_dd_mtm=max_dd(np.r_[equity0, mtm.to_numpy()]),
                worst_month=float(mret.min()) if len(mret) else 0.0,
                worst_month_id=str(mret.idxmin()) if len(mret) else "",
                monthly={k: float(v) for k, v in mret.items()})
    if len(tk) == 0:
        base.update(win_rate=np.nan, exp_R=np.nan, total_R=0.0, pf=np.nan, total_return=0.0,
                    cagr=0.0, final_equity=equity0, max_dd_closed=0.0, losing_streak=0,
                    exposure=0.0, avg_hold_h=np.nan, long_trades=0, short_trades=0,
                    median_notional=np.nan, symbols=0)
        return base
    final = equity0 + float(tk["pnl"].sum())
    closed = equity0 + tk.sort_values("exit_time")["pnl"].cumsum().to_numpy()
    pos, neg = tk.loc[tk["R"] > 0, "R"].sum(), -tk.loc[tk["R"] <= 0, "R"].sum()
    base.update(
        win_rate=float((tk["net"] > 0).mean()), exp_R=float(tk["R"].mean()),
        total_R=float(tk["R"].sum()), pf=float(pos / neg) if neg > 0 else np.inf,
        total_return=final / equity0 - 1,
        cagr=(final / equity0) ** (1 / years) - 1 if final > 0 else -1.0,
        final_equity=final, max_dd_closed=max_dd(closed, equity0),
        losing_streak=longest_losing_streak(tk.sort_values("entry_time")["net"].to_numpy()),
        exposure=exposure(tk, start, end),
        avg_hold_h=float(((tk["exit_time"] - tk["entry_time"]) / NS_H).mean()),
        long_trades=int((tk["side"] == 1).sum()), short_trades=int((tk["side"] == -1).sum()),
        median_notional=float(tk["notional"].median()), symbols=int(tk["symbol"].nunique()))
    return base


def positive(m: dict) -> bool:
    """'Positive' everywhere in the protocol = total return > 0 AND expectancy > 0R."""
    return bool(m["trades"] > 0 and m["total_return"] > 0 and m["exp_R"] > 0)


# ------------------------------------------------------------------ strategies (frozen grids)
STRATS = {
    "R1": dict(name="Cross-sectional meme momentum",
               line="Hold the 1-3 liquid memes with the strongest risk-adjusted recent return, "
                    "swap out any that drop out of the top, and sit in cash when BTC is below "
                    "its 50-day average or no meme has positive momentum.",
               grid={"L": [7, 14, 30], "K": [1, 2, 3], "reb": ["daily", "weekly"]},
               default={"L": 14, "K": 3, "reb": "weekly"}),
    "R2": dict(name="Edge-monitor rotation (owner's idea)",
               line="Run one breakout setup on every liquid meme but only trade a coin while its "
                    "own last N closed paper trades are net profitable, preferring the most "
                    "hyped admitted coin.",
               grid={"N": [5, 10, 20], "theta": [-0.1, 0.0, 0.1]},
               default={"N": 10, "theta": 0.0}),
    "R3": dict(name="Long strong / short faded",
               line="Hold the single strongest meme (when BTC is above its 50-day average) and "
                    "short up to two memes whose hype burst has died (M11).",
               grid={"L": [7, 14, 30], "z": [3.0, 4.0, 5.0]},
               default={"L": 14, "z": 4.0}),
    "R4": dict(name="Volatility-targeted best of R1-R3",
               line="Trade exactly like the best of R1-R3, but cut each position so its daily "
                    "volatility is at most v of equity (never above the 1%-risk size).",
               grid={"v": [0.0025, 0.005, 0.01]},
               default={"v": 0.005}),
    "R5": dict(name="Short the faders",
               line="Memes as a group bleed value, so short the 1-3 liquid memes with the "
                    "weakest risk-adjusted recent return and swap them out when they stop being "
                    "the weakest.",
               grid={"L": [7, 14, 30], "K": [1, 2, 3], "regime": ["none", "btc_below_sma50"]},
               default={"L": 14, "K": 3, "regime": "none"}),
}
R2_BASE = "S02"            # 4h 30-bar breakout + volume, 2xATR stop, 10-bar trailing exit
R3_SHORT = "M11"           # hype decay short
R3_LEG_CAPS = {"long": 1, "short": 2}
R2_MIN_HIST = 5


def combos(sid: str) -> list[dict]:
    g = STRATS[sid]["grid"]
    keys = list(g)
    return [dict(zip(keys, v)) for v in itertools.product(*(g[k] for k in keys))]


def key(p: dict) -> str:
    return "|".join(f"{k}={v}" for k, v in p.items())


def neighbours(sid: str) -> list[str]:
    g, d = STRATS[sid]["grid"], STRATS[sid]["default"]
    out = []
    for k, vals in g.items():
        i = vals.index(d[k])
        for j in (i - 1, i + 1):
            if 0 <= j < len(vals):
                c = dict(d)
                c[k] = vals[j]
                out.append(key(c))
    return out


class Lab:
    """Candidate generation with caching. `cands(sid, params, costs, exclude)` returns
    (candidates, portfolio kwargs)."""

    def __init__(self, ctx: Ctx, P: Panel, r4_base: str | None = None):
        self.ctx, self.P, self.r4_base = ctx, P, r4_base
        self._c: dict = {}

    def _xs(self, L, K, reb, side, gate_name, exclude, leg):
        k = ("xs", L, K, reb, side, gate_name, tuple(sorted(exclude)), leg)
        if k not in self._c:
            gate = {"btc_up": self.P.btc_up, "btc_down": self.P.btc_down, "none": None}[gate_name]
            self._c[k] = xs_candidates(self.ctx, self.P, L, K, reb, side, gate, exclude, leg)
        return self._c[k]

    def _setup(self, sid, params, exclude, leg):
        k = ("setup", sid, key(params), tuple(sorted(exclude)), leg)
        if k not in self._c:
            self._c[k] = setup_candidates(self.ctx, self.P, sid, params, exclude, leg)
        return self._c[k]

    def cands(self, sid: str, p: dict, costs: Costs = BASE, exclude=()):
        if sid == "R1":
            return self._xs(p["L"], p["K"], p["reb"], 1, "btc_up", exclude, "main"), {}
        if sid == "R5":
            gate = "none" if p["regime"] == "none" else "btc_down"
            return self._xs(p["L"], p["K"], "weekly", -1, gate, exclude, "main"), {}
        if sid == "R2":
            v = self._setup(R2_BASE, {}, exclude, "main")
            if p.get("monitor") == "off" or len(v) == 0:
                return v, {}
            R = add_net(v, costs)["R"].to_numpy()
            ok = edge_admit(v["symbol"], v["entry_time"], v["exit_time"], R, p["N"], p["theta"],
                            R2_MIN_HIST)
            return v[ok].reset_index(drop=True), {}
        if sid == "R3":
            lg = self._xs(p["L"], 1, "daily", 1, "btc_up", exclude, "long")
            sh = self._setup(R3_SHORT, {"z": p["z"]}, exclude, "short")
            # shorts: prefer the most faded coin (lowest volume z) -> prio = +z
            sh = sh.assign(prio=np.where(np.isfinite(sh["hype_z"]), sh["hype_z"], np.inf)) \
                if len(sh) else sh
            c = pd.concat([x for x in (lg, sh) if len(x)], ignore_index=True)
            return c, {"leg_caps": R3_LEG_CAPS}
        if sid == "R4":
            base = self.r4_base
            c, kw = self.cands(base, STRATS[base]["default"], costs, exclude)
            return c, dict(kw, vol_budget=p["v"])
        raise KeyError(sid)

    def run(self, sid, p, costs=BASE, start=DEV_OOS_START, end=DEV_OOS_END, exclude=()):
        c, kw = self.cands(sid, p, costs, exclude)
        pt, sk = run_portfolio(c, costs, start, end, **kw)
        return pt, sk, summarize(pt, sk, self.P, start, end)


# ------------------------------------------------------------------ walk-forward
def wf_windows(start=DEV_START, end=DEV_END):
    out, m = [], start
    while True:
        te0 = m + pd.DateOffset(months=TRAIN_M)
        te1 = te0 + pd.DateOffset(months=TEST_M)
        if te1 > end:
            break
        out.append((m, te0, te1))
        m = m + pd.DateOffset(months=TEST_M)
    return out


def walk_forward(lab: Lab, sid: str) -> dict:
    """Each window: pick the grid combination with the highest total R of TAKEN trades in the
    12 training months (fresh EUR100 portfolio, base costs, >= 10 trades, ties -> default), then
    take that combination's candidates entered in the next 3 months. The stitched candidates run
    through ONE portfolio over all test quarters (positions limits enforced across boundaries)."""
    d = STRATS[sid]["default"]
    dk = key(d)
    cs = combos(sid)
    picks, parts = [], []
    for tr0, te0, te1 in wf_windows():
        best, bv = dk, -np.inf
        for p in cs:
            c, kw = lab.cands(sid, p)
            pt, _ = run_portfolio(c, BASE, tr0, te0, **kw)
            if len(pt) == 0 or pt["taken"].sum() < MIN_TRAIN_TRADES:
                continue
            v = float(pt.loc[pt["taken"], "R"].sum())
            if v > bv or (v == bv and key(p) == dk):
                best, bv = key(p), v
        bp = next(p for p in cs if key(p) == best)
        c, kw = lab.cands(sid, bp)
        if len(c):
            parts.append(c[(c["entry_time"] >= te0.value) & (c["entry_time"] < te1.value)])
        picks.append(dict(train=f"{tr0:%Y-%m}..{te0 - pd.Timedelta(days=1):%Y-%m}",
                          test=f"{te0:%Y-%m}..{te1 - pd.Timedelta(days=1):%Y-%m}", params=best,
                          train_R=None if bv == -np.inf else bv))
    w = wf_windows()
    a, b = w[0][1], w[-1][2]
    c = pd.concat([x for x in parts if len(x)], ignore_index=True) if parts else pd.DataFrame()
    kw = lab.cands(sid, d)[1]
    pt, sk = run_portfolio(c, BASE, a, b, **kw)
    m = summarize(pt, sk, lab.P, a, b)
    tk = pt[pt["taken"]] if len(pt) else pt
    for pk, (_, te0, te1) in zip(picks, w):
        x = tk[(tk["entry_time"] >= te0.value) & (tk["entry_time"] < te1.value)] if len(tk) else tk
        pk["test_trades"] = int(len(x))
        pk["test_R"] = float(x["R"].sum()) if len(x) else 0.0
        pk["test_pnl_usdt"] = float(x["pnl"].sum()) if len(x) else 0.0
    oos = tk[tk["entry_time"] >= DEV_OOS_START.value] if len(tk) else tk
    m.pop("monthly", None)
    return dict(span=f"{a:%Y-%m}..{b - pd.Timedelta(days=1):%Y-%m}", picks=picks, **m,
                dev_oos_part_R=float(oos["R"].sum()) if len(oos) else 0.0,
                dev_oos_part_trades=int(len(oos)))


# ------------------------------------------------------------------ protocol
def _clean(o):
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, pd.Timestamp):
        return o.isoformat()
    return o


def best_coin(pt: pd.DataFrame) -> tuple[str | None, float, float]:
    tk = pt[pt["taken"]] if len(pt) else pt
    if len(tk) == 0:
        return None, 0.0, np.nan
    by = tk.groupby("symbol")["R"].sum().sort_values()
    tot = by.sum()
    return by.index[-1], float(by.iloc[-1]), float(by.iloc[-1] / tot) if tot > 0 else np.nan


def evaluate_variant(lab: Lab, sid: str, p: dict) -> dict:
    """Dev IS / dev OOS at base and double costs, and dev OOS at double costs with the best coin
    (by dev-OOS total R at base costs) removed from the universe."""
    r = dict(strategy=sid, params=key(p))
    pt, sk, m = lab.run(sid, p, BASE, DEV_OOS_START, DEV_OOS_END)
    r["oos"] = m
    r["oos_2x"] = lab.run(sid, p, SCEN["both2x"], DEV_OOS_START, DEV_OOS_END)[2]
    r["is"] = lab.run(sid, p, BASE, DEV_IS_START, DEV_IS_END)[2]
    bc, bR, bsh = best_coin(pt)
    r["best_coin"], r["best_coin_R"], r["best_coin_share"] = bc, bR, bsh
    if bc is not None:
        r["oos_2x_ex_best"] = lab.run(sid, p, SCEN["both2x"], DEV_OOS_START, DEV_OOS_END,
                                      exclude=(bc,))[2]
    else:
        r["oos_2x_ex_best"] = None
    r["_pt"] = pt
    return r


def gates(r: dict, bench: dict, wf: dict, robust_share: float) -> list[str]:
    fails = []
    o = r["oos"]
    if o["trades"] < 50:
        fails.append(f"<50 dev-OOS trades ({o['trades']})")
    if not positive(r["oos_2x"]):
        fails.append("not positive at double costs")
    if not (o["cagr"] > max(bench["btc_perp"]["cagr"], bench["btc_spot"]["cagr"])):
        fails.append("does not beat BTC buy-and-hold")
    if not (o["cagr"] > bench["meme_basket"]["cagr"]):
        fails.append("does not beat the equal-weight meme basket")
    if not (wf["total_R"] > 0 and wf["total_return"] > 0):
        fails.append("walk-forward not positive")
    xb = r.get("oos_2x_ex_best")
    if xb is None or not positive(xb):
        fails.append(f"not positive at double costs without the best coin ({r['best_coin']})")
    if robust_share < 0.5:
        fails.append(f"not robust to nearby parameters ({robust_share:.0%} of neighbours positive)")
    if o["min_notional_skip_share"] > 0.5 or o["trades"] == 0:
        fails.append("not tradeable at EUR100 (min-notional skips)")
    return fails


def _strip(r):
    return {k: v for k, v in r.items() if not k.startswith("_")}


def dev_main():
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    memes = meme_symbols()
    full = load_ctx(memes + ["BTCUSDT"])
    ctx = truncate_ctx(full, DEV_END)
    assert max(sy.h1["open_time"].max() for sy in ctx.syms.values()) < DEV_END
    P = build_panel(ctx, memes)
    lab = Lab(ctx, P)
    bench = dict(btc_perp=btc_buy_hold(ctx, DEV_OOS_START, DEV_OOS_END),
                 btc_spot=btc_buy_hold(ctx, DEV_OOS_START, DEV_OOS_END, funding=False),
                 meme_basket=meme_basket(ctx, DEV_OOS_START, DEV_OOS_END),
                 btc_perp_2x=btc_buy_hold(ctx, DEV_OOS_START, DEV_OOS_END, SCEN["both2x"]))
    print("bench", {k: round(v["cagr"], 4) for k, v in bench.items()}, flush=True)
    reg = btc_regimes(ctx)
    res = dict(generated=pd.Timestamp.now(tz="UTC").isoformat(), bench=bench,
               windows=dict(dev=f"{DEV_START:%Y-%m}..2026-04", dev_is="2022-11..2024-10",
                            dev_oos="2024-11..2026-04", holdout="2026-05..2026-10"),
               regime_months={k: int((reg.loc["2024-11":"2026-04"] == k).sum())
                              for k in ("bull", "bear", "sideways")},
               eligible_memes_per_day=dict(
                   dev_oos_mean=float(P.elig.loc[DEV_OOS_START:DEV_OOS_END].sum(axis=1).mean()),
                   dev_is_mean=float(P.elig.loc[DEV_IS_START:DEV_IS_END].sum(axis=1).mean())),
               strategies={}, variants=[])
    order = ["R1", "R2", "R3", "R4", "R5"]
    for sid in order:
        if sid == "R4":
            cand = {s: res["strategies"][s]["default"]["oos"]["cagr"] for s in ("R1", "R2", "R3")}
            dd = {s: res["strategies"][s]["default"]["oos"]["max_dd_mtm"] for s in cand}
            lab.r4_base = sorted(cand, key=lambda s: (-cand[s], dd[s]))[0]
            print("R4 base =", lab.r4_base, cand, flush=True)
        st = STRATS[sid]
        var = {}
        plist = combos(sid) + ([{"monitor": "off"}] if sid == "R2" else [])
        for p in plist:
            r = evaluate_variant(lab, sid, p)
            var[key(p)] = r
            o = r["oos"]
            print(f"{sid} {key(p):32s} oos n={o['trades']:4d} expR={o['exp_R']:+.3f} "
                  f"ret={o['total_return']:+.3f} 2x={r['oos_2x']['total_return']:+.3f} "
                  f"skipMN={o['skipped_min_notional']} {time.time() - t0:.0f}s", flush=True)
        dk = key(st["default"])
        nb = neighbours(sid)
        nb_pos = [positive(var[k]["oos"]) for k in nb]
        robust = float(np.mean(nb_pos)) if nb_pos else 0.0
        grid_pos = float(np.mean([positive(var[key(p)]["oos"]) for p in combos(sid)]))
        wf = walk_forward(lab, sid)
        d = var[dk]
        pt = d["_pt"]
        tk = pt[pt["taken"]] if len(pt) else pt
        mc = monte_carlo(tk["ret_on_equity"].to_numpy(), n=5000) if len(tk) else {}
        regimes = regime_split(tk, reg) if len(tk) else {}
        fails = gates(d, bench, wf, robust)
        yrs = {}
        for a, b in ((DEV_OOS_START, pd.Timestamp("2025-11-01", tz="UTC")),
                     (pd.Timestamp("2025-11-01", tz="UTC"), DEV_OOS_END)):
            m = lab.run(sid, st["default"], BASE, a, b)[2]
            yrs[f"{a:%Y-%m}..{b - pd.Timedelta(days=1):%Y-%m}"] = dict(
                trades=m["trades"], exp_R=m["exp_R"], total_return=m["total_return"],
                max_dd_mtm=m["max_dd_mtm"],
                btc_spot=btc_buy_hold(ctx, a, b, funding=False)["total_return"])
        by_sym = tk.groupby("symbol")["R"].agg(["count", "sum"]).sort_values("sum") \
            if len(tk) else pd.DataFrame()
        res["strategies"][sid] = dict(
            name=st["name"], line=st["line"], grid=st["grid"], default=_strip(d),
            default_key=dk, neighbours=nb, robust_neighbour_pos_share=robust,
            robust_grid_pos_share=grid_pos, wf=wf, mc=mc, regimes=regimes, by_half=yrs,
            exit_reasons=tk["reason"].value_counts().to_dict() if len(tk) else {},
            by_symbol={s: dict(trades=int(r_["count"]), R=float(r_["sum"]))
                       for s, r_ in by_sym.iterrows()},
            gates_failed=fails, status="PASS_DEV" if not fails else "KILLED",
            r4_base=lab.r4_base if sid == "R4" else None,
            variants_tried=len(plist))
        for k, r in var.items():
            res["variants"].append(dict(
                strategy=sid, params=k, default=(k == dk),
                oos_trades=r["oos"]["trades"], oos_exp_R=r["oos"]["exp_R"],
                oos_total_return=r["oos"]["total_return"], oos_cagr=r["oos"]["cagr"],
                oos_2x_total_return=r["oos_2x"]["total_return"],
                oos_2x_exp_R=r["oos_2x"]["exp_R"],
                ex_best_2x_total_return=(r["oos_2x_ex_best"] or {}).get("total_return"),
                ex_best_2x_exp_R=(r["oos_2x_ex_best"] or {}).get("exp_R"),
                best_coin=r["best_coin"], max_dd_mtm=r["oos"]["max_dd_mtm"],
                worst_month=r["oos"]["worst_month"], losing_streak=r["oos"]["losing_streak"],
                skipped_min_notional=r["oos"]["skipped_min_notional"],
                skipped_slots=r["oos"]["skipped"]["slots"],
                is_trades=r["is"]["trades"], is_exp_R=r["is"]["exp_R"],
                is_total_return=r["is"]["total_return"]))
        print(sid, "gates failed:", fails, "WF R", round(wf["total_R"], 1), flush=True)
    (OUT / "dev_results.json").write_text(json.dumps(_clean(res), indent=1, default=str))
    pd.DataFrame(res["variants"]).to_csv(OUT / "variants.csv", index=False, float_format="%.5g")
    print(f"done {time.time() - t0:.0f}s")


def holdout_main():
    path = OUT / "holdout.json"
    if path.exists():
        sys.exit("holdout.json exists: the holdout has already been run once. Refusing to rerun.")
    dev = json.loads((OUT / "dev_results.json").read_text())
    memes = meme_symbols()
    ctx = load_ctx(memes + ["BTCUSDT"])
    P = build_panel(ctx, memes)
    lab = Lab(ctx, P, r4_base=dev["strategies"]["R4"]["r4_base"])
    out = dict(generated=pd.Timestamp.now(tz="UTC").isoformat(), window="2026-05-01..2026-10-31",
               note="Run exactly once per strategy with the frozen default parameters.",
               bench=dict(btc_perp=btc_buy_hold(ctx, HOLDOUT_START, HOLDOUT_END),
                          btc_spot=btc_buy_hold(ctx, HOLDOUT_START, HOLDOUT_END, funding=False),
                          meme_basket=meme_basket(ctx, HOLDOUT_START, HOLDOUT_END)),
               strategies={})
    for sid in ["R1", "R2", "R3", "R4", "R5"]:
        p = STRATS[sid]["default"]
        pt, sk, m = lab.run(sid, p, BASE, HOLDOUT_START, HOLDOUT_END)
        m2 = lab.run(sid, p, SCEN["both2x"], HOLDOUT_START, HOLDOUT_END)[2]
        tk = pt[pt["taken"]] if len(pt) else pt
        out["strategies"][sid] = dict(params=key(p), base=m, both2x=m2,
                                      by_symbol=tk.groupby("symbol")["R"].sum().sort_values()
                                      .to_dict() if len(tk) else {},
                                      positive=positive(m))
        print(sid, m["trades"], round(m["total_return"], 4), round(m2["total_return"], 4))
    path.write_text(json.dumps(_clean(out), indent=1, default=str))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "dev"
    if cmd == "dev":
        dev_main()
    elif cmd == "holdout":
        holdout_main()
    elif cmd == "report":
        from .rotation_report import main as rep
        rep()
    else:
        sys.exit(f"unknown command {cmd}")
