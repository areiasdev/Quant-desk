"""Step 4/6: point-in-time universe (top-10 USDT-M crypto perps by trailing-30-day quote volume).

For every month start M (00:00 UTC on day 1), using ONLY bars that closed before M:
    trailing_qv(sym, M) = sum of quote_volume over bars with open_time in [M - 30 days, M)
Eligibility at M: symbol_class == "crypto" (data/symbol_classes.csv), and the contract was really
trading at M: its last bar with trades > 0 opened within the 24 hours before M. (After a delisting
the archive keeps writing flat zero-trade "ghost" bars, so "a bar exists" is NOT enough.)
Delisted coins are included naturally: the archive keeps their files.

Phase A (screen, needs only the 1d screen): rank ALL symbols -> top CANDIDATE_SIZE crypto
candidates per month (data/universe_screen_by_month.csv) and every excluded-class symbol that
would have made the top-10 (data/universe_excluded_hits.csv).
Phase B (final, needs 1h klines of the candidates): recompute trailing_qv from 1h bars for every
candidate, rank, keep the top UNIVERSE_SIZE -> data/universe_by_month.csv, with the 1d rank
alongside as a cross-check.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from .config import (CANDIDATE_SIZE, CLEAN, DATA, TRAILING_DAYS, UNIVERSE_SIZE, WINDOW_START,
                     month_starts, today_utc)

SCREEN_OUT = DATA / "universe_screen_by_month.csv"
EXCLUDED_OUT = DATA / "universe_excluded_hits.csv"
FINAL_OUT = DATA / "universe_by_month.csv"


def universe_months() -> list[dt.date]:
    return month_starts(WINDOW_START, today_utc())


def _classes() -> pd.DataFrame:
    return pd.read_csv(DATA / "symbol_classes.csv", dtype={"first_month": str, "last_month": str})


def trailing_rank(bars: pd.DataFrame, time_col: str, month: dt.date) -> pd.DataFrame:
    """Rank symbols by quote volume in [M-30d, M); require a real trade in the last 24h before M."""
    m = pd.Timestamp(month)
    lo = m - pd.Timedelta(days=TRAILING_DAYS)
    w = bars[(bars[time_col] >= lo) & (bars[time_col] < m)]
    agg = w.groupby("symbol").agg(trailing_qv=("quote_volume", "sum"), bars=("quote_volume", "size"))
    agg["last_bar"] = w[w["trades"] > 0].groupby("symbol")[time_col].max()
    agg = agg[agg["last_bar"] >= m - pd.Timedelta(days=1)]  # really trading at the month start
    agg = agg.sort_values("trailing_qv", ascending=False)
    agg["rank"] = range(1, len(agg) + 1)
    return agg.reset_index()


def screen() -> pd.DataFrame:
    s1d = pd.read_parquet(CLEAN / "screen_1d_all_symbols.parquet",
                          columns=["symbol", "date", "quote_volume", "trades"])
    cls = _classes().set_index("symbol")["symbol_class"]
    rows, excl = [], []
    for m in universe_months():
        r = trailing_rank(s1d, "date", m)
        r["symbol_class"] = r["symbol"].map(cls)
        r["month"] = m.strftime("%Y-%m")
        crypto = r[r["symbol_class"] == "crypto"].copy()
        crypto["screen_rank"] = range(1, len(crypto) + 1)
        rows.append(crypto[crypto["screen_rank"] <= CANDIDATE_SIZE])
        # excluded-class symbols that would have taken a top-10 slot in an unfiltered ranking
        top = r[r["rank"] <= UNIVERSE_SIZE]
        excl.append(top[top["symbol_class"] != "crypto"])
    out = pd.concat(rows)[["month", "screen_rank", "symbol", "trailing_qv", "bars", "last_bar"]]
    out = out.rename(columns={"trailing_qv": "trailing30d_quote_volume_1d", "bars": "days"})
    out.to_csv(SCREEN_OUT, index=False)
    ex = pd.concat(excl)[["month", "rank", "symbol", "symbol_class", "trailing_qv"]]
    ex = ex.rename(columns={"rank": "unfiltered_rank", "trailing_qv": "trailing30d_quote_volume_1d"})
    ex.to_csv(EXCLUDED_OUT, index=False)
    print(f"screen: {out['symbol'].nunique()} distinct top-{CANDIDATE_SIZE} candidates over "
          f"{out['month'].nunique()} months; {len(ex)} excluded-class top-10 hits -> {SCREEN_OUT}")
    return out


def candidate_symbols() -> list[str]:
    return sorted(pd.read_csv(SCREEN_OUT)["symbol"].unique())


def final() -> pd.DataFrame:
    from .clean import load_klines_raw  # 1h bars straight from the verified raw zips

    cand = pd.read_csv(SCREEN_OUT)
    cls = _classes().set_index("symbol")
    out = []
    for month, grp in cand.groupby("month"):
        m = dt.date.fromisoformat(month + "-01")
        lo = pd.Timestamp(m) - pd.Timedelta(days=TRAILING_DAYS)
        frames = []
        for sym in grp["symbol"]:
            k = load_klines_raw(sym, since=lo.to_pydatetime().date(), until=m)
            if len(k):
                frames.append(k[["symbol", "open_time", "quote_volume", "trades"]])
        bars = pd.concat(frames)
        bars["open_time"] = bars["open_time"].dt.tz_convert(None)
        bars["trades"] = pd.to_numeric(bars["trades"])
        r = trailing_rank(bars, "open_time", m)
        r = r.merge(grp[["symbol", "screen_rank", "trailing30d_quote_volume_1d"]], on="symbol",
                    how="left")
        r["month"] = month
        out.append(r[r["rank"] <= UNIVERSE_SIZE])
    u = pd.concat(out).rename(columns={"trailing_qv": "trailing30d_quote_volume_usdt",
                                       "bars": "hours_with_data", "last_bar": "last_bar_open_utc"})
    u["window_start_utc"] = [(pd.Timestamp(mo + "-01") - pd.Timedelta(days=TRAILING_DAYS)).isoformat()
                             for mo in u["month"]]
    u["window_end_utc"] = [pd.Timestamp(mo + "-01").isoformat() for mo in u["month"]]
    u["qv_diff_1h_vs_1d_pct"] = (100 * (u["trailing30d_quote_volume_usdt"]
                                        / u["trailing30d_quote_volume_1d"] - 1)).round(4)
    u["is_meme"] = u["symbol"].map(cls["is_meme"])
    u["symbol_last_trade_day"] = u["symbol"].map(cls["last_trade_day"])
    u["delisted_by_latest_data"] = u["symbol"].map(cls["status"]).eq("delisted")
    u = u[["month", "rank", "symbol", "trailing30d_quote_volume_usdt", "hours_with_data",
           "window_start_utc", "window_end_utc", "last_bar_open_utc", "screen_rank",
           "trailing30d_quote_volume_1d", "qv_diff_1h_vs_1d_pct", "is_meme",
           "symbol_last_trade_day", "delisted_by_latest_data"]]
    u["trailing30d_quote_volume_usdt"] = u["trailing30d_quote_volume_usdt"].round(2)
    u["trailing30d_quote_volume_1d"] = u["trailing30d_quote_volume_1d"].round(2)
    u.to_csv(FINAL_OUT, index=False)
    mism = (u["rank"] != u["screen_rank"]).sum()
    print(f"universe: {u['month'].nunique()} months, {u['symbol'].nunique()} distinct symbols, "
          f"{mism} rows where 1h rank != 1d rank, max |qv diff| "
          f"{u['qv_diff_1h_vs_1d_pct'].abs().max():.4f}% -> {FINAL_OUT}")
    return u


if __name__ == "__main__":
    import sys

    screen() if (len(sys.argv) < 2 or sys.argv[1] == "screen") else final()
