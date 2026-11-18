"""config.yaml loader and validator.

The owner edits only config.yaml. Validation is strict: an unknown key, a missing key or a value
outside the allowed range stops the bot with a plain message. Shield limits in config.yaml can
only be made STRICTER than the owner's limits (daily loss 3%, 3 losses in a row, 3x leverage,
3 open positions, stale feed after 2 bars, 3 fetch failures); costs can only be made more
pessimistic than LAB's. Tests that need LAB-equivalent (unlimited) settings build a Config in
Python with `strict=False`; config.yaml can never do that.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import REPO

DEFAULT_PATH = REPO / "config.yaml"

# Owner hard limits (RULES.md / FORGE brief). config.yaml may tighten, never loosen.
HARD = dict(risk_per_trade=0.01, daily_loss_halt=0.03, consecutive_losses_halt=3,
            stale_bars=2, fetch_failures_halt=3, max_leverage=3.0, max_open_positions=3)
LAB_COSTS = dict(taker_fee=0.0005, maker_fee=0.0002,
                 slippage_bp=dict(majors=1.0, other=3.0, meme=10.0))


class ConfigError(ValueError):
    pass


@dataclass
class Shield:
    daily_loss_halt: float = 0.03
    consecutive_losses_halt: int = 3
    stale_bars: int = 2
    fetch_failures_halt: int = 3
    max_leverage: float = 3.0
    max_open_positions: int = 3


@dataclass
class Config:
    mode: str = "paper"
    setup_id: str = "S16"
    params: dict = field(default_factory=dict)
    starting_equity_eur: float = 100.0
    eur_usd_estimate: float = 1.10
    starting_equity_usdt: float = 110.0
    risk_per_trade: float = 0.01
    taker_fee: float = 0.0005
    maker_fee: float = 0.0002
    slippage_bp: dict = field(default_factory=lambda: dict(LAB_COSTS["slippage_bp"]))
    majors: list = field(default_factory=lambda: ["BTCUSDT", "ETHUSDT"])
    min_notional_estimate: dict = field(default_factory=lambda: {
        "BTCUSDT": 100.0, "ETHUSDT": 20.0, "default": 5.0})
    shield: Shield = field(default_factory=Shield)
    universe_size: int = 10
    universe_candidates: int = 30
    universe_trailing_days: int = 30
    wake_after_close_seconds: float = 30.0
    jitter_seconds: float = 30.0
    max_fill_delay_minutes: float = 15.0
    retry_cycle_seconds: float = 300.0
    history_1h_bars: int = 3000
    max_weight_per_minute: int = 1200
    http_timeout_seconds: float = 15.0
    http_retries: int = 5
    base_url: str = "https://fapi.binance.com"
    logs_dir: Path = REPO / "logs"
    state_file: Path = REPO / "state.json"
    halt_file: Path = REPO / "HALT"
    monitoring: dict = field(default_factory=dict)
    backtest_reference: dict = field(default_factory=dict)
    source_path: Path | None = None

    def min_notional_est(self, sym: str) -> float:
        return float(self.min_notional_estimate.get(sym, self.min_notional_estimate["default"]))

    def slip_bp(self, sym: str, is_meme: bool) -> float:
        if sym in self.majors:
            return float(self.slippage_bp["majors"])
        return float(self.slippage_bp["meme" if is_meme else "other"])

    def with_paths(self, root: Path) -> "Config":
        c = copy.deepcopy(self)
        root = Path(root)
        c.logs_dir, c.state_file, c.halt_file = root / "logs", root / "state.json", root / "HALT"
        return c


_SCHEMA = {
    "mode": str,
    "setup": {"id": str, "params": dict},
    "account": {"starting_equity_eur": (int, float), "eur_usd_ESTIMATE": (int, float),
                "starting_equity_usdt": (int, float)},
    "risk": {"risk_per_trade": (int, float)},
    "costs": {"taker_fee": (int, float), "maker_fee": (int, float), "slippage_bp": dict,
              "majors": list},
    "exchange": {"min_notional_usdt_ESTIMATE": dict},
    "shield": {k: (int, float) for k in ("daily_loss_halt", "consecutive_losses_halt",
                                         "stale_bars", "fetch_failures_halt", "max_leverage",
                                         "max_open_positions")},
    "universe": {"size": int, "candidates": int, "trailing_days": int},
    "polling": {"wake_after_close_seconds": (int, float), "jitter_seconds": (int, float),
                "max_fill_delay_minutes": (int, float), "retry_cycle_seconds": (int, float),
                "history_1h_bars": int, "max_weight_per_minute": int,
                "http_timeout_seconds": (int, float), "http_retries": int},
    "paths": {"logs_dir": str, "state_file": str, "halt_file": str},
    "monitoring": dict,
    "backtest_reference": dict,
}


def _check(d: dict, schema: dict, where: str) -> None:
    if not isinstance(d, dict):
        raise ConfigError(f"{where or 'config.yaml'} must be a section (key: value lines)")
    for k in d:
        if k not in schema:
            raise ConfigError(f"unknown setting '{where}{k}' in config.yaml")
    for k, t in schema.items():
        if k not in d:
            raise ConfigError(f"missing setting '{where}{k}' in config.yaml")
        if isinstance(t, dict):
            _check(d[k], t, f"{where}{k}.")
        elif not isinstance(d[k], t) or isinstance(d[k], bool):
            raise ConfigError(f"setting '{where}{k}' has the wrong type: {d[k]!r}")


def _rng(name, v, lo, hi):
    if not (lo <= v <= hi):
        raise ConfigError(f"setting '{name}' = {v} is outside the allowed range [{lo}, {hi}]")


def from_dict(d: dict, *, strict: bool = True, base_dir: Path = REPO,
              source_path: Path | None = None) -> Config:
    from .strategy import validate_setup  # late import: strategy imports LAB code

    _check(d, _SCHEMA, "")
    if d["mode"] not in ("paper", "live"):
        raise ConfigError("mode must be 'paper' (or 'live', which is a stub that stops the bot)")
    params = validate_setup(d["setup"]["id"], d["setup"]["params"])
    a, c, s, u, p = d["account"], d["costs"], d["shield"], d["universe"], d["polling"]
    slip = c["slippage_bp"]
    if set(slip) != {"majors", "other", "meme"}:
        raise ConfigError("costs.slippage_bp needs exactly: majors, other, meme")
    mn = {str(k): float(v) for k, v in d["exchange"]["min_notional_usdt_ESTIMATE"].items()}
    if "default" not in mn:
        raise ConfigError("exchange.min_notional_usdt_ESTIMATE needs a 'default' entry")
    sh = Shield(daily_loss_halt=float(s["daily_loss_halt"]),
                consecutive_losses_halt=int(s["consecutive_losses_halt"]),
                stale_bars=int(s["stale_bars"]), fetch_failures_halt=int(s["fetch_failures_halt"]),
                max_leverage=float(s["max_leverage"]),
                max_open_positions=int(s["max_open_positions"]))
    eq_usdt = float(a["starting_equity_usdt"])
    if abs(float(a["starting_equity_eur"]) * float(a["eur_usd_ESTIMATE"]) - eq_usdt) > 0.01:
        raise ConfigError("account.starting_equity_usdt must equal starting_equity_eur x "
                          "eur_usd_ESTIMATE")
    if strict:
        _rng("risk.risk_per_trade", float(d["risk"]["risk_per_trade"]), 0.0001,
             HARD["risk_per_trade"])
        _rng("shield.daily_loss_halt", sh.daily_loss_halt, 0.001, HARD["daily_loss_halt"])
        _rng("shield.consecutive_losses_halt", sh.consecutive_losses_halt, 1,
             HARD["consecutive_losses_halt"])
        _rng("shield.stale_bars", sh.stale_bars, 1, HARD["stale_bars"])
        _rng("shield.fetch_failures_halt", sh.fetch_failures_halt, 1, HARD["fetch_failures_halt"])
        _rng("shield.max_leverage", sh.max_leverage, 0.1, HARD["max_leverage"])
        _rng("shield.max_open_positions", sh.max_open_positions, 1, HARD["max_open_positions"])
        _rng("costs.taker_fee", float(c["taker_fee"]), LAB_COSTS["taker_fee"], 0.01)
        _rng("costs.maker_fee", float(c["maker_fee"]), LAB_COSTS["maker_fee"], 0.01)
        for k, v in LAB_COSTS["slippage_bp"].items():
            _rng(f"costs.slippage_bp.{k}", float(slip[k]), v, 100.0)
        _rng("account.starting_equity_usdt", eq_usdt, 1.0, 1000.0)
        _rng("polling.max_weight_per_minute", int(p["max_weight_per_minute"]), 10, 1800)
        _rng("polling.max_fill_delay_minutes", float(p["max_fill_delay_minutes"]), 1, 30)
        for k, v in mn.items():
            _rng(f"exchange.min_notional_usdt_ESTIMATE.{k}", v, 0.01, 10000)
    _rng("polling.history_1h_bars", int(p["history_1h_bars"]), 1000, 6000)
    _rng("polling.wake_after_close_seconds", float(p["wake_after_close_seconds"]), 1, 600)
    _rng("polling.jitter_seconds", float(p["jitter_seconds"]), 0, 600)

    def path(x):
        q = Path(x)
        return q if q.is_absolute() else base_dir / q

    return Config(
        mode=d["mode"], setup_id=d["setup"]["id"], params=params,
        starting_equity_eur=float(a["starting_equity_eur"]),
        eur_usd_estimate=float(a["eur_usd_ESTIMATE"]), starting_equity_usdt=eq_usdt,
        risk_per_trade=float(d["risk"]["risk_per_trade"]), taker_fee=float(c["taker_fee"]),
        maker_fee=float(c["maker_fee"]), slippage_bp={k: float(v) for k, v in slip.items()},
        majors=[str(x) for x in c["majors"]], min_notional_estimate=mn, shield=sh,
        universe_size=int(u["size"]), universe_candidates=int(u["candidates"]),
        universe_trailing_days=int(u["trailing_days"]),
        wake_after_close_seconds=float(p["wake_after_close_seconds"]),
        jitter_seconds=float(p["jitter_seconds"]),
        max_fill_delay_minutes=float(p["max_fill_delay_minutes"]),
        retry_cycle_seconds=float(p["retry_cycle_seconds"]),
        history_1h_bars=int(p["history_1h_bars"]),
        max_weight_per_minute=int(p["max_weight_per_minute"]),
        http_timeout_seconds=float(p["http_timeout_seconds"]), http_retries=int(p["http_retries"]),
        logs_dir=path(d["paths"]["logs_dir"]), state_file=path(d["paths"]["state_file"]),
        halt_file=path(d["paths"]["halt_file"]), monitoring=dict(d["monitoring"]),
        backtest_reference=dict(d["backtest_reference"]), source_path=source_path)


def load(path: Path | str | None = None, *, strict: bool = True) -> Config:
    p = Path(path) if path else DEFAULT_PATH
    if not p.exists():
        raise ConfigError(f"config file not found: {p}")
    with open(p, encoding="utf-8") as f:
        d = yaml.safe_load(f)
    return from_dict(d, strict=strict, base_dir=p.resolve().parent, source_path=p)


def raw(path: Path | str | None = None) -> dict:
    with open(Path(path) if path else DEFAULT_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)
