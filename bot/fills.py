"""Paper fills: LAB's engine (src/lab/engine.py `simulate`) turned into bar-by-bar events.

`simulate` walks a whole symbol history at once. The bot sees bars one at a time, so the same
rules are split into an OPEN event (the moment a bar starts: its open price is known) and a
CLOSE event (the bar is complete). Order of events at one instant T for one symbol:
CLOSE(bar ending at T) then OPEN(bar starting at T); at OPEN, exits are handled before entries.

Rules (identical to lab.engine.simulate):
  entry   market: taker at the open of the bar after the signal bar. limit: resting from that
          open for `limit_bars` bars; marketable at an open -> taker at the open; else filled
          only if price trades THROUGH the limit (low < limit for a buy) -> maker at the limit,
          entry time = bar open + half a bar. No signal on a zero-trade bar.
  levels  stop = absolute stop and/or entry -/+ stop_dist ("the closer of the two"); the trade
          is skipped if the stop is already at/through the entry (or the target already passed).
          The stop is attached to the order from the first moment (resting stop-market).
  OPEN    (bars after the entry bar) pending close-exit or time stop -> exit at this open;
          else a bar that opens through the stop -> exit at this open ("stop_gap").
  CLOSE   stop touched -> exit at the stop (taker); target touched -> exit at the target
          (maker); BOTH in one bar -> the STOP (pessimistic); exit time = bar open + half a bar.
          Then a close-exit signal (setup exit mask or close_exit level) -> pending for the
          next open.
  funding every settlement T with entry_time < T <= exit_time, valued at the open of the 1h bar
          containing T: -side x rate x price / entry per unit of entry notional.
  costs   lab.engine.apply_costs (taker/maker fees, slippage on taker fills, funding).
One position per symbol; a signal that arrives while a position is open is only taken if that
position exits at (or before) the entry open - the same outcome as simulate's `next_free` rule.

Known, documented differences (none occur for S16, which uses market entries only):
  * zero-trade bars are only known at the bar close: an entry or open-exit falling on a bar
    that ends up with zero trades is not moved to the next bar as simulate does;
  * with several overlapping resting LIMIT orders on one symbol, simulate (which handles one
    signal at a time) may take an order that fills LATER than another; the bot takes the first
    order that actually fills and cancels the others.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from lab.engine import apply_costs

NS_H = 3_600_000_000_000


def _f(x) -> float:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return math.nan
    return x


def fin(x) -> bool:
    return x is not None and np.isfinite(_f(x))


# ------------------------------------------------------------------ orders
def new_order(sig: dict, sig_time: int, tf_ns: int, symbol: str) -> dict:
    """A queued entry from one LAB signal row (sig_time = open time of the signal bar, ns)."""
    idx = int(sig["idx"])
    tmax = _f(sig.get("tmax"))
    lim = _f(sig.get("limit"))
    nb = _f(sig.get("limit_bars"))
    return dict(
        symbol=symbol, side=int(sig["side"]), sig_time=int(sig_time),
        first_bar=int(sig_time) + tf_ns, kind="limit" if np.isfinite(lim) else "market",
        limit=lim if np.isfinite(lim) else None,
        limit_bars=int(nb) if np.isfinite(nb) else 1,
        stop=_none(sig.get("stop")), stop_dist=_none(sig.get("stop_dist")),
        target=_none(sig.get("target")), target_R=_none(sig.get("target_R")),
        tmax_time=int(sig_time) + int(round(tmax - idx)) * tf_ns if np.isfinite(tmax) else None,
        close_exit=_none(sig.get("close_exit")), placed=False)


def _none(x):
    x = _f(x)
    return float(x) if np.isfinite(x) else None


def levels(order: dict, entry: float) -> tuple[float | None, float | None, str]:
    """(stop, target, problem) exactly as simulate computes them for a fill at `entry`."""
    side = order["side"]
    st_abs, st_d = order["stop"], order["stop_dist"]
    if st_abs is not None and st_d is not None:
        cand = entry - side * st_d
        stop = max(st_abs, cand) if side == 1 else min(st_abs, cand)
    elif st_abs is not None:
        stop = st_abs
    elif st_d is not None:
        stop = entry - side * st_d
    else:
        return None, None, "no stop"
    if not np.isfinite(stop) or side * (entry - stop) <= 0:
        return None, None, "stop already at/through the entry price"
    tgt = order["target"]
    if tgt is None and order["target_R"] is not None:
        tgt = entry + side * order["target_R"] * abs(entry - stop)
    if tgt is not None and side * (tgt - entry) <= 0:
        return None, None, "target already passed at entry"
    return float(stop), (None if tgt is None else float(tgt)), ""


def order_on_open(order: dict, bar_time: int, o: float, tf_ns: int):
    """Fill (price, kind, time) at this open, 'expired', or None."""
    if order["kind"] == "market":
        return (o, "taker", bar_time) if bar_time == order["first_bar"] else "expired"
    end = order["first_bar"] + order["limit_bars"] * tf_ns
    if bar_time < order["first_bar"]:
        return None
    if bar_time >= end:
        return "expired"
    side, lim = order["side"], order["limit"]
    if (side == 1 and o <= lim) or (side == -1 and o >= lim):
        return (o, "taker", bar_time)
    return None


def order_on_close(order: dict, bar: dict, tf_ns: int):
    """Limit through-fill (price, 'maker', time) in this bar, 'expired' after its window, None."""
    if order["kind"] != "limit" or bar["time"] < order["first_bar"]:
        return None
    side, lim = order["side"], order["limit"]
    if bar["trades"] > 0 and ((side == 1 and bar["low"] < lim) or
                              (side == -1 and bar["high"] > lim)):
        return (lim, "maker", bar["time"] + tf_ns // 2)
    if bar["time"] >= order["first_bar"] + (order["limit_bars"] - 1) * tf_ns:
        return "expired"
    return None


# ------------------------------------------------------------------ positions
def open_position(order: dict, fill: tuple, stop: float, target, bar_time: int) -> dict:
    price, kind, t = fill
    return dict(symbol=order["symbol"], side=order["side"], sig_time=order["sig_time"],
                entry_bar=int(bar_time), entry_time=int(t), entry=float(price), entry_kind=kind,
                stop=float(stop), target=target, tmax_time=order["tmax_time"],
                close_exit=order["close_exit"], pending=False, funding=0.0,
                funding_booked=[], stop_frac=abs(price - stop) / price)


def pos_on_open(pos: dict, bar_time: int, o: float):
    """Exit (price, kind, reason, time) at this open, or None."""
    if bar_time <= pos["entry_bar"]:
        return None
    side = pos["side"]
    if pos["pending"] or (pos["tmax_time"] is not None and bar_time >= pos["tmax_time"]):
        return (o, "taker", "signal" if pos["pending"] else "time", bar_time)
    if side * (o - pos["stop"]) <= 0:
        return (o, "taker", "stop_gap", bar_time)
    return None


def pos_on_close(pos: dict, bar: dict, xsig: bool, tf_ns: int):
    """Intrabar stop/target exit (pessimistic: stop first), else maybe set `pending`."""
    side, stop, tgt = pos["side"], pos["stop"], pos["target"]
    hit_s = bar["low"] <= stop if side == 1 else bar["high"] >= stop
    hit_t = tgt is not None and (bar["high"] > tgt if side == 1 else bar["low"] < tgt)
    mid = bar["time"] + tf_ns // 2
    if hit_s:
        return (stop, "taker", "stop", mid)
    if hit_t:
        return (tgt, "maker", "target", mid)
    cx = pos["close_exit"]
    if xsig or (cx is not None and side * (bar["close"] - cx) > 0):
        pos["pending"] = True
    return None


def book_funding(pos: dict, fund_t: np.ndarray, fund_r: np.ndarray, upto_ns: int,
                 price_at) -> list[tuple[int, float, float]]:
    """Book settlements with entry_time < T <= upto_ns not booked yet. price_at(T_ns) -> 1h open
    of the bar containing T (None if not known yet: booked on a later call)."""
    done = set(pos["funding_booked"])
    out = []
    a = np.searchsorted(fund_t, pos["entry_time"], side="right")
    b = np.searchsorted(fund_t, upto_ns, side="right")
    for k in range(a, b):
        T = int(fund_t[k])
        if T in done:
            continue
        px = price_at(T)
        if px is None:
            continue
        amt = float(-pos["side"] * fund_r[k] * px / pos["entry"])
        pos["funding"] += amt
        pos["funding_booked"].append(T)
        out.append((T, float(fund_r[k]), amt))
    return out


def net_return(pos: dict, exit_px: float, exit_kind: str, slip_bp: float, taker: float,
               maker: float) -> float:
    """Net return per unit of entry notional - lab.engine.apply_costs on this one trade."""
    from lab.engine import Costs

    row = pd.DataFrame({"side": [pos["side"]], "slip_bp": [slip_bp],
                        "entry_kind": [pos["entry_kind"]], "exit_kind": [exit_kind],
                        "entry": [pos["entry"]], "exit": [exit_px], "funding": [pos["funding"]]})
    return float(apply_costs(row, Costs(taker=taker, maker=maker)).iloc[0])


# ------------------------------------------------------------------ offline driver
def drive(bars: pd.DataFrame, sig: pd.DataFrame, *, bar_ns: int, fund_t=None, fund_r=None,
          px_t=None, px_o=None, exit_long=None, exit_short=None) -> list[dict]:
    """Feed one symbol's bars through the event functions above, in time order, the way the
    bot does live. Returns trades in lab.engine.simulate's format (for parity tests)."""
    n = len(bars)
    t = bars["open_time"].astype("datetime64[ns, UTC]").astype("int64").to_numpy()
    o, h, lo, c = (bars[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    tr = bars["trades"].to_numpy()
    xl = np.zeros(n, bool) if exit_long is None else np.asarray(exit_long, bool)
    xs = np.zeros(n, bool) if exit_short is None else np.asarray(exit_short, bool)
    fund_t = np.array([], np.int64) if fund_t is None else np.asarray(fund_t)
    fund_r = np.array([]) if fund_r is None else np.asarray(fund_r)
    by_idx: dict[int, list[dict]] = {}
    for r in sig.sort_values("idx", kind="stable").to_dict("records"):
        by_idx.setdefault(int(r["idx"]), []).append(r)
    idx_of = {int(x): i for i, x in enumerate(t)}

    def price_at(T):
        k = int(np.clip(np.searchsorted(px_t, T, side="right") - 1, 0, len(px_o) - 1))
        return float(px_o[k])

    orders: list[dict] = []
    pos = None
    trades = []

    def close_trade(p, ex):
        price, kind, reason, xt = ex
        book_funding(p, fund_t, fund_r, xt, price_at)
        j = idx_of.get(int(xt // bar_ns * bar_ns) if reason in ("stop", "target") else xt)
        if j is None:  # mid-bar time -> its bar
            j = int(np.searchsorted(t, xt, side="right") - 1)
        trades.append(dict(side=p["side"], sig_idx=idx_of[p["sig_time"]],
                           entry_idx=idx_of[p["entry_bar"]], exit_idx=j,
                           entry_time=p["entry_time"], exit_time=xt, entry=p["entry"],
                           exit=price, stop=p["stop"],
                           target=np.nan if p["target"] is None else p["target"],
                           entry_kind=p["entry_kind"], exit_kind=kind, reason=reason,
                           delist=False, funding=p["funding"], stop_frac=p["stop_frac"]))

    for j in range(n):
        # ---- OPEN(j)
        if pos is not None:
            ex = pos_on_open(pos, t[j], o[j])
            if ex:
                close_trade(pos, ex)
                pos = None
        keep = []
        for od in orders:
            if pos is not None:
                break           # a position is open: remaining orders are dropped
            f = order_on_open(od, t[j], o[j], bar_ns)
            if f is None:
                keep.append(od)
            elif f != "expired":
                stop, tgt, why = levels(od, f[0])
                if not why:
                    pos = open_position(od, f, stop, tgt, t[j])
        orders = [] if pos is not None else keep
        # ---- CLOSE(j)
        bar = dict(time=int(t[j]), open=o[j], high=h[j], low=lo[j], close=c[j], trades=int(tr[j]))
        if pos is not None:
            ex = pos_on_close(pos, bar, bool(xl[j] if pos["side"] == 1 else xs[j]), bar_ns)
            if ex:
                close_trade(pos, ex)
                pos = None
        keep = []
        for od in orders:
            if pos is not None:
                break
            f = order_on_close(od, bar, bar_ns)
            if f is None:
                keep.append(od)
            elif f != "expired":
                stop, tgt, why = levels(od, f[0])
                if not why:
                    pos = open_position(od, f, stop, tgt, t[j])
                    ex = pos_on_close(pos, bar, bool(xl[j] if pos["side"] == 1 else xs[j]),
                                      bar_ns)
                    if ex:
                        close_trade(pos, ex)
                        pos = None
        orders = [] if pos is not None else keep
        if j + 1 < n and tr[j] > 0:
            for r in by_idx.get(j, []):
                orders.append(new_order(r, t[j], bar_ns, "X"))
    return trades
