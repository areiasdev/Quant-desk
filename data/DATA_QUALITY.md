# Data quality report

Generated 2026-11-18 12:56 UTC by `src/radar/quality.py`. Source: Binance public archive (data.binance.vision) only; every file is listed in `data/MANIFEST.csv` with its source URL, download time, row count and sha256. Research window starts 2022-11-01.

## 1. Downloads and checksums

Every zip was checked against the archive's `.CHECKSUM` (sha256) before being kept; a file that fails is retried and never stored. Files in the manifest = files that passed.

| data type | files | data rows | first downloaded (UTC) | last downloaded (UTC) |
|---|---:|---:|---|---|
| fundingRate | 3,935 | 531,544 | 2026-11-18T12:33:10Z | 2026-11-18T12:34:27Z |
| klines_1d | 19,937 | 595,794 | 2026-11-18T12:13:45Z | 2026-11-18T12:47:03Z |
| klines_1h | 9,798 | 7,075,440 | 2026-11-18T12:26:38Z | 2026-11-18T12:30:43Z |
| klines_1h (daily files) | 1,122 | 26,928 | 2026-11-18T12:26:38Z | 2026-11-18T12:30:44Z |
| metrics | 57,427 | 16,204,876 | 2026-11-18T12:34:47Z | 2026-11-18T12:46:54Z |
| **total** | **92,219** | **24,434,582** | | |

## 2. Coverage per data type (real start/end dates)

| data type | symbols (incl. delisted) | first data (UTC) | last data (UTC) | notes |
|---|---:|---|---|---|
| 1d klines (volume screen) | 911 (152 delisted) | 2022-10-01 | 2026-10-31 | every USDT symbol in the archive; Oct 2022 = ranking warm-up; last complete month only |
| 1h klines, raw (ranking candidates) | 311 | 2022-10-01 | 2026-11-17T23:00 | top-30 screen candidates of any month + memes; used for the 1h universe ranking |
| 1h klines, clean (`data/clean/klines_1h`) | 127 (16 delisted) | 2022-11-01T00:00 | 2026-11-17T23:00 | ever-top-10 universe (85) + memes (58) + BTC |
| funding (`data/clean/funding`) | 127 | 2022-11-01T00:00 | 2026-10-31T20:00 | monthly files only - the current month is NOT in the archive until it completes |
| metrics: OI + long/short (`data/clean/metrics_5m`) | 127 | 2022-11-01T00:00 | 2026-11-17T23:55 | 5-minute rows from daily files; universe symbols: universe months + the month before; memes: whole window |

**Start dates differ by symbol** (listing date or research-window start, whichever is later). Per-symbol first/last dates: `data/quality/klines_1h_summary.csv`, `funding_summary.csv`, `metrics_summary.csv`; memes also in `research/radar/meme_universe.md`.

Within the requested ranges, no symbol's metrics start later than its 1h klines.

**Metrics start later than klines in the archive itself** (all history, from the S3 listing; `data/quality/metrics_archive_span.csv`): BTCUSDT metrics begin 2020-09-01, while its klines begin in 2020-01. 33 of 127 research symbols have a first metrics day after their listing month: BTCUSDT (2020-09-01, listed 2020-01), ETHUSDT (2021-12-01, listed 2020-01), DOGEUSDT (2021-12-01, listed 2020-07), XRPUSDT (2021-12-01, listed 2020-01), SOLUSDT (2021-12-01, listed 2020-09), MATICUSDT (2021-12-01, listed 2020-10), ETCUSDT (2021-12-01, listed 2020-01), BNBUSDT (2021-12-01, listed 2020-02), MASKUSDT (2021-12-01, listed 2021-08), CHZUSDT (2021-12-01, listed 2021-01), LTCUSDT (2021-12-01, listed 2020-01), AXSUSDT (2021-12-01, listed 2020-11), GALAUSDT (2021-12-01, listed 2021-09), FILUSDT (2021-12-01, listed 2020-10), DYDXUSDT (2021-12-01, listed 2021-09), SXPUSDT (2021-12-01, listed 2020-07), LINAUSDT (2021-12-01, listed 2021-03), ARPAUSDT (2021-12-01, listed 2021-10), BCHUSDT (2021-12-01, listed 2020-01), COMPUSDT (2021-12-01, listed 2020-06), XLMUSDT (2021-12-01, listed 2020-01), 1000SHIBUSDT (2021-12-01, listed 2021-05), BLZUSDT (2021-12-01, listed 2020-09), LPTUSDT (2021-12-01, listed 2021-11), TRBUSDT (2021-12-01, listed 2020-09), LINKUSDT (2021-12-01, listed 2020-01), AVAXUSDT (2021-12-01, listed 2020-09), ADAUSDT (2021-12-01, listed 2020-01), REEFUSDT (2021-12-01, listed 2021-02), AAVEUSDT (2021-12-01, listed 2020-10), ZECUSDT (2021-12-01, listed 2020-02), NEARUSDT (2021-12-01, listed 2020-10), UNIUSDT (2021-12-01, listed 2020-09). All of these begin before the research window (2022-11-01), so the window itself is not affected.

**Misnamed files in the archive** (not date-stamped, never downloaded): MATICUSDT: MATICUSDT-metrics-1993.zip, TRBUSDT: TRBUSDT-metrics-1993.zip.

## 3. 1h klines

- Duplicates: 0 duplicate rows dropped, 0 conflicting duplicates (where a daily and a monthly file overlap, the monthly file wins).
- Gaps (missing hours between a symbol's first and last bar): 0 bars in 0 gaps. Gaps are never filled (`data/quality/klines_1h_gaps.csv`).
- Bad OHLC rows (high < max(open, close), low > min(open, close), price <= 0, NaN): 0; misaligned open times: 0; bad close times: 0.
- Zero-trade bars kept: 77. By hour: 2024-10-28 20:00 UTC in 77 symbols. A bar with no trades in many symbols at once is an exchange-wide halt: the archive writes a flat bar with zero volume. Do not fill orders on a bar with `trades == 0`.
- **Post-delisting ghost bars**: after a contract stops trading the archive keeps writing flat bars with zero trades (e.g. LINAUSDT has files to the latest day but its last trade was 2025-03-27). In the volume screen 152 of 911 symbols end in such runs. In the clean 1h data, a trailing run of >= 24 zero-trade bars is dropped: 134,016 bars in 16 symbols (the last real bar = delisting). The universe requires a real trade in the 24h before each month start, so a coin delisted days before a month cannot rank.
- Outliers are FLAGGED, not removed (`data/quality/klines_1h_outliers_flagged.csv`): 5,289 bars with a 1h close-to-close move > 20% or a range > 10x the symbol's trailing 30-day median 1h range; 725 of them are > 20% moves.
- Market-wide flag clusters (same hour, many symbols - real events, not data errors): 2025-10-10 21:00 UTC (110 symbols); 2026-08-22 05:00 UTC (103 symbols); 2025-10-10 22:00 UTC (90 symbols); 2025-02-03 01:00 UTC (65 symbols); 2024-03-05 19:00 UTC (58 symbols); 2026-01-19 00:00 UTC (57 symbols).
- Largest 1h moves (all checked as consistent OHLC; high/low show the move happened inside the bar):

| symbol | bar open (UTC) | open | high | low | close | close-to-close |
|---|---|---:|---:|---:|---:|---:|
| GIGGLEUSDT | 2025-10-25T03:00 | 86.69 | 289.38 | 86.12 | 273.88 | +216.0% |
| BROCCOLI714USDT | 2025-12-31T19:00 | 0.01417 | 0.04059 | 0.01415 | 0.03678 | +159.4% |
| TUTUSDT | 2025-03-27T17:00 | 0.01648 | 0.0464 | 0.01614 | 0.04272 | +159.2% |
| SIRENUSDT | 2026-04-17T09:00 | 0.3298 | 0.9 | 0.3134 | 0.7563 | +129.5% |
| HIPPOUSDT | 2025-11-06T21:00 | 0.003337 | 0.007425 | 0.003243 | 0.007319 | +119.3% |
| SIRENUSDT | 2026-02-07T16:00 | 0.05606 | 0.115 | 0.05139 | 0.11476 | +104.5% |
| CYBERUSDT | 2025-08-12T07:00 | 1.871 | 5.135 | 1.87 | 3.794 | +102.8% |
| VINEUSDT | 2025-07-24T12:00 | 0.03821 | 0.08589 | 0.03762 | 0.07556 | +97.7% |
| LABUSDT | 2026-06-02T23:00 | 8.0272 | 16.83 | 7.31 | 15.4302 | +92.2% |
| MOODENGUSDT | 2024-12-02T15:00 | 0.2995 | 0.612 | 0.29607 | 0.5691 | +90.1% |

## 4. Funding

- 525,822 settlements, 0 duplicates, 0 NaN rates.
- `calc_time` carries a few ms of jitter after the settlement instant (e.g. 08:00:00.003); `funding_time` is it rounded to the minute. Intervals seen: 4h: 53 symbols, 8h: 39 symbols, 1/4h: 12 symbols, 4/8h: 10 symbols, 1/4/8h: 9 symbols, 2/4/8h: 3 symbols, 2/4h: 1 symbols (Binance moves volatile contracts to 4h or 1h funding; scale to 8h before comparing).
- Missing settlements (a gap longer than the interval in force): 77 in 74 symbols. 2026-06-24 08:00 UTC (1 missing just before it) in 71 symbols; 2025-01-25 16:00 UTC (1 missing just before it) in 1 symbols; 2024-03-16 16:00 UTC (1 missing just before it) in 1 symbols. The rest are isolated (details in `data/quality/funding_summary.csv`).
- Settlements before a contract's first 1h bar: 52 (symbols: ARBUSDT, BANANAS31USDT, BOMEUSDT, BROCCOLI714USDT, BROCCOLIF3BUSDT, CFXUSDT, CYBERUSDT, GASUSDT, IDUSDT, LOOMUSDT, PERPUSDT, SEIUSDT, SIRENUSDT); kept (a strategy cannot hold a position then).
- **Post-delisting ghost funding**: the archive keeps writing settlements after a contract stops trading; 5,722 settlements after the last real 1h bar were dropped: BLZUSDT (1068), LINAUSDT (252), LOOMUSDT (1152), MATICUSDT (2362), REEFUSDT (888).
- Extreme rates kept as-is: 1599 settlements with |rate| >= 0.5% per interval; min -3.0000%, max 2.0000%.
- **End date**: funding ends 2026-10-31T20:00 UTC (last complete month). Setups that need funding cannot be evaluated on the current month until the monthly file appears.

## 5. Metrics (open interest, long/short ratios)

- 16,203,585 five-minute rows from 57,427 daily files (97.97% of 288 rows/day on days with a file); 0 duplicates dropped; 0 rows off the 5-minute grid.
- Days with fewer than 288 rows: 277; days missing inside downloaded months (no file in the archive): 61 (`data/quality/metrics_missing_days.csv`). Missing rows are NOT filled; the 1h as-of join leaves NaN when no row falls inside the bar.
- Most common missing days: 2026-10-06 (5 symbols); 2026-10-05 (5 symbols); 2026-10-12 (5 symbols); 2026-10-08 (5 symbols); 2026-10-13 (4 symbols); 2026-10-14 (4 symbols); 2026-10-09 (4 symbols); 2026-10-11 (4 symbols).
- Empty values per column: open_interest: 0, open_interest_value_usdt: 0, top_trader_account_ls_ratio: 184,493, top_trader_position_ls_ratio: 182,600, global_account_ls_ratio: 1,500, taker_buy_sell_vol_ratio: 3,644.
- **Post-delisting ghost metrics**: the archive keeps writing about one row per day with open interest 0 after a delisting; 1,291 such rows after the last real 1h bar were dropped (same cut-off as the klines).
- Open interest == 0 on a trading contract (a missing value written as 0, e.g. BTCUSDT 2023-06-06 11:20-...): 3,676 rows set to NaN so they cannot fake a -100% OI drop; 5-minute OI jumps > 50%: 168 (flag only).
- Raw column names were renamed: sum_open_interest -> open_interest, sum_open_interest_value -> open_interest_value_usdt, count_toptrader_long_short_ratio -> top_trader_account_ls_ratio, sum_toptrader_long_short_ratio -> top_trader_position_ls_ratio, count_long_short_ratio -> global_account_ls_ratio, sum_taker_long_short_vol_ratio -> taker_buy_sell_vol_ratio. Raw daily files are not always sorted by time; the clean files are.

## 6. Universe and classification

- 49 months (2022-11 .. 2026-11), 85 distinct symbols, 7 of them later delisted: BLZUSDT, LINAUSDT, LOOMUSDT, MATICUSDT, PERPUSDT, REEFUSDT, SXPUSDT.
- Cross-check: the 1h-based ranking equals the 1d screen ranking in 490 of 490 month-rank rows; max trailing-volume difference 0.352%.
- Classes: crypto: 686, tradfi: 232, index: 4, stablecoin: 2 (54 TradFi by weekend fingerprint).
- **TradFi perps would have taken top-10 slots in 10 months** (2026-02 .. 2026-11; 39 slots: XAUUSDT x9, XAGUSDT x6, CLUSDT x5, SNDKUSDT x5, SPCXUSDT x4, SOXLUSDT x4, SKHYNIXUSDT x3, MUUSDT x2, KORUUSDT x1). They are excluded as non-crypto products (`data/universe_excluded_hits.csv`); see `research/QUESTIONS.md`.
- Renamed contracts are separate symbols in the archive (e.g. MATICUSDT ends 2024-09; POLUSDT continues the asset) - join them manually if a study needs one continuous series.

## 7. Timing rules for LAB (no look-ahead)

- 1h bar = [open_time, open_time + 1h), UTC. Signal on a closed bar, order at the next bar's open.
- Funding joins as-of the bar close: a settlement is first visible on the bar that contains its calc_time, i.e. usable at the next bar's open (`src/radar/features.py`).
- Metrics join as-of open_time + 55 min and only if the row lies inside the bar; the 5-minute row stamped exactly at the bar close is not used.
- 4h bars = four complete 1h bars anchored at 00/04/08/12/16/20 UTC; incomplete groups are dropped.
- Universe month M uses only bars that closed before M 00:00 UTC.
