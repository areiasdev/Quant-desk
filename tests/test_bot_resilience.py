"""Crash-restart, stale feed, fetch failures, exceptions, downtime, state files, HALT reset."""
import json
import os
import random
import subprocess
import sys

import pandas as pd
import pytest

from bot import dashboard
from bot.journal import read_jsonl, save_state, load_state
from bot.replay import ReplayDataSource, SimClock
from bot.runner import Bot, next_wake
from radar.config import CLEAN
from replay_support import default_config
from test_bot_replay import assert_self_contained

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
archive = pytest.mark.skipif(not (CLEAN / "lab_cache" / "1h").exists(),
                             reason="needs the data/clean archive")
MS = lambda s: int(pd.Timestamp(s, tz="UTC").value // 10**6)  # noqa: E731
START, END = "2026-07-01", "2026-07-11"


def new_bot(cfg, clock, seed=1, **src_kw):
    return Bot(cfg, ReplayDataSource(clock, **src_kw), clock, rng=random.Random(seed))


def trade_events(cfg):
    out = []
    for r in read_jsonl(cfg.logs_dir / "trades.jsonl"):
        r = dict(r)
        r.pop("logged_at", None)
        out.append(r)
    return out


@pytest.fixture(scope="module")
def reference(tmp_path_factory):
    cfg = default_config(tmp_path_factory.mktemp("ref"))
    clock = SimClock(MS(START) + 30_000)
    b = new_bot(cfg, clock)
    b.run_forever(until_ms=MS(END))
    ev = trade_events(cfg)
    assert sum(r["event"] == "close" for r in ev) >= 2
    return cfg, ev


# ------------------------------------------------------------------ crash restart
@archive
def test_crash_restart_resumes_exactly(tmp_path, reference, monkeypatch):
    _, ref = reference
    cfg = default_config(tmp_path)
    clock = SimClock(MS(START) + 30_000)
    from bot import journal as J
    real_append = J.Journal.append
    crashes = []

    def crash_on_trades(self, name, rec, fsync=False):
        if name == "trades" and not crashes:
            crashes.append(("after state saved, before trades.jsonl", rec["rid"]))
            raise KeyboardInterrupt("simulated kill")
        return real_append(self, name, rec, fsync)

    monkeypatch.setattr(J.Journal, "append", crash_on_trades)
    with pytest.raises(KeyboardInterrupt):
        new_bot(cfg, clock).run_forever(until_ms=MS(END))
    monkeypatch.setattr(J.Journal, "append", real_append)
    st = load_state(cfg.state_file)
    assert st["outbox"] and st["outbox"][0]["rid"] == crashes[0][1]   # survived in state.json

    clock.sleep(60)                                    # run_bot restart delay
    real_close = Bot._close

    def crash_in_close(self, *a, **k):
        crashes.append(("mid-decision, before the close was booked", None))
        raise KeyboardInterrupt("simulated power cut")

    b2 = new_bot(cfg, clock, seed=2)                   # flushes the outbox exactly once
    assert not cfg.halt_file.exists()
    monkeypatch.setattr(Bot, "_close", crash_in_close)
    with pytest.raises(KeyboardInterrupt):
        b2.run_forever(until_ms=MS(END))
    monkeypatch.setattr(Bot, "_close", real_close)
    assert load_state(cfg.state_file)["positions"]      # open positions survived on disk

    clock.sleep(60)
    b3 = new_bot(cfg, clock, seed=3)
    b3.run_forever(until_ms=MS(END))
    assert len(crashes) == 2
    assert not cfg.halt_file.exists(), cfg.halt_file.read_text()
    got = trade_events(cfg)
    assert [r["rid"] for r in got] == [r["rid"] for r in ref]
    assert got == ref                                  # identical prices, funding, P&L
    starts = [r for r in read_jsonl(cfg.logs_dir / "system.jsonl") if r["type"] == "START"]
    assert [s["fresh_state"] for s in starts] == [True, False, False]


@archive
def test_downtime_books_resting_stops_at_their_price(tmp_path, reference):
    """PC asleep 2026-07-07 23:00 -> 07-08 09:20: the exchange-side stops hit at 07-08 02:00 are
    booked at the stop price and time, as in the uninterrupted run."""
    _, ref = reference
    cfg = default_config(tmp_path)
    clock = SimClock(MS(START) + 30_000)
    b = new_bot(cfg, clock)
    b.run_forever(until_ms=MS("2026-07-07 23:30"))
    clock.set(MS("2026-07-08 09:20"))
    b2 = new_bot(cfg, clock, seed=9)
    b2.run_forever(until_ms=MS(END))
    got = {r["rid"]: r for r in trade_events(cfg)}
    refd = {r["rid"]: r for r in ref}
    stops = [k for k, r in refd.items() if r["event"] == "close" and
             r["exit_time"].startswith("2026-07-08")]
    assert stops
    for k in stops:
        assert got[k]["exit"] == refd[k]["exit"] and got[k]["exit_time"] == refd[k]["exit_time"]
        assert got[k]["reason"] == "stop" and got[k]["pnl"] == refd[k]["pnl"]
    assert not cfg.halt_file.exists()


# ------------------------------------------------------------------ stale feed / outages
@archive
def test_stale_feed_halts_and_resumes_after_reset(tmp_path):
    cfg = default_config(tmp_path)
    clock = SimClock(MS("2026-07-01") + 30_000)
    b = new_bot(cfg, clock, freeze_after_ms=MS("2026-07-03 10:00"))
    b.run_forever(until_ms=MS("2026-07-03 16:00"))
    assert cfg.halt_file.exists()
    h = [r for r in read_jsonl(cfg.logs_dir / "shield.jsonl") if r["type"] == "HALT"]
    assert len(h) == 1 and h[0]["rule"] == "stale_feed"
    assert pd.Timestamp(h[0]["time"]) < pd.Timestamp("2026-07-03 13:05", tz="UTC")
    eq = read_jsonl(cfg.logs_dir / "equity.jsonl")
    assert eq[-1]["status"] == "halted" and eq[-1]["halted"]
    n_dec = len(read_jsonl(cfg.logs_dir / "decisions.jsonl"))
    b.cycle()
    assert len(read_jsonl(cfg.logs_dir / "decisions.jsonl")) == n_dec   # halted: no decisions
    open_before = sorted(b.state["positions"])
    # dashboard shows the halt
    assert "PARADO (HALT)" in dashboard.build(cfg)
    # feed comes back, owner deletes HALT: the bot catches up and carries on
    b.src.freeze_after_ms = None
    cfg.halt_file.unlink()
    assert b.cycle() == "ok"
    assert [r["type"] for r in read_jsonl(cfg.logs_dir / "shield.jsonl")][-1] == "RESET"
    assert sorted(b.state["positions"]) == open_before or not open_before


@archive
def test_three_fetch_failures_halt(tmp_path):
    cfg = default_config(tmp_path)
    clock = SimClock(MS("2026-07-01") + 30_000)
    b = new_bot(cfg, clock, fail_from_ms=MS("2026-07-02 05:10"))
    b.run_forever(until_ms=MS("2026-07-02 07:00"))
    sh = read_jsonl(cfg.logs_dir / "shield.jsonl")
    fails = [r for r in sh if r["type"] == "FETCH_FAILURE"]
    assert len(fails) == 3 and sh[-1]["type"] == "HALT" and sh[-1]["rule"] == "fetch_failures"
    gaps = pd.to_datetime([f["time"] for f in fails], format="ISO8601").to_series().diff().dropna()
    assert all(g == pd.Timedelta(minutes=5) for g in gaps)       # retry cycle every 5 minutes
    assert cfg.halt_file.exists()


@archive
def test_unhandled_exception_in_decision_code_halts(tmp_path, monkeypatch):
    cfg = default_config(tmp_path)
    clock = SimClock(MS("2026-07-01") + 30_000)
    b = new_bot(cfg, clock)
    b.run_forever(until_ms=MS("2026-07-02 00:00"))

    def boom(*a, **k):
        raise ZeroDivisionError("bug in signal code")

    monkeypatch.setattr(b.strategy, "evaluate", boom)
    b.run_forever(until_ms=MS("2026-07-02 09:00"))
    h = [r for r in read_jsonl(cfg.logs_dir / "shield.jsonl") if r["type"] == "HALT"]
    assert h and h[0]["rule"] == "exception" and "ZeroDivisionError" in h[0]["reason"]
    assert "Traceback" in h[0]["traceback"] and cfg.halt_file.exists()
    assert b.cycle() == "halted"
    monkeypatch.undo()
    cfg.halt_file.unlink()
    clock.sleep(60)
    assert b.cycle() == "ok"


@archive
def test_state_mismatch_halts(tmp_path):
    cfg = default_config(tmp_path)
    clock = SimClock(MS("2026-07-01") + 30_000)
    b = new_bot(cfg, clock)
    b.run_forever(until_ms=MS("2026-07-03 00:00"))
    assert b.state["positions"]
    st = load_state(cfg.state_file)
    st["equity_realized"] += 5.0                      # corrupted / hand-edited state
    save_state(cfg.state_file, st)
    clock.sleep(3600)
    b2 = new_bot(cfg, clock)
    assert b2.cycle() == "halted"
    h = read_jsonl(cfg.logs_dir / "shield.jsonl")[-1]
    assert h["rule"] == "mismatch" and "realised equity" in h["reason"]


# ------------------------------------------------------------------ files and loop
def test_atomic_state_write(tmp_path):
    p = tmp_path / "state.json"
    save_state(p, {"a": 1, "x": float("nan")})
    save_state(p, {"a": 2})
    assert load_state(p) == {"a": 2}
    assert sorted(x.name for x in tmp_path.iterdir()) == ["state.json"]   # no temp files left


def test_next_wake_after_each_hour_with_jitter():
    rng = random.Random(0)
    H = 3_600_000
    t = 10 * H + 45_000                             # just after the 10:00 cycle
    for k in range(50):
        w = next_wake(t, 30, 30, rng)
        assert w > t and 30_000 <= w % H <= 60_000
        assert w // H == 11 + k                     # exactly one cycle per hour
        t = w + int(rng.uniform(0, 20_000))         # the cycle itself takes a few seconds
    assert next_wake(10 * H + 59 * 60_000, 30, 0, rng) == 11 * H + 30_000
    assert next_wake(10 * H + 10_000, 30, 0, rng) == 10 * H + 30_000   # started at hh:00:10


def test_dashboard_with_empty_logs(tmp_path):
    cfg = default_config(tmp_path)
    h = dashboard.write(cfg, tmp_path / "d.html").read_text(encoding="utf-8")
    assert "PAPER ONLY" in h and "Ainda não iniciado" in h and "Nenhuma posição aberta" in h
    assert_self_contained(h)


def test_dashboard_when_halted_and_report_when_empty(tmp_path):
    from bot import report

    cfg = default_config(tmp_path)
    cfg.halt_file.write_text("HALT: daily loss -3.10% reached the limit -3% (UTC day 2026-07-02)\n",
                             encoding="utf-8")
    h = dashboard.build(cfg)
    assert "PARADO (HALT)" in h and "daily loss" in h
    assert_self_contained(h)
    assert "Ainda não há registos" in report.build(cfg, 30)


@pytest.mark.skipif(sys.platform.startswith("win"), reason="bash script")
def test_run_bot_sh_restarts_after_crash_and_stops_on_config_error(tmp_path):
    log = tmp_path / "runs.txt"
    fake = tmp_path / "fakepy"
    fake.write_text(f"#!/bin/sh\necho run >> {log}\nexit ${{FAKE_CODE:-1}}\n")
    fake.chmod(0o755)
    env = dict(os.environ, PYTHON=str(fake), RESTART_DELAY="0", MAX_RESTARTS="3")
    r = subprocess.run(["bash", str(ROOT / "run_bot.sh")], env=env, capture_output=True,
                       text=True, timeout=30)
    assert log.read_text().count("run") == 3 and "restarting" in r.stdout
    log.unlink()
    env["FAKE_CODE"] = "2"
    r = subprocess.run(["bash", str(ROOT / "run_bot.sh")], env=env, capture_output=True,
                       text=True, timeout=30)
    assert r.returncode == 2 and log.read_text().count("run") == 1


def test_cli_refuses_live_mode_and_bad_config(tmp_path):
    import yaml

    from bot import config

    d = config.raw()
    d["mode"] = "live"
    d["paths"] = {"logs_dir": str(tmp_path / "logs"), "state_file": str(tmp_path / "s.json"),
                  "halt_file": str(tmp_path / "HALT")}
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(d))
    r = subprocess.run([sys.executable, "-m", "bot", "--config", str(p)], cwd=ROOT,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 3 and "Real money needs the owner's 'go live'" in r.stderr
    d["mode"] = "paper"
    d["shield"]["max_open_positions"] = 9
    p.write_text(yaml.safe_dump(d))
    r = subprocess.run([sys.executable, "-m", "bot", "--config", str(p)], cwd=ROOT,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 2 and "CONFIG ERROR" in r.stderr
