"""Step 10: rank the latest month's universe by trend, momentum, volume, funding, OI, volatility.

Every number is computed from the cleaned archive data up to the latest closed 1h bar; nothing is
estimated. Output: research/radar/ranking_latest.md (regenerated nightly).

Factor definitions (1h bars; "d" = 24 bars):
  trend      close / SMA(20d of 1h closes) - 1, plus the SMA's own change over the last 5d
  momentum   7d and 30d close-to-close return (ranked on 30d)
  volume     7d quote volume vs the 30d before it, as a daily-average ratio (ranked on ratio)
  funding    mean funding over the last 30 days of settlements, as % per 8h (ranked low->high:
             rank 1 = least crowded longs); the archive has monthly funding files only
  OI         open-interest value change over the last 7d (ranked high->low)
  volatility realised volatility of 1h log returns over the last 7d, annualised (ranked
             high->low)
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from .config import DATA, RESEARCH
from .features import load_panel

D = 24
OUT = RESEARCH / "ranking_latest.md"


def factors(sym: str) -> dict:
    p = load_panel(sym)
    p = p[p["trades"] > 0].reset_index(drop=True)
    last = p.iloc[-1]
    c = p["close"]
    sma = c.rolling(20 * D).mean()
    qv = p["quote_volume"]
    lr = np.log(c).diff()
    f = p.dropna(subset=["funding_rate"])
    f_events = f.drop_duplicates("funding_time")
    cutoff_f = f_events["funding_time"].max() - pd.Timedelta(days=30) if len(f_events) else None
    f30 = f_events[f_events["funding_time"] > cutoff_f] if cutoff_f is not None else f_events
    f8 = f30["funding_rate"] * 8 / f30["funding_interval_hours"]
    oi = p.dropna(subset=["open_interest_value_usdt"]) if "open_interest_value_usdt" in p else p.iloc[0:0]
    oi_now = oi.iloc[-1] if len(oi) else None
    oi_then = oi[oi["open_time"] <= last["open_time"] - pd.Timedelta(days=7)]
    return {
        "symbol": sym,
        "last_bar_open_utc": last["open_time"],
        "close": last["close"],
        "trend_pct_vs_sma20d": 100 * (c.iloc[-1] / sma.iloc[-1] - 1),
        "sma20d_change_5d_pct": 100 * (sma.iloc[-1] / sma.iloc[-1 - 5 * D] - 1),
        "ret_7d_pct": 100 * (c.iloc[-1] / c.iloc[-1 - 7 * D] - 1),
        "ret_30d_pct": 100 * (c.iloc[-1] / c.iloc[-1 - 30 * D] - 1),
        "qv_7d_musdt": qv.iloc[-7 * D:].sum() / 1e6,
        "qv_ratio_7d_vs_prior30d": (qv.iloc[-7 * D:].sum() / 7) / (qv.iloc[-37 * D:-7 * D].sum() / 30),
        "funding_mean30d_pct_8h": 100 * f8.mean() if len(f8) else np.nan,
        "funding_last_pct_8h": 100 * f8.iloc[-1] if len(f8) else np.nan,
        "funding_last_time": f_events["funding_time"].max() if len(f_events) else pd.NaT,
        "funding_interval_h": "/".join(str(int(x)) for x in sorted(f30["funding_interval_hours"].unique())) if len(f30) else "",
        "oi_value_musdt": oi_now["open_interest_value_usdt"] / 1e6 if oi_now is not None else np.nan,
        "oi_change_7d_pct": 100 * (oi_now["open_interest_value_usdt"] / oi_then.iloc[-1]["open_interest_value_usdt"] - 1)
        if oi_now is not None and len(oi_then) else np.nan,
        "oi_last_time": oi_now["metrics_time"] if oi_now is not None else pd.NaT,
        "rvol_7d_ann_pct": 100 * lr.iloc[-7 * D:].std() * np.sqrt(24 * 365),
    }


def _fmt(x, nd=2):
    if isinstance(x, (float, np.floating)):
        return "n/a" if np.isnan(x) else f"{x:,.{nd}f}"
    return str(x)


def main() -> pd.DataFrame:
    uni = pd.read_csv(DATA / "universe_by_month.csv")
    month = uni["month"].max()
    syms = list(uni.loc[uni["month"] == month].sort_values("rank")["symbol"])
    vol_rank = dict(zip(uni.loc[uni["month"] == month, "symbol"], uni.loc[uni["month"] == month, "rank"]))
    df = pd.DataFrame([factors(s) for s in syms])
    df["universe_rank"] = df["symbol"].map(vol_rank)
    ranks = pd.DataFrame({"symbol": df["symbol"]})
    ranks["trend"] = df["trend_pct_vs_sma20d"].rank(ascending=False).astype(int)
    ranks["momentum"] = df["ret_30d_pct"].rank(ascending=False).astype(int)
    ranks["volume"] = df["qv_ratio_7d_vs_prior30d"].rank(ascending=False).astype(int)
    ranks["funding"] = df["funding_mean30d_pct_8h"].rank(ascending=True).astype("Int64")
    ranks["open_interest"] = df["oi_change_7d_pct"].rank(ascending=False).astype("Int64")
    ranks["volatility"] = df["rvol_7d_ann_pct"].rank(ascending=False).astype(int)

    kl_ts = df["last_bar_open_utc"].max()
    f_ts = df["funding_last_time"].max()
    oi_ts = df["oi_last_time"].max()
    gen = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    L = []
    L.append(f"# Universe ranking - {month}\n")
    L.append(f"Generated {gen} by `src/radar/ranking.py` from data.binance.vision archive files "
             "(see `data/MANIFEST.csv`). Descriptive only: nothing here is a backtest or a signal.\n")
    L.append("**Data timestamps (UTC)**\n")
    L.append(f"- 1h klines: last closed bar opened {kl_ts:%Y-%m-%d %H:%M} (closed {kl_ts + pd.Timedelta(hours=1):%Y-%m-%d %H:%M}).")
    L.append(f"- Open interest (metrics, 5-minute): last row used {oi_ts:%Y-%m-%d %H:%M}.")
    L.append(f"- Funding: last settlement {f_ts:%Y-%m-%d %H:%M}. The archive publishes funding only as "
             "monthly files, so the current month is NOT available yet; the funding column is "
             "stale by the gap shown and describes the previous month.\n")
    L.append(f"Universe = top-10 USDT-M crypto perps by trailing-30-day quote volume at "
             f"{month}-01 00:00 UTC (`data/universe_by_month.csv`).\n")
    L.append("## Ranks (1 = highest value, except funding: 1 = lowest/least crowded)\n")
    L.append("| vol rank | symbol | trend | momentum | volume | funding | open interest | volatility |")
    L.append("|---:|---|---:|---:|---:|---:|---:|---:|")
    for i, r in ranks.iterrows():
        L.append(f"| {df.loc[i, 'universe_rank']} | {r['symbol']} | {r['trend']} | {r['momentum']} | "
                 f"{r['volume']} | {r['funding'] if pd.notna(r['funding']) else 'n/a'} | "
                 f"{r['open_interest'] if pd.notna(r['open_interest']) else 'n/a'} | {r['volatility']} |")
    L.append("\n## Underlying values\n")
    L.append("| symbol | close | trend % vs SMA20d | SMA20d 5d chg % | ret 7d % | ret 30d % | "
             "quote vol 7d (M USDT) | vol ratio 7d/30d | funding mean 30d (%/8h) | funding last (%/8h) | "
             "funding interval h | OI value (M USDT) | OI chg 7d % | realised vol 7d (ann. %) |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for _, r in df.iterrows():
        L.append(f"| {r['symbol']} | {r['close']:.6g} | {_fmt(r['trend_pct_vs_sma20d'])} | "
                 f"{_fmt(r['sma20d_change_5d_pct'])} | {_fmt(r['ret_7d_pct'])} | {_fmt(r['ret_30d_pct'])} | "
                 f"{_fmt(r['qv_7d_musdt'], 0)} | {_fmt(r['qv_ratio_7d_vs_prior30d'])} | "
                 f"{_fmt(r['funding_mean30d_pct_8h'], 4)} | {_fmt(r['funding_last_pct_8h'], 4)} | "
                 f"{r['funding_interval_h']} | {_fmt(r['oi_value_musdt'], 0)} | {_fmt(r['oi_change_7d_pct'])} | "
                 f"{_fmt(r['rvol_7d_ann_pct'], 1)} |")
    L.append("\n## Definitions\n")
    L.append("- **trend**: last close vs the simple average of the last 20 days of 1h closes; the "
             "second column is how much that average itself moved in 5 days.")
    L.append("- **momentum**: close-to-close return over 7 and 30 days (ranked on 30 days).")
    L.append("- **volume**: average daily quote volume of the last 7 days divided by that of the 30 "
             "days before them (>1 = activity rising).")
    L.append("- **funding**: average funding rate of the last 30 days of settlements, scaled to an "
             "8h interval; positive = longs pay shorts.")
    L.append("- **open interest**: change in open-interest value (USDT) versus 7 days earlier.")
    L.append("- **volatility**: standard deviation of 1h log returns over 7 days x sqrt(8760).")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
    return df


if __name__ == "__main__":
    main()
