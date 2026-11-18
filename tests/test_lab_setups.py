"""Look-ahead test for every setup's signal code (synthetic data, no cache needed).

Targeted truncation test: for real signal bars s of each setup, rebuild all data so that it ENDS
at the close of bar s (every symbol cut at the same instant) and recompute the signals. Every
full-data signal with signal bar <= s must reappear identically (bar, side, stop, target, ...),
and no extra signal may appear. An indicator that peeks at bars after the signal bar (a centred
window, a non-shifted level, a day aggregate taken at the day's end, ...) breaks this.
`test_planted_leak_is_caught` proves the test has teeth.
"""
import numpy as np
import pandas as pd
import pytest

import lab.setups as S
from lab.data import Ctx, Sym, add_derived, resample_4h
from lab.setups import SETUPS

N = 6000
CMP = ["side", "stop", "stop_dist", "target", "target_R", "close_exit"]


def _base(n, rng):
    return rng.standard_t(3, n) * 0.006 + np.sin(np.arange(n) / 400) * 0.0015


def synth(n, seed, btc_r=None, start="2023-01-02"):
    rng = np.random.default_rng(seed)
    t = pd.date_range(start, periods=n, freq="h", tz="UTC")
    r = _base(n, rng) if btc_r is None else 0.8 * btc_r + 0.5 * _base(n, rng)
    oi_r = rng.standard_t(3, n) * 0.008
    vmult = np.ones(n)
    z = rng.normal(0, 1.2, n)
    # injected events: liquidation flushes, squeezes, pumps (incl. one in the first days)
    for p in list(rng.integers(300, n - 50, 14)) + [60]:
        kind = rng.integers(0, 3)
        if kind == 0:          # flush: three -6% bars, OI -7% each, heavy volume
            r[p:p + 3] -= 0.06
            oi_r[p:p + 3] -= 0.07
            vmult[p:p + 3] = 8
        elif kind == 1:        # squeeze up with falling OI
            r[p:p + 3] += 0.06
            oi_r[p:p + 3] -= 0.07
            vmult[p:p + 3] = 8
        else:                  # pump: +2%/bar for 24 bars, rising OI, then fade
            r[p:p + 24] += 0.02
            oi_r[p:p + 24] += 0.02
            vmult[p:p + 24] = 5
            z[p + 20:p + 26] += 7
            r[p + 24:p + 30] -= 0.03
    c = 100 * np.exp(np.cumsum(r))
    o = np.r_[c[0], c[:-1]]
    wick = np.abs(rng.normal(0, 0.004, n)) + 0.03 * (rng.random(n) < 0.01)
    h = np.maximum(o, c) * (1 + wick)
    lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.004, n)))
    vol = rng.lognormal(12, 0.5, n) * vmult
    oi = 1e6 * np.exp(np.cumsum(oi_r))
    settle = t.floor("8h")
    k = ((settle - settle[0]) / pd.Timedelta(hours=8)).astype(int)
    rates = rng.normal(0.0001, 0.0004, k.max() + 1)
    ext = rng.random(len(rates))
    rates[ext < 0.03] = 0.0015
    rates[(ext >= 0.03) & (ext < 0.06)] = -0.0015
    d = pd.DataFrame({
        "open_time": t, "open": o, "high": h, "low": lo, "close": c, "volume": vol,
        "quote_volume": vol * c, "trades": np.ones(n, int) * 100,
        "funding_rate": rates[k], "funding_time": settle, "funding_interval_hours": 8,
        "open_interest": oi, "open_interest_value_usdt": oi * c,
        "top_trader_position_ls_ratio": np.exp(np.cumsum(rng.normal(0, 0.03, n))),
        "global_account_ls_ratio": np.exp(np.cumsum(rng.normal(0, 0.03, n))),
        "hype_volume_z30d": z,
        "hype_oi_surge_7d": rng.normal(0.3, 0.4, n),
        "hype_funding_jump_bp": rng.normal(5, 15, n),
        "hype_move_vs_btc_24h": rng.normal(0, 0.12, n),
        "days_since_listing": (np.arange(n) + 1) / 24.0,
    })
    return d, r


def _raw():
    btc, br = synth(N, 1)
    return {"BTCUSDT": btc, "AAAUSDT": synth(N, 2, br)[0], "BBBUSDT": synth(N, 3, br)[0],
            "CCCUSDT": synth(N, 4, br)[0]}


RAW = _raw()


def make_ctx(end=None):
    raw = {s: (d if end is None else d[d["open_time"] < end]).copy() for s, d in RAW.items()}
    btc_lr = np.log(raw["BTCUSDT"].set_index("open_time")["close"]).diff()
    syms = {}
    for s, h1 in raw.items():
        add_derived(h1, btc_lr)
        syms[s] = Sym(name=s, h1=h1, h4=resample_4h(h1), fund_t=np.array([], np.int64),
                      fund_r=np.array([]), delisted=False, is_meme=s != "BTCUSDT")
    months = pd.date_range("2022-12-01", "2024-12-01", freq="MS").strftime("%Y-%m")
    return Ctx(syms=syms, universe={m: set(raw) for m in months},
               memes=[s for s in raw if s != "BTCUSDT"], core=list(raw))


FULL = make_ctx()


def signals(ctx, sid):
    st = SETUPS[sid]
    if st.cross:
        return dict(st.fn(ctx, st.defaults, lambda s, d: np.ones(len(d), bool)))
    syms = ctx.memes if st.kind == "meme" else ctx.core
    return {s: st.fn(ctx.tf(s, st.tf), st.defaults, ctx.syms[s], ctx) for s in syms}


def check_prefix(sid, full_sig, end):
    """Data cut at `end` (exclusive, = close of the last bar kept): compare."""
    st = SETUPS[sid]
    cut_ctx = make_ctx(end)
    part = signals(cut_ctx, sid)
    for s in set(full_sig) | set(part):
        bars = cut_ctx.tf(s, st.tf)
        kb = len(bars)
        a = full_sig[s][0] if s in full_sig else None
        b = part[s][0] if s in part else None
        a = a[a["idx"] < kb].reset_index(drop=True) if a is not None else None
        b = b[b["idx"] < kb].reset_index(drop=True) if b is not None else None
        if a is None or b is None:
            assert (a is None or len(a) == 0) and (b is None or len(b) == 0), (sid, s, end)
            continue
        assert a["idx"].tolist() == b["idx"].tolist(), (sid, s, end)
        pd.testing.assert_frame_equal(a[CMP], b[CMP], check_dtype=False, obj=f"{sid} {s} {end}")
        known = b["tmax"] < kb
        assert np.allclose(a.loc[known, "tmax"], b.loc[known, "tmax"], equal_nan=True), (sid, s)
        for i in (1, 2):
            xa, xb = full_sig[s][i], part[s][i]
            if xa is not None and xb is not None:
                assert np.array_equal(np.asarray(xa)[:kb], np.asarray(xb)[:kb]), (sid, s, end)


def cut_points(sid, full_sig, per_sym=3):
    """Ends right after real signal bars (the signal bar becomes the last bar)."""
    st = SETUPS[sid]
    bar = pd.Timedelta(hours=1 if st.tf == "1h" else 4)
    ends = []
    for s, (sig, _, _) in sorted(full_sig.items()):
        if len(sig) == 0:
            continue
        ix = np.unique(sig["idx"].to_numpy())
        pick = ix[np.linspace(0, len(ix) - 1, min(per_sym, len(ix))).astype(int)]
        t = FULL.tf(s, st.tf)["open_time"].to_numpy()
        ends += [pd.Timestamp(t[i]) + bar for i in pick]
    return sorted(set(ends))[:6]


@pytest.mark.parametrize("sid", list(SETUPS))
def test_signals_do_not_use_future_bars(sid):
    full_sig = signals(FULL, sid)
    ends = cut_points(sid, full_sig)
    for end in ends:
        check_prefix(sid, full_sig, end)


def test_truncation_test_has_teeth():
    fired = [sid for sid in SETUPS if cut_points(sid, signals(FULL, sid))]
    assert len(fired) >= 36, sorted(set(SETUPS) - set(fired))


LEAKS = {
    # centred moving average: uses the next n/2 closes
    "SMA": lambda x, n: x.rolling(n, center=True, min_periods=n).mean(),
    # ATR window moved two bars into the future
    "ATR": lambda d, n: S.pd.concat([d["high"] - d["low"],
                                    (d["high"] - d["close"].shift(1)).abs(),
                                    (d["low"] - d["close"].shift(1)).abs()], axis=1)
    .max(axis=1).shift(-2).rolling(n, min_periods=n).mean(),
}


@pytest.mark.parametrize("leak,sid", [("SMA", "S05"), ("SMA", "S16"), ("SMA", "M11"),
                                      ("ATR", "S02"), ("ATR", "S13"), ("ATR", "M01"),
                                      ("ATR", "S26")])
def test_planted_leak_is_caught(leak, sid, monkeypatch):
    """A planted look-ahead in a building block must be detected by the truncation check."""
    monkeypatch.setattr(S, leak, LEAKS[leak])
    full_sig = signals(FULL, sid)
    ends = cut_points(sid, full_sig)
    assert ends, f"{sid} does not fire with the planted leak; pick another setup"
    with pytest.raises(AssertionError):
        for end in ends:
            check_prefix(sid, full_sig, end)
