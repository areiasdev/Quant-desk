"""Unit tests for the LAB backtest engine: look-ahead (shift tests), costs, same-bar stop/target,
funding accrual, delisting, limit fills, portfolio sizing, Monte Carlo."""
import numpy as np
import pandas as pd
import pytest

from lab.engine import (BASE, Costs, NS_H, add_net, apply_costs, longest_losing_streak,
                        make_signals, max_dd, monte_carlo, portfolio, simulate)

T0 = pd.Timestamp("2025-01-01", tz="UTC")


def bars(o, h, l, c, trades=None):
    n = len(o)
    return pd.DataFrame({"open_time": pd.date_range(T0, periods=n, freq="h"), "open": o,
                         "high": h, "low": l, "close": c,
                         "trades": np.ones(n, int) if trades is None else trades})


def run(b, sig, delisted=False, fund_t=None, fund_r=None, **kw):
    t = b["open_time"].astype("datetime64[ns, UTC]").astype("int64").to_numpy()
    return simulate(b, sig, bar_ns=NS_H, delisted=delisted,
                    fund_t=np.array([], np.int64) if fund_t is None else fund_t,
                    fund_r=np.array([]) if fund_r is None else fund_r,
                    px_t=t, px_o=b["open"].to_numpy(float), **kw)


def frame(trs, sym_slip=0.0):
    df = pd.DataFrame(trs)
    df["slip_bp"] = sym_slip
    return df


def flat(n=10, p=100.0):
    return bars([p] * n, [p + 0.5] * n, [p - 0.5] * n, [p] * n)


def test_entry_is_next_bar_open_not_signal_close():
    b = flat()
    b.loc[3, ["open", "high", "low", "close"]] = [100, 101, 99, 100.7]
    b.loc[4, "open"] = 104.0
    b.loc[4, "high"] = 104.5
    tr = run(b, make_signals(idx=[3], side=[1], stop_dist=[50.0], tmax=[6]))
    assert tr[0]["entry_idx"] == 4 and tr[0]["entry"] == 104.0
    assert tr[0]["exit_idx"] == 6 and tr[0]["reason"] == "time"


def test_shift_test_future_bars_do_not_change_past_trades():
    rng = np.random.default_rng(0)
    c = 100 + np.cumsum(rng.normal(0, 1, 300))
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) + 0.3
    l = np.minimum(o, c) - 0.3
    b = bars(o, h, l, c)
    sig = make_signals(idx=[10, 50, 120], side=[1, -1, 1], stop_dist=[2.0] * 3,
                       target_R=[2.0] * 3, tmax=[40, 80, 150])
    full = run(b, sig)
    # perturb everything after bar 200: trades that closed before it must be identical
    b2 = b.copy()
    b2.loc[201:, ["open", "high", "low", "close"]] *= 1.5
    cut = run(b2, sig)
    for x, y in zip(full, cut):
        if x["exit_idx"] < 200:
            assert x == y


def test_signal_series_shift_changes_result_only_through_future_data():
    """Shift test on the signal side: a signal computed from bar t+1 data and lagged back one bar
    would be look-ahead; the engine must use bar idx+1 open, so moving idx by one moves the entry
    by exactly one bar."""
    b = bars(np.arange(100, 120.0), np.arange(100, 120.0) + 0.5, np.arange(100, 120.0) - 0.5,
             np.arange(100, 120.0) + 0.2)
    a = run(b, make_signals(idx=[5], side=[1], stop_dist=[5.0], tmax=[9]))[0]
    z = run(b, make_signals(idx=[6], side=[1], stop_dist=[5.0], tmax=[10]))[0]
    assert z["entry_idx"] == a["entry_idx"] + 1 and z["entry"] == b["open"][a["entry_idx"] + 1]


def test_same_bar_stop_and_target_assumes_stop():
    b = flat()
    b.loc[5, ["high", "low"]] = [110.0, 90.0]           # touches both
    tr = run(b, make_signals(idx=[3], side=[1], stop_dist=[5.0], target_R=[1.0]))
    assert tr[0]["reason"] == "stop" and tr[0]["exit"] == 95.0
    tr = run(b, make_signals(idx=[3], side=[-1], stop_dist=[5.0], target_R=[1.0]))
    assert tr[0]["reason"] == "stop" and tr[0]["exit"] == 105.0


def test_target_needs_trade_through_and_is_maker():
    b = flat()
    b.loc[5, "high"] = 110.0                            # equals target: NOT filled
    b.loc[7, "high"] = 110.01
    tr = run(b, make_signals(idx=[3], side=[1], stop_dist=[5.0], target_R=[2.0]))
    assert tr[0]["exit_idx"] == 7 and tr[0]["reason"] == "target"
    assert tr[0]["exit_kind"] == "maker" and tr[0]["exit"] == 110.0


def test_gap_through_stop_exits_at_open():
    b = flat()
    b.loc[6, ["open", "high", "low", "close"]] = [90, 91, 89, 90]
    tr = run(b, make_signals(idx=[3], side=[1], stop_dist=[5.0]))
    assert tr[0]["reason"] == "stop_gap" and tr[0]["exit"] == 90.0


def test_close_exit_signal_executes_next_open():
    b = flat()
    b.loc[6, "open"] = 101.0
    xl = np.zeros(10, bool)
    xl[5] = True
    tr = run(b, make_signals(idx=[3], side=[1], stop_dist=[5.0]), exit_long=xl)
    assert tr[0]["exit_idx"] == 6 and tr[0]["exit"] == 101.0 and tr[0]["reason"] == "signal"


def test_zero_trade_bars_never_filled():
    b = flat()
    b.loc[4, "trades"] = 0
    assert run(b, make_signals(idx=[3], side=[1], stop_dist=[5.0])) == []
    tr = run(b, make_signals(idx=[1], side=[1], stop_dist=[5.0], tmax=[4]))
    assert tr[0]["exit_idx"] == 5                          # time exit moved past the halt bar


def test_one_position_per_symbol():
    b = flat(20)
    tr = run(b, make_signals(idx=[2, 3, 6, 7], side=[1] * 4, stop_dist=[5.0] * 4,
                             tmax=[8, 9, 10, 15]))
    # first trade exits at the open of bar 8: signals at 3 and 6 are skipped, the signal at 7
    # enters at the open of bar 8 (after the exit)
    assert [x["entry_idx"] for x in tr] == [3, 8]


def test_costs_taker_maker_and_slippage():
    b = flat()
    b.loc[6, "high"] = 110.01
    tr = frame(run(b, make_signals(idx=[3], side=[1], stop_dist=[5.0], target_R=[2.0])),
               sym_slip=3.0)
    net = apply_costs(tr, BASE).iloc[0]
    ef = 100 * (1 + 3e-4)
    expect = 110 / ef - 1 - 0.0005 - 0.0002 * 110 / ef
    assert net == pytest.approx(expect, abs=1e-12)
    # doubled fees and slippage
    net2 = apply_costs(tr, BASE.scaled(2.0, 2.0)).iloc[0]
    ef2 = 100 * (1 + 6e-4)
    assert net2 == pytest.approx(110 / ef2 - 1 - 0.001 - 0.0004 * 110 / ef2, abs=1e-12)
    # short, taker both sides via time stop
    tr = frame(run(flat(), make_signals(idx=[3], side=[-1], stop_dist=[5.0], tmax=[6])), 10.0)
    net = apply_costs(tr, Costs(taker=0.001)).iloc[0]
    ef, xf = 100 * (1 - 1e-3), 100 * (1 + 1e-3)
    assert net == pytest.approx(-(xf / ef - 1) - 0.001 - 0.001 * xf / ef, abs=1e-12)


def test_funding_accrual_window_and_sign():
    b = flat(30)
    t = b["open_time"].astype("datetime64[ns, UTC]").astype("int64").to_numpy()
    # settlements at bars 4 (= entry instant: NOT held), 8 (held), 12 (= exit instant: held),
    # 16 (after exit)
    ft = t[[4, 8, 12, 16]]
    fr = np.array([0.01, 0.001, 0.002, 0.05])
    tr = run(b, make_signals(idx=[3], side=[1], stop_dist=[50.0], tmax=[12]), fund_t=ft, fund_r=fr)
    assert tr[0]["entry_idx"] == 4 and tr[0]["exit_idx"] == 12
    assert tr[0]["funding"] == pytest.approx(-(0.001 + 0.002))
    tr = run(b, make_signals(idx=[3], side=[-1], stop_dist=[50.0], tmax=[12]), fund_t=ft, fund_r=fr)
    assert tr[0]["funding"] == pytest.approx(+(0.001 + 0.002))
    # funding enters the net return
    df = frame(tr)
    no_fund = df.assign(funding=0.0)
    assert apply_costs(df).iloc[0] - apply_costs(no_fund).iloc[0] == pytest.approx(0.003)


def test_funding_valued_at_settlement_price():
    b = flat(20)
    b.loc[8:, ["open", "high", "low", "close"]] = [200.0, 200.5, 199.5, 200.0]
    t = b["open_time"].astype("datetime64[ns, UTC]").astype("int64").to_numpy()
    tr = run(b, make_signals(idx=[3], side=[1], stop_dist=[90.0], tmax=[12]),
             fund_t=t[[10]], fund_r=np.array([0.001]))
    assert tr[0]["funding"] == pytest.approx(-0.002)   # qty x 200 x rate / (qty x 100)


def test_delisted_force_close_with_haircut():
    b = flat(10)
    tr = run(b, make_signals(idx=[3], side=[1], stop_dist=[50.0]), delisted=True)
    assert tr[0]["reason"] == "delist" and tr[0]["exit"] == pytest.approx(100 * 0.98)
    tr = run(b, make_signals(idx=[3], side=[-1], stop_dist=[50.0]), delisted=True)
    assert tr[0]["exit"] == pytest.approx(100 * 1.02)
    tr = run(b, make_signals(idx=[3], side=[1], stop_dist=[50.0]), delisted=False)
    assert tr[0]["reason"] == "end" and tr[0]["exit"] == 100.0


def test_limit_entry_fills_only_through_price():
    b = flat(10)                                   # lows at 99.5
    assert run(b, make_signals(idx=[2], side=[1], stop_dist=[5.0], limit=[99.5],
                               limit_bars=[3])) == []      # touched, not traded through
    b.loc[4, "low"] = 99.4
    tr = run(b, make_signals(idx=[2], side=[1], stop_dist=[5.0], limit=[99.5], limit_bars=[3],
                             tmax=[8]))
    assert tr[0]["entry_idx"] == 4 and tr[0]["entry"] == 99.5 and tr[0]["entry_kind"] == "maker"


def test_stop_beyond_entry_skips_and_closer_stop_rule():
    b = flat()
    assert run(b, make_signals(idx=[3], side=[1], stop=[101.0])) == []
    tr = run(b, make_signals(idx=[3], side=[1], stop=[90.0], stop_dist=[3.0], tmax=[6]))
    assert tr[0]["stop"] == 97.0                    # the closer of LL-stop and ATR-stop


def test_portfolio_risk_sizing_and_leverage_cap():
    df = pd.DataFrame({"entry_time": [0, 0, 10], "exit_time": [5, 5, 20], "net": [-0.02, 0.01, 0.0],
                       "stop_frac": [0.02, 0.002, 0.05]})
    pt = portfolio(df, equity0=100.0)
    assert pt["notional"].iat[0] == pytest.approx(50.0)          # 1% risk / 2% stop
    assert pt["notional"].iat[1] == pytest.approx(250.0)         # capped: 3x100 - 50
    assert pt["pnl"].iat[0] == pytest.approx(-1.0)
    assert pt["equity_before"].iat[2] == pytest.approx(100 - 1 + 2.5)


def test_helpers():
    assert longest_losing_streak(np.array([1, -1, -1, 0, 2, -1])) == 3
    assert max_dd([100, 120, 90, 130]) == pytest.approx(0.25)
    mc = monte_carlo(np.array([0.01] * 50 + [-0.01] * 50), n=200)
    assert 0 < mc["shuffle_dd_p95"] < 1 and mc["shuffle_p_ruin"] == 0.0
    mc = monte_carlo(np.array([-0.05] * 20), n=50)
    assert mc["bootstrap_p_ruin"] == 1.0


def test_add_net_R():
    tr = frame(run(flat(), make_signals(idx=[3], side=[1], stop_dist=[5.0], tmax=[6])))
    x = add_net(tr, Costs(0.0, 0.0, 0.0))
    assert x["R"].iat[0] == pytest.approx(0.0)
