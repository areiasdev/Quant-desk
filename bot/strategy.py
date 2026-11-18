"""Setup registry for the paper bot: LAB's setups.py, unchanged.

The bot calls exactly the same signal function the backtest used (`lab.setups.SETUPS[id].fn`)
on bars built exactly the way LAB builds them (`bot.market`). Only setups whose inputs the bot
can get from the four public endpoints it is allowed to use (klines, premiumIndex,
fundingRate, exchangeInfo) are supported: per-symbol "core" setups that need neither open
interest / long-short metrics, nor hype features, nor BTC or cross-sectional data.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd

from lab.setups import ATR, SETUPS, SMA

# Checked by inspection of src/lab/setups.py (see tests/test_bot_config.py, which re-checks
# that none of these read open interest, metrics, hype, BTC or ctx).
SUPPORTED = {"S01", "S02", "S04", "S05", "S06", "S07", "S08", "S13", "S14", "S15", "S16",
             "S17", "S22", "S23", "S24", "S25"}


def validate_setup(setup_id: str, params: dict) -> dict:
    from .config import ConfigError

    if setup_id not in SETUPS:
        raise ConfigError(f"setup.id '{setup_id}' is not in LAB's setups.py")
    if setup_id not in SUPPORTED:
        raise ConfigError(f"setup.id '{setup_id}' needs data the paper bot cannot fetch "
                          f"(open interest, hype, BTC or cross-sectional); supported: "
                          f"{sorted(SUPPORTED)}")
    st = SETUPS[setup_id]
    unknown = set(params) - set(st.defaults)
    if unknown:
        raise ConfigError(f"setup.params has unknown keys for {setup_id}: {sorted(unknown)}; "
                          f"allowed: {sorted(st.defaults)}")
    return dict(st.defaults, **params)


class Strategy:
    def __init__(self, setup_id: str, params: dict):
        self.id = setup_id
        self.setup = SETUPS[setup_id]
        self.params = validate_setup(setup_id, params)
        self.tf = self.setup.tf
        self.tf_hours = 1 if self.tf == "1h" else 4

    def evaluate(self, symbol: str, h1: pd.DataFrame, d: pd.DataFrame):
        """(signals, exit_long, exit_short) from the LAB setup function, on the bot's bars."""
        sym = SimpleNamespace(name=symbol, h1=h1, h4=d if self.tf == "4h" else None,
                              is_meme=False, delisted=False)
        sig, xl, xs = self.setup.fn(d, self.params, sym, None)
        n = len(d)
        xl = np.zeros(n, bool) if xl is None else np.asarray(xl, bool)
        xs = np.zeros(n, bool) if xs is None else np.asarray(xs, bool)
        sig = sig[(sig["idx"] >= 0) & (sig["idx"] < n)].sort_values("idx", kind="stable")
        return sig, xl, xs

    def explain(self, d: pd.DataFrame, j: int, has_signal: bool) -> tuple[dict, str]:
        """Inputs at bar j and a plain-language reason (for decisions.jsonl)."""
        row = d.iloc[j]
        inputs = {"open": float(row["open"]), "high": float(row["high"]),
                  "low": float(row["low"]), "close": float(row["close"]),
                  "trades": int(row["trades"]),
                  "F8": None if pd.isna(row.get("F8")) else float(row["F8"])}
        if self.id != "S16":
            return inputs, "signal" if has_signal else "no signal"
        p = self.params
        if getattr(self, "_ind_d", None) is not d:     # indicators of the whole frame, once
            self._ind = (SMA(d["close"], p["fast"]).to_numpy(), SMA(d["close"], 200).to_numpy(),
                         ATR(d, 14).to_numpy())
            self._ind_d = d
        f, s, atr = (x[j] for x in self._ind)
        inputs.update(sma_fast=_f(f), sma_slow=_f(s), atr14=_f(atr),
                      settle_max_F8=_f(row.get("settle_max")), fast=p["fast"], atr_mult=p["atr"],
                      fexit=p["fexit"])
        F8, close = inputs["F8"], inputs["close"]
        if has_signal:
            return inputs, "signal: close > SMA_fast > SMA_slow and F8 < 0 (first in this leg)"
        if any(v is None for v in (inputs["sma_fast"], inputs["sma_slow"], F8)):
            return inputs, "no signal: an input is missing (NaN)"
        if close <= f:
            return inputs, "no signal: close is not above SMA_fast"
        if f <= s:
            return inputs, "no signal: SMA_fast is not above SMA_slow (no 4h uptrend)"
        if F8 >= 0:
            return inputs, "no signal: funding F8 is not negative"
        if inputs["trades"] == 0:
            return inputs, "no signal: zero-trade bar"
        return inputs, ("no signal: conditions true but this uptrend leg already gave its signal "
                        "(re-arms after a 4h close below SMA_fast)")


def _f(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if not np.isfinite(x) else x
