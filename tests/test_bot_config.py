"""config.yaml, setup registry, PAPER-ONLY guarantees."""
import copy
import inspect
import json
import re
from pathlib import Path

import pytest

from bot import REPO, config
from bot.config import ConfigError
from bot.strategy import SUPPORTED, Strategy, validate_setup


def raw():
    return copy.deepcopy(config.raw())


def test_config_loads_and_matches_lab_and_top_setup():
    from lab.data import MIN_NOTIONAL_DEFAULT_EST, MIN_NOTIONAL_EST
    from lab.engine import BASE, slip_bp

    c = config.load()
    top = json.loads((REPO / "research/lab/top_setup.json").read_text())
    assert c.mode == "paper" and c.setup_id == top["id"] == "S16"
    assert c.params == top["params"]
    assert c.risk_per_trade == top["sizing"]["risk_per_trade"] == 0.01
    assert c.starting_equity_eur == 100 and c.starting_equity_usdt == 110
    assert (c.taker_fee, c.maker_fee) == (BASE.taker, BASE.maker)
    for sym, meme in (("BTCUSDT", False), ("ETHUSDT", False), ("SOLUSDT", False),
                      ("DOGEUSDT", True)):
        assert c.slip_bp(sym, meme) == slip_bp(sym, meme)
    assert c.min_notional_est("BTCUSDT") == MIN_NOTIONAL_EST["BTCUSDT"]
    assert c.min_notional_est("ETHUSDT") == MIN_NOTIONAL_EST["ETHUSDT"]
    assert c.min_notional_est("SOLUSDT") == MIN_NOTIONAL_DEFAULT_EST
    sh = c.shield
    assert (sh.daily_loss_halt, sh.consecutive_losses_halt, sh.stale_bars, sh.fetch_failures_halt,
            sh.max_leverage, sh.max_open_positions) == (0.03, 3, 2, 3, 3.0, 3)
    b, o = c.backtest_reference, top["backtest"]["oos"]
    assert b["oos_trades"] == o["trades"]
    assert abs(b["oos_exp_R"] - o["exp_R"]) < 5e-4
    assert abs(b["oos_win_rate"] - o["win_rate"]) < 5e-4
    assert abs(b["oos_year2_exp_R"] - top["backtest"]["oos_by_year"]["2025-11..2026-10"]["exp_R"]) \
        < 5e-4


@pytest.mark.parametrize("path,value", [
    (("shield", "max_open_positions"), 4), (("shield", "max_leverage"), 5.0),
    (("shield", "daily_loss_halt"), 0.05), (("shield", "consecutive_losses_halt"), 4),
    (("shield", "stale_bars"), 5), (("shield", "fetch_failures_halt"), 10),
    (("risk", "risk_per_trade"), 0.02), (("costs", "taker_fee"), 0.0004),
])
def test_config_cannot_loosen_owner_limits(path, value):
    d = raw()
    d[path[0]][path[1]] = value
    with pytest.raises(ConfigError):
        config.from_dict(d)


def test_config_can_tighten():
    d = raw()
    d["shield"]["max_open_positions"] = 2
    d["shield"]["daily_loss_halt"] = 0.02
    c = config.from_dict(d)
    assert c.shield.max_open_positions == 2 and c.shield.daily_loss_halt == 0.02


def test_config_rejects_unknown_missing_and_bad_setups():
    d = raw()
    d["shield"]["typo"] = 1
    with pytest.raises(ConfigError, match="unknown"):
        config.from_dict(d)
    d = raw()
    del d["risk"]
    with pytest.raises(ConfigError, match="missing"):
        config.from_dict(d)
    for sid in ("S03", "S26", "M01", "XYZ"):
        d = raw()
        d["setup"] = {"id": sid, "params": {}}
        with pytest.raises(ConfigError):
            config.from_dict(d)
    d = raw()
    d["setup"]["params"]["nope"] = 1
    with pytest.raises(ConfigError, match="unknown keys"):
        config.from_dict(d)


def test_swapping_setup_is_id_plus_params():
    d = raw()
    d["setup"] = {"id": "S02", "params": {"n": 20}}
    c = config.from_dict(d)
    assert c.setup_id == "S02" and c.params == {"n": 20, "vol": 1.5, "exit_n": 10}
    assert Strategy("S02", {"n": 20}).tf == "4h"


def test_supported_setups_need_only_klines_and_funding():
    from lab import setups

    bad = ("open_interest", "top_trader", "global_account", "hype_", "corr30d", "ctx.", "_btc",
           "_flush", "_cross", "days_since")
    for sid in SUPPORTED:
        st = setups.SETUPS[sid]
        assert st.kind == "core" and not st.cross
        src = inspect.getsource(st.fn)
        assert not any(b in src for b in bad), sid
        validate_setup(sid, {})


def test_live_mode_is_a_stub_that_refuses(tmp_path):
    from bot.live import LiveBroker
    from bot.runner import Bot

    with pytest.raises(NotImplementedError, match="Real money needs the owner's 'go live'"):
        LiveBroker()
    d = raw()
    d["mode"] = "live"
    c = config.from_dict(d).with_paths(tmp_path)
    with pytest.raises(NotImplementedError, match="Real money needs the owner's 'go live'"):
        Bot(c, source=None)
    assert not (tmp_path / "state.json").exists()


def test_no_code_path_can_send_orders_or_use_keys():
    pat = re.compile(r"\.post\(|\.put\(|\.delete\(|/fapi/v1/order|/fapi/v1/batchOrders|"
                     r"X-MBX-APIKEY|['\"]signature['\"]|signature=|import hmac|"
                     r"/fapi/v1/leverage|os\.environ\[|getenv\(")
    for p in sorted((REPO / "bot").glob("*.py")):
        hits = [m.group(0) for m in pat.finditer(p.read_text(encoding="utf-8"))]
        assert not hits, (p.name, hits)
    from bot.datasource import ENDPOINTS
    assert set(ENDPOINTS) == {"/fapi/v1/exchangeInfo", "/fapi/v1/klines",
                              "/fapi/v1/premiumIndex", "/fapi/v1/fundingRate"}


def test_api_key_env_vars_are_warned_about_by_name_only(caplog):
    from bot.datasource import warn_if_api_keys

    env = {"BINANCE_API_KEY": "secret-value-123", "PATH": "/bin", "MY_API_SECRET": "x"}
    with caplog.at_level("WARNING", logger="bot"):
        names = warn_if_api_keys(env)
    assert names == ["BINANCE_API_KEY", "MY_API_SECRET"]
    assert "secret-value-123" not in caplog.text and "IGNORED" in caplog.text


def test_requirements_pinned_and_minimal():
    lines = [x.strip() for x in (REPO / "requirements.txt").read_text().splitlines() if x.strip()]
    assert sorted(x.split("==")[0] for x in lines) == ["numpy", "pandas", "pyyaml", "requests"]
    assert all("==" in x for x in lines)
