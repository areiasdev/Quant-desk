"""Replay data source: the live REST calls answered from the data.binance.vision archive.

Same method names and the same Binance JSON shapes as RestDataSource, driven by a simulated
clock. No look-ahead: a call at simulated time `now` only sees 1h/1d bars that opened at or
before `now`; the bar still forming is returned with high = low = close = open, volume 0 and
trades 0 (its real values are in the future); funding settlements only once calc_time <= now.

Sources: 1h klines from data/clean/klines_1h (what LAB used) or, for universe candidates that
have no cleaned file, the verified raw zips via radar.clean.load_klines_raw (what RADAR used);
1d klines from data/clean/screen_1d_all_symbols.parquet (RADAR's daily screen); funding from
data/clean/funding. exchangeInfo lists every symbol that traded in the last two days, with NO
filters, so the bot keeps its ESTIMATE minimum notionals in replay (as LAB did).

Test hooks: `freeze_after_ms` (no bar newer than this is served: a stalled feed) and
`fail_from_ms` (every call raises FetchError from this time on: an outage).
"""
from __future__ import annotations

import datetime as dt
import random

import numpy as np
import pandas as pd

from radar.config import CLEAN

from .datasource import DataSource, FetchError
from .market import H

DAY = 24 * H


def _s(x) -> str:
    return repr(float(x))


class SimClock:
    def __init__(self, now_ms: int):
        self.t = int(now_ms)

    def now_ms(self) -> int:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += max(0, int(round(seconds * 1000)))

    def set(self, now_ms: int) -> None:
        self.t = int(now_ms)


def _arrays(df: pd.DataFrame, time_col: str) -> dict:
    t = pd.to_datetime(df[time_col], utc=True).astype("datetime64[ms, UTC]").astype("int64")
    a = {"t": t.to_numpy()}
    for c in ("open", "high", "low", "close", "volume", "quote_volume"):
        a[c] = df[c].to_numpy(float)
    a["trades"] = df["trades"].to_numpy("int64")
    for c in ("taker_buy_volume", "taker_buy_quote_volume"):
        a[c] = df[c].to_numpy(float) if c in df else np.zeros(len(df))
    o = np.argsort(a["t"], kind="stable")
    return {k: v[o] for k, v in a.items()}


class ReplayDataSource(DataSource):
    name = "replay-archive"
    live = False

    def __init__(self, clock: SimClock, *, clean_dir=CLEAN, freeze_after_ms: int | None = None,
                 fail_from_ms: int | None = None):
        self.clock = clock
        self.dir = clean_dir
        self.freeze_after_ms = freeze_after_ms
        self.fail_from_ms = fail_from_ms
        self.calls = 0
        self._h1: dict[str, dict] = {}
        self._raw: dict[tuple, dict] = {}
        self._fund: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        s = pd.read_parquet(clean_dir / "screen_1d_all_symbols.parquet",
                            columns=["symbol", "date", "open", "high", "low", "close", "volume",
                                     "quote_volume", "trades"])
        self._d1 = {sym: _arrays(g, "date") for sym, g in s.groupby("symbol", sort=False)}
        self._first = {sym: int(a["t"][0]) for sym, a in self._d1.items()}

    # ---- helpers ----------------------------------------------------------------------------
    def _gate(self):
        self.calls += 1
        if self.fail_from_ms is not None and self.clock.now_ms() >= self.fail_from_ms:
            raise FetchError("replay: simulated outage (connection refused)")

    def _visible_until(self) -> int:
        now = self.clock.now_ms()
        return now if self.freeze_after_ms is None else min(now, self.freeze_after_ms)

    def _h1_arrays(self, sym: str, start_ms: int, end_ms: int) -> dict | None:
        if sym not in self._h1:
            p = self.dir / "klines_1h" / f"{sym}.parquet"
            self._h1[sym] = _arrays(pd.read_parquet(p), "open_time") if p.exists() else None
        if self._h1[sym] is not None:
            return self._h1[sym]
        from radar.clean import load_klines_raw  # RADAR's loader for the verified raw zips

        a = pd.Timestamp(start_ms, unit="ms").date().replace(day=1)
        b = pd.Timestamp(end_ms, unit="ms").date()
        key = (sym, a, b)
        if key not in self._raw:
            k = load_klines_raw(sym, since=a, until=b)
            if len(k) == 0 or "open" not in k:
                self._raw[key] = None
            else:
                for c in ("open", "high", "low", "close", "volume", "quote_volume",
                          "taker_buy_volume", "taker_buy_quote_volume"):
                    if c in k:
                        k[c] = pd.to_numeric(k[c], errors="coerce")
                k["trades"] = pd.to_numeric(k["trades"]).astype("int64")
                self._raw[key] = _arrays(k, "open_time")
        return self._raw[key]

    @staticmethod
    def _rows(a: dict, i0: int, i1: int, bar_ms: int, now: int) -> list[list]:
        out = []
        for i in range(i0, i1):
            t = int(a["t"][i])
            ct = t + bar_ms - 1
            if ct < now:
                out.append([t, _s(a["open"][i]), _s(a["high"][i]), _s(a["low"][i]),
                            _s(a["close"][i]), _s(a["volume"][i]), ct,
                            _s(a["quote_volume"][i]), int(a["trades"][i]),
                            _s(a["taker_buy_volume"][i]), _s(a["taker_buy_quote_volume"][i]),
                            "0"])
            else:   # still forming: only its open is knowable
                o = _s(a["open"][i])
                out.append([t, o, o, o, o, "0", ct, "0", 0, "0", "0", "0"])
        return out

    # ---- the four endpoints ---------------------------------------------------------------
    def exchange_info(self) -> dict:
        self._gate()
        now = self._visible_until()
        day0 = now // DAY * DAY
        syms = []
        for sym, a in self._d1.items():
            i = np.searchsorted(a["t"], day0, side="left")      # days that closed before now
            lo = np.searchsorted(a["t"], day0 - 2 * DAY, side="left")
            if i > lo and (a["trades"][lo:i] > 0).any():
                syms.append({"symbol": sym, "pair": sym, "contractType": "PERPETUAL",
                             "status": "TRADING", "quoteAsset": "USDT",
                             "onboardDate": self._first[sym], "filters": []})
        return {"timezone": "UTC", "serverTime": self.clock.now_ms(), "symbols": syms,
                "note": "replay: built from the archive, no exchange filters"}

    def klines(self, symbol, interval, start_ms=None, end_ms=None, limit=500):
        self._gate()
        now = self._visible_until()
        limit = min(int(limit), 1500)
        if interval == "1d":
            a, bar = self._d1.get(symbol), DAY
        elif interval == "1h":
            a = self._h1_arrays(symbol, start_ms if start_ms is not None else now - limit * H,
                                end_ms if end_ms is not None else now)
            bar = H
        else:
            raise FetchError(f"replay: interval {interval} not supported")
        if a is None or len(a["t"]) == 0:
            return []
        hi = now if end_ms is None else min(now, int(end_ms))
        i1 = int(np.searchsorted(a["t"], hi, side="right"))
        if start_ms is None:
            i0 = max(0, i1 - limit)
        else:
            i0 = int(np.searchsorted(a["t"], int(start_ms), side="left"))
            i1 = min(i1, i0 + limit)
        return self._rows(a, i0, i1, bar, self.clock.now_ms())

    def _funding(self, sym):
        if sym not in self._fund:
            p = self.dir / "funding" / f"{sym}.parquet"
            if p.exists():
                f = pd.read_parquet(p, columns=["calc_time", "funding_rate"])
                t = f["calc_time"].astype("datetime64[ms, UTC]").astype("int64").to_numpy()
                self._fund[sym] = (t, f["funding_rate"].to_numpy(float))
            else:
                self._fund[sym] = (np.array([], np.int64), np.array([]))
        return self._fund[sym]

    def funding_rate(self, symbol, start_ms=None, end_ms=None, limit=1000):
        self._gate()
        now = self._visible_until()
        t, r = self._funding(symbol)
        hi = now if end_ms is None else min(now, int(end_ms))
        i1 = int(np.searchsorted(t, hi, side="right"))
        i0 = max(0, i1 - limit) if start_ms is None else \
            int(np.searchsorted(t, int(start_ms), side="left"))
        i1 = min(i1, i0 + int(limit))
        return [{"symbol": symbol, "fundingTime": int(t[i]), "fundingRate": repr(float(r[i])),
                 "markPrice": ""} for i in range(i0, i1)]

    def premium_index(self):
        self._gate()
        now = self.clock.now_ms()
        out = []
        if not hasattr(self, "_fund_syms"):
            self._fund_syms = sorted(p.name[:-8] for p in (self.dir / "funding").glob("*.parquet"))
        for sym in self._fund_syms:
            t, _ = self._funding(sym)
            i = int(np.searchsorted(t, now, side="right"))
            out.append({"symbol": sym, "markPrice": "", "lastFundingRate": "",
                        "nextFundingTime": int(t[i]) if i < len(t) else 0, "time": now})
        return out


def run_replay(cfg, start: str | pd.Timestamp, end: str | pd.Timestamp, *, seed: int = 1,
               auto_reset_halt: bool = False, after_cycle=None, source=None, clock=None):
    """Run the real bot loop (run_forever, next_wake, jitter) on archive data from `start`
    to `end`. With auto_reset_halt the HALT file is deleted after the cycle that wrote it,
    i.e. the owner resets within the hour. Returns (bot, resets)."""
    from .runner import Bot

    t0 = int(pd.Timestamp(start, tz="UTC").value // 1_000_000) if pd.Timestamp(start).tzinfo \
        is None else int(pd.Timestamp(start).value // 1_000_000)
    t1 = int(pd.Timestamp(end, tz="UTC").value // 1_000_000) if pd.Timestamp(end).tzinfo \
        is None else int(pd.Timestamp(end).value // 1_000_000)
    rng = random.Random(seed)
    clock = clock or SimClock(t0 + int(cfg.wake_after_close_seconds * 1000))
    src = source or ReplayDataSource(clock)
    bot = Bot(cfg, src, clock, rng=rng)
    resets = []

    def hook(b, status):
        n = 0
        while auto_reset_halt and b.shield.halted() and n < 5:
            # the owner reads shield.jsonl and deletes HALT a minute later
            resets.append((clock.now_ms(), b.shield.halt_reason()))
            cfg.halt_file.unlink()
            clock.sleep(60)
            status = b.cycle()
            n += 1
        if after_cycle:
            after_cycle(b, status)

    bot.run_forever(until_ms=t1, after_cycle=hook)
    return bot, resets


def today_utc() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()
