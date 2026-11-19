"""Second paper bot: S02, OBSERVATION ONLY (not approved by LAB).

* config.s02.yaml loads, uses its own logs/state/HALT, and S02 is never reported as approved.
* A 1-month replay with LAB-equivalent settings gives exactly LAB's S02 trades.
* Dashboard and Telegram messages carry the 'not approved' label.
"""
import pytest

from bot import config, dashboard
from bot.notify import format_record
from bot.replay import run_replay
from bot.strategy import is_approved
from radar.config import CLEAN
from replay_support import bot_trades, lab_equivalent_config, lab_trades, match

S02_CFG = config.REPO / "config.s02.yaml"
START, END = "2026-09-01", "2026-10-01"


def test_s02_config_is_separate_and_not_approved():
    c = config.load(S02_CFG)
    d = config.load()
    assert c.setup_id == "S02" and d.setup_id == "S16"
    assert {c.logs_dir, c.state_file, c.halt_file}.isdisjoint(
        {d.logs_dir, d.state_file, d.halt_file})
    assert c.shield == d.shield and c.risk_per_trade == d.risk_per_trade
    assert c.mode == "paper"
    assert is_approved("S16") and not is_approved("S02")
    gi = (config.REPO / ".gitignore").read_text().splitlines()
    assert {"logs_s02/", "state_s02.json", "HALT_s02"} <= set(gi)


def test_telegram_label():
    rec = dict(event="open", symbol="SOLUSDT", side=-1, entry=74.0, stop=77.0, notional=30,
               risk_usdt=1.1, reason="entry")
    msg = format_record("trades", rec, label="[S02 · SÓ OBSERVAÇÃO, NÃO APROVADO]")
    assert msg.startswith("[S02 · SÓ OBSERVAÇÃO, NÃO APROVADO]") and "SHORT SOLUSDT" in msg


@pytest.mark.skipif(not (CLEAN / "lab_cache" / "1h").exists(),
                    reason="needs the data/clean archive")
def test_s02_replay_equals_lab_backtest_and_dashboard_is_labelled(tmp_path):
    cfg = lab_equivalent_config(tmp_path / "run", S02_CFG)
    assert cfg.setup_id == "S02"
    bot, resets = run_replay(cfg, START, END, seed=3)
    lt, _ = lab_trades(START, END, "S02")
    bt = bot_trades(cfg.logs_dir)
    pairs, only_bot, only_lab, diffs = match(bt, lt)
    assert len(lt) >= 10
    assert only_bot == [], only_bot
    assert len(only_lab) == 0, only_lab[["symbol", "entry_time"]]
    assert diffs == [], diffs[:5]
    html = dashboard.build(cfg)
    assert "NÃO foi aprovado" in html and "S02" in html
    s16 = dashboard.build(config.load().with_paths(tmp_path / "s16"))
    assert "NÃO foi aprovado" not in s16
