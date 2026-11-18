"""Meme-coin HYPE PROXIES built only from exchange data (no social/news archive exists for free).

Every proxy is computed per 1h bar from data known at that bar's close (see features.py):

hype_volume_z30d     : z-score of the last 24h quote volume against the 30 previous daily
                       (24h) volumes taken at the same hour - how unusual today's trading is.
hype_oi_surge_7d     : open-interest value now / its median over the previous 7 days - 1 - how
                       much more money sits in open positions than in a normal recent week.
hype_funding_jump_bp : latest settled funding (scaled to 8h) minus its mean over the previous
                       7 days, in basis points - how much more longs suddenly pay to stay long.
hype_move_vs_btc_24h : the coin's 24h return minus BTC's 24h return - how far it ran ahead of
                       (or fell behind) the market.
days_since_listing   : days from the symbol's first Binance-futures bar to the bar close - how
                       fresh the listing is.
Output: data/clean/meme_hype_1h.parquet (all memes, all bars) and a latest snapshot.
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from .config import CLEAN
from .features import load_panel

H = 24


def add_hype(panel: pd.DataFrame, btc_close: pd.Series, listing_ts: pd.Timestamp) -> pd.DataFrame:
    p = panel.copy()
    t = p["open_time"]
    qv24 = p["quote_volume"].rolling(H, min_periods=H).sum()
    # 30 previous daily volumes at the same hour: qv24 shifted by 1..30 days
    hist = np.column_stack([qv24.shift(H * k).to_numpy() for k in range(1, 31)])
    with np.errstate(invalid="ignore", divide="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN windows (new listings) -> NaN
        n = np.sum(~np.isnan(hist), axis=1)
        mu = np.nanmean(np.where(n[:, None] >= 20, hist, np.nan), axis=1)
        sd = np.nanstd(np.where(n[:, None] >= 20, hist, np.nan), axis=1, ddof=1)
        p["hype_volume_z30d"] = (qv24.to_numpy() - mu) / np.where(sd > 0, sd, np.nan)
    if "open_interest_value_usdt" in p:
        oi = p["open_interest_value_usdt"]
        base = oi.shift(1).rolling(7 * H, min_periods=3 * H).median()
        p["hype_oi_surge_7d"] = oi / base - 1
    else:
        p["hype_oi_surge_7d"] = np.nan
    f8 = p["funding_rate"].astype("float64") * 8 / p["funding_interval_hours"].astype("float64")
    # Work on the settlement events (the as-of series repeats each rate on every bar): jump =
    # this settlement minus the mean of the settlements in the 7 days before it (>= 5 of them).
    settle = p["funding_time"].notna() & p["funding_time"].ne(p["funding_time"].shift())
    ev = pd.DataFrame({"t": p.loc[settle, "funding_time"], "f8": f8[settle]}).set_index("t")
    prev_mean = ev["f8"].shift(1).rolling("7D", min_periods=5).mean()
    jump = ((ev["f8"] - prev_mean) * 1e4).to_numpy()
    idx = settle.cumsum().to_numpy() - 1           # index of the latest settlement per bar
    p["hype_funding_jump_bp"] = np.where(idx >= 0, jump[np.clip(idx, 0, None)], np.nan) \
        if len(jump) else np.nan
    ret24 = p["close"] / p["close"].shift(H) - 1  # positional: gaps make this > 24h, see note
    btc = btc_close.reindex(t)
    btc_ret24 = (btc / btc.shift(H) - 1).to_numpy()
    gap_ok = (t - t.shift(H)) == pd.Timedelta(hours=H)
    p["hype_move_vs_btc_24h"] = (ret24 - btc_ret24).where(gap_ok)
    p["days_since_listing"] = ((t + pd.Timedelta(hours=1)) - listing_ts) / pd.Timedelta(days=1)
    return p


def build(memes: list[str], listing: dict[str, pd.Timestamp]) -> pd.DataFrame:
    btc = load_panel("BTCUSDT").set_index("open_time")["close"]
    btc = btc[~btc.index.duplicated()]
    cols = ["symbol", "open_time", "close", "quote_volume", "funding_rate",
            "open_interest_value_usdt", "hype_volume_z30d", "hype_oi_surge_7d",
            "hype_funding_jump_bp", "hype_move_vs_btc_24h", "days_since_listing"]
    frames = []
    for s in memes:
        if not (CLEAN / "klines_1h" / f"{s}.parquet").exists():
            continue
        p = add_hype(load_panel(s), btc, listing[s])
        for c in cols:
            if c not in p:
                p[c] = np.nan
        frames.append(p[cols])
    out = pd.concat(frames, ignore_index=True)
    out.to_parquet(CLEAN / "meme_hype_1h.parquet", index=False)
    return out
