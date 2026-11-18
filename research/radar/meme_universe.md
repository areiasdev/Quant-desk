# Meme-coin universe

Generated 2026-11-18 12:56 UTC by `src/radar/meme.py`. Data: data.binance.vision archive only (`data/MANIFEST.csv`). Table also in `data/meme_universe.csv`.

**58 meme perps** (49 trading at the latest bar 2026-11-17 23:00 UTC, 9 delisted).

## How a coin was classified as a meme coin

1. Start from every USDT-M symbol in the S3 listing of the archive (`data/symbols_all.csv`, live and delisted).
2. Rule: a meme coin is a token whose main value proposition is a joke, mascot, animal, internet meme, celebrity or political figure, with no protocol or product whose usage the token captures. Each symbol was reviewed by name/known project against this rule; the list lives in `src/radar/symbols.py` (`MEME`).
3. The symbol must ALSO be classed `crypto` (`data/symbol_classes.csv`). Binance lists stock perps whose tickers collide with memes (WEN = Wendy's, PENG = Penguin Solutions, CAT = Caterpillar, all listed 2026-07); the weekend-volume fingerprint in `symbols.py` flags them as TradFi, so they are not memes.
4. Borderline names (AI-agent products, tap-to-earn games, launchpads, NFT brand tokens, unverified tickers) are listed below with the reason they were left out.
5. Listing date = first bar of the contract in the archive; delisting date = the day after the last bar with trades > 0 (the archive keeps writing flat zero-trade bars after a delisting, which are ignored). Multiplier contracts (1000PEPE, 1000000MOG, 1MBABYDOGE ...) quote the price of 1,000 / 1,000,000 tokens.

## Meme perps

| symbol | listed (UTC) | status | delisted (UTC) | months in top-10 | 1h klines | funding | metrics (OI) |
|---|---|---|---|---:|---|---|---|
| DOGEUSDT | 2020-07-10 | trading | - | 42 | 2022-11-01 .. 2026-11-17 | 2022-11-01 .. 2026-10-31 | 2022-11-01 .. 2026-11-17 |
| 1000SHIBUSDT | 2021-05-10 | trading | - | 2 | 2022-11-01 .. 2026-11-17 | 2022-11-01 .. 2026-10-31 | 2022-11-01 .. 2026-11-17 |
| PEOPLEUSDT | 2021-12-24 | trading | - | 3 | 2022-11-01 .. 2026-11-17 | 2022-11-01 .. 2026-10-31 | 2022-11-01 .. 2026-11-17 |
| 1000PEPEUSDT | 2023-05-05 | trading | - | 26 | 2023-05-05 .. 2026-11-17 | 2023-05-05 .. 2026-10-31 | 2023-05-05 .. 2026-11-17 |
| 1000FLOKIUSDT | 2023-05-06 | trading | - | 0 | 2023-05-06 .. 2026-11-17 | 2023-05-06 .. 2026-10-31 | 2023-05-06 .. 2026-11-17 |
| MEMEUSDT | 2023-11-03 | trading | - | 0 | 2023-11-03 .. 2026-11-17 | 2023-11-03 .. 2026-10-31 | 2023-11-03 .. 2026-11-17 |
| 1000BONKUSDT | 2023-11-22 | trading | - | 2 | 2023-11-22 .. 2026-11-17 | 2023-11-22 .. 2026-10-31 | 2023-11-22 .. 2026-11-17 |
| 1000SATSUSDT | 2023-12-12 | trading | - | 1 | 2023-12-12 .. 2026-11-17 | 2023-12-12 .. 2026-10-31 | 2023-12-12 .. 2026-11-17 |
| 1000RATSUSDT | 2023-12-15 | trading | - | 0 | 2023-12-15 .. 2026-11-17 | 2023-12-15 .. 2026-10-31 | 2023-12-15 .. 2026-11-17 |
| WIFUSDT | 2024-01-18 | trading | - | 9 | 2024-01-18 .. 2026-11-17 | 2024-01-18 .. 2026-10-31 | 2024-01-18 .. 2026-11-17 |
| MYROUSDT | 2024-03-05 | delisted | 2025-11-14 | 0 | 2024-03-05 .. 2025-11-14 | 2024-03-05 .. 2025-11-14 | 2024-03-05 .. 2025-11-14 |
| BOMEUSDT | 2024-03-16 | trading | - | 0 | 2024-03-16 .. 2026-11-17 | 2024-03-16 .. 2026-10-31 | 2024-03-16 .. 2026-11-17 |
| TURBOUSDT | 2024-05-30 | trading | - | 0 | 2024-05-30 .. 2026-11-17 | 2024-05-30 .. 2026-10-31 | 2024-05-30 .. 2026-11-17 |
| MEWUSDT | 2024-06-17 | trading | - | 0 | 2024-06-17 .. 2026-11-17 | 2024-06-17 .. 2026-10-31 | 2024-06-17 .. 2026-11-17 |
| BRETTUSDT | 2024-08-20 | trading | - | 0 | 2024-08-20 .. 2026-11-17 | 2024-08-20 .. 2026-10-31 | 2024-08-20 .. 2026-11-17 |
| POPCATUSDT | 2024-08-22 | trading | - | 0 | 2024-08-22 .. 2026-11-17 | 2024-08-22 .. 2026-10-31 | 2024-08-22 .. 2026-11-17 |
| DOGSUSDT | 2024-08-26 | trading | - | 1 | 2024-08-26 .. 2026-11-17 | 2024-08-26 .. 2026-10-31 | 2024-08-26 .. 2026-11-17 |
| NEIROETHUSDT | 2024-09-06 | delisted | 2025-09-26 | 0 | 2024-09-06 .. 2025-09-26 | 2024-09-06 .. 2025-09-26 | 2024-09-06 .. 2025-09-26 |
| 1MBABYDOGEUSDT | 2024-09-16 | trading | - | 0 | 2024-09-16 .. 2026-11-17 | 2024-09-16 .. 2026-10-31 | 2024-09-16 .. 2026-11-17 |
| NEIROUSDT | 2024-09-16 | trading | - | 3 | 2024-09-16 .. 2026-11-17 | 2024-09-16 .. 2026-10-31 | 2024-09-16 .. 2026-11-17 |
| 1000CATUSDT | 2024-10-21 | trading | - | 0 | 2024-10-21 .. 2026-11-17 | 2024-10-21 .. 2026-10-31 | 2024-10-21 .. 2026-11-17 |
| GOATUSDT | 2024-10-24 | trading | - | 0 | 2024-10-24 .. 2026-11-17 | 2024-10-24 .. 2026-10-31 | 2024-10-24 .. 2026-11-17 |
| MOODENGUSDT | 2024-10-25 | trading | - | 1 | 2024-10-25 .. 2026-11-17 | 2024-10-25 .. 2026-10-31 | 2024-10-25 .. 2026-11-17 |
| PONKEUSDT | 2024-11-04 | delisted | 2025-11-28 | 0 | 2024-11-04 .. 2025-11-28 | 2024-11-04 .. 2025-11-28 | 2024-11-04 .. 2025-11-28 |
| 1000000MOGUSDT | 2024-11-07 | trading | - | 0 | 2024-11-07 .. 2026-11-17 | 2024-11-07 .. 2026-10-31 | 2024-11-07 .. 2026-11-17 |
| ACTUSDT | 2024-11-11 | trading | - | 0 | 2024-11-11 .. 2026-11-17 | 2024-11-11 .. 2026-10-31 | 2024-11-11 .. 2026-11-17 |
| PNUTUSDT | 2024-11-11 | trading | - | 1 | 2024-11-11 .. 2026-11-17 | 2024-11-11 .. 2026-10-31 | 2024-11-11 .. 2026-11-17 |
| HIPPOUSDT | 2024-11-13 | delisted | 2026-04-08 | 0 | 2024-11-13 .. 2026-04-08 | 2024-11-13 .. 2026-04-08 | 2024-11-13 .. 2026-04-08 |
| DEGENUSDT | 2024-11-15 | delisted | 2026-04-28 | 0 | 2024-11-15 .. 2026-04-28 | 2024-11-15 .. 2026-04-28 | 2024-11-15 .. 2026-04-28 |
| BANUSDT | 2024-11-18 | trading | - | 0 | 2024-11-18 .. 2026-11-17 | 2024-11-18 .. 2026-10-31 | 2024-11-18 .. 2026-11-17 |
| SLERFUSDT | 2024-11-21 | delisted | 2025-10-20 | 0 | 2024-11-21 .. 2025-10-20 | 2024-11-21 .. 2025-10-20 | 2024-11-21 .. 2025-10-20 |
| 1000CHEEMSUSDT | 2024-11-25 | trading | - | 0 | 2024-11-25 .. 2026-11-17 | 2024-11-25 .. 2026-10-31 | 2024-11-25 .. 2026-11-17 |
| 1000WHYUSDT | 2024-11-25 | delisted | 2026-03-17 | 0 | 2024-11-25 .. 2026-03-17 | 2024-11-25 .. 2026-03-17 | 2024-11-25 .. 2026-03-17 |
| CHILLGUYUSDT | 2024-11-27 | trading | - | 0 | 2024-11-27 .. 2026-11-17 | 2024-11-27 .. 2026-10-31 | 2024-11-27 .. 2026-11-17 |
| KOMAUSDT | 2024-12-10 | trading | - | 0 | 2024-12-10 .. 2026-11-17 | 2024-12-10 .. 2026-10-31 | 2024-12-10 .. 2026-11-17 |
| SPXUSDT | 2024-12-10 | trading | - | 0 | 2024-12-10 .. 2026-11-17 | 2024-12-10 .. 2026-10-31 | 2024-12-10 .. 2026-11-17 |
| PENGUUSDT | 2024-12-17 | trading | - | 1 | 2024-12-17 .. 2026-11-17 | 2024-12-17 .. 2026-10-31 | 2024-12-17 .. 2026-11-17 |
| FARTCOINUSDT | 2024-12-20 | trading | - | 2 | 2024-12-20 .. 2026-11-17 | 2024-12-20 .. 2026-10-31 | 2024-12-20 .. 2026-11-17 |
| TRUMPUSDT | 2025-01-18 | trading | - | 6 | 2025-01-18 .. 2026-11-17 | 2025-01-18 .. 2026-10-31 | 2025-01-18 .. 2026-11-17 |
| MELANIAUSDT | 2025-01-20 | trading | - | 0 | 2025-01-20 .. 2026-11-17 | 2025-01-20 .. 2026-10-31 | 2025-01-20 .. 2026-11-17 |
| PIPPINUSDT | 2025-01-24 | trading | - | 1 | 2025-01-24 .. 2026-11-17 | 2025-01-24 .. 2026-10-31 | 2025-01-24 .. 2026-11-17 |
| VINEUSDT | 2025-01-24 | delisted | 2026-04-28 | 0 | 2025-01-24 .. 2026-04-28 | 2025-01-24 .. 2026-04-28 | 2025-01-24 .. 2026-04-28 |
| TSTUSDT | 2025-02-09 | trading | - | 0 | 2025-02-09 .. 2026-11-17 | 2025-02-09 .. 2026-10-31 | 2025-02-09 .. 2026-11-17 |
| MUBARAKUSDT | 2025-03-17 | trading | - | 0 | 2025-03-17 .. 2026-11-17 | 2025-03-17 .. 2026-10-31 | 2025-03-17 .. 2026-11-17 |
| TUTUSDT | 2025-03-20 | trading | - | 1 | 2025-03-20 .. 2026-11-17 | 2025-03-20 .. 2026-10-31 | 2025-03-20 .. 2026-11-17 |
| BROCCOLI714USDT | 2025-03-21 | trading | - | 0 | 2025-03-21 .. 2026-11-17 | 2025-03-20 .. 2026-10-31 | 2025-03-21 .. 2026-11-17 |
| BROCCOLIF3BUSDT | 2025-03-21 | trading | - | 0 | 2025-03-21 .. 2026-11-17 | 2025-03-20 .. 2026-10-31 | 2025-03-21 .. 2026-11-17 |
| BANANAS31USDT | 2025-03-22 | trading | - | 0 | 2025-03-22 .. 2026-11-17 | 2025-03-20 .. 2026-10-31 | 2025-03-22 .. 2026-11-17 |
| JELLYJELLYUSDT | 2025-03-26 | trading | - | 0 | 2025-03-26 .. 2026-11-17 | 2025-03-26 .. 2026-10-31 | 2025-03-26 .. 2026-11-17 |
| 1000000BOBUSDT | 2025-06-05 | delisted | 2026-10-05 | 0 | 2025-06-05 .. 2026-10-05 | 2025-06-05 .. 2026-10-05 | 2025-06-05 .. 2026-10-05 |
| USELESSUSDT | 2025-08-15 | trading | - | 0 | 2025-08-15 .. 2026-11-17 | 2025-08-15 .. 2026-10-31 | 2025-08-15 .. 2026-11-17 |
| TOSHIUSDT | 2025-09-17 | trading | - | 0 | 2025-09-17 .. 2026-11-17 | 2025-09-17 .. 2026-10-31 | 2025-09-17 .. 2026-11-17 |
| GIGGLEUSDT | 2025-10-09 | trading | - | 0 | 2025-10-09 .. 2026-11-17 | 2025-10-09 .. 2026-10-31 | 2025-10-09 .. 2026-11-17 |
| 币安人生USDT | 2025-10-20 | trading | - | 0 | 2025-10-20 .. 2026-11-17 | 2025-10-20 .. 2026-10-31 | 2025-10-20 .. 2026-11-17 |
| 我踏马来了USDT | 2026-01-21 | trading | - | 0 | 2026-01-21 .. 2026-11-17 | 2026-01-21 .. 2026-10-31 | 2026-01-21 .. 2026-11-17 |
| 龙虾USDT | 2026-03-11 | trading | - | 0 | 2026-03-11 .. 2026-11-17 | 2026-03-11 .. 2026-10-31 | 2026-03-11 .. 2026-11-17 |
| 牛来USDT | 2026-08-30 | trading | - | 0 | 2026-08-30 .. 2026-11-17 | 2026-08-30 .. 2026-10-31 | 2026-08-30 .. 2026-11-17 |
| 哈基米USDT | 2026-09-06 | trading | - | 0 | 2026-09-06 .. 2026-11-17 | 2026-09-06 .. 2026-10-31 | 2026-09-06 .. 2026-11-17 |

Data windows are clipped to the research window (from 2022-11-01); a contract listed earlier shows 2022-11-01 as its first 1h bar.

## Borderline symbols NOT counted as memes

| symbol | reason |
|---|---|
| 1000LUNCUSDT | Terra Classic L1 token |
| 1000XUSDT | X Empire: tap-to-earn game token |
| AI16ZUSDT | ai16z/ElizaOS: AI-agent framework with a product |
| AIXBTUSDT | aixbt: AI analytics agent with a product |
| ANIMEUSDT | Animecoin (Azuki): culture/IP token |
| BIRBUSDT | Moonbirds NFT brand token (PENGU is included: it is widely categorised as meme) |
| BOBUSDT | listed 2025-11 at ~$0.02: most likely BOB (Build on Bitcoin) L2, not the BOB meme |
| BUSDT | B / BUILDon (BNB chain): possibly a meme, not verified - excluded pending review |
| CATIUSDT | Catizen: game platform token |
| CATUSDT | listed 2026-07 with stock-like weekend fingerprint: Caterpillar |
| CLANKERUSDT | Clanker token-launchpad token |
| DOODUSDT | Doodles NFT brand token |
| GRIFFAINUSDT | Griffain: AI agent platform |
| HMSTRUSDT | Hamster Kombat: tap-to-earn game token |
| LUNA2USDT | Terra 2.0 L1 token |
| MEMEFIUSDT | MemeFi: tap-to-earn game token despite the name |
| NOTUSDT | Notcoin: tap-to-earn game token |
| ORDIUSDT | BRC-20 standard/infrastructure token, not a mascot meme |
| PENGUSDT | listed 2026-07 with stock-like weekend fingerprint: Penguin Solutions |
| PUMPUSDT | pump.fun platform token (meme launchpad, not a meme itself) |
| SIRENUSDT | AI-agent token |
| SWARMSUSDT | Swarms: AI agent framework |
| VIRTUALUSDT | Virtuals Protocol: AI agent launchpad |
| WENUSDT | listed 2026-07 with stock-like weekend fingerprint: Wendy's, not the WEN meme |
| WLFIUSDT | World Liberty Financial: DeFi governance token |
| ZEREBROUSDT | Zerebro: AI agent project |
| ZORAUSDT | Zora creator-coin platform token |

**Not reviewed (51 crypto perps first listed in 2026):** these projects are newer than the reviewer's knowledge and were treated as non-meme. Owner review requested in `research/QUESTIONS.md`: ACUUSDT, AIGENSYNUSDT, ARXUSDT, AZTECUSDT, BASEDUSDT, BILLUSDT, BSBUSDT, BTWUSDT, CAPUSDT, CFGUSDT, CHIPUSDT, CTRUSDT, CTUSDT, DATAIPUSDT, DOSUSDT, EDGEUSDT, ELSAUSDT, ESPUSDT, FIGHTUSDT, FOGOUSDT, FRAXUSDT, GENIUSUSDT, GRAMUSDT, GRVTUSDT, GWEIUSDT, INXUSDT, KATUSDT, MANTRAUSDT, MARSCOINUSDT, MEGAUSDT, MOONSHOTUSDT, OPGUSDT, OPNUSDT, OURAUSDT, OUSDT, PHAROSUSDT, PONSUSDT, PREDICTUSDT, PRLUSDT, REUSDT, ROBOUSDT, SKRUSDT, SLXUSDT, SPACEUSDT, SPORTFUNUSDT, STARUSDT, TRIAUSDT, USDEXUSDT, ZAMAUSDT, ZCSHUSDT, ZESTUSDT.

## Hype proxies (exchange data only)

No free historical social-media or news archive exists, so hype is measured from what the exchange records. Code: `src/radar/hype.py`; all bars: `data/clean/meme_hype_1h.parquet`. Each value uses only data known at the 1h bar's close.

| proxy | column | one-sentence meaning |
|---|---|---|
| volume z-score vs 30d | `hype_volume_z30d` | How many standard deviations the last 24h of quote volume sits above the 30 previous daily volumes (taken at the same hour). |
| OI surge vs 7d | `hype_oi_surge_7d` | How much more money sits in open positions now than the median of the previous 7 days (0.5 = +50%). |
| funding jump | `hype_funding_jump_bp` | How much the latest settled funding rate (scaled to 8h) exceeds the mean of the settlements in the 7 days before it, in basis points. |
| price move vs BTC | `hype_move_vs_btc_24h` | The coin's 24h return minus BTC's 24h return, i.e. how far it ran ahead of (or fell behind) the market. |
| days since listing | `days_since_listing` | Days since the contract's first Binance futures bar; fresh listings get the most attention and the steepest fades. |

### Snapshot at the latest bar (2026-11-17 23:00 UTC open), trading memes

Funding in the archive ends with the last complete month, so `funding jump` is stale for the current month (shown for reference only).

| symbol | 24h quote vol (M USDT) | volume z30d | OI surge 7d | funding jump (bp) | move vs BTC 24h | days since listing |
|---|---:|---:|---:|---:|---:|---:|
| 1000PEPEUSDT | 250.7 | -0.64 | -6.9% | -6.7 | -2.2% | 1293 |
| DOGEUSDT | 227.2 | -0.68 | -10.1% | -0.3 | +1.9% | 2322 |
| 龙虾USDT | 137.5 | 13.42 | +9.9% | -3.3 | +16.6% | 252 |
| PENGUUSDT | 71.4 | 1.24 | -13.8% | 0.5 | +0.9% | 701 |
| TRUMPUSDT | 55.0 | -0.52 | -1.5% | -2.6 | +3.1% | 669 |
| FARTCOINUSDT | 23.1 | 0.20 | -13.8% | 0.2 | -0.3% | 698 |
| 1000SHIBUSDT | 19.7 | -0.68 | -5.4% | 0.4 | +0.8% | 2018 |
| USELESSUSDT | 16.5 | -0.76 | -2.6% | 0.8 | +4.2% | 460 |
| 牛来USDT | 13.7 | -0.32 | -10.6% | 0.0 | +5.9% | 80 |
| 1000BONKUSDT | 12.1 | -0.54 | -10.1% | -0.6 | +3.2% | 1092 |
| WIFUSDT | 11.1 | -0.37 | -10.5% | 0.1 | +1.2% | 1035 |
| MUBARAKUSDT | 9.6 | 0.71 | -13.0% | 0.0 | +0.5% | 611 |
| BOMEUSDT | 6.5 | -0.58 | -8.3% | 0.6 | +2.6% | 977 |
| 我踏马来了USDT | 6.5 | 11.87 | -4.0% | -0.2 | +5.1% | 301 |
| BANANAS31USDT | 6.4 | 0.61 | +14.2% | 0.1 | +1.0% | 606 |
| SPXUSDT | 5.1 | -0.61 | -10.7% | 0.1 | +4.4% | 708 |
| 币安人生USDT | 4.4 | 0.27 | +1.6% | -0.1 | +1.2% | 394 |
| 1000FLOKIUSDT | 4.2 | -0.36 | -3.4% | 1.4 | -1.3% | 1292 |
| GIGGLEUSDT | 4.0 | -0.38 | -7.7% | 0.1 | +1.1% | 405 |
| JELLYJELLYUSDT | 3.4 | 0.43 | +7.3% | -0.8 | -5.2% | 602 |
| NEIROUSDT | 2.9 | -0.63 | -18.9% | -0.7 | +2.5% | 793 |
| PEOPLEUSDT | 2.9 | -0.66 | -15.6% | 0.5 | +1.0% | 1790 |
| MEMEUSDT | 2.3 | 0.28 | -17.0% | -2.6 | +0.5% | 1111 |
| ACTUSDT | 2.2 | -0.43 | -4.2% | 0.1 | +3.9% | 737 |
| TUTUSDT | 2.1 | -0.55 | -10.8% | 0.2 | +2.5% | 608 |
| PNUTUSDT | 2.1 | -0.89 | +1.8% | 0.5 | +1.0% | 737 |
| 哈基米USDT | 2.0 | 0.59 | -10.5% | -1.6 | +6.5% | 73 |
| TSTUSDT | 1.9 | -0.20 | -13.8% | 2.8 | +0.8% | 647 |
| PIPPINUSDT | 1.7 | -0.56 | -10.9% | -3.3 | +1.1% | 663 |
| KOMAUSDT | 1.6 | -0.25 | +2.5% | -5.0 | +2.4% | 708 |
| MOODENGUSDT | 1.6 | -0.51 | -0.3% | 0.4 | +3.7% | 754 |
| POPCATUSDT | 1.5 | -0.12 | +0.6% | 0.1 | +2.4% | 818 |
| 1000RATSUSDT | 1.5 | -0.67 | -5.1% | -0.1 | +5.2% | 1069 |
| DOGSUSDT | 1.4 | -0.17 | +4.9% | 0.0 | +3.8% | 814 |
| 1000SATSUSDT | 1.4 | -0.20 | -4.6% | -0.0 | +1.7% | 1072 |
| TURBOUSDT | 1.3 | -0.73 | -11.8% | -0.6 | +0.9% | 902 |
| BROCCOLI714USDT | 1.2 | -0.36 | -9.4% | 0.1 | +2.0% | 607 |
| MELANIAUSDT | 1.2 | -0.54 | +9.9% | 0.0 | +4.0% | 667 |
| TOSHIUSDT | 1.2 | -0.27 | +3.4% | 1.7 | +5.4% | 427 |
| BROCCOLIF3BUSDT | 1.1 | 1.07 | -5.8% | -2.2 | +0.0% | 607 |
| 1000CHEEMSUSDT | 1.0 | 0.78 | -13.6% | 0.2 | +3.6% | 723 |
| CHILLGUYUSDT | 0.8 | -0.65 | +6.8% | -0.3 | +4.0% | 721 |
| 1MBABYDOGEUSDT | 0.8 | -0.65 | -3.8% | -0.0 | +2.1% | 793 |
| BANUSDT | 0.8 | -0.27 | -4.5% | 2.3 | +5.3% | 730 |
| 1000CATUSDT | 0.6 | -0.33 | -14.3% | -0.4 | +3.4% | 758 |
| MEWUSDT | 0.6 | -0.40 | -2.3% | 1.2 | +3.5% | 884 |
| 1000000MOGUSDT | 0.6 | -0.31 | -1.6% | 0.0 | +4.3% | 741 |
| GOATUSDT | 0.5 | -1.15 | -5.3% | -1.0 | +1.8% | 755 |
| BRETTUSDT | 0.5 | -1.52 | -2.5% | -0.1 | +1.9% | 820 |
