"""LAB data layer: point-in-time 1h/4h panels for every research symbol, clipped to the backtest
window, plus symbol metadata (universe membership, meme flag, delisting).

Built on RADAR's `radar.features.load_panel` (funding as-of bar close, metrics as-of open+55m).
The backtest window ends 2026-10-31 23:59 UTC (last bar opens 23:00) so funding is real
throughout (owner decision; funding archive ends 2026-10-31 20:00).

Cache: data/clean/lab_cache/ (gitignored with the rest of data/clean). Rebuild with
`python -m lab.data` from src/.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from radar.config import CLEAN, DATA  # noqa: E402
from radar.features import load_funding, load_panel  # noqa: E402

CACHE = CLEAN / "lab_cache"
WINDOW_START = pd.Timestamp("2022-11-01 00:00", tz="UTC")
WINDOW_END_BAR = pd.Timestamp("2026-10-31 23:00", tz="UTC")   # last 1h bar OPEN in the window
WINDOW_END = pd.Timestamp("2026-11-01 00:00", tz="UTC")       # exclusive end instant
IS_START, IS_END = WINDOW_START, pd.Timestamp("2024-11-01", tz="UTC")
OOS_START, OOS_END = IS_END, WINDOW_END

KEEP = ["open_time", "open", "high", "low", "close", "volume", "quote_volume", "trades",
        "funding_rate", "funding_time", "funding_interval_hours", "open_interest",
        "open_interest_value_usdt", "top_trader_position_ls_ratio", "global_account_ls_ratio"]
HYPE = ["hype_volume_z30d", "hype_oi_surge_7d", "hype_funding_jump_bp", "hype_move_vs_btc_24h",
        "days_since_listing"]

# ESTIMATE values (exchangeInfo is blocked from this box, HTTP 451): owner-supplied.
MIN_NOTIONAL_EST = {"BTCUSDT": 100.0, "ETHUSDT": 20.0}
MIN_NOTIONAL_DEFAULT_EST = 5.0
DELIST_HAIRCUT_EST = 0.02


def min_notional(sym: str) -> float:
    return MIN_NOTIONAL_EST.get(sym, MIN_NOTIONAL_DEFAULT_EST)


def research_symbols() -> list[str]:
    return sorted(p[:-8] for p in os.listdir(CLEAN / "klines_1h") if p.endswith(".parquet"))


def build_cache() -> None:
    (CACHE / "1h").mkdir(parents=True, exist_ok=True)
    (CACHE / "funding").mkdir(parents=True, exist_ok=True)
    hype = pd.read_parquet(CLEAN / "meme_hype_1h.parquet", columns=["symbol", "open_time"] + HYPE)
    for sym in research_symbols():
        p = load_panel(sym)
        for c in KEEP:
            if c not in p:
                p[c] = np.nan
        p = p[KEEP]
        p = p[(p["open_time"] >= WINDOW_START) & (p["open_time"] <= WINDOW_END_BAR)].copy()
        h = hype[hype["symbol"] == sym].drop(columns="symbol")
        if len(h):
            p = p.merge(h, on="open_time", how="left")
        else:
            for c in HYPE:
                p[c] = np.nan
        p["open_time"] = p["open_time"].astype("datetime64[ms, UTC]")
        p["funding_time"] = p["funding_time"].astype("datetime64[ms, UTC]")
        p.reset_index(drop=True).to_parquet(CACHE / "1h" / f"{sym}.parquet", index=False)
        f = load_funding(sym)
        if len(f):
            f = f[(f["funding_time"] >= WINDOW_START) & (f["funding_time"] < WINDOW_END)]
        f.to_parquet(CACHE / "funding" / f"{sym}.parquet", index=False)
        print("cached", sym, len(p), flush=True)


def resample_4h(p: pd.DataFrame) -> pd.DataFrame:
    """4h bars from four complete 1h bars (00/04/..20 UTC). Funding/metrics/hype: value known at
    the 4h close = the last 1h bar's as-of value."""
    g = p.groupby(p["open_time"].dt.floor("4h"))
    out = pd.DataFrame({
        "open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
        "close": g["close"].last(), "volume": g["volume"].sum(),
        "quote_volume": g["quote_volume"].sum(), "trades": g["trades"].sum(),
        "n_1h": g["close"].count()})
    last_cols = ["funding_rate", "funding_time", "funding_interval_hours", "open_interest",
                 "open_interest_value_usdt", "top_trader_position_ls_ratio",
                 "global_account_ls_ratio", "qv24", "corr30d"] + HYPE
    for c in last_cols + ["F8"]:
        if c in p:
            out[c] = g[c].last()
    if "settleF8" in p:
        out["settle_min"] = g["settleF8"].min()
        out["settle_max"] = g["settleF8"].max()
    out = out[out["n_1h"] == 4].rename_axis("open_time").reset_index()
    return out


def add_derived(h1: pd.DataFrame, btc_lr: pd.Series) -> pd.DataFrame:
    """Columns every setup may use, all known at the bar close: qv24 (last-24h quote volume),
    corr30d (Pearson of 720 1h log returns vs BTC), F8 (latest settled funding scaled to 8h),
    new_settle (a new settlement first visible on this bar), settleF8 (F8 on that bar)."""
    h1["qv24"] = h1["quote_volume"].rolling(24, min_periods=24).sum()
    lr = np.log(h1.set_index("open_time")["close"]).diff()
    b = btc_lr.reindex(lr.index)
    h1["corr30d"] = lr.rolling(720, min_periods=720).corr(b).to_numpy()
    h1["F8"] = h1["funding_rate"] * 8 / h1["funding_interval_hours"]
    ft = h1["funding_time"]
    h1["new_settle"] = ft.notna() & ft.ne(ft.shift())
    h1.loc[h1.index[:1], "new_settle"] = False
    h1["settleF8"] = h1["F8"].where(h1["new_settle"])
    return h1


@dataclass
class Sym:
    name: str
    h1: pd.DataFrame
    h4: pd.DataFrame
    fund_t: np.ndarray          # settlement instants (int64 ns, funding_time)
    fund_r: np.ndarray          # settlement rates
    delisted: bool
    is_meme: bool
    last_close: float = field(default=np.nan)


@dataclass
class Ctx:
    syms: dict[str, Sym]
    universe: dict[str, set[str]]       # 'YYYY-MM' -> symbols
    memes: list[str]
    core: list[str]                     # every symbol ever in the universe

    def tf(self, sym: str, tf: str) -> pd.DataFrame:
        return self.syms[sym].h1 if tf == "1h" else self.syms[sym].h4


def load_ctx(symbols: list[str] | None = None) -> Ctx:
    if not (CACHE / "1h").exists():
        build_cache()
    cls = pd.read_csv(DATA / "symbol_classes.csv").set_index("symbol")
    uni = pd.read_csv(DATA / "universe_by_month.csv")
    universe = uni.groupby("month")["symbol"].apply(set).to_dict()
    syms = symbols or research_symbols()
    btc = pd.read_parquet(CACHE / "1h" / "BTCUSDT.parquet")
    btc_lr = np.log(btc.set_index("open_time")["close"]).diff()
    out = {}
    for s in syms:
        h1 = pd.read_parquet(CACHE / "1h" / f"{s}.parquet")
        add_derived(h1, btc_lr)
        f = pd.read_parquet(CACHE / "funding" / f"{s}.parquet")
        st = str(cls.loc[s, "status"]) if s in cls.index else "trading"
        out[s] = Sym(
            name=s, h1=h1, h4=resample_4h(h1),
            fund_t=f["funding_time"].astype("datetime64[ns, UTC]").astype("int64").to_numpy()
            if len(f) else np.array([], dtype=np.int64),
            fund_r=f["funding_rate"].to_numpy(dtype=float) if len(f) else np.array([]),
            delisted=(st == "delisted") and h1["open_time"].iloc[-1] < WINDOW_END_BAR,
            is_meme=bool(cls.loc[s, "is_meme"]) if s in cls.index else False,
            last_close=float(h1["close"].iloc[-1]))
    memes = [s for s in syms if out[s].is_meme]
    core = sorted(set(uni["symbol"]) & set(syms))
    return Ctx(syms=out, universe=universe, memes=memes, core=core)


if __name__ == "__main__":
    build_cache()
