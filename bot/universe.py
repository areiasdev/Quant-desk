"""Point-in-time universe, recomputed at each calendar month start - RADAR's method, live.

For month M (00:00 UTC on day 1), using only bars that closed before M (src/radar/universe.py):
  phase A: every TRADING USDT-M perpetual (exchangeInfo) -> 1d klines over [M-30d, M) ->
           radar.universe.trailing_rank (sum of quote volume, must have traded in the 24h before
           M) -> keep crypto only (same lists/rules as RADAR) -> top `candidates` (30);
  phase B: 1h klines over [M-30d, M) for those candidates -> trailing_rank -> top `size` (10).
Classes: data/symbol_classes.csv (RADAR's output) for known symbols; for a symbol listed after
it, RADAR's own rules - radar.symbols.static_class (stablecoin/index/TradFi lists) and the
weekend-volume TradFi fingerprint (radar.classify.weekend_fingerprint +
radar.symbols.fingerprint_is_tradfi) on 1d klines.
"""
from __future__ import annotations

import datetime as dt
import logging

import pandas as pd

from radar.classify import weekend_fingerprint
from radar.config import DATA
from radar.symbols import FINGERPRINT_FROM, MEME, fingerprint_is_tradfi, static_class
from radar.universe import trailing_rank

from .market import H, parse_klines

log = logging.getLogger("bot")
DAY = 24 * H


def month_start_ms(now_ms: int) -> int:
    t = pd.Timestamp(now_ms, unit="ms", tz="UTC")
    return int(pd.Timestamp(year=t.year, month=t.month, day=1, tz="UTC").value // 1_000_000)


def month_id(now_ms: int) -> str:
    return pd.Timestamp(now_ms, unit="ms", tz="UTC").strftime("%Y-%m")


class Classifier:
    def __init__(self, classes_csv=DATA / "symbol_classes.csv"):
        try:
            df = pd.read_csv(classes_csv, dtype={"first_month": str})
            self.known = dict(zip(df["symbol"], df["symbol_class"]))
            self.meme = dict(zip(df["symbol"], df["is_meme"].astype(bool)))
        except FileNotFoundError:
            self.known, self.meme = {}, {}
        self.cache: dict[str, tuple[str, str]] = {}

    def classify(self, sym: str, src=None, onboard_ms: int | None = None) -> tuple[str, str]:
        if sym in self.known:
            return str(self.known[sym]), "symbol_classes.csv"
        if sym in self.cache:
            return self.cache[sym]
        sc = static_class(sym)
        if sc != "crypto":
            out = (sc, "list")
        elif src is None:
            out = ("crypto", "default")
        else:
            first_month = pd.Timestamp(onboard_ms, unit="ms").strftime("%Y-%m") if onboard_ms \
                else ""
            start = int(pd.Timestamp(FINGERPRINT_FROM, tz="UTC").value // 1_000_000)
            k = parse_klines(src.klines(sym, "1d", start_ms=start, limit=1500))
            out = ("crypto", "default")
            if len(k):
                d = pd.DataFrame({"symbol": sym, "date": pd.to_datetime(k["open_time"], unit="ms"),
                                  "open": k["open"], "high": k["high"], "low": k["low"],
                                  "quote_volume": k["quote_volume"]})
                fp = weekend_fingerprint(d)
                if sym in fp.index:
                    wd = fp.loc[sym, "weekend_days"]
                    wr = fp.loc[sym, "weekend_volume_ratio"]
                    if fingerprint_is_tradfi(first_month, 0 if pd.isna(wd) else wd,
                                             9 if pd.isna(wr) else wr):
                        out = ("tradfi", "fingerprint")
            log.info("classified new symbol %s as %s (%s)", sym, *out)
        self.cache[sym] = out
        return out

    def is_meme(self, sym: str) -> bool:
        if sym in self.meme:
            return bool(self.meme[sym])
        return sym in MEME and self.classify(sym)[0] == "crypto"


def tradeable_perps(info: dict) -> list[dict]:
    return [s for s in info.get("symbols", [])
            if s.get("contractType") == "PERPETUAL" and s.get("quoteAsset") == "USDT"
            and s.get("status") == "TRADING" and "_" not in s.get("symbol", "_")]


def _frame(sym, rows, col):
    k = parse_klines(rows)
    return pd.DataFrame({"symbol": sym, col: pd.to_datetime(k["open_time"], unit="ms"),
                         "quote_volume": k["quote_volume"], "trades": k["trades"]})


def compute_universe(src, month_ms: int, classifier: Classifier, *, size: int = 10,
                     candidates: int = 30, trailing_days: int = 30) -> dict:
    month = pd.Timestamp(month_ms, unit="ms").date()
    lo = month_ms - trailing_days * DAY
    info = src.exchange_info()
    perps = tradeable_perps(info)
    onboard = {s["symbol"]: s.get("onboardDate") for s in perps}
    frames = [_frame(s["symbol"], src.klines(s["symbol"], "1d", start_ms=lo, end_ms=month_ms - 1,
                                             limit=trailing_days + 1), "date") for s in perps]
    frames = [f for f in frames if len(f)]
    if not frames:
        raise RuntimeError("universe: no 1d klines returned for any symbol")
    r = trailing_rank(pd.concat(frames, ignore_index=True), "date", month)
    cands, excluded = [], []
    for sym in r["symbol"]:
        cls, how = classifier.classify(sym, src, onboard.get(sym))
        if cls == "crypto":
            cands.append(sym)
            if len(cands) >= candidates:
                break
        elif len(cands) < size:
            excluded.append({"symbol": sym, "class": cls, "source": how})
    frames = [_frame(s, src.klines(s, "1h", start_ms=lo, end_ms=month_ms - 1,
                                   limit=trailing_days * 24), "open_time") for s in cands]
    frames = [f for f in frames if len(f)]
    r2 = trailing_rank(pd.concat(frames, ignore_index=True), "open_time", month)
    top = r2[r2["rank"] <= size]
    return {"month": month.strftime("%Y-%m"), "computed_from": "klines (RADAR method)",
            "symbols": list(top["symbol"]),
            "trailing30d_quote_volume": {s: float(v) for s, v in
                                         zip(top["symbol"], top["trailing_qv"])},
            "candidates": cands, "excluded_non_crypto_in_top": excluded,
            "perps_screened": len(perps)}


def parse_filters(info: dict) -> dict[str, dict]:
    """tick size, lot step, min qty and min notional per symbol from exchangeInfo."""
    out = {}
    for s in info.get("symbols", []):
        f = {x.get("filterType"): x for x in s.get("filters", [])}
        d = {}
        if "PRICE_FILTER" in f:
            d["tick_size"] = float(f["PRICE_FILTER"].get("tickSize", 0) or 0)
        lot = f.get("MARKET_LOT_SIZE") or f.get("LOT_SIZE")
        if lot:
            d["step_size"] = float(lot.get("stepSize", 0) or 0)
            d["min_qty"] = float(lot.get("minQty", 0) or 0)
        if "MIN_NOTIONAL" in f:
            d["min_notional"] = float(f["MIN_NOTIONAL"].get("notional", 0) or 0)
        if d:
            out[s["symbol"]] = d
    return out


def today(now_ms: int) -> dt.date:
    return pd.Timestamp(now_ms, unit="ms", tz="UTC").date()
