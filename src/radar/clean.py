"""Step 8: raw archive zips -> cleaned parquet + per-file quality facts.

Outputs (per symbol) in data/clean/:
  klines_1h/{SYM}.parquet   open_time (UTC, bar open), open, high, low, close, volume,
                            quote_volume, trades, taker_buy_volume, taker_buy_quote_volume, source
  funding/{SYM}.parquet     calc_time (UTC, exact), funding_time (UTC, calc_time rounded to the
                            minute), funding_interval_hours, funding_rate
  metrics_5m/{SYM}.parquet  create_time (UTC), open_interest, open_interest_value_usdt,
                            top_trader_account_ls_ratio, top_trader_position_ls_ratio,
                            global_account_ls_ratio, taker_buy_sell_vol_ratio
Quality facts in data/quality/*.csv feed data/DATA_QUALITY.md (quality.py).

Rules: rows are sorted and de-duplicated (a monthly file beats a daily file for the same bar);
nothing is interpolated or forward-filled - gaps stay gaps and are reported; suspicious bars are
FLAGGED in the quality files, never altered or removed.
"""
from __future__ import annotations

import datetime as dt
import glob
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from .archive import read_zip_csv
from .config import CLEAN, DATA, RAW, WINDOW_START

KLINE_COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
              "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore"]
METRIC_RENAME = {
    "sum_open_interest": "open_interest",
    "sum_open_interest_value": "open_interest_value_usdt",
    "count_toptrader_long_short_ratio": "top_trader_account_ls_ratio",
    "sum_toptrader_long_short_ratio": "top_trader_position_ls_ratio",
    "count_long_short_ratio": "global_account_ls_ratio",
    "sum_taker_long_short_vol_ratio": "taker_buy_sell_vol_ratio",
}
QUALITY = DATA / "quality"
UM = RAW / "futures" / "um"
# Outlier flags (reported only): 1h close-to-close move beyond this, or a bar range beyond
# RANGE_MULT x the symbol's trailing 30-day median 1h range.
RET_FLAG = 0.20
RANGE_MULT = 10.0
GHOST_MIN = 24  # trailing zero-trade bars treated as post-delisting ghost data


def _ts(series: pd.Series) -> pd.Series:
    t = pd.to_numeric(series, errors="coerce")
    unit = "us" if t.max() > 1e14 else "ms"
    return pd.to_datetime(t, unit=unit, utc=True)


def _kline_files(sym: str) -> list[tuple[str, str]]:
    m = sorted(glob.glob(str(UM / "monthly" / "klines" / sym / "1h" / "*.zip")))
    d = sorted(glob.glob(str(UM / "daily" / "klines" / sym / "1h" / "*.zip")))
    return [(p, "monthly") for p in m] + [(p, "daily") for p in d]


def load_klines_raw(sym: str, since: dt.date | None = None, until: dt.date | None = None,
                    stats: dict | None = None) -> pd.DataFrame:
    """All 1h bars for sym from the verified raw zips, open_time in [since, until)."""
    frames = []
    for path, src in _kline_files(sym):
        name = os.path.basename(path)
        ym = name.rsplit("-1h-", 1)[1][:7]
        if since and ym < since.strftime("%Y-%m"):
            continue
        if until and ym > until.strftime("%Y-%m"):
            continue
        df = read_zip_csv(Path(path), KLINE_COLS)
        df["source"] = src
        frames.append(df)
    if not frames:
        return pd.DataFrame(columns=["symbol", "open_time"])
    df = pd.concat(frames, ignore_index=True).rename(columns={"count": "trades"})
    df["open_time"] = _ts(df["open_time"])
    df["close_time"] = _ts(df["close_time"])
    n_raw = len(df)
    # monthly beats daily for the same bar ('monthly' sorts after 'daily', keep last)
    df = df.sort_values(["open_time", "source"])
    dup_mask = df.duplicated("open_time", keep=False)
    if stats is not None:
        vals = ["open", "high", "low", "close", "volume"]
        dups = df[dup_mask]
        conflicting = dups.groupby("open_time")[vals].nunique().gt(1).any(axis=1).sum()
        stats["raw_rows"] = n_raw
        stats["duplicate_rows_dropped"] = int(dup_mask.sum() - dups["open_time"].nunique())
        stats["duplicate_bars_conflicting"] = int(conflicting)
    df = df.drop_duplicates("open_time", keep="last")
    if since:
        df = df[df["open_time"] >= pd.Timestamp(since, tz="UTC")]
    if until:
        df = df[df["open_time"] < pd.Timestamp(until, tz="UTC")]
    df["symbol"] = sym
    return df.reset_index(drop=True)


def clean_klines(sym: str) -> dict:
    st: dict = {"symbol": sym}
    df = load_klines_raw(sym, since=WINDOW_START, stats=st)
    if df.empty:
        st["rows"] = 0
        return {"stats": st, "gaps": [], "outliers": []}
    num = ["open", "high", "low", "close", "volume", "quote_volume", "taker_buy_volume",
           "taker_buy_quote_volume"]
    for c in num:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    df["trades"] = pd.to_numeric(df["trades"], errors="coerce").astype("int64")
    # --- post-delisting ghost bars: the archive keeps writing flat bars with trades == 0 after a
    # contract stops trading. A trailing run of >= GHOST_MIN zero-trade bars is dropped (it is not
    # tradeable data); the last real bar marks the delisting. Shorter/mid-series runs are kept and
    # reported (a strategy must not trade a bar with trades == 0).
    real = (df["trades"] > 0).to_numpy()
    st["ghost_bars_dropped_after_last_trade"] = 0
    if real.any():
        last_real = int(np.flatnonzero(real)[-1])
        tail = len(df) - 1 - last_real
        if tail >= GHOST_MIN:
            st["ghost_bars_dropped_after_last_trade"] = tail
            st["ghost_from"] = df["open_time"].iloc[last_real + 1].isoformat()
            df = df.iloc[: last_real + 1].copy()
    zero = (df["trades"] == 0).astype(int)
    run_id = (zero.diff() != 0).cumsum()
    runs = zero.groupby(run_id).agg(["sum", "size"])
    st["zero_trade_bars_kept"] = int(zero.sum())
    st["zero_trade_runs_ge_24h_kept"] = int(((runs["sum"] > 0) & (runs["size"] >= 24)).sum())
    # --- checks (flag only) ---
    st["rows"] = len(df)
    st["first_bar"] = df["open_time"].min().isoformat()
    st["last_bar"] = df["open_time"].max().isoformat()
    st["misaligned_open_time"] = int((df["open_time"].dt.minute != 0).sum()
                                     + (df["open_time"].dt.second != 0).sum())
    st["bad_close_time"] = int((df["close_time"] - df["open_time"]
                                != pd.Timedelta(hours=1) - pd.Timedelta(milliseconds=1)).sum())
    bad_ohlc = ((df["high"] < df[["open", "close", "low"]].max(axis=1))
                | (df["low"] > df[["open", "close", "high"]].min(axis=1))
                | (df[["open", "high", "low", "close"]] <= 0).any(axis=1)
                | df[num].isna().any(axis=1))
    st["bad_ohlc_rows"] = int(bad_ohlc.sum())
    st["zero_volume_bars"] = int((df["volume"] == 0).sum())
    expected = int((df["open_time"].max() - df["open_time"].min()) / pd.Timedelta(hours=1)) + 1
    st["missing_bars"] = expected - len(df)
    # gaps
    gaps = []
    step = df["open_time"].diff()
    for i in np.flatnonzero(step > pd.Timedelta(hours=1)):
        gaps.append({"symbol": sym, "gap_after_bar": df["open_time"].iloc[i - 1].isoformat(),
                     "next_bar": df["open_time"].iloc[i].isoformat(),
                     "missing_bars": int(step.iloc[i] / pd.Timedelta(hours=1)) - 1})
    # outliers
    ret = df["close"].pct_change()
    rng = (df["high"] - df["low"]) / df["open"]
    med_rng = rng.rolling(24 * 30, min_periods=24 * 7).median().shift(1)
    flag = (ret.abs() > RET_FLAG) | (rng > RANGE_MULT * med_rng)
    outl = df.loc[flag, ["open_time", "open", "high", "low", "close", "volume"]].copy()
    outl["close_to_close_ret"] = ret[flag].round(4)
    outl["range_vs_30d_median"] = (rng / med_rng)[flag].round(1)
    outl.insert(0, "symbol", sym)
    outl["open_time"] = outl["open_time"].map(lambda t: t.isoformat())
    st["outlier_bars_flagged"] = len(outl)
    st["daily_file_bars"] = int((df["source"] == "daily").sum())
    keep = ["open_time", "open", "high", "low", "close", "volume", "quote_volume", "trades",
            "taker_buy_volume", "taker_buy_quote_volume", "source"]
    out = df[keep].copy()
    out["open_time"] = out["open_time"].astype("datetime64[ms, UTC]")
    path = CLEAN / "klines_1h" / f"{sym}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(path, index=False)
    return {"stats": st, "gaps": gaps, "outliers": outl.to_dict("records")}


def clean_funding(sym: str) -> dict:
    files = sorted(glob.glob(str(UM / "monthly" / "fundingRate" / sym / "*.zip")))
    st: dict = {"symbol": sym, "files": len(files)}
    if not files:
        st["rows"] = 0
        return {"stats": st}
    df = pd.concat([read_zip_csv(Path(p), ["calc_time", "funding_interval_hours",
                                           "last_funding_rate"]) for p in files], ignore_index=True)
    st["raw_rows"] = len(df)
    df["calc_time"] = _ts(df["calc_time"])
    df = df.sort_values("calc_time")
    st["duplicate_rows_dropped"] = int(df.duplicated("calc_time").sum())
    df = df.drop_duplicates("calc_time", keep="last")
    df = df[df["calc_time"] >= pd.Timestamp(WINDOW_START, tz="UTC")]
    df["funding_time"] = df["calc_time"].dt.round("1min")
    df["funding_rate"] = pd.to_numeric(df["last_funding_rate"], errors="coerce")
    df["funding_interval_hours"] = pd.to_numeric(df["funding_interval_hours"], errors="coerce")
    st["rows"] = len(df)
    st["first"] = df["funding_time"].min().isoformat()
    st["last"] = df["funding_time"].max().isoformat()
    st["nan_rates"] = int(df["funding_rate"].isna().sum())
    st["interval_hours_seen"] = "/".join(str(int(x)) for x in sorted(df["funding_interval_hours"].dropna().unique()))
    # a gap longer than the interval in force for the event that ends it = missed settlements
    gap_h = df["funding_time"].diff() / pd.Timedelta(hours=1)
    miss = ((gap_h / df["funding_interval_hours"]).round() - 1).clip(lower=0)
    st["missing_funding_events"] = int(miss.sum())
    st["missing_funding_detail"] = ";".join(
        f"{t:%Y-%m-%d %H:%M}(+{int(m)})" for t, m in zip(df["funding_time"][miss > 0], miss[miss > 0]))
    st["abs_rate_ge_0p5pct"] = int((df["funding_rate"].abs() >= 0.005).sum())
    st["min_rate"] = float(df["funding_rate"].min())
    st["max_rate"] = float(df["funding_rate"].max())
    out = df[["calc_time", "funding_time", "funding_interval_hours", "funding_rate"]]
    path = CLEAN / "funding" / f"{sym}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(path, index=False)
    return {"stats": st}


def clean_metrics(sym: str) -> dict:
    files = sorted(glob.glob(str(UM / "daily" / "metrics" / sym / "*.zip")))
    st: dict = {"symbol": sym, "files": len(files)}
    if not files:
        st["rows"] = 0
        return {"stats": st, "missing_days": []}
    frames = []
    for p in files:
        d = read_zip_csv(Path(p), ["create_time", "symbol"] + list(METRIC_RENAME))
        frames.append(d)
    df = pd.concat(frames, ignore_index=True)
    st["raw_rows"] = len(df)
    df["create_time"] = pd.to_datetime(df["create_time"], utc=True, errors="coerce")
    df = df.drop(columns=["symbol"]).rename(columns=METRIC_RENAME)
    for c in METRIC_RENAME.values():
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    df = df.dropna(subset=["create_time"]).sort_values("create_time")
    st["duplicate_rows_dropped"] = int(df.duplicated("create_time").sum())
    df = df.drop_duplicates("create_time", keep="last")
    df = df[df["create_time"] >= pd.Timestamp(WINDOW_START, tz="UTC")]
    st["rows"] = len(df)
    st["first"] = df["create_time"].min().isoformat()
    st["last"] = df["create_time"].max().isoformat()
    st["off_grid_rows"] = int((df["create_time"].dt.minute % 5 != 0).sum())
    for c in METRIC_RENAME.values():
        st[f"nan_{c}"] = int(df[c].isna().sum())
    st["zero_open_interest_rows"] = int((df["open_interest"] == 0).sum())
    oi_chg = df["open_interest"].pct_change().abs()
    st["oi_5m_jumps_gt_50pct"] = int((oi_chg > 0.5).sum())
    # coverage: days in [first day, last day] whose file exists, and rows per day
    days = pd.to_datetime([os.path.basename(p)[-14:-4] for p in files]).date
    have = set(days)
    full = pd.date_range(min(days), max(days), freq="D").date
    # only months we downloaded count (universe symbols get selected months by design)
    months = {d.strftime("%Y-%m") for d in have}
    missing = [d.isoformat() for d in full if d not in have and d.strftime("%Y-%m") in months]
    st["days_with_file"] = len(have)
    st["days_missing_inside_span"] = len(missing)
    per_day = df.groupby(df["create_time"].dt.date).size()
    st["days_with_lt_288_rows"] = int((per_day < 288).sum())
    st["expected_5m_rows_on_file_days"] = 288 * len(have)
    out = df[["create_time"] + list(METRIC_RENAME.values())]
    path = CLEAN / "metrics_5m" / f"{sym}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(path, index=False)
    return {"stats": st, "missing_days": [{"symbol": sym, "date": d} for d in missing]}


def _run(kind_sym: tuple[str, str]) -> tuple[str, dict]:
    kind, sym = kind_sym
    fn = {"klines": clean_klines, "funding": clean_funding, "metrics": clean_metrics}[kind]
    return kind, fn(sym)


def main(kinds: tuple[str, ...] = ("klines", "funding", "metrics")) -> None:
    from .download import research_sets

    sets = research_sets()
    research = sorted(set(sets["ever_universe"]) | set(sets["memes"]) | {"BTCUSDT"})
    QUALITY.mkdir(parents=True, exist_ok=True)
    jobs = [(k, s) for k in kinds for s in research]
    results: dict[str, list[dict]] = {k: [] for k in kinds}
    with ProcessPoolExecutor(max_workers=min(8, os.cpu_count() or 2)) as ex:
        for kind, res in ex.map(_run, jobs, chunksize=4):
            results[kind].append(res)
    if "klines" in kinds:
        r = [x for x in results["klines"] if "stats" in x]
        pd.DataFrame([x["stats"] for x in r]).to_csv(QUALITY / "klines_1h_summary.csv", index=False)
        pd.DataFrame([g for x in r for g in x["gaps"]],
                     columns=["symbol", "gap_after_bar", "next_bar", "missing_bars"]
                     ).to_csv(QUALITY / "klines_1h_gaps.csv", index=False)
        pd.DataFrame([o for x in r for o in x["outliers"]]).to_csv(
            QUALITY / "klines_1h_outliers_flagged.csv", index=False)
    if "funding" in kinds:
        pd.DataFrame([x["stats"] for x in results["funding"]]).to_csv(
            QUALITY / "funding_summary.csv", index=False)
    if "metrics" in kinds:
        pd.DataFrame([x["stats"] for x in results["metrics"]]).to_csv(
            QUALITY / "metrics_summary.csv", index=False)
        pd.DataFrame([m for x in results["metrics"] for m in x["missing_days"]],
                     columns=["symbol", "date"]).to_csv(QUALITY / "metrics_missing_days.csv",
                                                        index=False)
    print(f"cleaned {len(research)} symbols for {kinds} -> {CLEAN}")


if __name__ == "__main__":
    import sys

    main(tuple(sys.argv[1:]) or ("klines", "funding", "metrics"))
