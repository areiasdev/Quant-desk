# Universe ranking - 2026-11

Generated 2026-11-18 12:56 UTC by `src/radar/ranking.py` from data.binance.vision archive files (see `data/MANIFEST.csv`). Descriptive only: nothing here is a backtest or a signal.

**Data timestamps (UTC)**

- 1h klines: last closed bar opened 2026-11-17 23:00 (closed 2026-11-18 00:00).
- Open interest (metrics, 5-minute): last row used 2026-11-17 23:55.
- Funding: last settlement 2026-10-31 20:00. The archive publishes funding only as monthly files, so the current month is NOT available yet; the funding column is stale by the gap shown and describes the previous month.

Universe = top-10 USDT-M crypto perps by trailing-30-day quote volume at 2026-11-01 00:00 UTC (`data/universe_by_month.csv`).

## Ranks (1 = highest value, except funding: 1 = lowest/least crowded)

| vol rank | symbol | trend | momentum | volume | funding | open interest | volatility |
|---:|---|---:|---:|---:|---:|---:|---:|
| 1 | BTCUSDT | 4 | 5 | 1 | 7 | 7 | 10 |
| 2 | ETHUSDT | 8 | 6 | 2 | 3 | 4 | 8 |
| 3 | ZECUSDT | 9 | 3 | 8 | 9 | 2 | 1 |
| 4 | SOLUSDT | 5 | 9 | 3 | 5 | 9 | 5 |
| 5 | NEARUSDT | 1 | 10 | 10 | 8 | 1 | 2 |
| 6 | XRPUSDT | 6 | 8 | 4 | 4 | 8 | 7 |
| 7 | HYPEUSDT | 2 | 2 | 7 | 10 | 3 | 4 |
| 8 | BNBUSDT | 3 | 4 | 6 | 2 | 5 | 9 |
| 9 | DOGEUSDT | 7 | 7 | 5 | 6 | 6 | 6 |
| 10 | 1000PEPEUSDT | 10 | 1 | 9 | 1 | 10 | 3 |

## Underlying values

| symbol | close | trend % vs SMA20d | SMA20d 5d chg % | ret 7d % | ret 30d % | quote vol 7d (M USDT) | vol ratio 7d/30d | funding mean 30d (%/8h) | funding last (%/8h) | funding interval h | OI value (M USDT) | OI chg 7d % | realised vol 7d (ann. %) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BTCUSDT | 64921 | -12.14 | -4.71 | -11.99 | -20.90 | 100,697 | 1.31 | 0.0043 | 0.0100 | 8 | 5,975 | -18.64 | 58.1 |
| ETHUSDT | 1824.32 | -14.28 | -5.45 | -11.10 | -24.27 | 65,548 | 1.20 | 0.0029 | -0.0036 | 8 | 4,283 | -8.34 | 74.6 |
| ZECUSDT | 967.83 | -14.71 | -6.64 | -7.34 | -14.39 | 12,861 | 1.02 | 0.0060 | 0.0039 | 8 | 462 | 1.62 | 130.9 |
| SOLUSDT | 75.15 | -13.44 | -7.33 | -9.78 | -27.79 | 12,625 | 1.20 | 0.0040 | 0.0070 | 8 | 633 | -21.72 | 81.8 |
| NEARUSDT | 3.393 | -9.39 | -7.97 | -5.36 | -32.33 | 2,539 | 0.61 | 0.0050 | 0.0100 | 8 | 180 | 10.73 | 121.4 |
| XRPUSDT | 0.9834 | -13.65 | -6.73 | -6.87 | -24.73 | 5,440 | 1.18 | 0.0039 | 0.0100 | 8 | 259 | -21.27 | 75.2 |
| HYPEUSDT | 70.004 | -10.17 | -2.96 | -3.15 | -11.30 | 4,865 | 1.05 | 0.0060 | 0.0100 | 4 | 355 | -6.96 | 99.7 |
| BNBUSDT | 597.88 | -11.43 | -4.89 | -12.20 | -17.39 | 2,670 | 1.08 | 0.0020 | 0.0000 | 8 | 331 | -9.22 | 58.3 |
| DOGEUSDT | 0.06011 | -13.89 | -5.83 | -11.62 | -24.30 | 2,467 | 1.14 | 0.0042 | 0.0027 | 8 | 136 | -16.80 | 78.9 |
| 1000PEPEUSDT | 0.003824 | -18.99 | -2.34 | -28.86 | -4.68 | 2,480 | 0.95 | -0.0040 | -0.0713 | 8 | 75 | -36.82 | 103.2 |

## Definitions

- **trend**: last close vs the simple average of the last 20 days of 1h closes; the second column is how much that average itself moved in 5 days.
- **momentum**: close-to-close return over 7 and 30 days (ranked on 30 days).
- **volume**: average daily quote volume of the last 7 days divided by that of the 30 days before them (>1 = activity rising).
- **funding**: average funding rate of the last 30 days of settlements, scaled to an 8h interval; positive = longs pay shorts.
- **open interest**: change in open-interest value (USDT) versus 7 days earlier.
- **volatility**: standard deviation of 1h log returns over 7 days x sqrt(8760).
