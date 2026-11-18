"""Position size - LAB's portfolio rule (lab.engine.portfolio), plus exchange rounding.

notional = risk x equity / stop distance (fraction of entry), capped so that total open
notional stays <= max_leverage x equity (LAB: the trade is cut to the room left, skipped if no
room). Equity = realised (closed-trade) paper equity, as in LAB. The quantity is rounded DOWN
to the lot step once exchangeInfo has been fetched (never rounds the risk up); with ESTIMATE
filters (step unknown) the quantity is not rounded, which is what LAB assumes.
"""
from __future__ import annotations

import math


def round_down(x: float, step: float) -> float:
    if not step or step <= 0:
        return x
    return math.floor(x / step + 1e-9) * step


def round_stop(stop: float, side: int, tick: float) -> float:
    """Stop rounded to the tick, away from the entry (long: down, short: up)."""
    if not tick or tick <= 0:
        return stop
    q = stop / tick
    return (math.floor(q + 1e-9) if side == 1 else math.ceil(q - 1e-9)) * tick


def size(equity: float, open_notional: float, stop_frac: float, entry: float, *, risk: float,
         max_leverage: float, step: float = 0.0) -> dict:
    want = risk * equity / stop_frac if stop_frac > 0 else 0.0
    room = max_leverage * equity - open_notional
    nt = min(want, room) if equity > 0 else 0.0
    if nt <= 1e-9:
        return dict(want=want, room=room, qty=0.0, notional=0.0, capped=want > room)
    qty = round_down(nt / entry, step)
    return dict(want=want, room=room, qty=qty, notional=qty * entry, capped=want > room)
