# Open questions

## RADAR - 2026-11-18 (night 1)

1. **TradFi perps in the "top-10 by volume" universe.** Since 2026-02, Binance USDT-M lists
   stock, ETF, commodity and pre-IPO perps. Unfiltered, they would take 39 top-10 slots across
   10 months (2026-02 .. 2026-11), up to 7 of 10 in a month (2026-08): XAUUSDT, XAGUSDT, CLUSDT (crude oil),
   SNDKUSDT, SKHYNIXUSDT, SOXLUSDT, MUUSDT, KORUUSDT, SPCXUSDT
   (`data/universe_excluded_hits.csv`). RADAR excluded them as "odd products" (crypto desk).
   **Owner: should the universe stay crypto-only?** If not, relax the `symbol_class ==
   "crypto"` filter in `universe.screen` (`src/radar/universe.py`) and rerun.
2. **Classification without exchangeInfo.** Binance's own `underlyingType` is only in the live
   API (HTTP 451 here, not bypassed). Classes come from explicit lists plus a data fingerprint
   (weekend volume collapses for TradFi underlyings); see `src/radar/symbols.py`. Three new
   listings had too little history to fingerprint: PREDICTUSDT, USDEXUSDT, ZCSHUSDT (all
   low-volume, never near the top-10). If someone can export exchangeInfo from a permitted
   location, RADAR can verify the classes against it.
3. **Meme list review.** Memes were classified by name/known project (`MEME` in
   `src/radar/symbols.py`). Please review the borderline exclusions (BUSDT/BUILDon, BOBUSDT,
   DOODUSDT, BIRBUSDT, ANIMEUSDT, NOTUSDT, AI16ZUSDT, ...) and the 2026 listings marked "not
   reviewed" in `research/radar/meme_universe.md`: these projects are newer than RADAR's
   knowledge and were treated as non-meme.
4. **Funding for the current month is missing.** The archive only has monthly funding files,
   so funding ends 2026-10-31 20:00 UTC, and funding-based setups cannot fire on November data
   until the file appears (~early December). Option: download `premiumIndexKlines` (daily files
   exist) and build a funding ESTIMATE, but only if the owner wants estimates in research.
5. **Metrics coverage choice.** To keep downloads sane, non-meme universe symbols have
   OI/long-short metrics only for their universe months plus the previous month (enough for
   every look-back in `setups.md`); memes have the whole window. Full history for all 85
   universe symbols would add tens of thousands of daily files. OK as is?
6. **Renamed contracts** (e.g. MATICUSDT -> POLUSDT in 2024-09) are separate symbols in the
   archive and in the universe. Should LAB stitch them into one series?
7. **News scan limits.** WebSearch works but WebFetch cannot open news sites from this sandbox
   (hosts not in the network allowlist), so `research/radar/news_context.md` and
   `meme_news.md` rely on search summaries (prices cross-checked against the archive). Allow the
   news hosts in the environment if full-article reading is wanted.
8. **Minimum notional (RULES.md #5)** can only be verified from exchangeInfo (blocked). LAB must
   mark tradeability at a $500-$1000 account as ESTIMATE unless the owner provides the values.
