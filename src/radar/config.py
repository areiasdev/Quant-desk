"""Shared paths and window settings for the RADAR pipeline."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "data"
RAW = DATA / "raw"            # raw zips mirror the archive key layout (gitignored)
CLEAN = DATA / "clean"        # cleaned parquet
MANIFEST = DATA / "MANIFEST.csv"
RESEARCH = REPO / "research" / "radar"

# Research window starts here (inclusive, UTC).
WINDOW_START = dt.date(2022, 11, 1)
# The first universe month (2022-11) is ranked on the trailing 30 days before 2022-11-01,
# so the volume screen needs October 2022 as well. Those October files are used ONLY for
# ranking; cleaned datasets are clipped to WINDOW_START.
SCREEN_START = dt.date(2022, 10, 1)

UNIVERSE_SIZE = 10          # top-N traded universe per month
CANDIDATE_SIZE = 30         # top-N from the daily screen that get 1h klines for the final ranking
TRAILING_DAYS = 30          # ranking look-back

WORKERS = 16                # parallel HTTP downloads


def today_utc() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


def month_starts(start: dt.date, end: dt.date) -> list[dt.date]:
    """First day of every month from start's month to end's month (inclusive)."""
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(dt.date(y, m, 1))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out
