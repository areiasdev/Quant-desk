"""Point-in-time 1h research panel: klines + funding + open-interest metrics, no look-ahead.

Timing conventions (all UTC). A 1h bar is [open_time, open_time + 1h). A signal computed on a
bar may only use what was known at that bar's CLOSE; the order goes in at the NEXT bar's open.

* klines      : the bar itself (it is closed at open_time + 1h).
* funding     : the last settled rate with calc_time <= bar close (close_time = open + 1h - 1 ms).
                A rate settled at 08:00:00.003 therefore first appears on the 08:00-09:00 bar,
                i.e. it is usable for an order at 09:00. (Conservative: the rate is fixed at the
                settlement instant, and the archive has no predicted-rate history.)
* metrics     : the last 5-minute row with create_time <= open_time + 55 min, and only if that
                row lies inside the bar (create_time >= open_time); otherwise NaN. The 5-minute
                row stamped exactly at the bar close is NOT used, which is safe whether a row's
                stamp marks the start or the end of its 5-minute period. Gaps stay NaN (never
                forward-filled across a missing period).
"""
from __future__ import annotations

import pandas as pd

from .config import CLEAN

BAR = pd.Timedelta(hours=1)


def load_klines(sym: str) -> pd.DataFrame:
    df = pd.read_parquet(CLEAN / "klines_1h" / f"{sym}.parquet")
    df["close_time"] = df["open_time"] + BAR - pd.Timedelta(milliseconds=1)
    return df


def load_funding(sym: str) -> pd.DataFrame:
    p = CLEAN / "funding" / f"{sym}.parquet"
    return pd.read_parquet(p) if p.exists() else pd.DataFrame(
        columns=["calc_time", "funding_time", "funding_interval_hours", "funding_rate"])


def load_metrics_5m(sym: str) -> pd.DataFrame:
    p = CLEAN / "metrics_5m" / f"{sym}.parquet"
    return pd.read_parquet(p) if p.exists() else pd.DataFrame(columns=["create_time"])


def _unit_ms(s: pd.Series) -> pd.Series:
    return s.astype("datetime64[ms, UTC]")


def metrics_asof_1h(bars: pd.DataFrame, metrics: pd.DataFrame) -> pd.DataFrame:
    if metrics.empty:
        return pd.DataFrame(index=bars.index)
    left = pd.DataFrame({"key": _unit_ms(bars["open_time"] + pd.Timedelta(minutes=55))})
    right = metrics.rename(columns={"create_time": "metrics_time"}).copy()
    right["key"] = _unit_ms(right["metrics_time"])
    right["metrics_time"] = right["key"]
    m = pd.merge_asof(left.sort_values("key"), right.sort_values("key"), on="key",
                      direction="backward", tolerance=pd.Timedelta(minutes=55))
    m.index = bars.index
    return m.drop(columns=["key"])


def funding_asof_1h(bars: pd.DataFrame, funding: pd.DataFrame) -> pd.DataFrame:
    if funding.empty:
        return pd.DataFrame(index=bars.index, columns=["funding_rate", "funding_time",
                                                       "funding_interval_hours"])
    left = pd.DataFrame({"key": _unit_ms(bars["close_time"])})
    right = funding[["calc_time", "funding_time", "funding_interval_hours", "funding_rate"]].copy()
    right["key"] = _unit_ms(right["calc_time"])
    m = pd.merge_asof(left.sort_values("key"), right.sort_values("key"), on="key",
                      direction="backward")
    m.index = bars.index
    return m.drop(columns=["key", "calc_time"])


def load_panel(sym: str) -> pd.DataFrame:
    """One row per 1h bar with the funding and metrics that were known at the bar's close."""
    k = load_klines(sym).reset_index(drop=True)
    f = funding_asof_1h(k, load_funding(sym))
    m = metrics_asof_1h(k, load_metrics_5m(sym))
    out = pd.concat([k, f, m], axis=1)
    out["symbol"] = sym
    return out


def resample_4h(panel: pd.DataFrame) -> pd.DataFrame:
    """4h bars anchored at 00/04/08/12/16/20 UTC from complete groups of four 1h bars only."""
    p = panel.set_index("open_time")
    g = p.resample("4h", origin="epoch", label="left", closed="left")
    out = pd.DataFrame({
        "open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
        "close": g["close"].last(), "volume": g["volume"].sum(),
        "quote_volume": g["quote_volume"].sum(), "trades": g["trades"].sum(),
        "taker_buy_quote_volume": g["taker_buy_quote_volume"].sum(), "n_1h": g["close"].count(),
    })
    for c in ("funding_rate", "open_interest", "open_interest_value_usdt",
              "top_trader_position_ls_ratio", "global_account_ls_ratio"):
        if c in p:
            out[c] = g[c].last()
    out = out[out["n_1h"] == 4].reset_index()  # incomplete 4h bars (gaps) are dropped
    return out
