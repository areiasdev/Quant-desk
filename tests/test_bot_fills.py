"""Paper fills = LAB engine. The bot's bar-by-bar event functions (bot/fills.py), driven in time
order, must give the same trades as lab.engine.simulate on the same bars and signals."""
import numpy as np
import pandas as pd
import pytest

from bot import fills
from bot.sizing import round_down, round_stop, size
from lab.engine import NS_H, apply_costs, make_signals, simulate

T0 = pd.Timestamp("2025-01-01", tz="UTC")


def bars(o, h, l, c, trades=None, freq="h"):
    n = len(o)
    return pd.DataFrame({"open_time": pd.date_range(T0, periods=n, freq=freq), "open": o,
                         "high": h, "low": l, "close": c,
                         "trades": np.ones(n, int) if trades is None else trades})


def both(b, sig, bar_ns=NS_H, fund_t=None, fund_r=None, **kw):
    t = b["open_time"].astype("datetime64[ns, UTC]").astype("int64").to_numpy()
    fk = dict(fund_t=np.array([], np.int64) if fund_t is None else fund_t,
              fund_r=np.array([]) if fund_r is None else fund_r, px_t=t,
              px_o=b["open"].to_numpy(float))
    lab = simulate(b, sig, bar_ns=bar_ns, delisted=False, **fk, **kw)
    bot = fills.drive(b, sig, bar_ns=bar_ns, **fk, **kw)
    return lab, bot


KEYS = ["side", "sig_idx", "entry_idx", "exit_idx", "entry_time", "exit_time", "entry", "exit",
        "stop", "entry_kind", "exit_kind", "reason", "funding", "stop_frac"]


def same(lab, bot):
    # the engine closes a still-open trade at the end of the data ('end'); the bot keeps it open
    lab = [x for x in lab if x["reason"] != "end"]
    assert len(lab) == len(bot), (len(lab), len(bot))
    for a, b in zip(lab, bot):
        for k in KEYS:
            if isinstance(a[k], float):
                assert np.isclose(a[k], b[k], rtol=1e-12, atol=1e-15), (k, a, b)
            else:
                assert a[k] == b[k], (k, a, b)
        assert (np.isnan(a["target"]) and np.isnan(b["target"])) or np.isclose(a["target"],
                                                                                b["target"])


def walk(n, seed):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    o = np.r_[c[0], c[:-1]] * (1 + rng.normal(0, 0.003, n))      # gaps between bars
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.006, n)))
    lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.006, n)))
    return bars(o, h, lo, c), rng


@pytest.mark.parametrize("seed", range(12))
def test_random_market_signals_match_lab_engine(seed):
    b, rng = walk(600, seed)
    n = len(b)
    idx = np.sort(rng.choice(np.arange(5, n - 2), 90, replace=False))
    k = len(idx)
    side = rng.choice([1, -1], k)
    use_abs = rng.random(k) < 0.4
    c = b["close"].to_numpy()
    stop = np.where(use_abs, c[idx] * (1 - side * rng.uniform(0.002, 0.03, k)), np.nan)
    stop_dist = np.where(~use_abs | (rng.random(k) < 0.3), c[idx] * rng.uniform(0.003, 0.04, k),
                         np.nan)
    tR = np.where(rng.random(k) < 0.6, rng.uniform(0.5, 3, k), np.nan)
    tmax = np.where(rng.random(k) < 0.5, idx + 1 + rng.integers(1, 40, k), np.nan)
    cx = np.where(rng.random(k) < 0.3, c[idx] * (1 - side * 0.01), np.nan)
    sig = make_signals(idx=idx, side=side, stop=stop, stop_dist=stop_dist, target_R=tR,
                       tmax=tmax, close_exit=cx)
    xl = rng.random(n) < 0.03
    xs = rng.random(n) < 0.03
    ft = b["open_time"].astype("datetime64[ns, UTC]").astype("int64").to_numpy()[::8] + 60 * 10**9
    fr = rng.normal(0, 2e-4, len(ft))
    lab, bot = both(b, sig, fund_t=ft, fund_r=fr, exit_long=xl, exit_short=xs)
    assert len(lab) > 20
    same(lab, bot)


@pytest.mark.parametrize("seed", range(6))
def test_random_limit_entries_match_lab_engine(seed):
    b, rng = walk(500, 100 + seed)
    n = len(b)
    idx = np.sort(rng.choice(np.arange(5, n - 2), 70, replace=False))
    k = len(idx)
    side = rng.choice([1, -1], k)
    c = b["close"].to_numpy()
    lim = c[idx] * (1 - side * rng.uniform(-0.002, 0.01, k))     # some marketable at the open
    sig = make_signals(idx=idx, side=side, limit=lim, limit_bars=np.ones(k),
                       stop_dist=c[idx] * 0.02, target_R=np.full(k, 1.5),
                       tmax=idx + 1 + rng.integers(2, 30, k))
    lab, bot = both(b, sig)
    assert any(x["entry_kind"] == "maker" for x in lab) and any(x["entry_kind"] == "taker"
                                                                for x in lab)
    same(lab, bot)


def test_multi_bar_limit_window_matches_lab():
    b = bars([100] * 12, [101] * 12, [99.5] * 12, [100] * 12)
    b.loc[4, "low"] = 98.9                                   # buy limit 99 fills in its 3rd bar
    sig = make_signals(idx=[1], side=[1], limit=[99.0], limit_bars=[3], stop_dist=[2.0],
                       tmax=[9])
    lab, bot = both(b, sig)
    assert lab[0]["entry_idx"] == 4 and lab[0]["entry_kind"] == "maker"
    same(lab, bot)


def test_stop_and_target_in_same_bar_is_a_stop():
    b = bars([100] * 8, [100.5] * 8, [99.5] * 8, [100] * 8)
    b.loc[3, ["high", "low"]] = [104.0, 97.0]                # touches both levels
    sig = make_signals(idx=[1], side=[1], stop_dist=[2.0], target_R=[1.0])
    lab, bot = both(b, sig)
    assert bot[0]["reason"] == "stop" and bot[0]["exit"] == 98.0
    same(lab, bot)


def test_gap_through_stop_exits_at_the_open_and_stops_are_checked_on_entry_bar():
    b = bars([100] * 8, [100.5] * 8, [99.5] * 8, [100] * 8)
    b.loc[4, ["open", "high", "low", "close"]] = [95.0, 96, 94, 95]
    sig = make_signals(idx=[1], side=[1], stop_dist=[2.0])
    lab, bot = both(b, sig)
    assert bot[0]["reason"] == "stop_gap" and bot[0]["exit"] == 95.0
    same(lab, bot)
    b2 = bars([100] * 8, [100.5] * 8, [99.5] * 8, [100] * 8)
    b2.loc[2, "low"] = 97.0                                  # stop hit in the entry bar itself
    lab, bot = both(b2, sig)
    assert bot[0]["entry_idx"] == 2 == bot[0]["exit_idx"] and bot[0]["reason"] == "stop"
    same(lab, bot)


def test_no_entry_when_stop_already_through_entry_and_no_signal_on_zero_trade_bar():
    b = bars([100] * 8, [100.5] * 8, [99.5] * 8, [100] * 8)
    b.loc[2, "open"] = 90.0
    sig = make_signals(idx=[1], side=[1], stop=[95.0])
    lab, bot = both(b, sig)
    assert lab == [] and bot == []
    b = bars([100] * 8, [100.5] * 8, [99.5] * 8, [100] * 8, trades=np.array([1, 0, 1, 1, 1, 1, 1, 1]))
    sig = make_signals(idx=[1], side=[1], stop_dist=[2.0], tmax=[5])
    lab, bot = both(b, sig)
    assert lab == [] and bot == []


def test_funding_settlement_boundaries_match_lab():
    """Paid for entry_time < T <= exit_time, at the 1h open of the bar containing T."""
    b = bars(np.linspace(100, 110, 30), np.linspace(100, 110, 30) + 0.4,
             np.linspace(100, 110, 30) - 0.4, np.linspace(100, 110, 30) + 0.1)
    t = b["open_time"].astype("datetime64[ns, UTC]").astype("int64").to_numpy()
    sig = make_signals(idx=[3], side=[1], stop_dist=[5.0], tmax=[12])
    ft = np.array([t[4], t[8] + 3 * 10**6, t[12], t[13]])    # at entry: no, at exit: yes
    fr = np.array([0.001, -0.002, 0.0005, 0.009])
    lab, bot = both(b, sig, fund_t=ft, fund_r=fr)
    exp = -(-0.002 * b["open"][8] + 0.0005 * b["open"][12]) / b["open"][4]
    assert np.isclose(bot[0]["funding"], exp)
    same(lab, bot)


def test_book_funding_is_incremental_and_waits_for_price():
    pos = dict(side=1, entry_time=0, entry=100.0, funding=0.0, funding_booked=[])
    ft = np.array([10, 20, 30], np.int64)
    fr = np.array([0.001, 0.002, 0.003])
    got = fills.book_funding(pos, ft, fr, 25, lambda T: None if T == 20 else 100.0)
    assert [g[0] for g in got] == [10]
    got = fills.book_funding(pos, ft, fr, 30, lambda T: 110.0)
    assert [g[0] for g in got] == [20, 30]
    assert np.isclose(pos["funding"], -(0.001 * 100 + 0.002 * 110 + 0.003 * 110) / 100)
    assert fills.book_funding(pos, ft, fr, 99, lambda T: 1.0) == []    # nothing twice


def test_net_return_is_lab_apply_costs():
    pos = dict(side=1, entry_kind="taker", entry=100.0, funding=-0.0012)
    df = pd.DataFrame([dict(side=1, slip_bp=3.0, entry_kind="taker", exit_kind="taker",
                            entry=100.0, exit=104.0, funding=-0.0012)])
    assert fills.net_return(pos, 104.0, "taker", 3.0, 0.0005, 0.0002) == \
        pytest.approx(float(apply_costs(df).iloc[0]), rel=1e-15)


def test_sizing_matches_lab_portfolio_rule_and_rounds_down():
    from lab.engine import portfolio

    tr = pd.DataFrame(dict(entry_time=[0, 1, 2], exit_time=[10, 10, 10], net=[0.01, 0.01, 0.01],
                           stop_frac=[0.05, 0.01, 0.002]))
    pt = portfolio(tr, equity0=110.0)
    on = 0.0
    for sf, want in zip(tr["stop_frac"], pt["notional"]):
        s = size(110.0, on, sf, 50.0, risk=0.01, max_leverage=3.0)
        assert s["notional"] == pytest.approx(want, rel=1e-12)
        on += s["notional"]
    assert pt["notional"].iloc[-1] < 0.01 * 110 / 0.002          # capped by 3x room
    s = size(110.0, 0.0, 0.05, 3.3, risk=0.01, max_leverage=3.0, step=1.0)
    assert s["qty"] == 6.0 and s["notional"] == pytest.approx(19.8)     # 22/3.3 = 6.67 -> 6
    assert round_down(0.0129, 0.001) == pytest.approx(0.012)
    assert round_stop(98.567, 1, 0.01) == pytest.approx(98.56)
    assert round_stop(98.561, -1, 0.01) == pytest.approx(98.57)
    assert size(110.0, 330.0, 0.05, 1.0, risk=0.01, max_leverage=3.0)["notional"] == 0.0
