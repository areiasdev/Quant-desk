"""Bars and funding for one symbol, built exactly the way LAB builds them.

Binance JSON -> 1h bar frame -> funding known at each bar close (RADAR's `funding_asof_1h`) ->
LAB's `add_derived` (F8, new_settle, settleF8, ...) -> LAB's `resample_4h` (4h bars anchored at
00/04/../20 UTC from four complete 1h bars). Only CLOSED 1h bars enter the panel; the bar still
forming is kept apart (its open is the fill price for orders "at the next open").

Funding interval per settlement (LAB's F8 = rate x 8 / interval): the public fundingRate
endpoint has no interval field, so it is inferred as min(time since the previous settlement,
time to the next one), the next one for the latest settlement being premiumIndex
nextFundingTime. On the archive (525,822 settlements, 127 symbols) this rule matches the
archive's own interval column on all but 68 settlements (0.013%), all at interval changes or
missed settlements (tests/test_bot_market.py re-measures it).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from lab.data import KEEP, add_derived, resample_4h
from radar.features import funding_asof_1h

from .datasource import DataSource

H = 3_600_000
VALID_INTERVALS = (1, 2, 4, 8)
_EMPTY_BTC = pd.Series(dtype=float, index=pd.DatetimeIndex([], tz="UTC"))


def floor_h(ms: int) -> int:
    return int(ms) // H * H


def iso(ms) -> str:
    if ms is None:
        return None
    return pd.Timestamp(int(ms), unit="ms", tz="UTC").isoformat(timespec="milliseconds")


def ms(ts) -> int:
    return int(pd.Timestamp(ts).value // 1_000_000)


KCOLS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
         "quote_volume", "trades", "taker_buy_volume", "taker_buy_quote_volume"]
_FLOAT = (1, 2, 3, 4, 5, 7, 9, 10)


def kline_arrays(rows: list[list]) -> dict:
    """Binance kline rows -> numpy columns (times as int64 epoch ms, prices as float)."""
    n = len(rows)
    out = {}
    for i, c in enumerate(KCOLS):
        if i in _FLOAT:
            out[c] = np.array([r[i] for r in rows], dtype=float) if n else np.array([], float)
        else:
            out[c] = np.array([int(r[i]) for r in rows], dtype="int64") if n else \
                np.array([], "int64")
    return out


def parse_klines(rows: list[list]) -> pd.DataFrame:
    return pd.DataFrame(kline_arrays(rows))


def infer_intervals(t_ms: np.ndarray, next_ms: int | None) -> np.ndarray:
    t = np.asarray(t_ms, dtype="int64")
    n = len(t)
    if n == 0:
        return np.array([], dtype=float)
    back = np.full(n, np.nan)
    fwd = np.full(n, np.nan)
    if n > 1:
        d = np.round(np.diff(t) / H)
        back[1:] = d
        fwd[:-1] = d
    if next_ms is not None and next_ms > t[-1]:
        fwd[-1] = np.round((next_ms - t[-1]) / H)
    back = np.where(np.isin(back, VALID_INTERVALS), back, np.nan)
    fwd = np.where(np.isin(fwd, VALID_INTERVALS), fwd, np.nan)
    v = np.fmin(back, fwd)
    s = pd.Series(v).ffill().bfill()
    return s.fillna(8.0).to_numpy(float)


def parse_funding(rows: list[dict], next_ms: int | None = None) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame({"calc_time": pd.Series(dtype="datetime64[ms, UTC]"),
                             "funding_time": pd.Series(dtype="datetime64[ms, UTC]"),
                             "funding_interval_hours": pd.Series(dtype=float),
                             "funding_rate": pd.Series(dtype=float)})
    t = np.array([int(r["fundingTime"]) for r in rows], dtype="int64")
    rate = np.array([float(r["fundingRate"]) for r in rows], dtype=float)
    o = np.argsort(t, kind="stable")
    t, rate = t[o], rate[o]
    keep = np.r_[True, np.diff(t) > 0]
    t, rate = t[keep], rate[keep]
    calc = pd.to_datetime(t, unit="ms", utc=True).astype("datetime64[ms, UTC]")
    return pd.DataFrame({"calc_time": calc, "funding_time": calc.round("1min"),
                         "funding_interval_hours": infer_intervals(t, next_ms),
                         "funding_rate": rate})


def build_panel(bars: pd.DataFrame, funding: pd.DataFrame) -> pd.DataFrame:
    """LAB's 1h panel (lab.data.build_cache + load_ctx) from closed 1h bars and funding."""
    ot = pd.to_datetime(bars["open_time"].to_numpy(), unit="ms", utc=True) \
        .astype("datetime64[ms, UTC]")
    k = pd.DataFrame({"open_time": ot})
    k["close_time"] = k["open_time"] + pd.Timedelta(milliseconds=H - 1)
    f = funding_asof_1h(k, funding)
    n = len(k)
    cols = {"open_time": ot}
    for c in KEEP[1:]:
        if c in ("open", "high", "low", "close", "volume", "quote_volume"):
            cols[c] = bars[c].to_numpy(float)
        elif c == "trades":
            cols[c] = bars[c].to_numpy("int64")
        elif c == "funding_time":
            cols[c] = np.full(n, np.nan)        # replaced below (keeps KEEP's column order)
        elif c in ("funding_rate", "funding_interval_hours"):
            cols[c] = f[c].to_numpy(float) if c in f else np.full(n, np.nan)
        else:
            cols[c] = np.full(n, np.nan)
    p = pd.DataFrame(cols)
    ft = f["funding_time"] if "funding_time" in f else pd.Series(pd.NaT, index=p.index)
    p["funding_time"] = pd.to_datetime(ft, utc=True).astype("datetime64[ms, UTC]")
    add_derived(p, _EMPTY_BTC)
    return p


def tf_frame(panel: pd.DataFrame, tf: str) -> pd.DataFrame:
    return panel if tf == "1h" else resample_4h(panel)


class Feed:
    """Rolling cache of one symbol's closed 1h bars and settled funding."""

    def __init__(self, symbol: str, history_bars: int):
        self.symbol = symbol
        self.history = history_bars
        self._b = kline_arrays([])
        self.live: dict | None = None          # bar still forming: open_time, open, last
        self.fund_t: list[int] = []
        self.fund_r: list[str] = []
        self.next_funding_ms: int | None = None
        self._cache: dict = {}

    def _n(self) -> int:
        return len(self._b["open_time"])

    @property
    def bars(self) -> pd.DataFrame:
        return self._cached("bars", lambda: pd.DataFrame(self._b))

    def _key(self):
        n = self._n()
        return (n, int(self._b["open_time"][-1]) if n else None, len(self.fund_t),
                self.fund_t[-1] if self.fund_t else None, self.next_funding_ms)

    def _cached(self, name, fn):
        k = self._key()
        hit = self._cache.get(name)
        if hit is None or hit[0] != k:
            hit = (k, fn())
            self._cache[name] = hit
        return hit[1]

    # ---- fetch ---------------------------------------------------------------------------
    def update(self, src: DataSource, now_ms: int) -> None:
        cur = floor_h(now_ms)
        n = self._n()
        start = cur - self.history * H if n == 0 else int(self._b["open_time"][-1]) + H
        new = []
        while start <= cur:
            lim = 1500 if (cur - start) // H + 1 > 99 else 99
            rows = src.klines(self.symbol, "1h", start_ms=start, limit=lim)
            if not rows:
                break
            new.extend(rows)
            last = int(rows[-1][0])
            if last >= cur or len(rows) < lim:
                break
            start = last + H
        if new:
            k = kline_arrays(new)
            ot = k["open_time"]
            closed = k["close_time"] < now_ms
            if n:
                closed &= ot > int(self._b["open_time"][-1])
            live = np.flatnonzero(k["close_time"] >= now_ms)
            self.live = None
            if len(live):
                r = live[-1]
                self.live = {"open_time": int(ot[r]), "open": float(k["open"][r]),
                             "last": float(k["close"][r])}
            if closed.any():
                ix = np.flatnonzero(closed)
                ix = ix[np.r_[True, np.diff(ot[ix]) > 0]]
                for c in KCOLS:
                    self._b[c] = np.concatenate([self._b[c], k[c][ix]])[-self.history:]
        elif self.live is not None and self.live["open_time"] < cur:
            self.live = None
        # funding: everything since the first cached bar (minus one settlement), then new ones
        if self._n():
            first = int(self._b["open_time"][0]) - 8 * H
            fstart = first if not self.fund_t else self.fund_t[-1] + 1
            while True:
                rows = src.funding_rate(self.symbol, start_ms=fstart, limit=1000)
                rows = [r for r in rows if int(r["fundingTime"]) <= now_ms]
                for r in rows:
                    t = int(r["fundingTime"])
                    if not self.fund_t or t > self.fund_t[-1]:
                        self.fund_t.append(t)
                        self.fund_r.append(str(r["fundingRate"]))
                if len(rows) < 1000:
                    break
                fstart = int(rows[-1]["fundingTime"]) + 1
            k0 = int(np.searchsorted(np.asarray(self.fund_t, dtype="int64"), first))
            if k0:
                del self.fund_t[:k0], self.fund_r[:k0]

    # ---- derived ---------------------------------------------------------------------------
    def last_closed_close_ms(self) -> int | None:
        return None if self._n() == 0 else int(self._b["open_time"][-1]) + H

    def funding(self) -> pd.DataFrame:
        return self._cached("funding", lambda: parse_funding(
            [{"fundingTime": t, "fundingRate": r} for t, r in zip(self.fund_t, self.fund_r)],
            self.next_funding_ms))

    def settlements(self) -> tuple[np.ndarray, np.ndarray]:
        """(funding_time ns, rate) exactly like lab.data.Sym.fund_t/fund_r."""
        def mk():
            f = self.funding()
            return (f["funding_time"].astype("datetime64[ns, UTC]").astype("int64").to_numpy(),
                    f["funding_rate"].to_numpy(float))
        return self._cached("settlements", mk)

    def panel(self) -> pd.DataFrame:
        return self._cached("panel", lambda: build_panel(self.bars, self.funding()))

    def open_at(self, t_ms: int) -> float | None:
        """Open of the 1h bar starting at t_ms (closed or still forming)."""
        if self.live is not None and self.live["open_time"] == t_ms:
            return self.live["open"]
        ot = self._b["open_time"]
        i = int(np.searchsorted(ot, t_ms))
        if i < len(ot) and int(ot[i]) == t_ms:
            return float(self._b["open"][i])
        return None

    def price_for_funding(self, t_ms: int) -> float | None:
        """lab.engine: funding is valued at the open of the 1h bar containing the settlement."""
        return self.open_at(floor_h(t_ms))

    def last_price(self) -> float | None:
        if self.live is not None:
            return self.live["last"]
        return None if self._n() == 0 else float(self._b["close"][-1])
