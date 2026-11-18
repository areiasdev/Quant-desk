# Quant Desk – rules every agent follows

Owner settings (2026-11-18): Binance USDT-M perps, top-10 by point-in-time volume, 1h/4h,
account < $1k, risk 1% per trade, PAPER ONLY until the owner says "go live".

1. Only real data with a source URL and a download timestamp (see data/MANIFEST.csv). Never invent
   prices, fills, backtest numbers or profits. Label every estimate as ESTIMATE.
2. Every test includes taker fees (0.05%/side baseline, re-run at 0.10%), funding, slippage
   (≥1 bp majors, ≥3 bp others) and includes delisted coins (no survivorship bias).
3. No look-ahead: signals use only data closed before the bar on which the order is placed.
4. Out-of-sample/walk-forward results are the headline numbers; in-sample is secondary.
5. A setup needs ≥50 OOS trades, must beat buy-and-hold BTC after costs, and must be tradeable at a
   $500–$1000 account with Binance minimum notional (verify from exchange info, mark ESTIMATE if not).
6. Every strategy card ends with "WHY THIS COULD FAIL". Prefer boring and proven over exciting.
7. No API keys anywhere in code, logs or chat. This cloud box gets HTTP 451 from Binance live APIs;
   do not try to bypass geo-restrictions. Historical data comes from data.binance.vision only.
8. If stuck, write the question in research/QUESTIONS.md and continue.
