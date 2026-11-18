"""Step 2: broad volume screen – monthly 1d klines for EVERY USDT symbol (incl. delisted).

Why 1d: a trailing-30-day quote volume at a month start (00:00 UTC) is exactly the sum of the
30 preceding UTC daily bars, so the screen ranks every symbol exactly at ~1/24 of the download
cost of 1h data. The final universe is then re-ranked from 1h klines for the top candidates
(see universe.py) as an independent cross-check.

Months covered: SCREEN_START (2022-10, warm-up for the 2022-11 ranking) .. last complete month.
If a month's monthly zip is not yet published (first days of a new month), the daily 1d zips of
that month are used instead.

Output: data/clean/screen_1d_all_symbols.parquet (long: symbol, date, OHLC, volume, quote_volume,
trades, taker_buy_quote_volume, source)
"""
from __future__ import annotations

import re

import pandas as pd

from .archive import fetch_many, list_zip_keys, local_path, parallel_map, read_zip_csv
from .config import CLEAN, DATA, SCREEN_START, month_starts, today_utc

KLINE_COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
              "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore"]
OUT = CLEAN / "screen_1d_all_symbols.parquet"


def monthly_key(sym: str, interval: str, ym: str) -> str:
    return f"data/futures/um/monthly/klines/{sym}/{interval}/{sym}-{interval}-{ym}.zip"


def needed_keys(sym: str, months_avail: set[str], interval: str, first: str, last: str) -> list[str]:
    """Monthly keys for months in [first, last]; daily fallback for the newest month if needed."""
    keys = [monthly_key(sym, interval, m) for m in sorted(months_avail) if first <= m <= last]
    if last not in months_avail:
        keys += list_zip_keys(f"data/futures/um/daily/klines/{sym}/{interval}/{sym}-{interval}-{last}-")
    return keys


def to_frame(path, sym: str, source: str) -> pd.DataFrame:
    df = read_zip_csv(path, KLINE_COLS)
    df = df.rename(columns={"count": "trades"})
    t = pd.to_numeric(df["open_time"], errors="coerce")
    unit = "us" if t.max() > 1e14 else "ms"  # guard: some archives moved to microseconds
    df["open_time"] = pd.to_datetime(t, unit=unit, utc=True)
    df["symbol"] = sym
    df["source"] = source
    return df


def main() -> pd.DataFrame:
    syms = pd.read_csv(DATA / "symbols_all.csv", dtype=str, keep_default_na=False)
    syms = syms[~syms["symbol"].str.contains("_")]
    ms = [m.strftime("%Y-%m") for m in month_starts(SCREEN_START, today_utc().replace(day=1))]
    first, last, prev = ms[0], ms[-2], ms[-3]  # ms[-1] is the current (incomplete) month

    def keys_for(row):
        avail = set(filter(None, row["months"].split(";")))
        alive = bool(row["last_daily_file"]) or (bool(avail) and max(avail) >= prev)
        if avail and max(avail) < first:
            return []
        if last not in avail and not alive:
            return [monthly_key(row["symbol"], "1d", m) for m in sorted(avail) if m >= first]
        return needed_keys(row["symbol"], avail, "1d", first, last)

    all_keys = [k for ks in parallel_map(keys_for, [r for _, r in syms.iterrows()]) for k in ks]
    fetch_many(all_keys, "screen-1d", workers=32)

    frames = []
    for k in all_keys:
        p = local_path(k)
        if p.exists():
            sym = k.split("/")[5]
            frames.append(to_frame(p, sym, "daily" if "/daily/" in k else "monthly"))
    df = pd.concat(frames, ignore_index=True)
    df = (df.sort_values(["symbol", "open_time", "source"])  # 'daily' < 'monthly': monthly wins
            .drop_duplicates(["symbol", "open_time"], keep="last"))
    df["date"] = df["open_time"].dt.tz_convert(None).dt.normalize()
    keep = ["symbol", "date", "open", "high", "low", "close", "volume", "quote_volume", "trades",
            "taker_buy_quote_volume", "source"]
    df = df[keep].reset_index(drop=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT, index=False)
    print(f"screen: {df['symbol'].nunique()} symbols, {len(df)} daily rows, "
          f"{df['date'].min().date()}..{df['date'].max().date()} -> {OUT}")
    return df


if __name__ == "__main__":
    main()
