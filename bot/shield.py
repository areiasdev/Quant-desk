"""SHIELD: approves or blocks every paper order and halts the whole bot.

Blocks (the order is not placed; logged to shield.jsonl with the reason):
  * no stop attached, or the stop is at/through the entry;
  * size below the symbol's minimum notional (ESTIMATE until exchangeInfo is fetched), or below
    its minimum quantity;
  * total open notional after the trade > max_leverage x equity (3x);
  * already max_open_positions (3) open;
  * a position in the same symbol is already open (duplicate).
HARD HALT (writes the HALT file; nothing is opened or closed until the owner deletes it):
  * the UTC day's paper P&L <= -3% of the equity at the start of the day;
  * 3 losing trades in a row;
  * stale feed: the latest closed 1h bar is older than 2 bars, or 3 fetch failures in a row;
  * equity/position mismatch between state.json and trades.jsonl;
  * any unhandled exception in the decision code.
While halted, open paper positions keep their stop: it is a resting stop order, so on reset
the bot replays the bars it missed and books any stop that was hit at the stop price.
Deleting HALT resets the loss streak, the fetch-failure count and the day's starting equity.
"""
from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

from .config import Shield as Limits
from .journal import Journal
from .market import H, iso

log = logging.getLogger("bot")


class Shield:
    def __init__(self, limits: Limits, halt_file: Path, journal: Journal, state: dict):
        self.lim = limits
        self.halt_file = Path(halt_file)
        self.j = journal
        self.s = state.setdefault("shield", {})
        self.s.setdefault("consecutive_losses", 0)
        self.s.setdefault("fetch_failures", 0)
        self.s.setdefault("day", None)
        self.s.setdefault("day_start_equity", None)
        self.s.setdefault("halted_reason", None)

    # ---- halt file ---------------------------------------------------------------------
    def halted(self) -> bool:
        return self.halt_file.exists()

    def halt_reason(self) -> str | None:
        if not self.halted():
            return None
        try:
            return self.halt_file.read_text(encoding="utf-8").splitlines()[0]
        except Exception:
            return "HALT file present"

    def halt(self, now_ms: int, reason: str, **details) -> None:
        first = not self.halted()
        if first:
            self.halt_file.parent.mkdir(parents=True, exist_ok=True)
            self.halt_file.write_text(
                f"HALT: {reason}\n"
                f"time (UTC): {iso(now_ms)}\n"
                "The paper bot stopped itself. Read logs/shield.jsonl, then delete this file to "
                "resume (apague este ficheiro para retomar).\n", encoding="utf-8")
        self.s["halted_reason"] = reason
        self.j.append("shield", dict(time=iso(now_ms), type="HALT", reason=reason,
                                     already_halted=not first, **details), fsync=True)
        log.error("HALT: %s", reason)

    def check_reset(self, now_ms: int, mark_equity: float) -> bool:
        """True (once) when the owner deleted the HALT file after a halt."""
        if self.s.get("halted_reason") and not self.halted():
            self.j.append("shield", dict(time=iso(now_ms), type="RESET",
                                         reason="HALT file deleted by the owner",
                                         previous_halt=self.s["halted_reason"],
                                         consecutive_losses_before=self.s["consecutive_losses"],
                                         fetch_failures_before=self.s["fetch_failures"],
                                         new_day_start_equity=mark_equity), fsync=True)
            self.s.update(halted_reason=None, consecutive_losses=0, fetch_failures=0,
                          day_start_equity=mark_equity,
                          day=dt.datetime.fromtimestamp(now_ms / 1000, dt.timezone.utc)
                          .date().isoformat())
            log.warning("HALT reset by the owner; counters cleared")
            return True
        return False

    # ---- order approval ------------------------------------------------------------------
    def approve(self, now_ms: int, *, symbol: str, side: int, entry: float, stop: float | None,
                qty: float, notional: float, equity: float, open_notional: float,
                open_symbols: list[str], min_notional: float, min_notional_source: str,
                min_qty: float = 0.0) -> tuple[bool, str]:
        rule, reason = "", ""
        if self.halted():
            rule, reason = "halted", "the bot is halted (HALT file present)"
        elif stop is None or not side * (entry - stop) > 0:
            rule, reason = "no_stop", "no valid stop attached to the order"
        elif symbol in open_symbols:
            rule, reason = "duplicate", f"duplicate: a {symbol} position is already open"
        elif len(open_symbols) >= self.lim.max_open_positions:
            rule, reason = "max_positions", (f"max open positions: {len(open_symbols)} open, limit "
                      f"{self.lim.max_open_positions}")
        elif notional <= 0 or notional < min_notional - 1e-9:
            rule, reason = "min_notional", (f"size {notional:.2f} USDT below minimum notional {min_notional:.2f} USDT "
                      f"({min_notional_source})")
        elif min_qty and qty < min_qty - 1e-12:
            rule, reason = "min_qty", f"quantity {qty} below minimum quantity {min_qty}"
        elif equity <= 0 or (open_notional + notional) / equity > self.lim.max_leverage + 1e-9:
            lev = (open_notional + notional) / equity if equity > 0 else float("inf")
            rule, reason = "leverage", f"leverage {lev:.2f}x above limit {self.lim.max_leverage:.1f}x"
        if reason:
            self.j.append("shield", dict(time=iso(now_ms), type="BLOCK", rule=rule, symbol=symbol,
                                         side=side, reason=reason, entry=entry, stop=stop,
                                         qty=qty, notional=notional, equity=equity,
                                         open_notional=open_notional,
                                         open_positions=len(open_symbols)))
            return False, reason
        return True, "approved"

    # ---- halt rules ------------------------------------------------------------------------
    def on_trade_closed(self, now_ms: int, pnl: float, symbol: str) -> None:
        self.s["consecutive_losses"] = self.s["consecutive_losses"] + 1 if pnl <= 0 else 0
        if self.s["consecutive_losses"] >= self.lim.consecutive_losses_halt:
            self.halt(now_ms, f"{self.s['consecutive_losses']} losing trades in a row "
                              f"(limit {self.lim.consecutive_losses_halt})", last_symbol=symbol,
                      rule="consecutive_losses")

    def roll_day(self, now_ms: int, prev_mark_equity: float) -> None:
        day = dt.datetime.fromtimestamp(now_ms / 1000, dt.timezone.utc).date().isoformat()
        if self.s["day"] != day:
            self.s["day"] = day
            self.s["day_start_equity"] = prev_mark_equity

    def check_daily(self, now_ms: int, mark_equity: float) -> None:
        d0 = self.s.get("day_start_equity")
        if not d0:
            return
        pnl = mark_equity / d0 - 1
        if pnl <= -self.lim.daily_loss_halt + 1e-12:
            self.halt(now_ms, f"daily loss {pnl:.2%} reached the limit -{self.lim.daily_loss_halt:.0%}"
                              f" (UTC day {self.s['day']})", day_start_equity=d0, rule="daily_loss",
                      equity=mark_equity)

    def on_fetch(self, now_ms: int, ok: bool, what: str = "") -> None:
        if ok:
            self.s["fetch_failures"] = 0
            return
        self.s["fetch_failures"] += 1
        self.j.append("shield", dict(time=iso(now_ms), type="FETCH_FAILURE", reason=what,
                                     consecutive=self.s["fetch_failures"]))
        if self.s["fetch_failures"] >= self.lim.fetch_failures_halt:
            self.halt(now_ms, f"stale feed: {self.s['fetch_failures']} fetch failures in a row "
                              f"(last: {what})", rule="fetch_failures")

    def check_stale(self, now_ms: int, last_close: dict[str, int | None]) -> bool:
        limit = self.lim.stale_bars * H
        bad = {s: iso(t) for s, t in last_close.items() if t is None or now_ms - t > limit}
        if bad:
            self.halt(now_ms, f"stale feed: latest closed 1h bar older than "
                              f"{self.lim.stale_bars} bars for {', '.join(sorted(bad))}",
                      last_bar_close=bad, rule="stale_feed")
            return True
        return False
