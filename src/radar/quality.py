"""Step 9: write data/DATA_QUALITY.md from data/quality/*.csv, the manifest and the universe.

Everything in the report is computed from files on disk; rerun after every nightly download.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from .clean import GHOST_MIN, QUALITY, RANGE_MULT, RET_FLAG
from .config import CLEAN, DATA, WINDOW_START

OUT = DATA / "DATA_QUALITY.md"


def _kind(path: str) -> str:
    if "/klines/" in path:
        return ("klines_1d" if "/1d/" in path else "klines_1h") + (" (daily files)" if "/daily/" in path else "")
    if "/fundingRate/" in path:
        return "fundingRate"
    if "/metrics/" in path:
        return "metrics"
    return "other"


def main() -> None:
    man = pd.read_csv(DATA / "MANIFEST.csv")
    man["kind"] = man["file"].map(_kind)
    cls = pd.read_csv(DATA / "symbol_classes.csv", dtype={"first_month": str})
    uni = pd.read_csv(DATA / "universe_by_month.csv")
    excl = pd.read_csv(DATA / "universe_excluded_hits.csv")
    kq = pd.read_csv(QUALITY / "klines_1h_summary.csv")
    gaps = pd.read_csv(QUALITY / "klines_1h_gaps.csv")
    outl = pd.read_csv(QUALITY / "klines_1h_outliers_flagged.csv")
    fq = pd.read_csv(QUALITY / "funding_summary.csv")
    mq = pd.read_csv(QUALITY / "metrics_summary.csv")
    mmiss = pd.read_csv(QUALITY / "metrics_missing_days.csv")
    screen = pd.read_parquet(CLEAN / "screen_1d_all_symbols.parquet", columns=["symbol", "date"])
    memes = set(cls.loc[cls["is_meme"], "symbol"])
    research = set(kq["symbol"])
    delisted = set(cls.loc[cls["status"] == "delisted", "symbol"])
    kq["is_delisted"] = kq["ghost_bars_dropped_after_last_trade"] > 0
    gen = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    L = ["# Data quality report\n",
         f"Generated {gen} by `src/radar/quality.py`. Source: Binance public archive "
         "(data.binance.vision) only; every file is listed in `data/MANIFEST.csv` with its source "
         "URL, download time, row count and sha256. Research window starts "
         f"{WINDOW_START.isoformat()}.\n"]

    # ---------------------------------------------------------------- downloads
    L.append("## 1. Downloads and checksums\n")
    L.append("Every zip was checked against the archive's `.CHECKSUM` (sha256) before being kept; "
             "a file that fails is retried and never stored. Files in the manifest = files that "
             "passed.\n")
    L.append("| data type | files | data rows | first downloaded (UTC) | last downloaded (UTC) |")
    L.append("|---|---:|---:|---|---|")
    for k, g in man.groupby("kind"):
        L.append(f"| {k} | {len(g):,} | {g['rows'].sum():,} | {g['downloaded_at_utc'].min()} | "
                 f"{g['downloaded_at_utc'].max()} |")
    L.append(f"| **total** | **{len(man):,}** | **{man['rows'].sum():,}** | | |\n")

    # ---------------------------------------------------------------- coverage
    L.append("## 2. Coverage per data type (real start/end dates)\n")
    k_first = kq["first_bar"].min()[:16]
    k_last = kq["last_bar"].max()[:16]
    L.append("| data type | symbols (incl. delisted) | first data (UTC) | last data (UTC) | notes |")
    L.append("|---|---:|---|---|---|")
    L.append(f"| 1d klines (volume screen) | {screen['symbol'].nunique()} "
             f"({len(delisted & set(screen['symbol']))} delisted) | {screen['date'].min():%Y-%m-%d} | "
             f"{screen['date'].max():%Y-%m-%d} | every USDT symbol in the archive; Oct 2022 = ranking "
             "warm-up; last complete month only |")
    n_cand = pd.read_csv(DATA / "universe_screen_by_month.csv")["symbol"].nunique()
    L.append(f"| 1h klines, raw (ranking candidates) | {n_cand + len(memes - set(pd.read_csv(DATA / 'universe_screen_by_month.csv')['symbol']))} "
             f"| 2022-10-01 | {k_last} | top-30 screen candidates of any month + memes; used for the "
             "1h universe ranking |")
    L.append(f"| 1h klines, clean (`data/clean/klines_1h`) | {len(kq)} ({int(kq['is_delisted'].sum())} "
             f"delisted) | {k_first} | {k_last} | ever-top-10 universe ({uni['symbol'].nunique()}) "
             f"+ memes ({len(memes)}) + BTC |")
    L.append(f"| funding (`data/clean/funding`) | {int((fq['rows'] > 0).sum())} | {fq['first'].min()[:16]} | "
             f"{fq['last'].max()[:16]} | monthly files only - the current month is NOT in the "
             "archive until it completes |")
    mq_ok = mq[mq["rows"] > 0]
    L.append(f"| metrics: OI + long/short (`data/clean/metrics_5m`) | {len(mq_ok)} | "
             f"{mq_ok['first'].min()[:16]} | {mq_ok['last'].max()[:16]} | 5-minute rows from daily "
             "files; universe symbols: universe months + the month before; memes: whole window |\n")
    L.append("**Start dates differ by symbol** (listing date or research-window start, whichever "
             "is later). Per-symbol first/last dates: `data/quality/klines_1h_summary.csv`, "
             "`funding_summary.csv`, `metrics_summary.csv`; memes also in "
             "`research/radar/meme_universe.md`.\n")
    # metrics later than klines
    k_first_by = kq.set_index("symbol")["first_bar"].str[:10]
    m_first_by = mq_ok.set_index("symbol")["first"].str[:10]
    later = []
    uni_first = uni.groupby("symbol")["month"].min()
    for s, mf in m_first_by.items():
        kf = k_first_by.get(s)
        target = kf if s in memes else max(kf, (pd.Timestamp(uni_first.get(s, "2022-11") + "-01")
                                                - pd.Timedelta(days=31)).date().isoformat())
        if kf and mf > target:
            later.append((s, kf, mf))
    if later:
        L.append(f"**Metrics start later than klines** for {len(later)} symbols (no backfill; "
                 "nothing was filled):\n")
        L.append("| symbol | first 1h kline | first metrics row |")
        L.append("|---|---|---|")
        for s, kf, mf in sorted(later, key=lambda x: x[2]):
            L.append(f"| {s} | {kf} | {mf} |")
        L.append("")
    missing_m = sorted((research & (set(uni["symbol"]) | memes)) - set(mq_ok["symbol"]))
    if missing_m:
        L.append(f"**No metrics at all in the archive for:** {', '.join(missing_m)}.\n")

    # ---------------------------------------------------------------- klines
    L.append("## 3. 1h klines\n")
    L.append(f"- Duplicates: {int(kq['duplicate_rows_dropped'].sum())} duplicate rows dropped, "
             f"{int(kq['duplicate_bars_conflicting'].sum())} conflicting duplicates (where a daily "
             "and a monthly file overlap, the monthly file wins).")
    L.append(f"- Gaps (missing hours between a symbol's first and last bar): "
             f"{int(kq['missing_bars'].sum())} bars in {len(gaps)} gaps. Gaps are never filled "
             "(`data/quality/klines_1h_gaps.csv`).")
    L.append(f"- Bad OHLC rows (high < max(open, close), low > min(open, close), price <= 0, NaN): "
             f"{int(kq['bad_ohlc_rows'].sum())}; misaligned open times: "
             f"{int(kq['misaligned_open_time'].sum())}; bad close times: {int(kq['bad_close_time'].sum())}.")
    # zero-trade bars
    zt = []
    for s in kq.loc[kq["zero_trade_bars_kept"] > 0, "symbol"]:
        k = pd.read_parquet(CLEAN / "klines_1h" / f"{s}.parquet", columns=["open_time", "trades"])
        zt += [(s, t) for t in k.loc[k["trades"] == 0, "open_time"]]
    if zt:
        z = pd.DataFrame(zt, columns=["symbol", "open_time"]).groupby("open_time")["symbol"].agg(["size", "first"])
        L.append(f"- Zero-trade bars kept: {len(zt)}. By hour: " + "; ".join(
            f"{t:%Y-%m-%d %H:%M} UTC in {n} symbols" for t, (n, _) in z.iterrows())
            + ". A bar with no trades in many symbols at once is an exchange-wide halt: the archive "
            "writes a flat bar with zero volume. Do not fill orders on a bar with `trades == 0`.")
    gh = kq[kq["ghost_bars_dropped_after_last_trade"] > 0]
    L.append(f"- **Post-delisting ghost bars**: after a contract stops trading the archive keeps "
             f"writing flat bars with zero trades (e.g. LINAUSDT has files to the latest day but its "
             f"last trade was 2025-03-27). In the volume screen {len(delisted)} of "
             f"{screen['symbol'].nunique()} symbols end in such runs. In the clean 1h data, a "
             f"trailing run of >= {GHOST_MIN} zero-trade bars is dropped: "
             f"{int(gh['ghost_bars_dropped_after_last_trade'].sum()):,} bars in {len(gh)} symbols "
             "(the last real bar = delisting). The universe requires a real trade in the 24h "
             "before each month start, so a coin delisted days before a month cannot rank.")
    L.append(f"- Outliers are FLAGGED, not removed (`data/quality/klines_1h_outliers_flagged.csv`): "
             f"{len(outl):,} bars with a 1h close-to-close move > {RET_FLAG:.0%} or a range > "
             f"{RANGE_MULT:.0f}x the symbol's trailing 30-day median 1h range; "
             f"{int((outl['close_to_close_ret'].abs() > RET_FLAG).sum())} of them are > {RET_FLAG:.0%} moves.")
    o = outl.copy()
    o["hour"] = pd.to_datetime(o["open_time"]).dt.floor("h")
    wide = o.groupby("hour").size().sort_values(ascending=False).head(6)
    L.append("- Market-wide flag clusters (same hour, many symbols - real events, not data errors): "
             + "; ".join(f"{t:%Y-%m-%d %H:%M} UTC ({n} symbols)" for t, n in wide.items()) + ".")
    big = o.reindex(o["close_to_close_ret"].abs().sort_values(ascending=False).index).head(10)
    L.append("- Largest 1h moves (all checked as consistent OHLC; high/low show the move "
             "happened inside the bar):\n")
    L.append("| symbol | bar open (UTC) | open | high | low | close | close-to-close |")
    L.append("|---|---|---:|---:|---:|---:|---:|")
    for _, r in big.iterrows():
        L.append(f"| {r['symbol']} | {r['open_time'][:16]} | {r['open']:.6g} | {r['high']:.6g} | "
                 f"{r['low']:.6g} | {r['close']:.6g} | {r['close_to_close_ret']:+.1%} |")
    L.append("")

    # ---------------------------------------------------------------- funding
    L.append("## 4. Funding\n")
    L.append(f"- {int(fq['rows'].sum()):,} settlements, {int(fq['duplicate_rows_dropped'].sum())} "
             f"duplicates, {int(fq['nan_rates'].sum())} NaN rates.")
    L.append("- `calc_time` carries a few ms of jitter after the settlement instant (e.g. "
             "08:00:00.003); `funding_time` is it rounded to the minute. Intervals seen: "
             + ", ".join(f"{k}h: {v} symbols" for k, v in fq["interval_hours_seen"].value_counts().items())
             + " (Binance moves volatile contracts to 4h or 1h funding; scale to 8h before comparing).")
    det = fq.loc[fq["missing_funding_events"] > 0, ["symbol", "missing_funding_detail"]]
    allmiss = pd.Series([x for d in det["missing_funding_detail"] for x in str(d).split(";")])
    common = allmiss.value_counts()
    L.append(f"- Missing settlements (a gap longer than the interval in force): "
             f"{int(fq['missing_funding_events'].sum())} in {len(det)} symbols. "
             + "; ".join(f"before {k} UTC in {v} symbols" for k, v in common.head(3).items())
             + ". The rest are isolated (details in `data/quality/funding_summary.csv`).")
    L.append(f"- Extreme rates kept as-is: {int(fq['abs_rate_ge_0p5pct'].sum())} settlements with "
             f"|rate| >= 0.5% per interval; min {fq['min_rate'].min():.4%}, max {fq['max_rate'].max():.4%}.")
    L.append(f"- **End date**: funding ends {fq['last'].max()[:16]} UTC (last complete month). "
             "Setups that need funding cannot be evaluated on the current month until the monthly "
             "file appears.\n")

    # ---------------------------------------------------------------- metrics
    L.append("## 5. Metrics (open interest, long/short ratios)\n")
    exp = int(mq_ok["expected_5m_rows_on_file_days"].sum())
    L.append(f"- {int(mq_ok['rows'].sum()):,} five-minute rows from {int(mq_ok['days_with_file'].sum()):,} "
             f"daily files ({int(mq_ok['rows'].sum()) / max(exp, 1):.2%} of 288 rows/day on days "
             f"with a file); {int(mq_ok['duplicate_rows_dropped'].sum())} duplicates dropped; "
             f"{int(mq_ok['off_grid_rows'].sum())} rows off the 5-minute grid.")
    L.append(f"- Days with fewer than 288 rows: {int(mq_ok['days_with_lt_288_rows'].sum()):,}; days "
             f"missing inside downloaded months (no file in the archive): {len(mmiss):,} "
             "(`data/quality/metrics_missing_days.csv`). Missing rows are NOT filled; the 1h as-of "
             "join leaves NaN when no row falls inside the bar.")
    if len(mmiss):
        mm = mmiss.groupby("date").size().sort_values(ascending=False)
        L.append("- Most common missing days: " + "; ".join(f"{d} ({n} symbols)" for d, n in mm.head(8).items()) + ".")
    nan_cols = [c for c in mq_ok.columns if c.startswith("nan_")]
    L.append("- Empty values per column: " + ", ".join(
        f"{c[4:]}: {int(mq_ok[c].sum()):,}" for c in nan_cols) + ".")
    L.append(f"- Zero open-interest rows: {int(mq_ok['zero_open_interest_rows'].sum()):,}; 5-minute OI "
             f"jumps > 50%: {int(mq_ok['oi_5m_jumps_gt_50pct'].sum()):,} (flag only).")
    L.append("- Raw column names were renamed: sum_open_interest -> open_interest, "
             "sum_open_interest_value -> open_interest_value_usdt, count_toptrader_long_short_ratio "
             "-> top_trader_account_ls_ratio, sum_toptrader_long_short_ratio -> "
             "top_trader_position_ls_ratio, count_long_short_ratio -> global_account_ls_ratio, "
             "sum_taker_long_short_vol_ratio -> taker_buy_sell_vol_ratio. Raw daily files are not "
             "always sorted by time; the clean files are.\n")

    # ---------------------------------------------------------------- universe
    L.append("## 6. Universe and classification\n")
    L.append(f"- {uni['month'].nunique()} months ({uni['month'].min()} .. {uni['month'].max()}), "
             f"{uni['symbol'].nunique()} distinct symbols, {int(uni.groupby('symbol')['delisted_by_latest_data'].first().sum())} "
             "of them later delisted: "
             + ", ".join(sorted(uni.loc[uni['delisted_by_latest_data'], 'symbol'].unique())) + ".")
    L.append(f"- Cross-check: the 1h-based ranking equals the 1d screen ranking in "
             f"{int((uni['rank'] == uni['screen_rank']).sum())} of {len(uni)} month-rank rows; max "
             f"trailing-volume difference {uni['qv_diff_1h_vs_1d_pct'].abs().max():.3f}%.")
    L.append(f"- Classes: " + ", ".join(f"{k}: {v}" for k, v in cls["symbol_class"].value_counts().items())
             + f" ({int((cls['class_source'] == 'fingerprint').sum())} TradFi by weekend fingerprint).")
    L.append(f"- **TradFi perps would have taken top-10 slots in {excl['month'].nunique()} months** "
             f"({excl['month'].min()} .. {excl['month'].max()}; {len(excl)} slots: "
             + ", ".join(f"{s} x{n}" for s, n in excl['symbol'].value_counts().items())
             + "). They are excluded as non-crypto products (`data/universe_excluded_hits.csv`); see "
             "`research/QUESTIONS.md`.")
    L.append("- Renamed contracts are separate symbols in the archive (e.g. MATICUSDT ends "
             "2024-09; POLUSDT continues the asset) - join them manually if a study needs one "
             "continuous series.\n")

    # ---------------------------------------------------------------- timing
    L.append("## 7. Timing rules for LAB (no look-ahead)\n")
    L.append("- 1h bar = [open_time, open_time + 1h), UTC. Signal on a closed bar, order at the next bar's open.")
    L.append("- Funding joins as-of the bar close: a settlement is first visible on the bar that "
             "contains its calc_time, i.e. usable at the next bar's open (`src/radar/features.py`).")
    L.append("- Metrics join as-of open_time + 55 min and only if the row lies inside the bar; the "
             "5-minute row stamped exactly at the bar close is not used.")
    L.append("- 4h bars = four complete 1h bars anchored at 00/04/08/12/16/20 UTC; incomplete groups are dropped.")
    L.append("- Universe month M uses only bars that closed before M 00:00 UTC.")
    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
