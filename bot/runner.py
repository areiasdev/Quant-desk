"""The paper bot: one hourly cycle = fetch -> Shield feed checks -> decisions -> save.

Timeline of a normal 4h decision (S16): the 4h bar 08:00-12:00 closes at 12:00. The bot wakes at
12:00 + 30 s + jitter, fetches the closed 1h bars and the bar that just started, rebuilds LAB's
4h bars, and processes, in time order across all symbols:
  CLOSE(08:00 bar)  stops/targets touched inside the bar, close-exit signals, new signals;
  OPEN(12:00 bar)   pending exits at the 12:00 open, then new entries at the 12:00 open
                    (sized on realised equity, approved by Shield, stop attached).
The 12:00 open is the fill price for "market at the next open" (as in LAB). It is only used
if the bot runs within `max_fill_delay_minutes` of that open; otherwise the entry is logged as
missed and a pending exit is executed at the latest price ("late exit"). Stops and resting
limit orders are exchange-side orders: when the bot comes back after downtime or a HALT, it
replays the bars it missed and books any stop hit at the stop price.
"""
from __future__ import annotations

import logging
import random
import traceback

import numpy as np
import pandas as pd

from . import fills
from .config import Config
from .datasource import Clock, DataSource, FetchError, warn_if_api_keys
from .journal import Journal, load_state, save_state
from .market import H, Feed, floor_h, iso, tf_frame
from .shield import Shield
from .sizing import round_stop, size
from .strategy import Strategy
from .universe import Classifier, compute_universe, month_id, month_start_ms, parse_filters, \
    tradeable_perps

log = logging.getLogger("bot")
NS_MS = 1_000_000
STATE_VERSION = 1


def next_wake(now_ms: int, after_s: float, jitter_s: float, rng: random.Random) -> int:
    """The next hour's wake time: hour boundary + after_s + U(0, jitter_s). Only if `now` is
    still before this hour's earliest wake point (hh:00:00 .. hh:00:after_s) is it this hour."""
    base = floor_h(now_ms)
    if now_ms >= base + int(after_s * 1000):
        base += H
    return base + int(after_s * 1000) + int(rng.uniform(0, jitter_s) * 1000)


class Bot:
    def __init__(self, cfg: Config, source: DataSource, clock: Clock | None = None, *,
                 rng: random.Random | None = None, classifier: Classifier | None = None):
        if cfg.mode != "paper":
            from .live import LiveBroker
            LiveBroker(cfg)  # raises NotImplementedError: real money needs the owner's go-live
        self.cfg = cfg
        self.src = source
        self.clock = clock or Clock()
        self.rng = rng or random.Random()
        self.j = Journal(cfg.logs_dir)
        self.strategy = Strategy(cfg.setup_id, cfg.params)
        self.tf_ns = self.strategy.tf_hours * H * NS_MS
        self.tf_ms = self.strategy.tf_hours * H
        self.classifier = classifier or Classifier()
        self.feeds: dict[str, Feed] = {}
        self._info = None
        st = load_state(cfg.state_file)
        self.fresh = st is None
        self.state = st or self._new_state()
        self.shield = Shield(cfg.shield, cfg.halt_file, self.j, self.state)
        self.api_key_vars = warn_if_api_keys()
        now = self.clock.now_ms()
        self.j.append("system", dict(
            time=iso(now), type="START", fresh_state=self.fresh, mode=cfg.mode,
            data_source=source.name, setup=cfg.setup_id, params=self.strategy.params,
            equity_realized=self.state["equity_realized"],
            open_positions=sorted(self.state["positions"]),
            api_key_env_vars_ignored=self.api_key_vars,
            config=str(cfg.source_path) if cfg.source_path else None))
        if not self.fresh and (self.state.get("setup") != cfg.setup_id or
                               self.state.get("params") != self.strategy.params):
            self.j.append("system", dict(time=iso(now), type="WARNING",
                                         reason="setup or params changed since the state was "
                                                "created; open positions keep their stops",
                                         state_setup=self.state.get("setup"),
                                         state_params=self.state.get("params")))
            self.state["setup"], self.state["params"] = cfg.setup_id, self.strategy.params
        self.flush_outbox()

    # ------------------------------------------------------------------ state
    def _new_state(self) -> dict:
        return dict(version=STATE_VERSION, setup=self.cfg.setup_id, params=self.strategy.params,
                    created=iso(self.clock.now_ms()),
                    starting_equity_usdt=self.cfg.starting_equity_usdt,
                    equity_realized=self.cfg.starting_equity_usdt, positions={}, orders=[],
                    symbols={}, universe=None, universe_history={}, filters={},
                    filters_day=None, last_mark_equity=self.cfg.starting_equity_usdt,
                    last_cycle=None, outbox=[], trade_seq=0)

    def save(self) -> None:
        save_state(self.cfg.state_file, self.state)

    def flush_outbox(self) -> None:
        """Exactly-once trades.jsonl: records are in state.json BEFORE they are appended."""
        if not self.state["outbox"]:
            return
        self.save()
        have = {r.get("rid") for r in self.j.read("trades")}
        for rec in self.state["outbox"]:
            if rec["rid"] not in have:
                self.j.append("trades", rec, fsync=True)
        self.state["outbox"] = []
        self.save()

    def consistent(self, now: int) -> bool:
        recs = self.j.read("trades")
        eq = self.state["starting_equity_usdt"]
        opened, closed = set(), set()
        for r in recs:
            if r.get("event") == "open":
                opened.add(r["id"])
            elif r.get("event") == "close":
                closed.add(r["id"])
                eq += r["pnl"]
        problems = []
        if abs(eq - self.state["equity_realized"]) > 1e-6 * max(1.0, abs(eq)):
            problems.append(f"realised equity {self.state['equity_realized']:.6f} in state.json != "
                            f"{eq:.6f} from trades.jsonl")
        if (opened - closed) != set(self.state["positions"]):
            problems.append(f"open positions in state.json {sorted(self.state['positions'])} != "
                            f"trades.jsonl {sorted(opened - closed)}")
        on = 0.0
        for pid, p in self.state["positions"].items():
            on += p["notional"]
            if p["qty"] <= 0 or abs(p["qty"] * p["entry"] - p["notional"]) > 1e-6 * p["notional"]:
                problems.append(f"position {pid}: qty x entry != notional")
        e = self.state["equity_realized"]
        if not np.isfinite(e) or e <= 0:
            problems.append(f"realised equity not positive: {e}")
        elif on / e > self.cfg.shield.max_leverage + 1e-6:
            problems.append(f"open notional {on:.2f} > {self.cfg.shield.max_leverage}x equity")
        if problems:
            self.shield.halt(now, "equity/position mismatch: " + "; ".join(problems),
                             rule="mismatch")
            return False
        return True

    # ------------------------------------------------------------------ helpers
    def active_symbols(self) -> list[str]:
        s = set(self.state["universe"]["symbols"]) if self.state["universe"] else set()
        s |= {p["symbol"] for p in self.state["positions"].values()}
        s |= {o["symbol"] for o in self.state["orders"]}
        return sorted(s)

    def min_notional(self, sym: str) -> tuple[float, str]:
        f = self.state["filters"].get(sym, {})
        if "min_notional" in f:
            return f["min_notional"], f"exchangeInfo {self.state.get('filters_fetched_at')}"
        return self.cfg.min_notional_est(sym), "ESTIMATE"

    def mark_equity(self) -> float:
        eq = self.state["equity_realized"]
        for p in self.state["positions"].values():
            f = self.feeds.get(p["symbol"])
            px = f.last_price() if f else None
            if px is None:
                px = p.get("last_price", p["entry"])
            p["last_price"] = px
            net = fills.net_return(p, px, "taker", p["slip_bp"], self.cfg.taker_fee,
                                   self.cfg.maker_fee)
            p["unrealized"] = p["notional"] * net
            eq += p["unrealized"]
        return eq

    # ------------------------------------------------------------------ fetch
    def fetch(self, now: int) -> None:
        day = iso(floor_h(now))[:10]
        info = None
        if self.state.get("filters_day") != day:
            info = self.src.exchange_info()
            self._apply_filters(now, info, day)
        mon = month_id(now)
        if not self.state["universe"] or self.state["universe"]["month"] != mon:
            if info is None:
                info = self.src.exchange_info()
            u = compute_universe(_CachedInfo(self.src, info), month_start_ms(now),
                                 self.classifier, size=self.cfg.universe_size,
                                 candidates=self.cfg.universe_candidates,
                                 trailing_days=self.cfg.universe_trailing_days)
            u["computed_at"] = iso(now)
            old = self.state["universe"]["symbols"] if self.state["universe"] else []
            self.state["universe"] = u
            self.state["universe_history"][mon] = u["symbols"]
            self.j.append("system", dict(time=iso(now), type="UNIVERSE", month=mon,
                                         symbols=u["symbols"],
                                         added=sorted(set(u["symbols"]) - set(old)),
                                         removed=sorted(set(old) - set(u["symbols"])),
                                         trailing30d_quote_volume=u["trailing30d_quote_volume"],
                                         excluded_non_crypto=u["excluded_non_crypto_in_top"],
                                         perps_screened=u["perps_screened"]))
        active = self.active_symbols()
        for s in list(self.feeds):
            if s not in active:
                del self.feeds[s]
        for s in list(self.state["symbols"]):
            if s not in active:
                del self.state["symbols"][s]
        prem = {r["symbol"]: r for r in self.src.premium_index() if "symbol" in r}
        for s in active:
            f = self.feeds.get(s)
            if f is None:
                f = self.feeds[s] = Feed(s, self.cfg.history_1h_bars)
            nft = prem.get(s, {}).get("nextFundingTime")
            f.next_funding_ms = int(nft) if nft not in (None, "", 0, "0") else None
            f.update(self.src, now)

    def _apply_filters(self, now: int, info: dict, day: str) -> None:
        filt = parse_filters(info)
        self.state["filters_day"] = day
        self._info = info
        if not filt:
            self.j.append("system", dict(time=iso(now), type="EXCHANGE_INFO",
                                         result="no filters in exchangeInfo; ESTIMATE values "
                                                "kept", min_notional_ESTIMATE=
                                         self.cfg.min_notional_estimate))
            return
        old = self.state["filters"]
        self.state["filters"] = filt
        self.state["filters_fetched_at"] = iso(now)
        watch = set(self.active_symbols()) | set(self.cfg.min_notional_estimate) - {"default"}
        rows = {s: dict(ESTIMATE_min_notional=self.cfg.min_notional_est(s),
                        fetched=filt.get(s), previous=old.get(s)) for s in sorted(watch)}
        self.j.append("system", dict(time=iso(now), type="EXCHANGE_INFO",
                                     result=f"filters fetched for {len(filt)} symbols; they "
                                            "replace the ESTIMATE values", symbols=rows))

    # ------------------------------------------------------------------ the cycle
    def cycle(self) -> str:
        now = self.clock.now_ms()
        self.state["last_cycle"] = iso(now)
        mark0 = self.state.get("last_mark_equity") or self.state["equity_realized"]
        if self.shield.halted():
            if not self.state["shield"].get("halted_reason"):
                self.state["shield"]["halted_reason"] = self.shield.halt_reason()
            self._equity_log(now, mark0, status="halted")
            self.save()
            return "halted"
        self.shield.check_reset(now, mark0)
        if not self.consistent(now):
            self.save()
            return "halted"
        try:
            self.fetch(now)
        except FetchError as e:
            self.shield.on_fetch(now, False, str(e)[:300])
            self._equity_log(now, mark0, status="fetch_failed")
            self.save()
            return "halted" if self.shield.halted() else "fetch_failed"
        except Exception as e:  # a bug in the data code is treated like one in decisions
            self.shield.halt(now, f"unhandled exception while fetching: {type(e).__name__}: {e}",
                             rule="exception",
                             traceback=traceback.format_exc()[-3000:])
            self.save()
            return "halted"
        self.shield.on_fetch(now, True)
        last = {s: self.feeds[s].last_closed_close_ms() for s in self.active_symbols()}
        if self.shield.check_stale(now, last):
            self.save()
            return "halted"
        try:
            self.shield.roll_day(now, mark0)
            self.decide(now)
            if not self.shield.halted():
                self._book_open_funding(now)
            mark = self.mark_equity()
            self.state["last_mark_equity"] = mark
            if not self.shield.halted():
                self.shield.check_daily(now, mark)
        except Exception as e:
            self.flush_outbox()
            self.shield.halt(now, f"unhandled exception in decision code: {type(e).__name__}: "
                                  f"{e}", traceback=traceback.format_exc()[-3000:], rule="exception")
            self.save()
            return "halted"
        self._equity_log(now, mark, status="halted" if self.shield.halted() else "ok")
        self.flush_outbox()
        self.save()
        return "halted" if self.shield.halted() else "ok"

    def _equity_log(self, now, mark, status):
        self.j.append("equity", dict(
            time=iso(now), status=status, equity_mark_usdt=mark,
            equity_realized_usdt=self.state["equity_realized"],
            equity_mark_eur_ESTIMATE=mark / self.cfg.eur_usd_estimate,
            open_positions=len(self.state["positions"]),
            open_notional_usdt=sum(p["notional"] for p in self.state["positions"].values()),
            day=self.state["shield"].get("day"),
            day_start_equity=self.state["shield"].get("day_start_equity"),
            consecutive_losses=self.state["shield"].get("consecutive_losses"),
            halted=self.shield.halted()))

    # ------------------------------------------------------------------ decisions
    def decide(self, now: int) -> None:
        events = []
        frames = {}
        cur_tf = now * NS_MS // self.tf_ns * self.tf_ns
        for sym in self.active_symbols():
            feed = self.feeds[sym]
            mk = self.state["symbols"].setdefault(sym, {})
            lc = feed.last_closed_close_ms()
            if lc is None:
                continue
            latest_tf = (lc // self.tf_ms) * self.tf_ms * NS_MS - self.tf_ns
            if "last_close" not in mk:          # newly tracked: start at the latest closed bar
                mk["last_close"] = int(latest_tf - self.tf_ns)
                mk["last_open"] = int(latest_tf)
            if latest_tf > mk["last_close"]:
                panel = feed.panel()
                d = tf_frame(panel, self.strategy.tf)
                if len(d):
                    sig, xl, xs = self.strategy.evaluate(sym, panel, d)
                    t = d["open_time"].astype("datetime64[ns, UTC]").astype("int64").to_numpy()
                    frames[sym] = (d, t, sig, xl, xs)
                    for j in np.flatnonzero(t > mk["last_close"]):
                        if t[j] > mk["last_open"]:
                            events.append((int(t[j]), 1, sym, "open", int(j)))
                        events.append((int(t[j]) + self.tf_ns, 0, sym, "close", int(j)))
            if cur_tf > mk["last_open"] and not any(e[2] == sym and e[0] >= cur_tf
                                                     for e in events if e[3] == "open"):
                o = feed.open_at(cur_tf // NS_MS)
                if o is not None:
                    events.append((int(cur_tf), 1, sym, "open_live", o))
        # CLOSE before OPEN at one instant; at OPEN all exits before any entry
        expanded = []
        for ev in events:
            if ev[1] == 1:
                expanded.append((ev[0], 1, ev[2], ev[3] + ":exit", ev[4]))
                expanded.append((ev[0], 2, ev[2], ev[3] + ":entry", ev[4]))
            else:
                expanded.append(ev)
        expanded.sort(key=lambda e: (e[0], e[1], e[2]))
        for T, _, sym, kind, arg in expanded:
            if self.shield.halted():
                return
            if kind.startswith("open"):
                if kind.startswith("open_live"):
                    o = float(arg)
                else:
                    o = float(frames[sym][0]["open"].iat[arg])
                timely = now - T // NS_MS <= self.cfg.max_fill_delay_minutes * 60_000
                if kind.endswith(":exit"):
                    self._on_open_exits(now, sym, T, o, timely)
                else:
                    self._on_open_entries(now, sym, T, o, timely)
                    self.state["symbols"][sym]["last_open"] = int(T)
            else:
                self._on_close(now, sym, frames[sym], arg)
                self.state["symbols"][sym]["last_close"] = int(frames[sym][1][arg])
        if not self.shield.halted():
            self._late_exits(now)

    def _positions(self, sym):
        return [p for p in self.state["positions"].values() if p["symbol"] == sym]

    def _on_open_exits(self, now, sym, T, o, timely):
        for p in self._positions(sym):
            ex = fills.pos_on_open(p, T, o)
            if ex is None:
                continue
            if ex[2] in ("signal", "time") and not timely:
                continue          # the market exit was not sent in time: late exit below
            self._close(now, p, ex)

    def _on_open_entries(self, now, sym, T, o, timely):
        keep = []
        for od in [x for x in self.state["orders"] if x["symbol"] == sym]:
            if od["first_bar"] > T:
                keep.append(od)
                continue
            if not od["placed"] and not timely:
                self._decision(now, sym, od["sig_time"], "entry", "missed",
                               f"entry open {iso(T // NS_MS)} passed "
                               f"{(now - T // NS_MS) / 60000:.0f} min before the bot ran "
                               f"(limit {self.cfg.max_fill_delay_minutes:.0f} min)")
                continue
            f = fills.order_on_open(od, T, o, self.tf_ns)
            if f is None:
                od["placed"] = True
                keep.append(od)
            elif f == "expired":
                self._decision(now, sym, od["sig_time"], "entry", "expired",
                               "limit order expired unfilled")
            else:
                self._try_open(now, od, f, T)
        others = [x for x in self.state["orders"] if x["symbol"] != sym]
        self.state["orders"] = others + ([] if self._positions(sym) else keep)

    def _on_close(self, now, sym, frame, j):
        d, t, sig, xl, xs = frame
        r = d.iloc[j]
        bar = dict(time=int(t[j]), open=float(r["open"]), high=float(r["high"]),
                   low=float(r["low"]), close=float(r["close"]), trades=int(r["trades"]))
        for p in self._positions(sym):
            ex = fills.pos_on_close(p, bar, bool(xl[j] if p["side"] == 1 else xs[j]), self.tf_ns)
            if p["pending"] and "pending_at" not in p:
                p["pending_at"] = int(t[j]) + self.tf_ns
            if ex:
                self._close(now, p, ex)
        keep = []
        for od in [x for x in self.state["orders"] if x["symbol"] == sym]:
            if od["kind"] != "limit" or not od["placed"] or self._positions(sym):
                keep.append(od)
                continue
            f = fills.order_on_close(od, bar, self.tf_ns)
            if f is None:
                keep.append(od)
            elif f == "expired":
                self._decision(now, sym, od["sig_time"], "entry", "expired",
                               "limit order expired unfilled")
            else:
                p = self._try_open(now, od, f, int(t[j]))
                if p is not None:
                    ex = fills.pos_on_close(p, bar, bool(xl[j] if p["side"] == 1 else xs[j]),
                                            self.tf_ns)
                    if ex:
                        self._close(now, p, ex)
        others = [x for x in self.state["orders"] if x["symbol"] != sym]
        self.state["orders"] = others + ([] if self._positions(sym) else keep)
        # ---- signal evaluation at this close
        rows = sig[sig["idx"] == j].to_dict("records")
        inputs, why = self.strategy.explain(d, j, bool(rows))
        close_ms = (int(t[j]) + self.tf_ns) // NS_MS
        uni = self.state["universe_history"].get(month_id(close_ms))
        in_uni = uni is not None and sym in uni
        if not rows:
            if in_uni:
                self._decision(now, sym, int(t[j]), "signal", "no_signal", why, inputs)
            return
        for row in rows:
            side = int(row["side"])
            if bar["trades"] == 0:
                self._decision(now, sym, int(t[j]), "signal", "skipped",
                               "signal bar had zero trades", inputs)
            elif uni is None:
                self._decision(now, sym, int(t[j]), "signal", "skipped",
                               f"universe for {month_id(close_ms)} unknown (bot was off at the "
                               "month start)", inputs)
            elif not in_uni:
                self._decision(now, sym, int(t[j]), "signal", "skipped",
                               f"{sym} not in the {month_id(close_ms)} top-10 universe", inputs)
            elif any(o["symbol"] == sym for o in self.state["orders"]):
                self._decision(now, sym, int(t[j]), "signal", "skipped",
                               "an entry order for this symbol is already waiting", inputs)
            else:
                od = fills.new_order(row, int(t[j]), self.tf_ns, sym)
                self.state["orders"].append(od)
                self._decision(now, sym, int(t[j]), "signal", "queued",
                               f"{why}; {'long' if side == 1 else 'short'} {od['kind']} order "
                               f"queued for the open at {iso(od['first_bar'] // NS_MS)}", inputs)

    def _late_exits(self, now):
        for p in list(self.state["positions"].values()):
            mk = self.state["symbols"].get(p["symbol"], {})
            due = None
            if p["pending"] and p.get("pending_at") and mk.get("last_open", 0) >= p["pending_at"]:
                due = "signal_late"
            elif p["tmax_time"] is not None and mk.get("last_open", 0) >= p["tmax_time"]:
                due = "time_late"
            if due:
                f = self.feeds.get(p["symbol"])
                px = f.last_price() if f else None
                if px is None:
                    continue
                self._close(now, p, (px, "taker", due, now * NS_MS))
                if self.shield.halted():
                    return

    # ------------------------------------------------------------------ open / close
    def _try_open(self, now, od, fill, bar_time):
        sym, side = od["symbol"], od["side"]
        stop, tgt, problem = fills.levels(od, fill[0])
        if problem:
            self._decision(now, sym, od["sig_time"], "entry", "skipped", problem)
            return None
        flt = self.state["filters"].get(sym, {})
        stop = round_stop(stop, side, flt.get("tick_size", 0.0))
        pos = fills.open_position(od, fill, stop, tgt, bar_time)
        eq = self.state["equity_realized"]
        on = sum(p["notional"] for p in self.state["positions"].values())
        sz = size(eq, on, pos["stop_frac"], pos["entry"], risk=self.cfg.risk_per_trade,
                  max_leverage=self.cfg.shield.max_leverage, step=flt.get("step_size", 0.0))
        mn, mn_src = self.min_notional(sym)
        ok, why = self.shield.approve(
            now, symbol=sym, side=side, entry=pos["entry"], stop=pos["stop"], qty=sz["qty"],
            notional=sz["notional"], equity=eq, open_notional=on,
            open_symbols=[p["symbol"] for p in self.state["positions"].values()],
            min_notional=mn, min_notional_source=mn_src, min_qty=flt.get("min_qty", 0.0))
        if not ok:
            self._decision(now, sym, od["sig_time"], "entry", "blocked", f"Shield: {why}",
                           dict(entry=pos["entry"], stop=pos["stop"], **sz))
            return None
        self.state["trade_seq"] += 1
        pid = f"{sym}-{iso(pos['entry_time'] // NS_MS)[:16].replace(':', '')}"
        is_meme = self.classifier.is_meme(sym)
        pos.update(id=pid, qty=sz["qty"], notional=sz["notional"], want_notional=sz["want"],
                   capped=sz["capped"], equity_before=eq,
                   slip_bp=self.cfg.slip_bp(sym, is_meme), is_meme=is_meme, opened_at=iso(now),
                   min_notional=mn, min_notional_source=mn_src)
        self.state["positions"][pid] = pos
        rec = dict(rid=f"{pid}:open", event="open", id=pid, symbol=sym, side=side,
                   setup=self.cfg.setup_id, signal_bar=iso(od["sig_time"] // NS_MS),
                   entry_time=iso(pos["entry_time"] // NS_MS), entry=pos["entry"],
                   entry_kind=pos["entry_kind"], stop=pos["stop"], target=pos["target"],
                   qty=pos["qty"], notional=pos["notional"], stop_frac=pos["stop_frac"],
                   risk_usdt=pos["notional"] * pos["stop_frac"], equity_before=eq,
                   leverage_after=(on + pos["notional"]) / eq, slip_bp=pos["slip_bp"],
                   min_notional=mn, min_notional_source=mn_src, logged_at=iso(now),
                   reason="entry: " + _entry_reason(self.cfg.setup_id))
        self.state["outbox"].append(rec)
        self._decision(now, sym, od["sig_time"], "entry", "filled",
                       f"{'long' if side == 1 else 'short'} {pos['qty']:.6g} @ {pos['entry']:.6g},"
                       f" stop {pos['stop']:.6g} attached, notional {pos['notional']:.2f} USDT")
        return pos

    def _close(self, now, p, ex):
        price, kind, reason, xt = ex
        feed = self.feeds.get(p["symbol"])
        if feed is not None:
            ft, fr = feed.settlements()
            fills.book_funding(p, ft, fr, min(int(xt), now * NS_MS),
                               lambda T: feed.price_for_funding(T // NS_MS))
        net = fills.net_return(p, price, kind, p["slip_bp"], self.cfg.taker_fee,
                               self.cfg.maker_fee)
        pnl = p["notional"] * net
        self.state["equity_realized"] += pnl
        del self.state["positions"][p["id"]]
        rec = dict(rid=f"{p['id']}:close", event="close", id=p["id"], symbol=p["symbol"],
                   side=p["side"], setup=self.cfg.setup_id,
                   signal_bar=iso(p["sig_time"] // NS_MS),
                   entry_time=iso(p["entry_time"] // NS_MS), entry=p["entry"],
                   entry_kind=p["entry_kind"], exit_time=iso(int(xt) // NS_MS), exit=price,
                   exit_kind=kind, reason=reason, exit_reason=_exit_reason(reason),
                   late=reason.endswith("_late"), stop=p["stop"], target=p["target"],
                   qty=p["qty"], notional=p["notional"], stop_frac=p["stop_frac"],
                   funding=p["funding"], funding_usdt=p["funding"] * p["notional"],
                   funding_settlements=len(p["funding_booked"]), net=net, R=net / p["stop_frac"],
                   pnl=pnl, equity_after=self.state["equity_realized"], logged_at=iso(now))
        self.state["outbox"].append(rec)
        self.shield.on_trade_closed(now, pnl, p["symbol"])

    def _book_open_funding(self, now):
        for p in self.state["positions"].values():
            feed = self.feeds.get(p["symbol"])
            if feed is None:
                continue
            ft, fr = feed.settlements()
            fills.book_funding(p, ft, fr, now * NS_MS,
                               lambda T, f=feed: f.price_for_funding(T // NS_MS))
            p["funding_usdt"] = p["funding"] * p["notional"]

    def _decision(self, now, sym, bar_ns, stage, result, reason, inputs=None):
        self.j.append("decisions", dict(
            time=iso(now), symbol=sym, setup=self.cfg.setup_id, stage=stage, result=result,
            bar_open=iso(bar_ns // NS_MS), bar_close=iso((bar_ns + self.tf_ns) // NS_MS)
            if stage == "signal" else None, reason=reason, inputs=inputs))

    # ------------------------------------------------------------------ main loop
    def run_forever(self, until_ms: int | None = None, after_cycle=None) -> None:
        log.info("paper bot started (%s, setup %s); PAPER ONLY", self.src.name, self.cfg.setup_id)
        while True:
            status = self.cycle()
            now = self.clock.now_ms()
            log.info("cycle %s: %s | equity %.2f USDT | open %d", iso(now), status,
                     self.state.get("last_mark_equity") or 0, len(self.state["positions"]))
            if status == "halted":
                log.error("HALTED: %s -> read logs/shield.jsonl, then delete the HALT file",
                          self.shield.halt_reason())
            if after_cycle is not None:
                after_cycle(self, status)
            if status == "fetch_failed":
                wake = now + int(self.cfg.retry_cycle_seconds * 1000)
            else:
                wake = next_wake(now, self.cfg.wake_after_close_seconds, self.cfg.jitter_seconds,
                                 self.rng)
            if until_ms is not None and wake > until_ms:
                return
            while self.clock.now_ms() < wake:   # short naps: survives PC sleep/clock jumps
                self.clock.sleep(min(60.0, (wake - self.clock.now_ms()) / 1000))


class _CachedInfo:
    """Wraps a source so compute_universe reuses this cycle's exchangeInfo."""

    def __init__(self, src, info):
        self._s, self._i = src, info

    def exchange_info(self):
        return self._i

    def __getattr__(self, k):
        return getattr(self._s, k)


def _entry_reason(setup_id):
    if setup_id == "S16":
        return "4h close > SMA50 > SMA200 and settled funding F8 < 0 (first signal of the leg)"
    return f"{setup_id} signal"


def _exit_reason(reason):
    return {"stop": "stop hit inside the bar (filled at the stop price)",
            "stop_gap": "bar opened through the stop (filled at that open)",
            "target": "target hit", "signal": "exit signal at a close -> sold at the next open "
                                               "(S16: 4h close < SMA50, or F8 > +0.01%)",
            "time": "time stop", "signal_late": "exit signal, sold late (bot was not running at"
                                                " the open)",
            "time_late": "time stop, executed late"}.get(reason, reason)
