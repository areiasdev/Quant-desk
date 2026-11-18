"""Market data sources. Read-only, public endpoints only.

Two implementations share one interface and return Binance-shaped JSON, so every parser and every
decision downstream is the same code in both:

* `RestDataSource`  - live Binance USDT-M futures public REST (fapi.binance.com), GET only, no API
                      key, weight-based rate limiter, retries with exponential backoff + jitter,
                      honours 429/418 Retry-After. NOT testable from the cloud box (HTTP 451):
                      its HTTP layer is unit-tested with a fake session; the real endpoints are
                      listed as untested in bot/README.md.
* `ReplayDataSource` (bot/replay.py) - the same calls served from the data/clean archive, with a
                      simulated clock and no look-ahead.

There is deliberately no method that writes anything to the exchange.
"""
from __future__ import annotations

import logging
import os
import random
import threading
import time
from collections import deque

log = logging.getLogger("bot")

ENDPOINTS = {  # path -> documented request weight (Binance USDT-M docs; ESTIMATE, re-check)
    "/fapi/v1/exchangeInfo": 1,
    "/fapi/v1/klines": None,          # depends on limit, see kline_weight()
    "/fapi/v1/premiumIndex": None,    # 1 with symbol, 10 without
    "/fapi/v1/fundingRate": 1,        # separate 500 / 5 min / IP limit
}
KEY_ENV_HINTS = ("BINANCE", "API_KEY", "APIKEY", "API_SECRET", "SECRET_KEY")


class FetchError(Exception):
    """Data could not be fetched (network, HTTP error, rate limit, geo-block). Counted by Shield."""


class GeoBlocked(FetchError):
    pass


def kline_weight(limit: int) -> int:
    if limit < 100:
        return 1
    if limit < 500:
        return 2
    if limit <= 1000:
        return 5
    return 10


def warn_if_api_keys(env=None) -> list[str]:
    """Return (and log) the NAMES of API-key-like env vars. Values are never read or logged."""
    env = os.environ if env is None else env
    names = sorted(k for k in env if any(h in k.upper() for h in KEY_ENV_HINTS))
    if names:
        log.warning("API-key-like environment variables found and IGNORED (paper bot needs no "
                    "keys and never reads them): %s", ", ".join(names))
    return names


class Clock:
    """Wall clock. Replay uses bot.replay.SimClock with the same two methods."""

    def now_ms(self) -> int:
        return int(time.time() * 1000)

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


class DataSource:
    """Interface. All times are epoch milliseconds (UTC)."""

    name = "base"
    live = False

    def exchange_info(self) -> dict:
        raise NotImplementedError

    def klines(self, symbol: str, interval: str, start_ms: int | None = None,
               end_ms: int | None = None, limit: int = 500) -> list[list]:
        raise NotImplementedError

    def funding_rate(self, symbol: str, start_ms: int | None = None, end_ms: int | None = None,
                     limit: int = 1000) -> list[dict]:
        raise NotImplementedError

    def premium_index(self) -> list[dict]:
        raise NotImplementedError


class WeightLimiter:
    """Sliding 60 s window of request weight, plus a separate count for fundingRate
    (500 / 5 min). Also slows down when Binance's X-MBX-USED-WEIGHT-1M header says we are near
    the 2400/min IP limit."""

    def __init__(self, max_per_minute: int, clock: Clock, funding_per_5min: int = 400):
        self.max = max_per_minute
        self.clock = clock
        self.w: deque = deque()
        self.f: deque = deque()
        self.fmax = funding_per_5min
        self.server_used = 0
        self.lock = threading.Lock()

    def _trim(self, now):
        while self.w and now - self.w[0][0] >= 60_000:
            self.w.popleft()
        while self.f and now - self.f[0] >= 300_000:
            self.f.popleft()

    def acquire(self, weight: int, funding: bool = False) -> float:
        waited = 0.0
        while True:
            with self.lock:
                now = self.clock.now_ms()
                self._trim(now)
                used = sum(x for _, x in self.w)
                ok = used + weight <= self.max and (not funding or len(self.f) < self.fmax)
                if self.server_used > 0.8 * 2400:
                    ok = False
                    self.server_used = 0      # wait once, then trust our own window again
                if ok:
                    self.w.append((now, weight))
                    if funding:
                        self.f.append(now)
                    return waited
                if self.w:
                    wait = max(0.25, (self.w[0][0] + 60_000 - now) / 1000)
                else:
                    wait = 1.0
                if funding and len(self.f) >= self.fmax:
                    wait = max(wait, (self.f[0] + 300_000 - now) / 1000)
            self.clock.sleep(min(wait, 60.0))
            waited += min(wait, 60.0)


class RestDataSource(DataSource):
    """Live public REST. GET only; never sends API keys or signatures."""

    name = "binance-rest"
    live = True

    def __init__(self, base_url: str = "https://fapi.binance.com", *, clock: Clock | None = None,
                 max_weight_per_minute: int = 1200, timeout: float = 15.0, retries: int = 5,
                 session=None, rng: random.Random | None = None):
        import requests  # local import: tests inject a fake session

        self.base = base_url.rstrip("/")
        self.clock = clock or Clock()
        self.timeout = timeout
        self.retries = retries
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "paper-bot/1.0 (read-only)"})
        self.limiter = WeightLimiter(max_weight_per_minute, self.clock)
        self.rng = rng or random.Random()
        warn_if_api_keys()

    # ---- HTTP core -----------------------------------------------------------------------
    def _get(self, path: str, params: dict, weight: int, funding: bool = False):
        if path not in ENDPOINTS:
            raise ValueError(f"endpoint not allowed: {path}")
        last = None
        for attempt in range(self.retries + 1):
            self.limiter.acquire(weight, funding=funding)
            try:
                r = self.session.get(self.base + path, params=params, timeout=self.timeout)
            except Exception as e:  # connection errors, timeouts, DNS ...
                last = f"{type(e).__name__}: {e}"
                self._backoff(attempt, None, last)
                continue
            used = r.headers.get("X-MBX-USED-WEIGHT-1M") if r.headers else None
            if used and str(used).isdigit():
                self.limiter.server_used = int(used)
            if r.status_code == 200:
                try:
                    return r.json()
                except ValueError as e:
                    last = f"bad JSON: {e}"
                    self._backoff(attempt, None, last)
                    continue
            if r.status_code == 451:
                raise GeoBlocked(f"HTTP 451 from {self.base}{path}: Binance blocks this location "
                                 "(do not try to bypass it)")
            ra = r.headers.get("Retry-After") if r.headers else None
            last = f"HTTP {r.status_code} on {path}: {str(getattr(r, 'text', ''))[:200]}"
            if r.status_code in (418, 429) or r.status_code >= 500:
                self._backoff(attempt, ra, last)
                continue
            raise FetchError(last)            # other 4xx: our request is wrong, do not hammer
        raise FetchError(f"giving up after {self.retries + 1} attempts: {last}")

    def _backoff(self, attempt: int, retry_after, why: str) -> None:
        if attempt >= self.retries:
            return
        if retry_after is not None and str(retry_after).isdigit():
            wait = float(retry_after)
        else:
            wait = min(60.0, 2.0 ** attempt) * (1 + 0.25 * self.rng.random())
        log.warning("fetch problem (%s); retry %d/%d in %.1fs", why, attempt + 1, self.retries,
                    wait)
        self.clock.sleep(wait)

    # ---- endpoints -------------------------------------------------------------------------
    def exchange_info(self) -> dict:
        return self._get("/fapi/v1/exchangeInfo", {}, 1)

    def klines(self, symbol, interval, start_ms=None, end_ms=None, limit=500):
        p = {"symbol": symbol, "interval": interval, "limit": int(limit)}
        if start_ms is not None:
            p["startTime"] = int(start_ms)
        if end_ms is not None:
            p["endTime"] = int(end_ms)
        return self._get("/fapi/v1/klines", p, kline_weight(int(limit)))

    def funding_rate(self, symbol, start_ms=None, end_ms=None, limit=1000):
        p = {"symbol": symbol, "limit": int(limit)}
        if start_ms is not None:
            p["startTime"] = int(start_ms)
        if end_ms is not None:
            p["endTime"] = int(end_ms)
        return self._get("/fapi/v1/fundingRate", p, 1, funding=True)

    def premium_index(self):
        out = self._get("/fapi/v1/premiumIndex", {}, 10)
        return out if isinstance(out, list) else [out]
