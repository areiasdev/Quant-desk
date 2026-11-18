"""Tests for the meme-rotation lab (src/lab/rotation.py): look-ahead shift tests for the
cross-sectional ranking and the edge monitor, daily bars, portfolio limits (max positions, one
per symbol, leg caps, minimum-notional skips, leverage), vol-budget sizing, holdout guard."""
import json

import numpy as np
import pandas as pd
import pytest

from lab import rotation as R
from lab.data import Ctx, Sym, resample_4h
from lab.engine import BASE, NS_H

T0 = pd.Timestamp("2025-01-01", tz="UTC")
DAYS = 140


def h1_frame(seed, drift=0.0, days=DAYS, start=T0, vol=0.01, qv=5e6, age0=100.0):
    rng = np.random.default_rng(seed)
    n = days * 24
    c = 100 * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    o = np.r_[100.0, c[:-1]]
    h = np.maximum(o, c) * 1.002
    lo = np.minimum(o, c) * 0.998
    t = pd.date_range(start, periods=n, freq="h")
    return pd.DataFrame({"open_time": t, "open": o, "high": h, "low": lo, "close": c,
                         "volume": qv / c, "quote_volume": qv * rng.lognormal(-0.125, 0.5, n),
                         "trades": np.ones(n, int),
                         "days_since_listing": age0 + (np.arange(n) + 1) / 24,
                         "hype_volume_z30d": rng.normal(0, 2, n)})


def make_ctx(frames: dict) -> Ctx:
    syms = {}
    for s, h1 in frames.items():
        syms[s] = Sym(name=s, h1=h1.reset_index(drop=True), h4=resample_4h(h1),
                      fund_t=np.array([], np.int64), fund_r=np.array([]), delisted=False,
                      is_meme=(s != "BTCUSDT"), last_close=float(h1["close"].iloc[-1]))
    return Ctx(syms=syms, universe={}, memes=[s for s in frames if s != "BTCUSDT"], core=[])


def base_frames():
    return {"BTCUSDT": h1_frame(0, drift=0.0004),
            "AAAUSDT": h1_frame(1, drift=0.0006), "BBBUSDT": h1_frame(2, drift=0.0002),
            "CCCUSDT": h1_frame(3, drift=-0.0003), "DDDUSDT": h1_frame(4, drift=0.0)}


# ------------------------------------------------------------------ daily bars
def test_daily_bars_complete_day_and_decision_bar():
    h1 = h1_frame(0, days=3)
    h1 = h1.drop(index=[47]).reset_index(drop=True)            # day 2 loses its 23:00 bar
    d = R.daily_of(h1)
    assert len(d) == 3
    assert d["idx23"].iloc[0] == 23 and h1["open_time"].iloc[23].hour == 23
    assert d["close"].iloc[0] == h1["close"].iloc[23]
    assert np.isnan(d["close"].iloc[1]) and d["idx23"].iloc[1] == -1
    assert d["qv"].iloc[0] == pytest.approx(h1["quote_volume"].iloc[:24].sum())


# ------------------------------------------------------------------ ranking look-ahead
def _panel(frames):
    ctx = make_ctx(frames)
    return ctx, R.build_panel(ctx, ctx.memes)


def _small_qv_ok(monkeypatch):
    monkeypatch.setattr(R, "MIN_QV30", 1e6)


def test_ranking_uses_no_future_data(monkeypatch):
    _small_qv_ok(monkeypatch)
    fr = base_frames()
    _, P = _panel(fr)
    t1, r1 = R.select(P.score(7), P.elig, 2, 1, P.btc_up)
    cut = T0 + pd.Timedelta(days=90)
    fr2 = {k: v.copy() for k, v in fr.items()}
    for s in ("AAAUSDT", "CCCUSDT", "BTCUSDT"):
        m = fr2[s]["open_time"] >= cut
        fr2[s].loc[m, ["open", "high", "low", "close"]] *= np.linspace(0.3, 3.0, m.sum())[:, None]
    _, P2 = _panel(fr2)
    t2, r2 = R.select(P2.score(7), P2.elig, 2, 1, P2.btc_up)
    before = t1.index < cut                       # day labels whose close is <= cut
    pd.testing.assert_frame_equal(t1[before], t2[before])
    pd.testing.assert_frame_equal(r1[before], r2[before])
    assert not t1[~before].equals(t2[~before])    # the perturbation has teeth


def test_score_shift_moves_ranking_by_exactly_one_day(monkeypatch):
    _small_qv_ok(monkeypatch)
    fr = base_frames()
    _, P = _panel(fr)
    sc = P.score(14)
    fr2 = {k: v.copy() for k, v in fr.items()}
    a = fr2["AAAUSDT"]
    a["open_time"] = a["open_time"] + pd.Timedelta(days=1)    # same path, one day later
    fr2["AAAUSDT"] = a
    _, P2 = _panel(fr2)
    sc2 = P2.score(14)
    x = sc["AAAUSDT"].dropna()
    y = sc2["AAAUSDT"].shift(-1).reindex(x.index)
    np.testing.assert_allclose(x.to_numpy()[:-1], y.to_numpy()[:-1])


def test_select_is_rowwise_topk_and_gated():
    idx = pd.date_range(T0, periods=3, freq="D")
    sc = pd.DataFrame({"A": [3, -1, 2], "B": [2, -2, 1], "C": [1, -3, np.nan]}, index=idx)
    el = pd.DataFrame(True, index=idx, columns=sc.columns)
    el.loc[idx[2], "A"] = False
    tg, rk = R.select(sc, el, 2, 1)
    assert tg.iloc[0].tolist() == [True, True, False]
    assert not tg.iloc[1].any()                   # no positive momentum -> cash
    assert tg.iloc[2].tolist() == [False, True, False]
    tg, _ = R.select(sc, el, 1, -1)               # weakest, score < 0
    assert tg.iloc[1].tolist() == [False, False, True] and not tg.iloc[0].any()
    gate = pd.Series([True, True, False], index=idx)
    tg, _ = R.select(sc, el, 2, 1, gate)
    assert not tg.iloc[2].any()


def test_xs_candidates_entry_timing_and_no_lookahead(monkeypatch):
    _small_qv_ok(monkeypatch)
    fr = base_frames()
    ctx, P = _panel(fr)
    c = R.xs_candidates(ctx, P, 7, 2, "daily", 1, P.btc_up)
    assert len(c) > 0
    # entry = the 00:00 open right after the decision day's 23:00 close
    for r in c.head(20).itertuples():
        et = pd.Timestamp(r.entry_time, tz="UTC")
        assert et == r.decision_day + pd.Timedelta(days=1)
        h1 = ctx.syms[r.symbol].h1
        assert r.entry == h1.loc[h1["open_time"] == et, "open"].iat[0]
    # never more than K positions open at once
    ev = pd.Series(np.r_[np.ones(len(c)), -np.ones(len(c))],
                   index=np.r_[c["entry_time"].to_numpy(), c["exit_time"].to_numpy()])
    ev = ev.groupby(level=0).sum().sort_index()       # exits at the same instant net out first
    assert ev.cumsum().max() <= 2
    # perturbing bars after `cut` leaves every trade that closed before `cut` unchanged
    cut = T0 + pd.Timedelta(days=100)
    fr2 = {k: v.copy() for k, v in fr.items()}
    for s in fr2:
        m = fr2[s]["open_time"] >= cut
        fr2[s].loc[m, ["open", "high", "low", "close"]] *= 1.7
    ctx2, P2 = _panel(fr2)
    c2 = R.xs_candidates(ctx2, P2, 7, 2, "daily", 1, P2.btc_up)
    cols = ["symbol", "entry_time", "exit_time", "entry", "exit", "reason"]
    a = c[c["exit_time"] < cut.value][cols].reset_index(drop=True)
    b = c2[c2["exit_time"] < cut.value][cols].reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b)


def test_weekly_decisions_are_sundays(monkeypatch):
    _small_qv_ok(monkeypatch)
    ctx, P = _panel(base_frames())
    c = R.xs_candidates(ctx, P, 7, 3, "weekly", 1, None)
    assert len(c) and (pd.DatetimeIndex(c["decision_day"]).weekday == 6).all()
    assert (pd.to_datetime(c["entry_time"], utc=True).dt.weekday == 0).all()


def test_eligibility_listing_age_and_liquidity():
    fr = base_frames()
    fr["NEWUSDT"] = h1_frame(9, age0=0.0, qv=30e6)
    _, P = _panel(fr)
    e = P.elig["NEWUSDT"]
    first = e[e].index[0]
    assert P.age.at[first, "NEWUSDT"] >= 30
    assert not e[P.age["NEWUSDT"] < 30].any()


def test_liquidity_floor_is_daily_mean_over_30d():
    fr = {"BTCUSDT": h1_frame(0), "LOWUSDT": h1_frame(1, qv=0.7e6), "HIUSDT": h1_frame(2, qv=0.9e6)}
    _, P = _panel(fr)
    # ~0.7M/h -> ~16.8M/day < 20M ; ~0.9M/h -> ~21.6M/day >= 20M (30-day mean)
    assert not P.elig["LOWUSDT"].any() and P.elig["HIUSDT"].any()


# ------------------------------------------------------------------ edge monitor look-ahead
def _paper():
    H = NS_H
    sym = ["A"] * 8
    et = np.array([0, 10, 20, 30, 40, 50, 60, 70]) * H
    xt = et + 10 * H                   # each exits exactly when the next one enters
    R_ = np.array([1.0, -0.5, 0.8, 0.6, -0.2, 0.9, -3.0, 0.4])
    return sym, et, xt, R_


def test_edge_monitor_uses_only_trades_closed_strictly_before_entry():
    sym, et, xt, R_ = _paper()
    ok = R.edge_admit(sym, et, xt, R_, N=3, theta=0.0, min_hist=3)
    # trade i sees trades 0..i-2 (trade i-1 exits AT trade i's entry: not known yet)
    assert ok.tolist()[:4] == [False, False, False, False]
    assert ok[4] == (np.mean(R_[0:3]) > 0)
    assert ok[7] == (np.mean(R_[3:6]) > 0)
    # perturb every R that is not yet closed at trade 5's entry -> its admission is unchanged
    for j in range(len(R_)):
        R2 = R_.copy()
        if xt[j] >= et[5]:
            R2[j] = -100.0
            assert R.edge_admit(sym, et, xt, R2, 3, 0.0, 3)[5] == ok[5]


def test_edge_monitor_shift_has_teeth():
    sym, et, xt, R_ = _paper()
    R_ = R_.copy()
    R_[4] = -10.0
    xt2 = xt.copy()
    xt2[4] = et[5] - 1                  # trade 4 now closes one ns BEFORE trade 5 enters
    a = R.edge_admit(sym, et, xt, R_, 3, 0.0, 3)[5]
    b = R.edge_admit(sym, et, xt2, R_, 3, 0.0, 3)[5]
    assert a and not b


def test_edge_monitor_is_per_coin_and_respects_theta():
    H = NS_H
    sym = ["A", "B", "A", "B", "A", "B", "A", "B"]
    et = np.arange(8) * 10 * H
    xt = et + 5 * H
    R_ = np.array([0.2, -1, 0.2, -1, 0.2, -1, 0.0, 0.0])
    ok = R.edge_admit(sym, et, xt, R_, N=3, theta=0.0, min_hist=3)
    assert ok[6] and not ok[7]
    assert not R.edge_admit(sym, et, xt, R_, 3, 0.25, 3)[6]


# ------------------------------------------------------------------ portfolio
def cand(sym, et_h, xt_h, sf=0.05, ret=0.01, side=1, leg="main", prio=1.0, sigma=np.nan):
    entry = 100.0
    ex = entry * (1 + side * ret)
    return dict(symbol=sym, side=side, entry_time=int(T0.value + et_h * NS_H),
                exit_time=int(T0.value + xt_h * NS_H), entry=entry, exit=ex,
                entry_kind="taker", exit_kind="taker", funding=0.0, stop_frac=sf,
                slip_bp=10.0, leg=leg, prio=prio, sigma=sigma)


def test_portfolio_max_positions_symbol_and_min_notional():
    c = pd.DataFrame([cand("A", 0, 10), cand("B", 0, 10), cand("C", 0, 10), cand("D", 0, 10),
                      cand("A", 5, 20), cand("E", 10, 20), cand("F", 11, 20, sf=0.30)])
    pt, sk = R.run_portfolio(c, BASE, T0, T0 + pd.Timedelta(days=5))
    assert pt["taken"].tolist() == [True, True, True, False, False, True, False]
    assert sk["slots"] == 1 and sk["symbol_open"] == 1 and sk["min_notional"] == 1
    # 1% of 110 / 5% stop = 22 USDT
    assert pt.loc[0, "notional"] == pytest.approx(22.0)
    # 1% of ~110 / 30% stop = 3.7 USDT < 5 USDT minimum -> skipped
    assert pt.loc[6, "skip"] == "min_notional"


def test_portfolio_leverage_room_and_leg_caps():
    c = pd.DataFrame([cand("A", 0, 10, sf=0.005), cand("B", 0, 10, sf=0.005),
                      cand("C", 0, 10, sf=0.01)])
    pt, sk = R.run_portfolio(c, BASE, T0, T0 + pd.Timedelta(days=5))
    # wants 220 USDT each; 3x room = 330 -> A 220, B 110, C no room
    assert pt["notional"].tolist()[:2] == pytest.approx([220.0, 110.0])
    assert sk["leverage"] == 1
    c = pd.DataFrame([cand("A", 0, 10, leg="long"), cand("B", 0, 10, leg="long"),
                      cand("C", 0, 10, leg="short", side=-1), cand("D", 0, 10, leg="short",
                                                                    side=-1),
                      cand("E", 0, 10, leg="short", side=-1)])
    pt, sk = R.run_portfolio(c, BASE, T0, T0 + pd.Timedelta(days=5),
                             leg_caps={"long": 1, "short": 2})
    assert pt["taken"].tolist() == [True, False, True, True, False]


def test_portfolio_exit_frees_slot_at_same_instant():
    c = pd.DataFrame([cand("A", 0, 10), cand("B", 0, 10), cand("C", 0, 10), cand("D", 10, 20)])
    pt, _ = R.run_portfolio(c, BASE, T0, T0 + pd.Timedelta(days=5))
    assert pt["taken"].all()


def test_vol_budget_never_exceeds_risk_size():
    c = pd.DataFrame([cand("A", 0, 10, sf=0.10, sigma=0.01), cand("B", 0, 10, sf=0.05,
                                                                 sigma=0.10)])
    pt, sk = R.run_portfolio(c, BASE, T0, T0 + pd.Timedelta(days=5), vol_budget=0.005)
    assert pt.loc[0, "notional"] == pytest.approx(11.0)       # risk size 11 < vol size 55
    assert pt.loc[1, "notional"] == pytest.approx(5.5)        # vol size 5.5 < risk size 22


def test_min_notional_values():
    assert R.min_notional("BTCUSDT", 100_000.0) == pytest.approx(100.0)   # 0.001 BTC
    assert R.min_notional("BTCUSDT", 30_000.0) == pytest.approx(50.0)
    assert R.min_notional("ETHUSDT") == 20.0 and R.min_notional("1000PEPEUSDT") == 5.0


def test_mtm_equity_marks_open_positions():
    fr = base_frames()
    ctx, P = _panel(fr)
    d0 = P.dates[10]
    px0 = P.close.at[d0, "AAAUSDT"]
    pt = pd.DataFrame([dict(symbol="AAAUSDT", side=1, entry_time=(d0 + pd.Timedelta(days=1)).value,
                            exit_time=(d0 + pd.Timedelta(days=4)).value, entry=px0,
                            notional=50.0, pnl=2.0, taken=True)])
    eq = R.mtm_equity(pt, P, d0 - pd.Timedelta(days=2), d0 + pd.Timedelta(days=8))
    d1 = d0 + pd.Timedelta(days=1)
    assert eq[d0] == 110.0
    assert eq[d1] == pytest.approx(110 + 50 * (P.close.at[d1, "AAAUSDT"] / px0 - 1))
    assert eq.iloc[-1] == pytest.approx(112.0)


# ------------------------------------------------------------------ protocol guards
def test_truncate_ctx_removes_every_holdout_bar():
    fr = base_frames()
    ctx = make_ctx(fr)
    cut = T0 + pd.Timedelta(days=60)
    t = R.truncate_ctx(ctx, cut)
    for s, sy in t.syms.items():
        assert sy.h1["open_time"].max() < cut and sy.h4["open_time"].max() < cut
        assert not sy.delisted


def test_holdout_refuses_to_run_twice(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "OUT", tmp_path)
    (tmp_path / "holdout.json").write_text(json.dumps({}))
    with pytest.raises(SystemExit):
        R.holdout_main()


def test_windows_and_grids_are_frozen_and_small():
    assert R.DEV_END == R.HOLDOUT_START == pd.Timestamp("2026-05-01", tz="UTC")
    assert R.HOLDOUT_END == pd.Timestamp("2026-11-01", tz="UTC")
    for sid, st in R.STRATS.items():
        assert len(st["grid"]) <= 3
        for k, v in st["grid"].items():
            assert len(v) <= 4 and st["default"][k] in v
    w = R.wf_windows()
    assert w[0][1] == pd.Timestamp("2023-11-01", tz="UTC") and w[-1][2] <= R.DEV_END
    assert len(w) == 10


def test_setup_candidates_eligibility_uses_last_complete_day(monkeypatch):
    _small_qv_ok(monkeypatch)
    ctx, P = _panel(base_frames())
    d4 = ctx.syms["AAAUSDT"].h4
    day = R._day_of_4h(d4)
    # the 20:00 bar closes at 00:00 next day -> that day is complete; the 00:00 bar -> previous day
    i20 = int(np.flatnonzero(d4["open_time"].dt.hour == 20)[5])
    assert day[i20] == d4["open_time"].iat[i20].floor("D")
    assert day[i20 - 5] == d4["open_time"].iat[i20 - 5].floor("D") - pd.Timedelta(days=1)
    c = R.setup_candidates(ctx, P, "S02", {})
    assert len(c) > 0
    for r in c.itertuples():
        assert P.elig.at[r.decision_day, r.symbol]
        assert r.entry_time >= (r.decision_day + pd.Timedelta(days=1)).value


@pytest.mark.parametrize("sid", ["R1", "R2", "R3", "R4", "R5"])
def test_every_strategy_runs_end_to_end_on_synthetic_data(sid, monkeypatch):
    _small_qv_ok(monkeypatch)
    ctx, P = _panel(base_frames())
    lab = R.Lab(ctx, P, r4_base="R1")
    s, e = T0 + pd.Timedelta(days=40), T0 + pd.Timedelta(days=DAYS)
    for p in R.combos(sid)[:3] + [R.STRATS[sid]["default"]]:
        pt, sk, m = lab.run(sid, p, BASE, s, e)
        assert m["trades"] <= len(pt)
        if len(pt):
            tk = pt[pt["taken"]]
            ev = pd.Series(np.r_[np.ones(len(tk)), -np.ones(len(tk))],
                           index=np.r_[tk["entry_time"].to_numpy(), tk["exit_time"].to_numpy()])
            assert ev.groupby(level=0).sum().sort_index().cumsum().max() <= R.MAX_POS
            assert (tk["notional"] >= 5.0).all()
