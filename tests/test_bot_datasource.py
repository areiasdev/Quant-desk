"""Live REST data source, tested offline with a fake HTTP session (Binance is HTTP 451 here)."""
import pytest

from bot.datasource import FetchError, GeoBlocked, RestDataSource, WeightLimiter, kline_weight
from bot.replay import SimClock


class Resp:
    def __init__(self, code, body=None, headers=None):
        self.status_code, self._b, self.headers, self.text = code, body, headers or {}, str(body)

    def json(self):
        return self._b


class Session:
    def __init__(self, responses):
        self.r = list(responses)
        self.calls = []
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        x = self.r.pop(0)
        if isinstance(x, Exception):
            raise x
        return x


def src(responses, retries=3):
    clock = SimClock(1_000_000_000_000)
    s = Session(responses)
    return RestDataSource(clock=clock, session=s, retries=retries, max_weight_per_minute=1200), s, \
        clock


def test_get_only_public_endpoints_without_keys():
    d, s, _ = src([Resp(200, {"symbols": []}), Resp(200, [[1, "1"]]), Resp(200, []),
                   Resp(200, [])])
    d.exchange_info()
    d.klines("BTCUSDT", "1h", start_ms=5, limit=99)
    d.funding_rate("BTCUSDT", start_ms=7)
    d.premium_index()
    urls = [u for u, _ in s.calls]
    assert urls == ["https://fapi.binance.com/fapi/v1/exchangeInfo",
                    "https://fapi.binance.com/fapi/v1/klines",
                    "https://fapi.binance.com/fapi/v1/fundingRate",
                    "https://fapi.binance.com/fapi/v1/premiumIndex"]
    assert s.calls[1][1] == {"symbol": "BTCUSDT", "interval": "1h", "limit": 99, "startTime": 5}
    assert not any("key" in k.lower() for k in s.headers)
    with pytest.raises(ValueError):
        d._get("/fapi/v1/order", {}, 1)


def test_retries_with_backoff_then_succeeds():
    d, s, clock = src([ConnectionError("reset"), Resp(500, "oops"), Resp(200, {"ok": 1})])
    t0 = clock.now_ms()
    assert d.exchange_info() == {"ok": 1}
    assert len(s.calls) == 3
    assert clock.now_ms() - t0 >= 1000 + 2000          # backoff 1 s then 2 s (+ jitter)


def test_429_honours_retry_after_and_418_backs_off():
    d, s, clock = src([Resp(429, "slow down", {"Retry-After": "7"}), Resp(200, [])])
    t0 = clock.now_ms()
    d.premium_index()
    assert clock.now_ms() - t0 >= 7000
    d, s, _ = src([Resp(418, "banned", {"Retry-After": "120"}), Resp(200, [])])
    d.premium_index()
    assert len(s.calls) == 2


def test_gives_up_after_retries_and_does_not_retry_client_errors():
    d, s, _ = src([Resp(503, "x")] * 4, retries=3)
    with pytest.raises(FetchError, match="giving up after 4"):
        d.exchange_info()
    d, s, _ = src([Resp(400, "bad symbol")])
    with pytest.raises(FetchError, match="HTTP 400"):
        d.klines("NOPE", "1h")
    assert len(s.calls) == 1


def test_http_451_is_geoblock_not_retried():
    d, s, _ = src([Resp(451, "restricted location")])
    with pytest.raises(GeoBlocked, match="do not try to bypass"):
        d.exchange_info()
    assert len(s.calls) == 1


def test_weight_limiter_waits_when_budget_used():
    clock = SimClock(0)
    lim = WeightLimiter(20, clock)
    for _ in range(4):
        assert lim.acquire(5) == 0
    waited = lim.acquire(5)
    assert waited >= 59 and clock.now_ms() >= 60_000
    lim.server_used = 2300                      # Binance says we are near its limit
    assert lim.acquire(1) > 0
    assert [kline_weight(x) for x in (99, 100, 500, 1000, 1500)] == [1, 2, 5, 5, 10]
