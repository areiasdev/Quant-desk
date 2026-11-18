"""Step 1: list every USDT-M futures symbol in the archive (live AND delisted).

Source: S3 listing of data.binance.vision (no live Binance API is used).
For each USDT-quoted symbol we also list its monthly 1d kline files, which tells us which months
the contract traded; the current month is covered by daily 1d files.

Output: data/symbols_all.csv
    symbol, quote, first_month, last_month, n_months, months, current_month_daily_files,
    last_daily_file
(classification is done later by classify.py, which needs the volume screen.)
Rerun: python -m radar.list_symbols   (from src/, or via radar.run_nightly)
"""
from __future__ import annotations

import re

import pandas as pd

from .archive import list_zip_keys, parallel_map, s3_list
from .config import DATA, today_utc

KL_MONTHLY = "data/futures/um/monthly/klines/"
KL_DAILY = "data/futures/um/daily/klines/"
OUT = DATA / "symbols_all.csv"


def _symbols_under(prefix: str) -> set[str]:
    prefixes, _ = s3_list(prefix)
    return {p[len(prefix):].rstrip("/") for p in prefixes}


def _months_for(sym: str) -> dict:
    keys = list_zip_keys(f"{KL_MONTHLY}{sym}/1d/")
    months = sorted(m.group(1) for k in keys if (m := re.search(r"-1d-(\d{4}-\d{2})\.zip$", k)))
    cur = today_utc().strftime("%Y-%m")
    dkeys = list_zip_keys(f"{KL_DAILY}{sym}/1d/{sym}-1d-{cur}-")
    days = sorted(m.group(1) for k in dkeys if (m := re.search(r"-1d-(\d{4}-\d{2}-\d{2})\.zip$", k)))
    return {
        "symbol": sym,
        "first_month": months[0] if months else "",
        "last_month": months[-1] if months else "",
        "n_months": len(months),
        "months": ";".join(months),
        "current_month_daily_files": len(days),
        "last_daily_file": days[-1] if days else "",
    }


def main() -> pd.DataFrame:
    syms = _symbols_under(KL_MONTHLY) | _symbols_under(KL_DAILY)
    usdt = sorted(s for s in syms if s.endswith("USDT"))
    print(f"archive symbols: {len(syms)} total, {len(usdt)} quoted in USDT (perps + dated futures)")
    rows = parallel_map(_months_for, usdt)
    df = pd.DataFrame(rows)
    df.insert(1, "quote", "USDT")
    df.to_csv(OUT, index=False)
    print(f"wrote {OUT} ({len(df)} symbols)")
    return df


if __name__ == "__main__":
    main()
