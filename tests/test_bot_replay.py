"""The paper bot replayed over archive data (data/clean) through its real hourly loop.

* 3 months (2026-08..2026-10) with LAB-equivalent settings (no Shield position/notional limits,
  as LAB's portfolio() assumes): every trade must equal LAB's S16 backtest of the same period and
  universe - entry, stop, exit time/price/reason, funding, net return and size.
* 1 month (2026-07) with the real config.yaml: every trade the bot takes equals LAB's trade;
  every LAB trade it does not take is explained by a logged Shield block / missed entry.
"""
import json

import numpy as np
import pandas as pd
import pytest

from bot import dashboard, report
from bot.journal import read_jsonl
from bot.replay import run_replay
from radar.config import CLEAN
from replay_support import bot_trades, default_config, lab_equivalent_config, lab_trades, match

pytestmark = pytest.mark.skipif(not (CLEAN / "lab_cache" / "1h").exists(),
                                reason="needs the data/clean archive")

LAB_START, LAB_END = "2026-08-01", "2026-11-01"
DEF_START, DEF_END = "2026-07-01", "2026-08-01"


@pytest.fixture(scope="module")
def lab_equiv(tmp_path_factory):
    cfg = lab_equivalent_config(tmp_path_factory.mktemp("labeq"))
    bot, resets = run_replay(cfg, LAB_START, LAB_END, seed=11)
    lt, pt = lab_trades(LAB_START, LAB_END)
    return cfg, bot, resets, lt, pt


@pytest.fixture(scope="module")
def default_run(tmp_path_factory):
    cfg = default_config(tmp_path_factory.mktemp("default"))
    bot, resets = run_replay(cfg, DEF_START, DEF_END, seed=5, auto_reset_halt=True)
    lt, _ = lab_trades(DEF_START, DEF_END)
    return cfg, bot, resets, lt


def test_three_month_replay_equals_lab_backtest(lab_equiv):
    cfg, bot, resets, lt, pt = lab_equiv
    assert (pd.Timestamp(LAB_END) - pd.Timestamp(LAB_START)).days >= 89
    bt = bot_trades(cfg.logs_dir)
    pairs, only_bot, only_lab, diffs = match(bt, lt)
    assert len(lt) >= 20
    assert only_bot == [], only_bot
    assert len(only_lab) == 0, only_lab[["symbol", "entry_time", "reason"]]
    assert diffs == [], diffs[:5]
    assert resets == [] and not cfg.halt_file.exists()
    # same size and P&L as LAB's portfolio() (1% risk on realised equity, 3x cap)
    pt = pt.set_index(["symbol", "entry_time"])
    for b, _ in pairs:
        L = pt.loc[(b["symbol"], int(b["entry_time"]))]
        assert b["notional"] == pytest.approx(L["notional"], rel=1e-9)
        if b["pnl"] is not None and not pd.isna(b["pnl"]):
            assert b["pnl"] == pytest.approx(L["pnl"], rel=1e-9)
    # trades still open at the end of the replay are LAB trades still open then
    still = sorted((p["symbol"], p["entry_time"]) for p in bot.state["positions"].values())
    lab_open = sorted(zip(lt.loc[~lt["closed_in_window"], "symbol"],
                          lt.loc[~lt["closed_in_window"], "entry_time"]))
    assert still == [(s, int(t)) for s, t in lab_open]
    # exit reasons cover the S16 rules
    assert {"stop", "signal"} <= set(bt["reason"].dropna())


def test_replay_logs_every_signal_evaluation(lab_equiv):
    cfg, bot, *_ = lab_equiv
    dec = read_jsonl(cfg.logs_dir / "decisions.jsonl")
    sig = [d for d in dec if d["stage"] == "signal"]
    days = (pd.Timestamp(LAB_END) - pd.Timestamp(LAB_START)).days
    assert len(sig) >= 0.95 * 10 * 6 * days          # ~10 symbols x 6 closes a day
    assert all(d["reason"] and d["inputs"] and "F8" in d["inputs"] for d in sig)
    assert {"no_signal", "queued"} <= {d["result"] for d in sig}
    eq = read_jsonl(cfg.logs_dir / "equity.jsonl")
    assert abs(len(eq) - 24 * days) <= 2             # one record per hourly cycle
    sysl = read_jsonl(cfg.logs_dir / "system.jsonl")
    assert sum(r["type"] == "UNIVERSE" for r in sysl) == 3


def test_default_config_trades_are_lab_trades_and_every_skip_is_explained(default_run):
    cfg, bot, resets, lt = default_run
    bt = bot_trades(cfg.logs_dir)
    pairs, only_bot, only_lab, diffs = match(bt, lt)
    assert only_bot == [] and diffs == [], (only_bot, diffs)
    assert len(pairs) >= 5
    dec = read_jsonl(cfg.logs_dir / "decisions.jsonl")
    sh = read_jsonl(cfg.logs_dir / "shield.jsonl")
    for _, L in only_lab.iterrows():
        sig_bar = pd.Timestamp(int(L["entry_time"]) - 4 * 3600 * 10**9, tz="UTC")
        why = [d for d in dec if d["symbol"] == L["symbol"] and d["stage"] == "entry"
               and pd.Timestamp(d["bar_open"]) == sig_bar and d["result"] in ("blocked", "missed")]
        assert why, (L["symbol"], sig_bar)
        if why[0]["result"] == "blocked":
            assert any(s["type"] == "BLOCK" and s["symbol"] == L["symbol"] for s in sh)
    # Shield invariants on what was actually traded
    opens = [r for r in read_jsonl(cfg.logs_dir / "trades.jsonl") if r["event"] == "open"]
    assert all(r["leverage_after"] <= 3.0 + 1e-9 for r in opens)
    assert all(r["notional"] >= r["min_notional"] for r in opens)
    t = sorted([(pd.Timestamp(r["entry_time"]), 1) for r in opens] +
               [(pd.Timestamp(r["exit_time"]), -1) for r in
                read_jsonl(cfg.logs_dir / "trades.jsonl") if r["event"] == "close"],
               key=lambda x: (x[0], x[1]))
    assert max(np.cumsum([x[1] for x in t])) <= 3
    halts = [s for s in sh if s["type"] == "HALT"]
    assert len(halts) == len(resets)
    assert all(h["reason"] and h.get("rule") for h in halts)


def test_report_from_replay_logs(default_run):
    cfg, *_ = default_run
    txt = report.build(cfg, 30)
    for part in ("RELATÓRIO MENSAL", "Trades fechados", "Papel vs backtest", "Shield",
                 "Saúde do setup", "Universo top-10", "+0.208R"):
        assert part in txt, part
    assert "trades: 0" not in txt


def test_dashboard_from_replay_logs(default_run, tmp_path):
    cfg, *_ = default_run
    out = dashboard.write(cfg, tmp_path / "d.html")
    h = out.read_text(encoding="utf-8")
    assert "PAPER ONLY" in h and "Curva de capital" in h and "<svg" in h
    assert "Últimos 50 trades" in h and "Eventos do Shield" in h and "Saúde do setup" in h
    assert "Universo top-10" in h and "SOLUSDT" in h
    assert_self_contained(h)


def assert_self_contained(h: str):
    low = h.lower()
    assert "://" not in low                       # no http(s)://, no file://, no CDN
    assert "<script src" not in low and "<link" not in low and "@import" not in low
    assert "url(" not in low and "src=" not in low and "href=" not in low
