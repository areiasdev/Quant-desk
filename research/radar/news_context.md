# Market context - last two weeks (scan of 2026-11-18)

**Method and limits.** WebSearch was available; WebFetch could not open the article pages from
this sandbox (the news hosts are outside the session's network allowlist), so the points below
come from search-result summaries only, are single-source unless stated, and were NOT read in
full. Several search hits were November **2025** articles; those were discarded. Where a report
states a price, it was cross-checked against our own archive data (`data/clean/klines_1h`,
UTC) and the check is shown. Nothing here is used by any rule in `setups.md`.

1. **Sharp crypto sell-off in the first half of November.** Reports describe a long squeeze on
   2026-11-05 (KuCoin flash, 11-05) and a liquidation-driven "Black Weekend" on 11-14/15 with
   about $1.71bn of leveraged positions wiped out overnight and the Fear & Greed index at 10
   (Bitcoin.com, 11-15; RootData/PANews, 11-15; Bankless, 11-16). *Archive check:* BTCUSDT
   closed 80,466 on 11-04 and 64,130 on 11-14 (-20.3%), low 61,956 on 11-15; on 11-05 BTC fell
   5.5%, ETH 7.7%, XRP 7.6%, SOL 6.5% (daily close-to-close); ETHUSDT first traded below 2,000
   on 11-12 (13:00 UTC bar), the first time since 2026-08-19, consistent with the "first time
   since August" reports, and closed 1,824 on 11-17.
   Sources: [KuCoin](https://www.kucoin.com/news/flash/crypto-market-loses-120-billion-as-top-coins-drop-4-6),
   [Bitcoin.com](https://news.bitcoin.com/crypto-market-rout-deepens-as-xrp-price-hits-2-year-low-and-etf-money-flees/),
   [RootData](https://www.rootdata.com/news/830270),
   [Bankless](https://www.bankless.com/read/cryptos-worst-week-since-june.md).
2. **Hawkish Fed: a December hike is being priced.** After strong US jobs data, December hike
   odds were reported at about 81% (CCN, 11-09) and about 100% for the 12-09 meeting in fed funds
   futures (COINOTAG, 11-12); the Fed held at 3.75-4.00% on 10-28 with two dissents in favour of
   a hike (BeInCrypto). RTTNews (11-15) also cites a stronger dollar.
   Sources: [CCN](https://www.ccn.com/news/crypto/bitcoin-etf-outflows-1-28b-fed-rate-hike/),
   [COINOTAG](https://en.coinotag.com/cme-fedwatch-100-fed-rate-hike-odds-pressuring-bitcoin),
   [BeInCrypto](https://beincrypto.com/fed-hike-odds-bitcoin-etf-outflows/),
   [RTTNews](https://www.rttnews.com/3714577/cryptos-drop-as-dollar-gains-bitcoin-falls-below-68k.aspx?type=cryp).
3. **ETF outflows.** US spot bitcoin ETFs lost about $1.28bn in the week to 11-06, the largest
   weekly outflow since late June (BeInCrypto, CCN); about $696m of crypto ETF outflows were
   reported for the week of 11-13 (search summary of CoinIdol/Cointelegraph coverage). Anchorage
   Digital's David Lawant said the selling came mainly from US sources (Cointelegraph).
   Sources: [BeInCrypto](https://beincrypto.com/fed-hike-odds-bitcoin-etf-outflows/),
   [CCN](https://www.ccn.com/news/crypto/bitcoin-etf-outflows-1-28b-fed-rate-hike/),
   [Cointelegraph](https://cointelegraph.com/magazine/us-sellers-drive-bitcoin-crash-jensen-plots-comeback-hodlers-digest).
4. **Spill-over from an AI/chip-stock sell-off.** Tech and chip stocks (Nvidia, AMD, Broadcom,
   Micron) fell on Friday 11-13 together with crypto; several outlets link the drop to news of
   Nvidia CEO Jensen Huang's resignation (CoinIdol, 11-13/14; Cointelegraph). This is reported,
   not verified here. Relevant to us: the chip-linked TradFi perps (SNDKUSDT, SKHYNIXUSDT,
   SOXLUSDT) were among Binance's highest-volume perps in Aug-Nov 2026 and are excluded from our
   universe (`data/universe_excluded_hits.csv`).
   Sources: [CoinIdol](https://coinidol.com/crypto-spillover-from-nvidia),
   [Cointelegraph](https://cointelegraph.com/magazine/us-sellers-drive-bitcoin-crash-jensen-plots-comeback-hodlers-digest).
5. **Exchange events in the window.** Binance delisted the USDT-M PTBUSDT and MITOUSDT perps on
   2026-10-23 09:00 UTC (notice dated 2026-10-21, per search results; no single URL pinned).
   *Archive check:* both contracts' last bars with trades are on 2026-10-23, after which the
   archive only writes flat zero-trade bars - the "ghost bar" issue documented in
   `data/DATA_QUALITY.md`. No Binance futures listing/delisting notice dated November 2026 was
   found by the search.
   Source: search results for "Binance futures delisting November 2026" (2026-11-18);
   independent confirmation: `data/clean/screen_1d_all_symbols.parquet`.

Market-context sources are dated as reported by the search tool on 2026-11-18. Treat figures
quoted from articles as provisional and unaudited (RULES.md #1): they are context, not data.
