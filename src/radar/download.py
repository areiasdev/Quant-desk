"""Step 5/7: download 1h klines, funding rates and metrics for the research symbols.

What is downloaded (all from data.binance.vision, checksum-verified, logged in MANIFEST.csv):
* 1h klines  - every top-30 screen candidate (any month) and every meme coin, all months from
               SCREEN_START that exist; daily files for the current month only for the
               current-month universe, the meme coins and BTC.
* fundingRate- monthly files (the archive has no daily funding files) for every symbol that was
               ever in the top-10 universe, plus memes and BTC.
* metrics    - daily files (5-minute open interest + long/short ratios): for each universe
               month the universe symbols get that month and the previous month (look-back);
               meme coins get every available day in the window.
"""
from __future__ import annotations

import datetime as dt
import re

import pandas as pd

from .archive import fetch_many, list_zip_keys, parallel_map
from .config import DATA, SCREEN_START, WINDOW_START, month_starts, today_utc

UM = "data/futures/um"


def _ym(d: dt.date) -> str:
    return d.strftime("%Y-%m")


def _month_of(key: str) -> str:
    m = re.search(r"-(\d{4}-\d{2})(?:-\d{2})?\.zip$", key)
    return m.group(1) if m else ""


def kline_keys_1h(sym: str, with_current_month: bool) -> list[str]:
    """Monthly 1h zips from SCREEN_START; daily fallback for an unpublished last month;
    daily zips for the current month if requested."""
    cur = _ym(today_utc())
    last = _ym(today_utc().replace(day=1) - dt.timedelta(days=1))
    monthly = [k for k in list_zip_keys(f"{UM}/monthly/klines/{sym}/1h/")
               if _ym(SCREEN_START) <= _month_of(k) <= last]
    keys = list(monthly)
    have = {_month_of(k) for k in monthly}
    if last not in have:
        keys += list_zip_keys(f"{UM}/daily/klines/{sym}/1h/{sym}-1h-{last}-")
    if with_current_month:
        keys += list_zip_keys(f"{UM}/daily/klines/{sym}/1h/{sym}-1h-{cur}-")
    return keys


def funding_keys(sym: str) -> list[str]:
    return [k for k in list_zip_keys(f"{UM}/monthly/fundingRate/{sym}/")
            if _month_of(k) >= _ym(WINDOW_START)]


def metrics_keys(sym: str, wanted_months: set[str] | None) -> list[str]:
    """Daily metrics zips for a symbol; restricted to wanted months (None = whole window)."""
    keys = [k for k in list_zip_keys(f"{UM}/daily/metrics/{sym}/")
            if _month_of(k) >= _ym(WINDOW_START)]
    if wanted_months is not None:
        keys = [k for k in keys if _month_of(k) in wanted_months]
    return keys


def research_sets() -> dict[str, list[str]]:
    """Symbol sets used by the downloads (from the screen, the final universe and classes)."""
    cls = pd.read_csv(DATA / "symbol_classes.csv")
    memes = sorted(cls.loc[cls["is_meme"], "symbol"])
    screen = pd.read_csv(DATA / "universe_screen_by_month.csv")
    candidates = sorted(screen["symbol"].unique())
    uni_path = DATA / "universe_by_month.csv"
    uni = pd.read_csv(uni_path) if uni_path.exists() else screen[screen["screen_rank"] <= 10]
    ever_universe = sorted(uni["symbol"].unique())
    cur_universe = sorted(uni.loc[uni["month"] == uni["month"].max(), "symbol"])
    return {"memes": memes, "candidates": candidates, "ever_universe": ever_universe,
            "current_universe": cur_universe}


def universe_metric_months() -> dict[str, set[str]]:
    """symbol -> months of metrics needed: each universe month plus the month before it."""
    uni = pd.read_csv(DATA / "universe_by_month.csv")
    need: dict[str, set[str]] = {}
    for month, sym in zip(uni["month"], uni["symbol"]):
        m = dt.date.fromisoformat(month + "-01")
        prev = _ym(m - dt.timedelta(days=1))
        need.setdefault(sym, set()).update({month, prev})
    return need


def download_klines(symbols: list[str], current_month_for: set[str]) -> None:
    keys = parallel_map(lambda s: kline_keys_1h(s, s in current_month_for), symbols)
    fetch_many([k for ks in keys for k in ks], "klines-1h", workers=32)


def download_funding(symbols: list[str]) -> None:
    keys = parallel_map(funding_keys, symbols)
    fetch_many([k for ks in keys for k in ks], "fundingRate", workers=32)


def download_metrics(per_symbol_months: dict[str, set[str] | None]) -> None:
    items = list(per_symbol_months.items())
    keys = parallel_map(lambda it: metrics_keys(it[0], it[1]), items)
    fetch_many([k for ks in keys for k in ks], "metrics", workers=48)


def main(stage: str) -> None:
    sets = research_sets()
    if stage == "klines":
        syms = sorted(set(sets["candidates"]) | set(sets["memes"]) | {"BTCUSDT"})
        cur = set(sets["current_universe"]) | set(sets["memes"]) | {"BTCUSDT"}
        print(f"1h klines for {len(syms)} symbols ({len(cur)} incl. current month)")
        download_klines(syms, cur)
    elif stage == "funding_metrics":
        fsyms = sorted(set(sets["ever_universe"]) | set(sets["memes"]) | {"BTCUSDT"})
        print(f"funding for {len(fsyms)} symbols")
        download_funding(fsyms)
        need: dict[str, set[str] | None] = dict(universe_metric_months())
        for s in sets["memes"]:
            need[s] = None  # whole window
        print(f"metrics for {len(need)} symbols")
        download_metrics(need)
    else:
        raise SystemExit("stage must be 'klines' or 'funding_metrics'")


if __name__ == "__main__":
    import sys

    main(sys.argv[1])
