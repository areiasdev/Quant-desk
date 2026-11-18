"""Step 3: classify every USDT symbol (crypto / stablecoin / index / tradfi) and flag memes.

Inputs : data/symbols_all.csv, data/clean/screen_1d_all_symbols.parquet
Output : data/symbol_classes.csv  (one row per symbol, with the evidence used)
    symbol, first_month, last_month, first_bar, last_trade_day, status, static_class,
    weekend_days, weekend_range_ratio, weekend_volume_ratio, symbol_class, class_source,
    is_meme, meme_note
first_bar      : first 1d bar in the screen window (>= 2022-10-01; older listings start earlier)
last_trade_day : last 1d bar with trades > 0 (the archive keeps writing flat zero-trade "ghost"
                 bars after a delisting, so the last FILE date is not the delisting date)
status         : "delisted" if last_trade_day is before the last screened day, else "trading"
                 (as of the last complete month; current-month 1h data refines this for the
                 research symbols in DATA_QUALITY.md / meme_universe.md)
"""
from __future__ import annotations

import pandas as pd

from .config import CLEAN, DATA
from .symbols import (FINGERPRINT_FROM, MEME, MEME_BORDERLINE_EXCLUDED, fingerprint_is_tradfi,
                      static_class)

OUT = DATA / "symbol_classes.csv"


def weekend_fingerprint(screen: pd.DataFrame) -> pd.DataFrame:
    d = screen[screen["date"] >= FINGERPRINT_FROM].copy()
    d["rng"] = (d["high"] - d["low"]) / d["open"]
    d["wkend"] = d["date"].dt.dayofweek >= 5
    g = d.groupby(["symbol", "wkend"]).agg(rng=("rng", "median"), qv=("quote_volume", "median"),
                                           n=("rng", "size")).unstack()
    out = pd.DataFrame(index=g.index)
    out["weekend_days"] = g[("n", True)].fillna(0)
    out["weekend_range_ratio"] = g[("rng", True)] / g[("rng", False)]
    out["weekend_volume_ratio"] = g[("qv", True)] / g[("qv", False)].where(g[("qv", False)] > 0)
    return out


def main() -> pd.DataFrame:
    syms = pd.read_csv(DATA / "symbols_all.csv", dtype=str, keep_default_na=False)
    syms = syms[~syms["symbol"].str.contains("_")][["symbol", "first_month", "last_month",
                                                     "last_daily_file"]]
    screen = pd.read_parquet(CLEAN / "screen_1d_all_symbols.parquet")
    span = screen.groupby("symbol")["date"].agg(["min"])
    span["last_trade"] = screen[screen["trades"] > 0].groupby("symbol")["date"].max()
    last_screen_day = screen["date"].max()
    fp = weekend_fingerprint(screen)
    df = syms.set_index("symbol").join(fp).join(span)
    df["first_bar"] = df["min"].dt.date.astype("string")
    df["last_trade_day"] = df["last_trade"].dt.date.astype("string")
    df["status"] = (df["last_trade"] < last_screen_day).map({True: "delisted", False: "trading"})
    df.loc[df["last_trade"].isna() & (df["last_daily_file"] != ""), "status"] = "new_this_month"
    df.loc[df["last_trade"].isna() & (df["last_daily_file"] == ""), "status"] = "no_trades_in_window"
    df["static_class"] = [static_class(s) for s in df.index]
    cls, src = [], []
    for s, r in df.iterrows():
        if r["static_class"] != "crypto":
            cls.append(r["static_class"]); src.append("list")
        elif fingerprint_is_tradfi(r["first_month"] or (r["last_daily_file"] or "")[:7],
                                   r["weekend_days"] if pd.notna(r["weekend_days"]) else 0,
                                   r["weekend_volume_ratio"] if pd.notna(r["weekend_volume_ratio"]) else 9):
            cls.append("tradfi"); src.append("fingerprint")
        else:
            cls.append("crypto"); src.append("default")
    df["symbol_class"] = cls
    df["class_source"] = src
    df["is_meme"] = [s in MEME and c == "crypto" for s, c in zip(df.index, df["symbol_class"])]
    df["meme_note"] = [MEME_BORDERLINE_EXCLUDED.get(s, "") for s in df.index]
    cols = ["first_month", "last_month", "first_bar", "last_trade_day", "status", "static_class",
            "weekend_days",
            "weekend_range_ratio", "weekend_volume_ratio", "symbol_class", "class_source",
            "is_meme", "meme_note"]
    df = df[cols].round(4)
    df.index.name = "symbol"
    df.to_csv(OUT)
    print(df.groupby(["symbol_class", "class_source"]).size().to_string())
    print(f"memes: {int(df['is_meme'].sum())}; listed-meme symbols missing from archive:",
          sorted(MEME - set(df.index)))
    print(f"wrote {OUT}")
    return df


if __name__ == "__main__":
    main()
