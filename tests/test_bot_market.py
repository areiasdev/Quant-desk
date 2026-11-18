"""Bars, funding and universe: the bot's inputs are built exactly like LAB's / RADAR's."""
import os

import numpy as np
import pandas as pd
import pytest

from bot.market import H, Feed, build_panel, infer_intervals, parse_funding, parse_klines, \
    tf_frame
from bot.universe import Classifier, compute_universe, parse_filters, tradeable_perps
from radar.config import CLEAN, DATA

needs_archive = pytest.mark.skipif(not (CLEAN / "lab_cache" / "1h").exists(),
                                   reason="needs the data/clean archive")


def test_parse_klines_binance_format():
    rows = [[1700000000000, "1.5", "2.0", "1.0", "1.8", "10", 1700003599999, "18.1", 42, "5",
             "9", "0"]]
    k = parse_klines(rows)
    assert k["open_time"].iat[0] == 1700000000000 and k["close"].iat[0] == 1.8
    assert k["trades"].dtype == np.int64 and len(parse_klines([])) == 0


def test_infer_intervals_rule():
    t = np.array([0, 8, 16, 20, 24, 28, 36]) * H
    assert list(infer_intervals(t, 44 * H)) == [8, 8, 4, 4, 4, 4, 8]
    # a missed settlement (16 -> 32) does not make a 16h interval
    t = np.array([0, 8, 16, 32, 40]) * H
    assert list(infer_intervals(t, None)) == [8, 8, 8, 8, 8]


@needs_archive
def test_inferred_funding_intervals_match_archive():
    tot = bad = 0
    for p in sorted((CLEAN / "funding").glob("*.parquet")):
        f = pd.read_parquet(p)
        if len(f) < 2:
            continue
        t = f["calc_time"].astype("datetime64[ms, UTC]").astype("int64").to_numpy()
        got = infer_intervals(t, None)
        tot += len(f)
        bad += int((got != f["funding_interval_hours"].to_numpy()).sum())
    assert tot > 500_000
    assert bad / tot < 2e-4, (bad, tot)          # 68 of 525,822 when this was written


@needs_archive
@pytest.mark.parametrize("sym", ["SOLUSDT", "ETHUSDT"])
def test_panel_and_4h_bars_equal_lab(sym):
    """Feed the archive through the bot's parsers: panel and 4h bars equal LAB's cache."""
    from lab.data import add_derived, resample_4h

    lab = pd.read_parquet(CLEAN / "lab_cache" / "1h" / f"{sym}.parquet")
    add_derived(lab, pd.Series(dtype=float, index=pd.DatetimeIndex([], tz="UTC")))
    w = lab[(lab["open_time"] >= "2026-03-01") & (lab["open_time"] < "2026-07-01")]
    rows = [[int(t.value // 10**6), str(o), str(h), str(l), str(c), str(v), 0, str(q), int(n),
             "0", "0", "0"] for t, o, h, l, c, v, q, n in
            zip(w["open_time"], w["open"], w["high"], w["low"], w["close"], w["volume"],
                w["quote_volume"], w["trades"])]
    f = pd.read_parquet(CLEAN / "funding" / f"{sym}.parquet")
    fr = [{"fundingTime": int(x.value // 10**6), "fundingRate": str(r)}
          for x, r in zip(f["calc_time"], f["funding_rate"])]
    p = build_panel(parse_klines(rows), parse_funding(fr))
    m = w.merge(p, on="open_time", suffixes=("_l", "_b"))
    assert len(m) == len(w)
    for c in ("close", "F8", "funding_rate", "funding_interval_hours", "funding_time"):
        a, b = m[c + "_l"], m[c + "_b"]
        assert ((a == b) | (a.isna() & b.isna())).all(), c
    # first row has no 'previous' settlement in the window; everything after equals LAB
    for c in ("new_settle", "settleF8"):
        a, b = m[c + "_l"].iloc[1:], m[c + "_b"].iloc[1:]
        assert ((a == b) | (a.isna() & b.isna())).all(), c
    d_lab = resample_4h(w.reset_index(drop=True))
    d_bot = tf_frame(p, "4h")
    pd.testing.assert_frame_equal(d_lab.iloc[1:].reset_index(drop=True)[["open_time", "open",
                                  "high", "low", "close", "trades", "F8"]],
                                  d_bot.iloc[1:].reset_index(drop=True)[["open_time", "open",
                                  "high", "low", "close", "trades", "F8"]])


def test_feed_keeps_closed_bars_and_the_live_open():
    class Src:
        def klines(self, symbol, interval, start_ms=None, end_ms=None, limit=500):
            out = []
            t = start_ms
            while t <= now and len(out) < limit:
                out.append([t, "1", "2", "0.5", "1.5", "1", t + H - 1, "1", 1, "0", "0", "0"])
                t += H
            return out

        def funding_rate(self, symbol, start_ms=None, end_ms=None, limit=1000):
            return [{"fundingTime": 8 * H * k, "fundingRate": "0.0001"}
                    for k in range(1, 400) if start_ms <= 8 * H * k <= now][:limit]

    now = 2000 * H + 45_000
    f = Feed("X", 1000)
    f.update(Src(), now)
    assert f.last_closed_close_ms() == 2000 * H and len(f.bars) == 1000
    assert f.live["open_time"] == 2000 * H and f.open_at(2000 * H) == 1.0
    now += H
    f.update(Src(), now)
    assert f.last_closed_close_ms() == 2001 * H and len(f.bars) == 1000
    assert f.fund_t[-1] == 8 * H * 250 and f.price_for_funding(8 * H * 250 + 5) == 1.0


def test_exchange_info_filters_and_perp_selection():
    info = {"symbols": [
        {"symbol": "BTCUSDT", "contractType": "PERPETUAL", "quoteAsset": "USDT", "status":
         "TRADING", "filters": [
             {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
             {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
             {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
             {"filterType": "MIN_NOTIONAL", "notional": "100"}]},
        {"symbol": "BTCUSDT_261225", "contractType": "CURRENT_QUARTER", "quoteAsset": "USDT",
         "status": "TRADING", "filters": []},
        {"symbol": "OLDUSDT", "contractType": "PERPETUAL", "quoteAsset": "USDT",
         "status": "SETTLING", "filters": []},
        {"symbol": "ETHBTC", "contractType": "PERPETUAL", "quoteAsset": "BTC",
         "status": "TRADING", "filters": []}]}
    f = parse_filters(info)
    assert f["BTCUSDT"] == {"tick_size": 0.1, "step_size": 0.001, "min_qty": 0.001,
                            "min_notional": 100.0}
    assert [s["symbol"] for s in tradeable_perps(info)] == ["BTCUSDT"]


def test_classifier_uses_radar_lists_and_fingerprint():
    c = Classifier()
    assert c.classify("BTCUSDT")[0] == "crypto"
    assert c.classify("USDCUSDT")[0] == "stablecoin"
    assert c.classify("NEWSTOCKUSDT")[0] == "crypto"     # unknown, no data: default

    class Src:   # a new listing whose weekend volume collapses = TradFi (RADAR fingerprint)
        def klines(self, symbol, interval, start_ms=None, end_ms=None, limit=500):
            d0 = pd.Timestamp("2026-01-05", tz="UTC")
            out = []
            for k in range(70):
                t = d0 + pd.Timedelta(days=k)
                qv = "100" if t.dayofweek >= 5 else "1000"
                out.append([int(t.value // 10**6), "1", "1.1", "0.9", "1", "1", 0, qv, 5, "0",
                            "0", "0"])
            return out

    onboard = int(pd.Timestamp("2026-01-05").value // 10**6)
    assert Classifier().classify("ZZZSTOCKUSDT", Src(), onboard) == ("tradfi", "fingerprint")


@needs_archive
def test_universe_recomputed_from_klines_equals_radar():
    """Point-in-time top-10 from (replayed) klines == data/universe_by_month.csv."""
    from bot.replay import ReplayDataSource, SimClock

    u = pd.read_csv(DATA / "universe_by_month.csv")
    clock = SimClock(0)
    src = ReplayDataSource(clock)
    cl = Classifier()
    for month in ("2024-03", "2025-06", "2026-08", "2026-10"):
        m = int(pd.Timestamp(month + "-01", tz="UTC").value // 10**6)
        clock.set(m + 30_000)
        got = compute_universe(src, m, cl)
        exp = u[u["month"] == month].sort_values("rank")["symbol"].tolist()
        assert got["symbols"] == exp, month
