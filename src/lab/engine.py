"""LAB backtest engine: event-driven on CLOSED bars, one position per symbol per setup.

Timing
  * A signal row refers to a closed bar `idx` (all inputs known at its close).
  * Market entry: the open of bar idx+1 (taker). Limit entry: resting from the open of idx+1 for
    `limit_bars` bars, filled only if price trades THROUGH the limit (low < limit for a buy),
    maker fee; if the bar opens already through the limit it is a marketable order (taker at open).
  * Never enter on a bar with trades == 0 (nor signal on one). An open-price exit that falls on a
    zero-trade bar moves to the next bar's open.
  * Stops/targets are checked inside every bar from the entry bar on, with that bar's high/low.
    If a bar opens beyond the stop: exit at that open. If one bar touches stop AND target:
    the STOP is assumed (pessimistic). Targets are resting limits: maker, filled only when price
    trades through (high > target for a long).
  * Close-based exits (trailing / indicator / funding rules, `close_exit` level) are seen at a bar
    close and executed at the next bar's open (taker).
  * Time stop: exit at the open of bar `tmax` (absolute bar index).
  * Delisted symbol: a position still open at the last real bar is force-closed at that bar's close
    moved 2% against the position (DELIST_HAIRCUT_EST, an ESTIMATE). A position open at the end of
    the backtest window is closed at the last bar's close (taker).

Costs are NOT baked into the path: the simulator records raw prices and fill types, and
`apply_costs` turns them into net returns for any fee/slippage scenario. Funding is real: every
settlement with entry_time < T <= exit_time is booked at qty x price(T) x rate (long pays a
positive rate, short receives it).
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .data import DELIST_HAIRCUT_EST

NS_H = 3_600_000_000_000

SIG_COLS = ["idx", "side", "stop", "stop_dist", "target", "target_R", "tmax", "close_exit",
            "limit", "limit_bars"]


@dataclass
class Costs:
    taker: float = 0.0005
    maker: float = 0.0002
    slip_mult: float = 1.0

    def scaled(self, fee_mult: float = 1.0, slip_mult: float = 1.0) -> "Costs":
        return Costs(self.taker * fee_mult, self.maker * fee_mult, self.slip_mult * slip_mult)


BASE = Costs()


def slip_bp(sym: str, is_meme: bool) -> float:
    if sym in ("BTCUSDT", "ETHUSDT"):
        return 1.0
    return 10.0 if is_meme else 3.0


def make_signals(**cols) -> pd.DataFrame:
    """Build a signal frame; missing optional columns are NaN."""
    n = len(cols["idx"])
    d = {}
    for c in SIG_COLS:
        v = cols.get(c)
        if v is None:
            v = np.full(n, np.nan)
        d[c] = np.asarray(v, dtype=float) if c not in ("idx", "side") else np.asarray(v)
    df = pd.DataFrame(d)
    df["idx"] = df["idx"].astype(np.int64)
    df["side"] = df["side"].astype(np.int64)
    return df


def simulate(bars: pd.DataFrame, sig: pd.DataFrame, *, bar_ns: int, delisted: bool,
             fund_t: np.ndarray, fund_r: np.ndarray, px_t: np.ndarray, px_o: np.ndarray,
             exit_long: np.ndarray | None = None, exit_short: np.ndarray | None = None,
             ) -> list[dict]:
    """Simulate one symbol. `px_t`/`px_o`: 1h open times/prices used to value funding."""
    n = len(bars)
    if n == 0 or len(sig) == 0:
        return []
    t = bars["open_time"].astype("datetime64[ns, UTC]").astype("int64").to_numpy()
    o = bars["open"].to_numpy(float)
    h = bars["high"].to_numpy(float)
    lo = bars["low"].to_numpy(float)
    c = bars["close"].to_numpy(float)
    tr = bars["trades"].to_numpy()
    last = n - 1
    xl = np.zeros(n, bool) if exit_long is None else np.asarray(exit_long, bool)
    xs = np.zeros(n, bool) if exit_short is None else np.asarray(exit_short, bool)
    sig = sig.sort_values("idx", kind="stable")
    S = {k: sig[k].to_numpy() for k in SIG_COLS}
    trades = []
    next_free = 0
    for k in range(len(sig)):
        s = int(S["idx"][k])
        side = int(S["side"][k])
        if s < next_free or s + 1 > last or s < 0:
            continue
        if tr[s] == 0:
            continue
        e = s + 1
        # ---- entry ----
        lim = S["limit"][k]
        if np.isfinite(lim):
            nb = int(S["limit_bars"][k]) if np.isfinite(S["limit_bars"][k]) else 1
            fill_j, entry, ekind = -1, np.nan, "maker"
            for j in range(e, min(e + nb, n)):
                if tr[j] == 0:
                    continue
                if (side == 1 and o[j] <= lim) or (side == -1 and o[j] >= lim):
                    fill_j, entry, ekind = j, o[j], "taker"     # marketable at the open
                    break
                if (side == 1 and lo[j] < lim) or (side == -1 and h[j] > lim):
                    fill_j, entry, ekind = j, lim, "maker"
                    break
            if fill_j < 0:
                continue
            e = fill_j
            entry_time = t[e] + (bar_ns // 2 if ekind == "maker" else 0)
        else:
            if tr[e] == 0:
                continue
            entry, ekind, entry_time = o[e], "taker", t[e]
        # ---- levels ----
        st_abs, st_d = S["stop"][k], S["stop_dist"][k]
        if np.isfinite(st_abs) and np.isfinite(st_d):     # "the closer of the two"
            cand = entry - side * st_d
            stop = max(st_abs, cand) if side == 1 else min(st_abs, cand)
        elif np.isfinite(st_abs):
            stop = st_abs
        elif np.isfinite(st_d):
            stop = entry - side * st_d
        else:
            continue
        if not np.isfinite(stop) or side * (entry - stop) <= 0:
            continue                                      # stop already breached at entry
        R = abs(entry - stop)
        tgt = S["target"][k]
        if not np.isfinite(tgt) and np.isfinite(S["target_R"][k]):
            tgt = entry + side * S["target_R"][k] * R
        if np.isfinite(tgt) and side * (tgt - entry) <= 0:
            continue                                      # target already passed at entry
        tmax = int(S["tmax"][k]) if np.isfinite(S["tmax"][k]) else n + 10
        cx = S["close_exit"][k]
        xsig = xl if side == 1 else xs
        # ---- walk the bars ----
        exit_px, xkind, reason, xj, xtime = np.nan, "taker", "", -1, 0
        pending = False
        j = e
        while True:
            if j > last:
                j = last
                exit_px, reason = c[last], "end"
                xj, xtime = last, t[last] + bar_ns
                break
            if j > e and (pending or j >= tmax):
                jj = j
                while jj < last and tr[jj] == 0:
                    jj += 1
                exit_px, reason = o[jj], ("signal" if pending else "time")
                xj, xtime = jj, t[jj]
                break
            if j > e and side * (o[j] - stop) <= 0:
                exit_px, reason, xj, xtime = o[j], "stop_gap", j, t[j]
                break
            hit_s = (lo[j] <= stop) if side == 1 else (h[j] >= stop)
            hit_t = np.isfinite(tgt) and ((h[j] > tgt) if side == 1 else (lo[j] < tgt))
            if hit_s:
                exit_px, reason, xj, xtime = stop, "stop", j, t[j] + bar_ns // 2
                break
            if hit_t:
                exit_px, reason, xkind, xj, xtime = tgt, "target", "maker", j, t[j] + bar_ns // 2
                break
            if j == last:
                exit_px, reason, xj, xtime = c[last], "end", last, t[last] + bar_ns
                break
            if xsig[j] or (np.isfinite(cx) and side * (c[j] - cx) > 0):
                pending = True
            j += 1
        delist = False
        if reason == "end" and delisted:
            exit_px = c[last] * (1 - side * DELIST_HAIRCUT_EST)
            reason, delist = "delist", True
        # ---- funding: settlements with entry_time < T <= exit_time ----
        fund = 0.0
        if len(fund_t):
            a = np.searchsorted(fund_t, entry_time, side="right")
            b = np.searchsorted(fund_t, xtime, side="right")
            if b > a:
                ft = fund_t[a:b]
                pi = np.clip(np.searchsorted(px_t, ft, side="right") - 1, 0, len(px_o) - 1)
                fund = float(-side * np.sum(fund_r[a:b] * px_o[pi] / entry))
        trades.append(dict(side=side, sig_idx=s, entry_idx=e, exit_idx=xj,
                           entry_time=entry_time, exit_time=xtime, entry=entry, exit=exit_px,
                           stop=stop, target=tgt, entry_kind=ekind, exit_kind=xkind,
                           reason=reason, delist=delist, funding=fund,
                           stop_frac=R / entry))
        # one position per symbol: next entry no earlier than the exit bar (open exits) or the
        # bar after it (intrabar exits)
        next_free = xj - 1 if reason in ("signal", "time", "stop_gap") else xj
    return trades


def apply_costs(tr: pd.DataFrame, costs: Costs = BASE) -> pd.Series:
    """Net return per unit of entry notional, for a cost scenario."""
    if len(tr) == 0:
        return pd.Series(dtype=float)
    side = tr["side"].to_numpy()
    slip = tr["slip_bp"].to_numpy() * 1e-4 * costs.slip_mult
    et = tr["entry_kind"].eq("taker").to_numpy()
    xt = tr["exit_kind"].eq("taker").to_numpy()
    ef = tr["entry"].to_numpy() * (1 + side * slip * et)
    xf = tr["exit"].to_numpy() * (1 - side * slip * xt)
    gross = side * (xf / ef - 1)
    fee = np.where(et, costs.taker, costs.maker) + np.where(xt, costs.taker, costs.maker) * xf / ef
    return pd.Series(gross - fee + tr["funding"].to_numpy() * tr["entry"].to_numpy() / ef,
                     index=tr.index)


def add_net(tr: pd.DataFrame, costs: Costs = BASE) -> pd.DataFrame:
    tr = tr.copy()
    tr["net"] = apply_costs(tr, costs)
    tr["R"] = tr["net"] / tr["stop_frac"]
    return tr


# ------------------------------------------------------------------ portfolio
def portfolio(tr: pd.DataFrame, *, equity0: float = 110.0, risk: float = 0.01,
              lev_cap: float = 3.0) -> pd.DataFrame:
    """Size each trade at `risk` x equity / stop distance, capped so total open notional stays
    <= lev_cap x equity (trades that find no room are skipped). Equity = realised (closed-trade)
    equity. Returns the trades with notional, pnl, equity_before and taken flag."""
    tr = tr.sort_values(["entry_time", "exit_time"], kind="stable").copy()
    eq = equity0
    open_heap: list[tuple[int, int, float, float]] = []   # (exit_time, id, notional, pnl)
    open_notional = 0.0
    notl, pnl, eqb, taken = [], [], [], []
    for i, (et, net, sf) in enumerate(zip(tr["entry_time"].to_numpy(), tr["net"].to_numpy(),
                                          tr["stop_frac"].to_numpy())):
        while open_heap and open_heap[0][0] <= et:
            _, _, nn, pp = heapq.heappop(open_heap)
            eq += pp
            open_notional -= nn
        want = risk * eq / sf
        room = lev_cap * eq - open_notional
        nt = min(want, room) if eq > 0 else 0.0
        if nt <= 1e-9:
            notl.append(0.0), pnl.append(0.0), eqb.append(eq), taken.append(False)
            continue
        p = nt * net
        heapq.heappush(open_heap, (int(tr["exit_time"].iat[i]), i, nt, p))
        open_notional += nt
        notl.append(nt), pnl.append(p), eqb.append(eq), taken.append(True)
    tr["notional"], tr["pnl"], tr["equity_before"], tr["taken"] = notl, pnl, eqb, taken
    tr["ret_on_equity"] = np.where(tr["taken"], tr["pnl"] / tr["equity_before"], 0.0)
    return tr


def equity_curve(pt: pd.DataFrame, equity0: float = 110.0) -> pd.Series:
    """Closed-trade equity indexed by exit time."""
    x = pt[pt["taken"]].sort_values("exit_time")
    if len(x) == 0:
        return pd.Series([equity0], index=[pd.NaT])
    idx = pd.to_datetime(x["exit_time"].to_numpy(), utc=True)
    return pd.Series(equity0 + x["pnl"].cumsum().to_numpy(), index=idx)


def max_dd(eq: pd.Series | np.ndarray, equity0: float | None = None) -> float:
    v = np.asarray(eq, float)
    if equity0 is not None:
        v = np.concatenate([[equity0], v])
    if len(v) == 0:
        return 0.0
    peak = np.maximum.accumulate(v)
    return float(np.max(1 - v / peak))


def longest_losing_streak(net: np.ndarray) -> int:
    best = cur = 0
    for x in net:
        cur = cur + 1 if x <= 0 else 0
        best = max(best, cur)
    return best


def exposure(tr: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> float:
    """Share of hours in [start, end) with at least one position open."""
    hrs = int((end - start) / pd.Timedelta(hours=1))
    if len(tr) == 0 or hrs <= 0:
        return 0.0
    s0 = start.value
    occ = np.zeros(hrs + 1, np.int32)
    a = np.clip((tr["entry_time"].to_numpy() - s0) // NS_H, 0, hrs)
    b = np.clip((tr["exit_time"].to_numpy() - s0 + NS_H - 1) // NS_H, 0, hrs)
    np.add.at(occ, a, 1)
    np.add.at(occ, b, -1)
    return float((np.cumsum(occ)[:hrs] > 0).mean())


def metrics(tr: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp,
            equity0: float = 110.0) -> dict:
    """Trade + portfolio metrics for trades ENTERED in [start, end)."""
    w = tr[(tr["entry_time"] >= start.value) & (tr["entry_time"] < end.value)]
    years = (end - start) / pd.Timedelta(days=365.25)
    if len(w) == 0:
        return dict(trades=0, win_rate=np.nan, exp_R=np.nan, total_R=0.0, pf=np.nan,
                    cagr=0.0, total_return=0.0, max_dd=0.0, worst_month=0.0, worst_month_id="",
                    losing_streak=0, exposure=0.0, avg_hold_h=np.nan, skipped_cap=0)
    pt = portfolio(w, equity0=equity0)
    eq = equity_curve(pt, equity0)
    final = float(eq.iloc[-1]) if len(eq) else equity0
    tot = final / equity0 - 1
    cagr = (final / equity0) ** (1 / years) - 1 if final > 0 else -1.0
    # monthly returns on closed-trade equity
    x = pt[pt["taken"]].copy()
    x["m"] = pd.to_datetime(x["exit_time"], utc=True).dt.strftime("%Y-%m")
    months = pd.period_range(start.tz_localize(None), (end - pd.Timedelta(seconds=1))
                             .tz_localize(None), freq="M").strftime("%Y-%m")
    mp = x.groupby("m")["pnl"].sum().reindex(months, fill_value=0.0)
    eq_start = equity0 + mp.cumsum().shift(1, fill_value=0.0)
    mret = mp / eq_start
    pos, neg = w.loc[w["R"] > 0, "R"].sum(), -w.loc[w["R"] <= 0, "R"].sum()
    order = w.sort_values("entry_time")
    return dict(
        trades=int(len(w)), win_rate=float((w["net"] > 0).mean()), exp_R=float(w["R"].mean()),
        total_R=float(w["R"].sum()), pf=float(pos / neg) if neg > 0 else np.inf,
        cagr=float(cagr), total_return=float(tot), max_dd=max_dd(eq.to_numpy(), equity0),
        worst_month=float(mret.min()), worst_month_id=str(mret.idxmin()),
        losing_streak=longest_losing_streak(order["net"].to_numpy()),
        exposure=exposure(w, start, end),
        avg_hold_h=float(((w["exit_time"] - w["entry_time"]) / NS_H).mean()),
        skipped_cap=int((~pt["taken"]).sum()), monthly=mret)


# ------------------------------------------------------------------ Monte Carlo
def monte_carlo(ret_on_equity: np.ndarray, n: int = 5000, seed: int = 7,
                ruin: float = 0.5) -> dict:
    """Trade-order reshuffle and bootstrap of per-trade equity returns (sequential compounding)."""
    r = np.asarray(ret_on_equity, float)
    if len(r) == 0:
        return {}
    rng = np.random.default_rng(seed)
    out = {}
    for kind in ("shuffle", "bootstrap"):
        if kind == "shuffle":
            m = np.array([rng.permutation(r) for _ in range(n)])
        else:
            m = rng.choice(r, size=(n, len(r)), replace=True)
        eq = np.cumprod(1 + m, axis=1)
        eq = np.concatenate([np.ones((n, 1)), eq], axis=1)
        dd = 1 - eq / np.maximum.accumulate(eq, axis=1)
        mdd = dd.max(axis=1)
        out[f"{kind}_dd_p95"] = float(np.percentile(mdd, 95))   # 5% worst-case max drawdown
        out[f"{kind}_dd_median"] = float(np.median(mdd))
        out[f"{kind}_p_ruin"] = float((eq.min(axis=1) <= ruin).mean())
        out[f"{kind}_final_p5"] = float(np.percentile(eq[:, -1], 5))
        out[f"{kind}_final_median"] = float(np.median(eq[:, -1]))
    return out
