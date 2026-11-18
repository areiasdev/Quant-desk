"""SHIELD rules: every block and every hard halt, the HALT file and its manual reset."""
import json

import pytest

from bot.config import Shield as Limits
from bot.journal import Journal
from bot.market import H
from bot.shield import Shield

NOW = 1_780_000_000_000


@pytest.fixture
def sh(tmp_path):
    j = Journal(tmp_path / "logs")
    return Shield(Limits(), tmp_path / "HALT", j, {}), j, tmp_path


def ok_args(**kw):
    a = dict(symbol="SOLUSDT", side=1, entry=100.0, stop=95.0, qty=0.22, notional=22.0,
             equity=110.0, open_notional=0.0, open_symbols=[], min_notional=5.0,
             min_notional_source="ESTIMATE")
    a.update(kw)
    return a


def last(j, name="shield"):
    return j.read(name)[-1]


def test_approves_a_normal_trade(sh):
    s, j, _ = sh
    assert s.approve(NOW, **ok_args()) == (True, "approved")
    assert j.read("shield") == []


@pytest.mark.parametrize("kw,rule", [
    (dict(stop=None), "no_stop"), (dict(stop=101.0), "no_stop"),
    (dict(notional=4.99, qty=0.0499), "min_notional"),
    (dict(symbol="BTCUSDT", notional=62.3, min_notional=100.0), "min_notional"),
    (dict(open_notional=300.0, notional=31.0), "leverage"),
    (dict(open_symbols=["A", "B", "C"]), "max_positions"),
    (dict(open_symbols=["SOLUSDT"]), "duplicate"),
    (dict(qty=0.001, min_qty=0.01), "min_qty"),
])
def test_blocks_with_logged_reason(sh, kw, rule):
    s, j, _ = sh
    ok, why = s.approve(NOW, **ok_args(**kw))
    assert not ok and why
    rec = last(j)
    assert rec["type"] == "BLOCK" and rec["rule"] == rule and rec["reason"] == why


def test_three_positions_allowed_fourth_blocked(sh):
    s, _, _ = sh
    assert s.approve(NOW, **ok_args(open_symbols=["A", "B"]))[0]
    assert not s.approve(NOW, **ok_args(open_symbols=["A", "B", "C"]))[0]


def test_halt_after_three_consecutive_losses_and_reset(sh):
    s, j, tmp = sh
    s.on_trade_closed(NOW, -1.0, "A")
    s.on_trade_closed(NOW, 0.5, "B")          # a win resets the count
    s.on_trade_closed(NOW, -1.0, "C")
    s.on_trade_closed(NOW, -0.0, "D")         # break-even counts as a loss (LAB: net <= 0)
    assert not s.halted()
    s.on_trade_closed(NOW, -1.0, "E")
    assert s.halted() and (tmp / "HALT").exists()
    assert "3 losing trades in a row" in (tmp / "HALT").read_text()
    assert last(j)["rule"] == "consecutive_losses"
    assert not s.approve(NOW, **ok_args())[0]                        # halted: nothing passes
    assert not s.check_reset(NOW, 100.0)                             # HALT still there
    (tmp / "HALT").unlink()
    assert s.check_reset(NOW + H, 105.0)
    assert s.s["consecutive_losses"] == 0 and s.s["day_start_equity"] == 105.0
    assert last(j)["type"] == "RESET"
    assert not s.check_reset(NOW + 2 * H, 105.0)


def test_daily_loss_halt_uses_utc_day_start(sh):
    s, j, _ = sh
    s.roll_day(NOW, 100.0)
    s.check_daily(NOW, 97.01)
    assert not s.halted()
    s.check_daily(NOW, 97.0)
    assert s.halted() and last(j)["rule"] == "daily_loss"


def test_new_utc_day_takes_the_previous_equity_as_baseline(sh):
    s, _, _ = sh
    day = 24 * H
    t = NOW // day * day + 23 * H
    s.roll_day(t, 100.0)
    s.roll_day(t + H, 98.0)                   # next UTC day
    assert s.s["day_start_equity"] == 98.0
    s.check_daily(t + 2 * H, 95.2)
    assert not s.halted()


def test_stale_feed_halts(sh):
    s, j, _ = sh
    assert not s.check_stale(NOW, {"A": NOW - H, "B": NOW - 2 * H})
    assert s.check_stale(NOW, {"A": NOW - H, "B": NOW - 2 * H - 1})
    rec = last(j)
    assert rec["rule"] == "stale_feed" and "B" in rec["reason"]


def test_three_fetch_failures_halt_and_success_resets(sh):
    s, j, _ = sh
    s.on_fetch(NOW, False, "timeout")
    s.on_fetch(NOW, False, "timeout")
    s.on_fetch(NOW, True)
    s.on_fetch(NOW, False, "timeout")
    s.on_fetch(NOW, False, "timeout")
    assert not s.halted()
    s.on_fetch(NOW, False, "HTTP 503")
    assert s.halted() and last(j)["rule"] == "fetch_failures"
    assert sum(1 for r in j.read("shield") if r["type"] == "FETCH_FAILURE") == 5


def test_halt_file_is_plain_text_with_instructions(sh):
    s, _, tmp = sh
    s.halt(NOW, "test reason", rule="exception")
    txt = (tmp / "HALT").read_text(encoding="utf-8")
    assert txt.startswith("HALT: test reason") and "delete this file" in txt
    s.halt(NOW, "second reason")              # already halted: logged, file kept
    assert (tmp / "HALT").read_text(encoding="utf-8") == txt
    assert json.loads((tmp / "logs" / "shield.jsonl").read_text().splitlines()[-1])[
        "already_halted"]
