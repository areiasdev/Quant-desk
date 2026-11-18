# RADAR setup ideas (night 1)

Ideas only. **Nothing here has been backtested** (that is LAB's job), and no performance is
claimed or implied. Every rule is written so it can be coded without look-ahead on the data in
`data/clean/` (columns as produced by `src/radar/features.py:load_panel`). Parameters are
reasonable starting points, NOT optimised values; LAB should test them as written first and treat
heavy tuning as overfitting risk.

## Shared definitions (apply to every setup)

**Bars and timing.** UTC. 1h bars come from `data/clean/klines_1h`; 4h bars are four complete 1h
bars anchored at 00/04/08/12/16/20 UTC (`features.resample_4h`). Bar *t* is the last CLOSED bar.
Every signal uses bars up to and including *t* only; the order is placed at the **open of bar
t+1** (taker, market). LAB adds fees, slippage and funding per `RULES.md`.

**Stops and targets.** Levels are fixed at entry (unless a trailing rule says otherwise) and
checked on bars after entry with that bar's high/low. If a bar touches both stop and target, assume
the **stop hit first**. If a bar opens beyond the stop, exit at that open. *R* = |entry - stop|.
"2R target" = entry +/- 2 x R. "Time stop N" = exit at the open of the bar N bars after entry.

**Building blocks** (all on the setup's timeframe, computed at bar *t*):

| name | meaning (one line) | formula |
|---|---|---|
| `HH(n)` / `LL(n)` | highest high / lowest low of the previous n bars, NOT including bar t | max(high[t-n..t-1]) / min(low[t-n..t-1]) |
| `ATR(n)` | average size of one bar over the last n bars | mean over t-n+1..t of max(high-low, abs(high-prev close), abs(low-prev close)) |
| `SMA(n)` | average close of the last n bars | mean(close[t-n+1..t]) |
| `QVavg(n)` | normal quote volume per bar | mean(quote_volume[t-n..t-1]) |
| `ret(n)` | return over n bars | close[t] / close[t-n] - 1 |
| `VWAP(window)` | average price actually paid in the window | sum(quote_volume) / sum(volume) over the window |
| `OI` | number of open contracts (`open_interest`, base units) - used instead of OI value because OI value rises mechanically with price | as-of value at bar t |
| `F8` | latest settled funding scaled to 8h | funding_rate x 8 / funding_interval_hours (as-of) |
| `F8pct(q)` | the q-th percentile of this symbol's own settled F8 over the 90 days before t | settlement events only |
| `corr30d` | how closely the coin's 1h moves follow BTC (Pearson, last 720 1h log returns) | |
| `beta30d` | how many % the coin usually moves per 1% BTC move (last 180 4h returns) | cov(coin, BTC) / var(BTC) |

**Eligibility.** Core setups (S..) trade a symbol in month M only if it is in
`data/universe_by_month.csv` for M (an open position may run past month end; no new entries
after the symbol leaves). Meme setups (M..) trade symbols with `is_meme == True` in
`data/symbol_classes.csv` whose last 24h quote volume is >= 20M USDT (liquidity floor, a starting
parameter). Never enter on a bar with `trades == 0`; skip a signal if any input is NaN (e.g.
funding for the current month, metrics outside downloaded months). A delisted symbol's position
is closed at its last real bar's close (LAB). One position per symbol per setup.
**Shorts** are the exact mirror of longs where a setup says "mirror" (HH<->LL, above<->below).

**Session clock** (DST-aware, use `zoneinfo`): Asia = 00:00-07:00 UTC (Tokyo 09:00-16:00);
EU open bar = the 1h bar opening at 08:00 Europe/London; US open bar = the 1h bar opening at
09:00 America/New_York (it contains the 09:30 cash open); US weekdays only for US rules.

---

## A. Range breakouts on rising volume

### S01 - Two-day range breakout on a volume burst
- **Why it should work / who loses:** when price escapes a narrow two-day range on at least double
  volume, the stops of traders who sold the range top get filled as market buys and add fuel;
  range-faders with stops just beyond the range lose.
- **Regime:** ranging (breaks out of a range). **Timeframe:** 1h.
- **Entry (long):** width = HH(48) - LL(48) at bar t; require width / close[t-1] to be below the
  25th percentile of the same ratio over the previous 60 days (1,440 bars); close[t] > HH(48) and
  quote_volume[t] >= 2 x QVavg(48). Buy at open t+1. Short: mirror.
- **Stop:** midpoint of the prior range, (HH(48) + LL(48)) / 2.
- **Exit:** 2R target, or time stop 48 bars.
- **Fails when:** the volume burst is one liquidation print and the range simply widens.
- **Columns:** open, high, low, close, quote_volume, trades.

### S02 - Five-day range breakout, trailing exit
- **Why / who loses:** a multi-day range stores many resting orders, and a 4h close outside it on
  rising volume shows new participants; traders positioned for the range to continue are forced out.
- **Regime:** ranging -> trending. **Timeframe:** 4h.
- **Entry (long):** close[t] > HH(30) (30 x 4h = 5 days) and quote_volume[t] >= 1.5 x QVavg(30).
  Buy at open t+1. Short: mirror.
- **Stop:** entry - 2 x ATR(14) (fixed at entry).
- **Exit:** first 4h close below LL(10) (trailing), or time stop 30 bars.
- **Fails when:** breakouts in a choppy market reverse before the trailing exit catches profit.
- **Columns:** open, high, low, close, quote_volume, trades.

### S03 - Breakout with new money (rising open interest)
- **Why / who loses:** a breakout while open interest rises means new positions are opening
  rather than shorts merely covering, so the move has fuel; traders fading the break get trapped.
- **Regime:** ranging -> trending. **Timeframe:** 1h.
- **Entry (long):** close[t] > HH(24), quote_volume[t] >= 1.5 x QVavg(24), and OI[t] / OI[t-24]
  - 1 >= +3%. Buy at open t+1. Short: mirror (close < LL(24), same OI rise - new shorts).
- **Stop:** LL(6) at bar t (long) / HH(6) (short).
- **Exit:** 2R target, or time stop 24 bars.
- **Fails when:** OI rises because both sides add hedged positions with no directional intent.
- **Columns:** high, low, close, quote_volume, open_interest, metrics_time, trades.

### S04 - Prior-week high/low breakout
- **Why / who loses:** last week's high and low are the most watched levels, and a 4h close beyond
  them on rising volume triggers clustered stops; sellers who defended the weekly high lose.
- **Regime:** ranging. **Timeframe:** 4h.
- **Entry (long):** WH = highest high of the previous UTC week (Mon 00:00 - Sun 24:00). First 4h
  close of the current week with close[t] > WH and quote_volume[t] >= 1.5 x QVavg(42) (one week of
  4h bars). Buy at open t+1. One trade per symbol per week. Short: mirror with the weekly low.
- **Stop:** midpoint of the previous week's range.
- **Exit:** 2R target, or exit at the close of the week's last 4h bar (Sun 20:00 UTC bar).
- **Fails when:** the weekly level is a magnet for stop-hunts that reverse immediately.
- **Columns:** open_time, high, low, close, quote_volume, trades.

## B. Trend pullbacks to support

### S05 - Dip to the 50-bar average in an established 4h uptrend
- **Why / who loses:** in a clear trend, dip buyers and trend followers defend the moving average
  where they add, so the first touch tends to hold; early reversal shorts lose.
- **Regime:** trending. **Timeframe:** 4h.
- **Entry (long):** close[t] > SMA(200), SMA(50)[t] > SMA(200)[t], SMA(50)[t] > SMA(50)[t-6];
  low[t] <= SMA(50)[t] and close[t] > SMA(50)[t]. Buy at open t+1. Short: mirror in a downtrend.
- **Stop:** min(low[t], LL(3)) - 0.5 x ATR(14).
- **Exit:** 2R target, or a 4h close below SMA(200), or time stop 30 bars.
- **Fails when:** the trend is ending and the average breaks on the next leg down.
- **Columns:** high, low, close, trades.

### S06 - Retest of a broken range high
- **Why / who loses:** a broken resistance becomes support because traders who missed the
  breakout buy the retest; shorts who faded the breakout add to buying when they cover.
- **Regime:** trending. **Timeframe:** 1h.
- **Entry (long):** within the last 24 bars there was a bar b with close[b] > HH(48) at b; let
  B = that HH(48) level. At bar t (t > b): low[t] <= B + 0.25 x ATR(14) and close[t] > B, and no
  close below B since b. Buy at open t+1. One trade per breakout. Short: mirror.
- **Stop:** B - 1 x ATR(14).
- **Exit:** 2R target, or time stop 36 bars.
- **Fails when:** the original breakout was false and the retest is the start of the reversal.
- **Columns:** high, low, close, trades.

### S07 - Dip below yesterday's VWAP in an uptrend
- **Why / who loses:** yesterday's average traded price is where the day's buyers broke even, and
  in an uptrend they defend it; sellers who dump the dip below it get squeezed.
- **Regime:** trending. **Timeframe:** 1h.
- **Entry (long):** close[t] > SMA(200) and SMA(200)[t] > SMA(200)[t-24]; Vy = VWAP of the
  previous UTC day; low[t] < Vy and close[t] > Vy. Buy at open t+1. Max one trade per day.
  Short: mirror in a downtrend.
- **Stop:** low[t] - 0.5 x ATR(14).
- **Exit:** 2R target, or time stop 24 bars.
- **Fails when:** news shifts the day's value area and VWAP stops mattering.
- **Columns:** high, low, close, volume, quote_volume, trades.

### S08 - Three down bars inside a strong trend
- **Why / who loses:** short pullbacks in a strong trend are mostly profit-taking that runs out
  quickly; traders who short after small dips get caught when the trend resumes.
- **Regime:** trending. **Timeframe:** 4h.
- **Entry (long):** close[t] > SMA(50) > SMA(200) (both at t); close[t] < close[t-1] <
  close[t-2] < close[t-3]. Buy at open t+1. Short: mirror.
- **Stop:** min(low[t-2..t]) - 0.5 x ATR(14).
- **Exit:** first 4h close above high[t-2], or time stop 12 bars.
- **Fails when:** the three bars are the start of a trend change, not a pause.
- **Columns:** high, low, close, trades.

## C. Reversals after liquidation flushes (proxy: big OI drop + big candle)

### S09 - Long-liquidation flush rebound
- **Why / who loses:** liquidations sell at any price and push it below where voluntary sellers
  would; once forced selling ends, price snaps back, and the liquidated over-levered longs lose.
- **Regime:** volatile. **Timeframe:** 1h.
- **Entry (long):** OI[t] / OI[t-2] - 1 <= -5%; open[t] - close[t] >= 2.5 x ATR(24)[t-1]; and
  quote_volume[t] >= 3 x QVavg(24). Buy at open t+1.
- **Stop:** low[t] - 0.5 x ATR(24)[t-1].
- **Exit:** target (open[t] + close[t]) / 2 (half of the flush candle), or time stop 12 bars.
- **Fails when:** the flush is caused by real news and selling continues after liquidations.
- **Columns:** open, high, low, close, quote_volume, open_interest, trades.

### S10 - Short-squeeze candle fade
- **Why / who loses:** forced short covering overshoots in the same way, and buyers chasing the
  squeeze candle are left holding the top.
- **Regime:** volatile. **Timeframe:** 1h.
- **Entry (short):** OI[t] / OI[t-2] - 1 <= -5%; close[t] - open[t] >= 2.5 x ATR(24)[t-1];
  quote_volume[t] >= 3 x QVavg(24). Sell at open t+1.
- **Stop:** high[t] + 0.5 x ATR(24)[t-1].
- **Exit:** target (open[t] + close[t]) / 2, or time stop 12 bars.
- **Fails when:** the squeeze is the start of a genuine trend reversal.
- **Columns:** open, high, low, close, quote_volume, open_interest, trades.

### S11 - Confirmed 4h flush reversal
- **Why / who loses:** waiting for the bar after a flush to close higher filters flushes that keep
  going; the bounce is paid for by liquidated longs and panic sellers.
- **Regime:** volatile. **Timeframe:** 4h.
- **Entry (long):** flush bar f = t-1 with OI[f] / OI[f-1] - 1 <= -8%, high[f] - low[f] >=
  3 x ATR(14)[f-1], and close[f] in the lower 30% of its range; confirmation close[t] > close[f].
  Buy at open t+1.
- **Stop:** low[f].
- **Exit:** target open[f], or time stop 6 bars.
- **Fails when:** a second liquidation wave follows the first.
- **Columns:** open, high, low, close, open_interest, trades.

### S12 - Retest of the liquidation low
- **Why / who loses:** a second test of the flush low on lower volume shows the forced sellers are
  gone; late shorts who sell into the old low lose when it holds.
- **Regime:** volatile. **Timeframe:** 1h.
- **Entry (long):** an S09 flush bar f within the last 4..24 bars; at bar t: low[t] <= low[f] x
  1.005, close[t] > low[f], quote_volume[t] < quote_volume[f], and no close below low[f] since f.
  Buy at open t+1. One trade per flush.
- **Stop:** low[f] - 0.5 x ATR(24)[t].
- **Exit:** target open[f], or time stop 24 bars.
- **Fails when:** the market is in a persistent downtrend and the low simply breaks.
- **Columns:** open, high, low, close, quote_volume, open_interest, trades.

## D. Funding extremes

### S13 - Crowded-long fade
- **Why / who loses:** when longs pay unusually high funding the market is crowded and the next
  buyer is scarce, so the first downturn unwinds leveraged longs, who lose (and shorts are paid
  funding meanwhile).
- **Regime:** volatile. **Timeframe:** 4h.
- **Entry (short):** F8[t] >= max(0.05%, F8pct(95)) and close[t] < low[t-1]. Sell at open t+1.
- **Stop:** HH(6) at t + 0.5 x ATR(14).
- **Exit:** 2R target, or first settlement with F8 <= 0.01%, or time stop 18 bars.
- **Fails when:** a strong trend keeps funding high for weeks while price keeps rising.
- **Columns:** high, low, close, funding_rate, funding_interval_hours, funding_time, trades.

### S14 - Crowded-short squeeze
- **Why / who loses:** shorts paying high negative funding are crowded and get squeezed on the
  first upturn; the crowded shorts lose.
- **Regime:** volatile. **Timeframe:** 4h.
- **Entry (long):** F8[t] <= min(-0.03%, F8pct(5)) and close[t] > high[t-1]. Buy at open t+1.
- **Stop:** LL(6) at t - 0.5 x ATR(14).
- **Exit:** 2R target, or first settlement with F8 >= 0, or time stop 18 bars.
- **Fails when:** negative funding reflects a hedge/basis trade, not directional shorts.
- **Columns:** high, low, close, funding_rate, funding_interval_hours, funding_time, trades.

### S15 - Failed breakout with crowded funding
- **Why / who loses:** breakouts bought mostly with leverage (high funding) fail more easily, and
  the leveraged breakout chasers are the losers when price falls back into the range.
- **Regime:** ranging. **Timeframe:** 1h.
- **Entry (short):** within the last 6 bars a close above HH(168) (7-day high) occurred at bar b;
  now close[t] < HH(168) as measured at b; F8[t] >= F8pct(90). Sell at open t+1. Long: mirror
  (failed breakdown with F8 <= F8pct(10)).
- **Stop:** max(high[b..t]) + 0.25 x ATR(14).
- **Exit:** target = midpoint of the 7-day range at b, or time stop 24 bars.
- **Fails when:** the first breakout attempt was just a test and the second one succeeds.
- **Columns:** high, low, close, funding_rate, funding_interval_hours, funding_time, trades.

### S16 - Paid to hold: negative funding in an uptrend
- **Why / who loses:** when price trends up but shorts still pay funding, the crowd is positioned
  against the trend; longs collect funding while trend-fighting shorts lose.
- **Regime:** trending. **Timeframe:** 4h.
- **Entry (long):** close[t] > SMA(50) > SMA(200) and F8[t] < 0 (latest settlement). Buy at open
  t+1. One entry per uptrend leg (re-arm after a close below SMA(50)).
- **Stop:** entry - 2 x ATR(14).
- **Exit:** a 4h close below SMA(50), or first settlement with F8 > 0.01%.
- **Fails when:** negative funding comes from cash-and-carry unwinds, not directional shorts.
- **Columns:** close, high, low, funding_rate, funding_interval_hours, funding_time, trades.

### S17 - Pre-settlement drift with high funding
- **Why / who loses:** longs facing a large funding payment close just before the settlement to
  avoid paying it, which pushes price down in the last hour; longs who hold through pay both
  the funding and the drift.
- **Regime:** volatile. **Timeframe:** 1h.
- **Entry (short):** bar t is the bar that ends exactly 1h before the next settlement (e.g. the
  06:00-07:00 UTC bar for an 08:00 settlement; use funding_interval_hours of the latest
  settlement to project the next one); F8[t] >= 0.05%. Sell at open t+1. Long: mirror with
  F8 <= -0.05%.
- **Stop:** entry + 1 x ATR(24) (short).
- **Exit:** at the open of bar t+2, the first bar after the settlement (the position is open at
  the settlement instant; LAB books that settlement's funding).
- **Fails when:** the realised drift is smaller than fees + slippage (very likely at small size;
  LAB must check costs first).
- **Columns:** open_time, close, high, low, funding_rate, funding_interval_hours, funding_time.

## E. Open-interest divergences

### S18 - Rally on falling OI (short-covering) fade
- **Why / who loses:** a rally made of shorts closing, not new buyers, runs out once covering
  ends; buyers who chase a move with no new money behind it lose.
- **Regime:** volatile. **Timeframe:** 1h.
- **Entry (short):** ret(24) >= +5% and OI[t] / OI[t-24] - 1 <= -5%; trigger close[t] <
  low[t-1]. Sell at open t+1.
- **Stop:** HH(24) at t + 0.5 x ATR(14).
- **Exit:** target close[t-24] + 0.5 x (HH(24) - close[t-24]) (give back half the rally), or
  time stop 24 bars.
- **Fails when:** covering turns into fresh buying (OI starts rising again).
- **Columns:** high, low, close, open_interest, trades.

### S19 - Sell-off into support on rising OI (late shorts) squeeze
- **Why / who loses:** new shorts opened late, at support, become squeeze fuel when the level
  holds; those late shorts lose.
- **Regime:** volatile. **Timeframe:** 1h.
- **Entry (long):** ret(24) <= -5%, OI[t] / OI[t-24] - 1 >= +8%, low[t] < LL(168) (new 7-day
  low intrabar) and close[t] > LL(168). Buy at open t+1.
- **Stop:** low[t] - 0.5 x ATR(14).
- **Exit:** 2R target, or time stop 24 bars.
- **Fails when:** the new shorts are right (news-driven repricing).
- **Columns:** high, low, close, open_interest, trades.

### S20 - Price up and OI up: trend with fresh participation
- **Why / who loses:** rising price with rising open interest means new longs are committing
  money, so the move tends to persist; early shorts lose.
- **Regime:** trending. **Timeframe:** 4h.
- **Entry (long):** ret(6) >= +3% and OI[t] / OI[t-6] - 1 >= +5% and close[t] > high[t-1].
  Buy at open t+1. Short: mirror (price down, OI up, close < low[t-1]).
- **Stop:** LL(6) at t.
- **Exit:** 2R target, or time stop 12 bars.
- **Fails when:** the OI build is late leverage at the end of the move (overlaps S13).
- **Columns:** high, low, close, open_interest, trades.

### S21 - Big accounts versus the crowd
- **Why / who loses:** large accounts are on average better informed than the mass of small
  accounts, so when the top traders add longs while the crowd adds shorts, the crowd loses.
- **Regime:** ranging. **Timeframe:** 1h.
- **Entry (long):** top_trader_position_ls_ratio[t] / [t-24] - 1 >= +10% and
  global_account_ls_ratio[t] / [t-24] - 1 <= -10%. Buy at open t+1. Short: mirror.
- **Stop:** entry - 2 x ATR(14).
- **Exit:** 2R target, or time stop 24 bars.
- **Fails when:** "top trader" accounts are market makers whose positions are hedges.
- **Columns:** close, high, low, top_trader_position_ls_ratio, global_account_ls_ratio,
  metrics_time, trades.

## F. Session opens (Asia / EU / US)

### S22 - Asian range breakout at the European open
- **Why / who loses:** Europeans act on overnight news and a break of the quiet Asian range
  triggers the stops parked just outside it; Asian-range sellers with stops above the high lose.
- **Regime:** ranging. **Timeframe:** 1h.
- **Entry (long):** Asia range = high/low of the seven 1h bars 00:00-06:00 UTC of the same day;
  require its width to be below the median Asia width of the previous 20 days. Signal bar = EU
  open bar (08:00 London); close[t] > Asia high. Buy at open t+1. Short: mirror below the low.
- **Stop:** Asia range midpoint.
- **Exit:** 2R target, or at the open of the bar starting 16:00 London.
- **Fails when:** no news arrives and Europe simply trades back into the range.
- **Columns:** open_time, high, low, close, trades.

### S23 - US-open reversal of the European move
- **Why / who loses:** larger US flows at the cash open often reverse European positioning, and
  traders who bought the European push late lose.
- **Regime:** volatile. **Timeframe:** 1h. US weekdays only.
- **Entry (short case):** EU move = close of the bar opening 08:00 New York minus open of the EU
  open bar; require EU move >= +1.5 x ATR(24) (ATR at the EU open bar). US open bar t: close[t]
  - open[t] <= -0.5 x ATR(24). Sell at open t+1. Long: mirror after a down EU move.
- **Stop:** highest high from the EU open bar to bar t.
- **Exit:** 2R target, or at the open of the bar starting 16:00 New York.
- **Fails when:** US flows agree with Europe and the first-hour dip is bought.
- **Columns:** open_time, open, high, low, close, trades.

### S24 - US-open momentum
- **Why / who loses:** US ETF and institutional orders cluster at the cash open, so a strong,
  heavy first hour signals the day's flow; traders fading the open lose.
- **Regime:** trending. **Timeframe:** 1h. US weekdays only.
- **Entry:** US open bar t with abs(close[t] - open[t]) >= 1 x ATR(24) and quote_volume[t] >=
  2 x the mean quote_volume of the US open bars of the previous 10 US weekdays. Enter in the
  direction of the bar at open t+1.
- **Stop:** the bar's opposite extreme (low[t] for longs, high[t] for shorts).
- **Exit:** 2R target, or at the open of the bar starting 15:00 New York.
- **Fails when:** the opening move is a single block order with no follow-through.
- **Columns:** open_time, open, high, low, close, quote_volume, trades.

### S25 - Monday breakout of the weekend range
- **Why / who loses:** thin weekend trading builds a compressed range, and when weekday traders
  return on Monday (Asia first) the break triggers stops; weekend range traders lose.
- **Regime:** ranging. **Timeframe:** 1h.
- **Entry (long):** weekend range = high/low of Sat 00:00 - Sun 23:00 UTC bars; require width <
  0.5 x the mean daily (24h) range of the previous four weekdays. Between Mon 00:00 and Mon 11:00
  UTC, first 1h close above the weekend high. Buy at open t+1. Short: mirror.
- **Stop:** weekend range midpoint.
- **Exit:** 2R target, or at the open of Tue 00:00 UTC.
- **Fails when:** Monday opens with a gap of news that already moved price through the range.
- **Columns:** open_time, high, low, close, trades.

## G. Altcoins lagging BTC

### S26 - Alt catch-up after a BTC breakout
- **Why / who loses:** money rotates from BTC into alts with a lag, so an alt that normally
  follows BTC but has not moved yet tends to catch up; traders who sold the laggard lose.
- **Regime:** trending. **Timeframe:** 1h.
- **Entry (long alt):** BTC close[t] > BTC HH(72) and BTC ret(24) >= +3%; alt (universe member,
  not BTC) with corr30d >= 0.6 and alt ret(24) <= BTC ret(24) - 3 percentage points. Buy the alt
  at open t+1 (max 3 alts per signal: the three with the largest lag).
- **Stop:** alt LL(24) - 0.5 x alt ATR(14).
- **Exit:** 2R target, or time stop 24 bars.
- **Fails when:** the alt lags for a reason (unlock, hack, delisting risk).
- **Columns:** alt high, low, close, trades; BTC high, close.

### S27 - Alt catch-down after a BTC drop
- **Why / who loses:** alts that have not followed a sharp BTC drop are usually propped by stale
  bids that get pulled; holders who think the coin is "strong" lose.
- **Regime:** trending (down). **Timeframe:** 1h.
- **Entry (short alt):** BTC close[t] < BTC LL(72) and BTC ret(24) <= -3%; alt with corr30d >= 0.6
  and alt ret(24) >= BTC ret(24) + 3 points. Sell at open t+1 (max 3 alts).
- **Stop:** alt HH(24) + 0.5 x alt ATR(14).
- **Exit:** 2R target, or time stop 24 bars.
- **Fails when:** the alt has its own good news.
- **Columns:** alt high, low, close, trades; BTC low, close.

### S28 - Beta-adjusted laggard in a BTC uptrend
- **Why / who loses:** if an alt usually moves beta x BTC but has fallen two "normal surprises"
  behind, the gap tends to close while BTC trends; sellers of the lag lose.
- **Regime:** trending. **Timeframe:** 4h.
- **Entry (long alt):** BTC close > BTC SMA(50) (4h). residual = alt ret(6) - beta30d x BTC ret(6);
  z = residual / std of the same residual over the previous 180 4h bars; z[t] <= -2. Buy at open t+1.
- **Stop:** entry - 2 x alt ATR(14).
- **Exit:** z >= 0, or time stop 18 bars.
- **Fails when:** beta shifts (e.g. after a narrative change) and the "lag" is the new normal.
- **Columns:** alt close, high, low, trades; BTC close.

### S29 - Alt still below its high when BTC makes a new 30-day high
- **Why / who loses:** when BTC breaks out, money spills into alts still below their own highs;
  shorts in those alts and sellers at lower prices lose.
- **Regime:** trending. **Timeframe:** 4h.
- **Entry (long alt):** BTC close[t] > BTC HH(180) (30 days of 4h); alt close[t] <= 0.9 x alt
  HH(180) and alt close[t] > alt SMA(50). Buy at open t+1.
- **Stop:** alt LL(12) at t.
- **Exit:** alt close > alt HH(180) at entry (new 30-day high) or 2R, or time stop 42 bars.
- **Fails when:** the market is BTC-only (alts structurally weak; check BTC dominance regime).
- **Columns:** alt high, low, close, trades; BTC high, close.

---

## M. Meme-coin track (all regime = volatile)

Hype proxies are defined in `research/radar/meme_universe.md` and computed by `src/radar/hype.py`
(`hype_volume_z30d`, `hype_oi_surge_7d`, `hype_funding_jump_bp`, `hype_move_vs_btc_24h`,
`days_since_listing`). Thresholds below are starting parameters.

### M01 - Ride a fresh hype burst
- **Why / who loses:** an attention spike brings waves of new buyers that last hours to days, and
  shorts or early sellers who sold before the crowd arrived lose.
- **Regime:** volatile. **Timeframe:** 1h. **Side:** long (ride).
- **Entry:** hype_volume_z30d[t] >= 3, hype_oi_surge_7d[t] >= +30%, close[t] > HH(24),
  days_since_listing >= 30. Buy at open t+1.
- **Stop:** max(LL(6), entry - 1.5 x ATR(14)) (the closer of the two).
- **Exit:** first close below LL(6) (trailing), or time stop 48 bars.
- **Fails when:** the burst is a single whale or exchange event and attention dies within hours.
- **Columns:** high, low, close, quote_volume, open_interest_value_usdt, hype_volume_z30d,
  hype_oi_surge_7d, days_since_listing, trades.

### M02 - Second leg of a not-yet-crowded pump
- **Why / who loses:** a strong move vs BTC that is not yet crowded (funding barely moved) tends to
  get a second leg after the first dip; dip sellers lose.
- **Regime:** volatile. **Timeframe:** 1h. **Side:** long (ride).
- **Entry:** signal bar s: hype_move_vs_btc_24h[s] >= +15%, hype_volume_z30d[s] >= 2,
  hype_funding_jump_bp[s] <= +2. Within the next 12 bars, the first bar t whose close is between
  VWAP(last 24 bars) and 1.02 x VWAP(last 24 bars). Buy at open t+1.
- **Stop:** VWAP(last 24 bars at t) - 1 x ATR(14).
- **Exit:** 2R target, or time stop 24 bars.
- **Fails when:** funding data is stale (current month) and the move is in fact crowded.
- **Columns:** close, high, low, volume, quote_volume, hype_move_vs_btc_24h, hype_volume_z30d,
  hype_funding_jump_bp, trades.

### M03 - Fade extreme funding after a pump
- **Why / who loses:** when meme longs pay extreme funding after a pump, the late leveraged buyers
  are the exit liquidity for early holders, and they lose in the unwind.
- **Regime:** volatile. **Timeframe:** 1h. **Side:** short (fade).
- **Entry:** F8[t] >= 0.10%, hype_funding_jump_bp[t] >= +20, hype_move_vs_btc_24h[t] >= +20%,
  and close[t] < low[t-1]. Sell at open t+1.
- **Stop:** HH(12) + 0.5 x ATR(14).
- **Exit:** target = VWAP of the 24 bars ending at t, or first settlement with F8 <= 0.01%, or
  time stop 36 bars.
- **Fails when:** a squeeze continues (short side of memes has unlimited upside risk; LAB must
  check stop slippage on gap bars).
- **Columns:** high, low, close, volume, quote_volume, funding_rate, funding_interval_hours,
  hype_funding_jump_bp, hype_move_vs_btc_24h, trades.

### M04 - Post-flush rebound
- **Why / who loses:** meme liquidation cascades overshoot far beyond any information and bounce
  once forced selling ends; liquidated longs and panic sellers lose.
- **Regime:** volatile. **Timeframe:** 1h. **Side:** long (fade the flush).
- **Entry:** OI[t] / OI[t-3] - 1 <= -15%, ret(3) <= -15%, and max quote_volume of bars t-2..t >=
  3 x QVavg(24) at t-2. Buy at open t+1.
- **Stop:** min(low[t-2..t]) - 0.5 x ATR(24).
- **Exit:** target close[t-3] - 0.5 x (close[t-3] - close[t]) (half the drop back), or time
  stop 12 bars.
- **Fails when:** the flush follows a rug, exploit or delisting announcement.
- **Columns:** high, low, close, quote_volume, open_interest, trades.

### M05 - New-listing breakdown
- **Why / who loses:** new meme perps list at peak attention while early holders and airdrop
  recipients sell, so they drift lower once the launch buzz fades; launch-week buyers lose.
- **Regime:** volatile. **Timeframe:** 1h. **Side:** short (fade).
- **Entry:** 2 <= days_since_listing <= 14; close[t] < LL(24); close[t] < VWAP of the contract's
  first 24 bars. Sell at open t+1. One trade per symbol.
- **Stop:** HH(24) at t.
- **Exit:** 2R target, or time stop 72 bars.
- **Fails when:** the listing is the start of a real hype cycle (strong M01 conditions).
- **Columns:** open_time, high, low, close, volume, quote_volume, days_since_listing, trades.

### M06 - Fade a first-week spike
- **Why / who loses:** a violent rally in a fresh listing is usually short-covering plus FOMO with
  no new holders behind it, and the FOMO buyers lose.
- **Regime:** volatile. **Timeframe:** 1h. **Side:** short (fade).
- **Entry:** days_since_listing <= 10; ret(24) >= +40%; hype_volume_z30d >= 3 (or, with less than
  20 days of history, quote_volume of the last 24 bars >= 3 x the mean 24h volume since listing);
  close[t] < low[t-1]. Sell at open t+1.
- **Stop:** HH(24) + 0.5 x ATR(14).
- **Exit:** 2R target, or time stop 24 bars.
- **Fails when:** float is tiny and shorts get squeezed again.
- **Columns:** high, low, close, quote_volume, hype_volume_z30d, days_since_listing, trades.

### M07 - Volume-climax reversal
- **Why / who loses:** a climactic volume bar with a long upper wick after a big run marks the last
  buyers arriving, and those FOMO buyers lose.
- **Regime:** volatile. **Timeframe:** 1h. **Side:** short (fade).
- **Entry:** hype_volume_z30d[t] >= 5, ret(24) >= +30%, close[t] < open[t], and (high[t] -
  max(open[t], close[t])) >= 0.6 x (high[t] - low[t]). Sell at open t+1.
- **Stop:** high[t] + 0.25 x ATR(14).
- **Exit:** 2R target, or time stop 24 bars.
- **Fails when:** the wick is just a pause in a parabolic move.
- **Columns:** open, high, low, close, hype_volume_z30d, trades.

### M08 - Meme laggard after a BTC risk-on move
- **Why / who loses:** memes are the highest-beta corner of crypto and usually follow a BTC risk-on
  move with a lag; holders who sold the lag lose.
- **Regime:** volatile. **Timeframe:** 1h. **Side:** long (ride the catch-up).
- **Entry:** BTC ret(24) >= +3% and BTC close[t] > BTC HH(72); meme with hype_move_vs_btc_24h <= -3%
  and corr30d >= 0.5. Buy at open t+1 (max 3 memes: largest lag).
- **Stop:** LL(24) - 0.5 x ATR(14).
- **Exit:** 2R target, or time stop 24 bars.
- **Fails when:** risk appetite stops at BTC (institutional, ETF-led rally).
- **Columns:** high, low, close, hype_move_vs_btc_24h, trades; BTC high, close.

### M09 - Coiled OI break
- **Why / who loses:** open interest piling up while price stays flat means a crowd is positioned,
  and the break of the range forces the wrong side out; the trapped side loses.
- **Regime:** volatile. **Timeframe:** 1h. **Side:** both.
- **Entry:** hype_oi_surge_7d[t] >= +50% and abs(ret(24)) <= 5%; long if close[t] > HH(48), short
  if close[t] < LL(48). Enter at open t+1.
- **Stop:** the opposite side of the 48-bar range (LL(48) for longs, HH(48) for shorts), but no
  further than 2 x ATR(14) from entry.
- **Exit:** 2R target, or time stop 48 bars.
- **Fails when:** the OI build is market-maker inventory around a new listing on other venues.
- **Columns:** high, low, close, open_interest_value_usdt, hype_oi_surge_7d, trades.

### M10 - Squeeze of a heavily shorted meme
- **Why / who loses:** when shorts pay heavily to hold a meme and price turns up, covering feeds on
  itself; the crowded shorts lose.
- **Regime:** volatile. **Timeframe:** 1h. **Side:** long (ride the squeeze).
- **Entry:** F8[t] <= -0.10%, hype_oi_surge_7d[t] >= +20%, close[t] > HH(12). Buy at open t+1.
- **Stop:** LL(12) at t.
- **Exit:** 2R target, first settlement with F8 >= 0, or time stop 24 bars.
- **Fails when:** shorts are right (unlock or delisting coming) and price breaks back down.
- **Columns:** high, low, close, funding_rate, funding_interval_hours, hype_oi_surge_7d, trades.

### M11 - Hype decay short
- **Why / who loses:** once attention fades after a burst, the people who bought the burst sell
  into thinning demand, and they are the ones who lose.
- **Regime:** volatile. **Timeframe:** 4h. **Side:** short (fade).
- **Entry:** a burst bar b (1h) with hype_volume_z30d[b] >= 4 between 72 and 168 hours before t;
  at the 4h bar t: hype_volume_z30d of the last 1h bar <= 0, close[t] < close of burst bar b,
  close[t] < SMA(18) (3 days of 4h), close[t] < low[t-1]. Sell at open t+1.
- **Stop:** HH(6) at t + 0.5 x ATR(14).
- **Exit:** 2R target, or time stop 42 bars (7 days).
- **Fails when:** a second catalyst revives attention.
- **Columns:** high, low, close, quote_volume, hype_volume_z30d, trades.

---

## Setup index

| id | family | timeframe | regime | side |
|---|---|---|---|---|
| S01 | range breakout + volume | 1h | ranging | both |
| S02 | range breakout + volume | 4h | ranging | both |
| S03 | breakout + rising OI | 1h | ranging | both |
| S04 | prior-week breakout | 4h | ranging | both |
| S05 | trend pullback (SMA50) | 4h | trending | both |
| S06 | breakout retest | 1h | trending | both |
| S07 | pullback to prior-day VWAP | 1h | trending | both |
| S08 | three down bars in trend | 4h | trending | both |
| S09 | flush rebound | 1h | volatile | long |
| S10 | squeeze fade | 1h | volatile | short |
| S11 | confirmed 4h flush | 4h | volatile | long |
| S12 | flush-low retest | 1h | volatile | long |
| S13 | crowded-long fade | 4h | volatile | short |
| S14 | crowded-short squeeze | 4h | volatile | long |
| S15 | failed breakout + funding | 1h | ranging | both |
| S16 | negative funding in uptrend | 4h | trending | long |
| S17 | pre-settlement drift | 1h | volatile | both |
| S18 | rally on falling OI fade | 1h | volatile | short |
| S19 | sell-off on rising OI squeeze | 1h | volatile | long |
| S20 | price + OI up continuation | 4h | trending | both |
| S21 | top traders vs crowd | 1h | ranging | both |
| S22 | Asia range break at EU open | 1h | ranging | both |
| S23 | US-open reversal | 1h | volatile | both |
| S24 | US-open momentum | 1h | trending | both |
| S25 | Monday weekend-range break | 1h | ranging | both |
| S26 | alt catch-up | 1h | trending | long |
| S27 | alt catch-down | 1h | trending | short |
| S28 | beta-adjusted laggard | 4h | trending | long |
| S29 | alt below high on BTC breakout | 4h | trending | long |
| M01 | hype burst ride | 1h | volatile | long |
| M02 | second leg, not crowded | 1h | volatile | long |
| M03 | extreme funding fade | 1h | volatile | short |
| M04 | post-flush rebound | 1h | volatile | long |
| M05 | new-listing breakdown | 1h | volatile | short |
| M06 | first-week spike fade | 1h | volatile | short |
| M07 | volume-climax reversal | 1h | volatile | short |
| M08 | meme laggard after BTC move | 1h | volatile | long |
| M09 | coiled OI break | 1h | volatile | both |
| M10 | heavily-shorted squeeze | 1h | volatile | long |
| M11 | hype decay short | 4h | volatile | short |

Notes for LAB: S17 and other short-holding-time setups are the most cost-sensitive (RULES.md fee
and slippage floors). Funding-based rules cannot fire in the current month until its funding file
is published. Universe metrics exist only for universe months plus the month before, which
covers every look-back used here (max 7 days for OI).
