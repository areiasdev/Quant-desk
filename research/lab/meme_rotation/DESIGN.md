# Meme rotation - pre-registered design (FROZEN before any backtest)

Author: LAB, 2026-11-18, branch `research/meme-rotation`. Owner request: *"build an algorithm made
to profit on meme coins that swaps coin when it sees it's not worth a trade there anymore"*.
Paper only (RULES.md). This file was committed **before any backtest of these strategies was run
on real data**; the code that implements it is `src/lab/rotation.py` (tests:
`tests/test_lab_rotation.py`, synthetic data only). Any later change to this file is visible in git
history and must be listed under "Amendments" at the bottom with the reason.

## What we already know (disclosed bias)

LAB night 1 (`research/lab/REPORT.md`) already looked at meme results over 2024-11..2026-10:
no meme setup survived, M11 (hype-decay short) was best but too thin, the ride-the-hype longs lost,
and the equal-weight meme basket lost 77.7% (2024-11..2026-10). That window overlaps both the
development OOS below AND the final holdout. Consequences, stated up front:

- R3 re-uses M11 and R5 (short the faders) is motivated partly by that basket result. Their
  holdout is therefore **not a fully clean test**: we know memes as a group fell over
  2025-11..2026-10 and that M11 lost in that year (-0.22R/trade).
- R1 and R2 use logic that was not run on memes before (cross-sectional momentum; S02 breakout on
  memes), so their holdout is cleaner, but the aggregate meme bear market is still known.
- To limit damage: small grids, defaults fixed here, every variant reported, holdout run once.

## Data, universe, timing (common to all strategies)

- Data: Binance USDT-M archive (data.binance.vision, `data/MANIFEST.csv`) via the LAB cache
  (`data/clean/lab_cache`, `src/lab/data.py`): 1h klines, real funding settlements, RADAR hype
  proxies (`src/radar/hype.py`, `data/clean/meme_hype_1h.parquet`). 58 meme perps (`is_meme` in
  `data/symbol_classes.csv`, list in `research/radar/meme_universe.md`), **9 delisted ones
  included**. BTCUSDT for the regime filter and the benchmark.
- Days: 00:00-24:00 UTC built from 1h bars. A day is complete only if its 23:00 bar exists. The
  decision for day d is taken at the close of d's 23:00 bar (= d+1 00:00) using only days <= d,
  and executed at market at the open of the next 1h bar (d+1 00:00). 4h bars (R2 base, R3 shorts)
  are the LAB engine's 00/04/../20 UTC bars; their decision is at the 4h close, entry at the next
  4h open.
- **Point-in-time eligible meme on day d** (all must hold, no look-ahead):
  1. listed >= 30 days at the decision (`days_since_listing` of the 23:00 bar >= 30);
  2. liquidity: mean daily quote volume over days d-29..d >= 20M USDT (30 days required);
  3. the 23:00 bar of day d has trades > 0 and the day is complete;
  4. ATR14 (daily) available.
  For 4h signals the filter of the last complete day before the 4h bar's close is used.
- Delisted coins: kept in the panel until their last real trade; an open position is force-closed
  at the last real bar's close moved 2% against the position (engine ESTIMATE haircut).
- Momentum score(L) on day d = ln(C_d / C_{d-L}) / (sd of the last L daily log returns x sqrt(L))
  - a risk-adjusted (t-stat-like) L-day return. Needs all L+1 daily closes.
- BTC regime: "BTC up" = BTC daily close_d > SMA50 of BTC daily closes (days d-49..d).
- Protective stop for the daily strategies (R1, R3 long leg, R5): fixed at entry -/+ 2 x ATR14
  (daily, simple mean of 14 true ranges up to day d), resting stop-market checked inside every 1h
  bar (pessimistic; a bar opening beyond the stop exits at that open). Not a tuned parameter.

## Execution, costs, sizing (common)

- Engine: `src/lab/engine.py` (`simulate`) for fills, stops, close-based exits and real funding
  (every settlement held is booked at qty x price x rate).
- Costs: taker 0.05%/side, meme slippage 10bp/side, real funding. **Double costs** = taker 0.10%
  AND slippage 20bp together.
- Account EUR100 = 110 USDT. Each position risks 1% of closed-trade equity: notional =
  0.01 x equity / stop distance. At most **3 positions** open, total open notional <= **3x**
  equity, one position per symbol. A candidate is SKIPPED and counted if: its symbol is already
  held; no slot is free; its notional is below the minimum notional; or the leverage room left is
  below the minimum notional.
- Minimum notional (owner, from exchangeInfo): BTC 50 USDT (min qty 0.001), ETH 20, default 5.
  **Memes use the default 5 USDT - their own filters were not exported, so for memes this is an
  ESTIMATE.** Meme min qty / step size not modelled (ESTIMATE: not binding above 5 USDT).
- Candidates are generated per symbol (each strategy's own one-position-per-symbol trade list =
  "paper trades"); the portfolio then takes them in entry-time order (ties by priority). A skipped
  candidate is not re-tried while its paper trade is still open.

## The five strategies (each explainable in one sentence)

### R1 - Cross-sectional meme momentum (long only)
*Hold the 1-3 liquid memes with the strongest risk-adjusted recent return, swap out any that drop
out of the top, and sit in cash when BTC is below its 50-day average or no meme has positive
momentum.*
- At each decision: if not "BTC up" -> target set empty. Else target = the K eligible memes with
  the highest score(L) among those with score > 0.
- Enter target coins not held; exit (next open) held coins not in the target set; stop as above.
- Rebalance `reb`: daily (every day) or weekly (Sunday's close -> Monday 00:00). Stops are always
  live.
- Grid: L in {7, 14, 30} days, K in {1, 2, 3}, reb in {daily, weekly}. **Default L=14, K=3,
  weekly.** 18 variants.

### R2 - Edge-monitor rotation (the owner's idea)
*Run one breakout setup on every liquid meme but only trade a coin while its own last N closed
paper trades are net profitable, preferring the most hyped admitted coin.*
- Base setup (fixed, not tuned): LAB S02 logic on 4h meme bars with its default parameters -
  long when the 4h close > highest high of the previous 30 bars and the bar's quote volume >= 1.5x
  the mean of the previous 30; short mirror; stop 2 x ATR14 (4h); exit when the close breaks the
  10-bar opposite extreme (next open) or after 30 bars. Chosen because it fires often enough per
  coin for a per-coin monitor (M11 fires ~4 times per coin in 2 years) and was never run on memes.
- Paper tracking: every base signal on an eligible meme is paper-traded (one at a time per coin)
  whether or not it is traded, with the scenario's costs.
- Edge monitor: a signal on coin c is admitted iff c has at least 5 paper trades that CLOSED
  STRICTLY BEFORE the decision instant and the mean net R of the last N of them is > theta.
  (Drop when it turns <= theta, re-admit when it recovers.)
- Priority among same-time admitted signals: higher `hype_volume_z30d` first (hype proxy rank).
- Grid: N in {5, 10, 20}, theta in {-0.10, 0.00, +0.10} R. **Default N=10, theta=0.** 9 variants
  plus one reference variant "monitor off" (base on all eligible memes) = 10 reported.

### R3 - Long strong / short faded
*Hold the single strongest meme (when BTC is above its 50-day average) and short up to two memes
whose hype burst has died (M11).*
- Long leg: R1 with K=1, daily rebalance, lookback L (max 1 position).
- Short leg: LAB M11 (4h; short a meme 3-7 days after a 24h-volume z >= z burst once volume z <= 0,
  the close is below the burst close, below SMA18 and below the previous low; stop = 6-bar high +
  0.5 ATR14; target 2R; time stop 42 bars) on eligible memes (max 2 positions; most faded = lowest
  volume z first).
- Grid: L in {7, 14, 30}, z in {3, 4, 5}. **Default L=14, z=4.** 9 variants.

### R4 - Volatility-targeted version of the best of R1-R3
*Trade exactly like the best of R1-R3, but cut each position so its daily volatility is at most v
of equity (never above the 1%-risk size).*
- Base = the one of R1, R2, R3 (default parameters) with the highest dev-OOS CAGR at base costs
  (tie: lower max drawdown). Decided mechanically by the code after R1-R3 run.
- Notional = min(1% risk / stop distance, v x equity / sigma30), sigma30 = sd of the coin's last
  30 daily log returns at the decision. Same skips (a cut position below 5 USDT is skipped).
- Grid: v in {0.25%, 0.5%, 1.0%}. **Default v = 0.5%.** 3 variants.

### R5 - Short the faders (LAB's own idea)
*Memes as a group bleed value, so short the 1-3 liquid memes with the weakest risk-adjusted recent
return and swap them out when they stop being the weakest.*
- Mirror of R1: target = the K eligible memes with the lowest score(L) among those with score < 0;
  weekly rebalance (fixed); stop = entry + 2 x ATR14d. Optional regime filter.
- Grid: L in {7, 14, 30}, K in {1, 2, 3}, regime in {none, only when BTC close < SMA50}.
  **Default L=14, K=3, none.** 18 variants.

**Total variants tried: 18 + 10 + 9 + 3 + 18 = 58.**

## Windows and protocol

- Development: 2022-11-01 .. 2026-04-30. The dev run **truncates all data at 2026-05-01** (no bar,
  no funding settlement at or after it is loaded; positions still open are closed at the last
  close).
  - dev IS (secondary): 2022-11 .. 2024-10.
  - **dev OOS (headline): 2024-11 .. 2026-04**, default parameters, fresh 110 USDT portfolio.
  - Walk-forward: choose the grid combination with the highest total R of taken trades over 12
    months (fresh portfolio, base costs, >= 10 trades, ties -> default), trade it for the next 3
    months, roll by 3: 10 test quarters 2023-11 .. 2026-04, stitched into one portfolio.
- **FINAL HOLDOUT: 2026-05-01 .. 2026-10-31**, run exactly ONCE per strategy, with the default
  parameters frozen here (no re-selection), only after the dev results are written and committed.
  Base and double costs come from that single run. The code refuses to run it a second time
  (`holdout.json` exists). Never tuned on.

## Gates (same as LAB night 1, never loosened). A strategy qualifies only if its DEFAULT variant passes all

"Positive" = portfolio total return > 0 AND expectancy > 0R.
1. >= 50 taken trades in dev OOS.
2. Positive in dev OOS at double costs.
3. Beats BTC buy-and-hold (the stricter of perp-with-funding and spot-like, 1x, same window, after
   costs) AND the equal-weight meme basket (monthly rebalance, all memes trading at the month
   start incl. delisted, full costs and funding), by dev-OOS CAGR after base costs.
4. Positive walk-forward (stitched test quarters: total R > 0 and portfolio return > 0).
5. Not dependent on one coin: positive at double costs in dev OOS with the best coin (largest
   total R in dev OOS at base costs) removed from the universe (the rotation re-ranks without it).
6. Robust to nearby parameter values: >= 50% of the one-step grid neighbours of the default are
   positive in dev OOS at base costs.
7. Tradeable at EUR100: <= 50% of would-be entries skipped for minimum notional (and gate 1).
8. Holdout total return > 0 after base costs.

## Also reported

Per variant: dev OOS trades / expectancy / return / CAGR, double costs, without the best coin
(double costs), max drawdown (mark-to-market at daily closes), worst month (MTM), longest losing
streak, trades skipped for minimum notional / slots, dev IS. Per strategy (default): walk-forward
picks per quarter, Monte Carlo (5,000 reshuffles and 5,000 bootstraps of dev-OOS per-trade
returns on equity: 95th-percentile max drawdown = the 5% worst case, risk of ruin = equity touching
50%), BTC regime split (calendar-month BTC return: bull > +5%, bear < -5%, else sideways), the two
dev-OOS half-years, exits, per-coin R. Outputs: `REPORT.md`, `top.json`, `dev_results.json`,
`variants.csv`, `holdout.json`, cards `research/cards/R*.md` for survivors only.

## Amendments

(none)
