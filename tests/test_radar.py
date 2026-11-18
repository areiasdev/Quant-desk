"""Unit tests for the RADAR data pipeline (no network)."""
import datetime as dt
import io
import zipfile

import numpy as np
import pandas as pd

from radar.archive import _is_header, count_csv_rows
from radar.features import funding_asof_1h, metrics_asof_1h
from radar.symbols import fingerprint_is_tradfi, static_class
from radar.universe import trailing_rank


def _zip(text: str) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("x.csv", text)
    return b.getvalue()


def test_header_detection_and_row_count():
    assert _is_header("open_time,open,high\n")
    assert not _is_header("1667260800000,20482.1,20492.1\n")
    assert not _is_header("2026-11-17 02:30:00,BTCUSDT,1\n")  # metrics rows start with a date
    assert count_csv_rows(_zip("open_time,open\n1,2\n3,4\n")) == 2
    assert count_csv_rows(_zip("1,2\n3,4\n5,6\n")) == 3


def _bars(sym, start, n, qv, trades=1, freq="h"):
    t = pd.date_range(start, periods=n, freq=freq)
    return pd.DataFrame({"symbol": sym, "open_time": t, "quote_volume": qv, "trades": trades})


def test_trailing_rank_is_point_in_time_and_skips_ghost_bars():
    m = dt.date(2024, 2, 1)
    a = _bars("AAA", "2024-01-02", 30 * 24, 10.0)            # trades up to the month start
    b = _bars("BBB", "2024-01-02", 30 * 24, 20.0)
    b.loc[b["open_time"] >= "2024-01-29", "trades"] = 0       # delisted 3 days before: ghost bars
    c = _bars("CCC", "2024-02-01", 24, 1e9)                  # volume AFTER the month start
    r = trailing_rank(pd.concat([a, b, c]), "open_time", m)
    assert list(r["symbol"]) == ["AAA"]                      # BBB is a ghost, CCC is the future


def test_fingerprint_only_reclasses_new_listings():
    assert fingerprint_is_tradfi("2026-05", 20, 0.10)
    assert not fingerprint_is_tradfi("2026-05", 20, 0.60)
    assert not fingerprint_is_tradfi("2026-05", 4, 0.10)     # too little weekend history
    assert not fingerprint_is_tradfi("2020-01", 148, 0.45)   # BTC-like old listing never reclassed
    assert static_class("XAUUSDT") == "tradfi" and static_class("BTCUSDT") == "crypto"


def test_funding_is_visible_only_after_settlement():
    t = pd.date_range("2024-01-01 06:00", periods=4, freq="h", tz="UTC")
    bars = pd.DataFrame({"open_time": t})
    bars["close_time"] = bars["open_time"] + pd.Timedelta(hours=1) - pd.Timedelta(milliseconds=1)
    f = pd.DataFrame({"calc_time": [pd.Timestamp("2024-01-01 08:00:00.003", tz="UTC")],
                      "funding_time": [pd.Timestamp("2024-01-01 08:00", tz="UTC")],
                      "funding_interval_hours": [8], "funding_rate": [0.001]})
    out = funding_asof_1h(bars, f)
    # 06:00 and 07:00 bars close before the settlement; the 08:00 bar contains it
    assert out["funding_rate"].isna().tolist() == [True, True, False, False]


def test_metrics_asof_uses_rows_inside_the_bar_only():
    t = pd.date_range("2024-01-01 00:00", periods=3, freq="h", tz="UTC")
    bars = pd.DataFrame({"open_time": t})
    m = pd.DataFrame({"create_time": pd.to_datetime(["2024-01-01 00:55", "2024-01-01 01:00"], utc=True),
                      "open_interest": [1.0, 2.0]})
    out = metrics_asof_1h(bars, m)
    assert out["open_interest"].iloc[0] == 1.0      # 00:55 row inside bar 00:00
    assert out["open_interest"].iloc[1] == 2.0      # 01:00 row belongs to bar 01:00, not bar 00:00
    assert np.isnan(out["open_interest"].iloc[2])   # nothing inside bar 02:00 -> NaN, not ffill
