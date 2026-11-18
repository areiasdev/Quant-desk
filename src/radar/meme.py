"""Step 11: meme-coin track - universe table, coverage, hype proxies -> meme_universe.md.

Listing date  = first 1h/1d bar of the contract in the archive (the archive starts at listing).
Delisting date= last bar with trades > 0, when the contract then stops trading (ghost bars after
                it are ignored, see clean.py). "trading" = traded in the latest 1h bar.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from .archive import fetch_many, local_path
from .clean import KLINE_COLS, QUALITY
from .config import CLEAN, DATA, RESEARCH
from .symbols import MEME_BORDERLINE_EXCLUDED

OUT = RESEARCH / "meme_universe.md"
CSV = DATA / "meme_universe.csv"


def _first_bar_before_window(sym: str, first_month: str) -> pd.Timestamp | None:
    """Exact listing day for contracts listed before the screen window (one monthly 1d file)."""
    from .archive import read_zip_csv

    key = f"data/futures/um/monthly/klines/{sym}/1d/{sym}-1d-{first_month}.zip"
    fetch_many([key], f"listing-{sym}")
    p = local_path(key)
    if not p.exists():
        return None
    d = read_zip_csv(p, KLINE_COLS)
    return pd.to_datetime(pd.to_numeric(d["open_time"]).min(), unit="ms", utc=True)


def build_table() -> pd.DataFrame:
    cls = pd.read_csv(DATA / "symbol_classes.csv", dtype={"first_month": str})
    memes = cls[cls["is_meme"]].copy()
    uni = pd.read_csv(DATA / "universe_by_month.csv")
    kq = pd.read_csv(QUALITY / "klines_1h_summary.csv").set_index("symbol")
    fq = pd.read_csv(QUALITY / "funding_summary.csv").set_index("symbol")
    mq = pd.read_csv(QUALITY / "metrics_summary.csv").set_index("symbol")
    rows = []
    for _, r in memes.iterrows():
        s = r["symbol"]
        k = pd.read_parquet(CLEAN / "klines_1h" / f"{s}.parquet", columns=["open_time", "trades"])
        real = k[k["trades"] > 0]
        if str(r["first_month"]) < "2022-10":
            lst = _first_bar_before_window(s, r["first_month"])
        else:
            lst = k["open_time"].min()
        last_trade = real["open_time"].max()
        latest = pd.Timestamp(kq["last_bar"].max())
        status = "trading" if last_trade >= latest else "delisted"
        rows.append({
            "symbol": s,
            "listing_date_utc": lst.date().isoformat() if lst is not None else "",
            "status": status,
            "delisting_date_utc": "" if status == "trading" else (last_trade + pd.Timedelta(hours=1)).date().isoformat(),
            "last_trade_bar_utc": last_trade.isoformat(),
            "months_in_top10": int((uni["symbol"] == s).sum()),
            "klines_1h_first": kq.loc[s, "first_bar"][:10] if s in kq.index else "",
            "klines_1h_last": last_trade.date().isoformat(),
            "funding_first": str(fq.loc[s, "first"])[:10] if s in fq.index and fq.loc[s, "rows"] > 0 else "",
            "funding_last": str(fq.loc[s, "last"])[:10] if s in fq.index and fq.loc[s, "rows"] > 0 else "",
            "metrics_first": str(mq.loc[s, "first"])[:10] if s in mq.index and mq.loc[s, "rows"] > 0 else "",
            "metrics_last": str(mq.loc[s, "last"])[:10] if s in mq.index and mq.loc[s, "rows"] > 0 else "",
        })
    t = pd.DataFrame(rows).sort_values("listing_date_utc").reset_index(drop=True)
    t.to_csv(CSV, index=False)
    return t


def main() -> None:
    from .hype import build

    t = build_table()
    listing = {s: pd.Timestamp(d, tz="UTC") for s, d in zip(t["symbol"], t["listing_date_utc"])}
    hype = build(list(t["symbol"]), listing)
    latest_t = hype["open_time"].max()
    snap = hype[hype["open_time"] == latest_t].set_index("symbol")
    cls = pd.read_csv(DATA / "symbol_classes.csv", dtype={"first_month": str})
    gen = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    trading = t[t["status"] == "trading"]
    L = [f"# Meme-coin universe\n",
         f"Generated {gen} by `src/radar/meme.py`. Data: data.binance.vision archive only "
         f"(`data/MANIFEST.csv`). Table also in `data/meme_universe.csv`.\n",
         f"**{len(t)} meme perps** ({len(trading)} trading at the latest bar "
         f"{latest_t:%Y-%m-%d %H:%M} UTC, {len(t) - len(trading)} delisted).\n",
         "## How a coin was classified as a meme coin\n",
         "1. Start from every USDT-M symbol in the S3 listing of the archive "
         "(`data/symbols_all.csv`, live and delisted).",
         "2. Rule: a meme coin is a token whose main value proposition is a joke, mascot, animal, "
         "internet meme, celebrity or political figure, with no protocol or product whose usage "
         "the token captures. Each symbol was reviewed by name/known project against this rule; "
         "the list lives in `src/radar/symbols.py` (`MEME`).",
         "3. The symbol must ALSO be classed `crypto` (`data/symbol_classes.csv`). Binance lists "
         "stock perps whose tickers collide with memes (WEN = Wendy's, PENG = Penguin Solutions, "
         "CAT = Caterpillar, all listed 2026-07); the weekend-volume fingerprint in `symbols.py` "
         "flags them as TradFi, so they are not memes.",
         "4. Borderline names (AI-agent products, tap-to-earn games, launchpads, NFT brand tokens, "
         "unverified tickers) are listed below with the reason they were left out.",
         "5. Listing date = first bar of the contract in the archive; delisting date = the day "
         "after the last bar with trades > 0 (the archive keeps writing flat zero-trade bars "
         "after a delisting, which are ignored). Multiplier contracts (1000PEPE, 1000000MOG, "
         "1MBABYDOGE ...) quote the price of 1,000 / 1,000,000 tokens.\n",
         "## Meme perps\n",
         "| symbol | listed (UTC) | status | delisted (UTC) | months in top-10 | 1h klines | funding | metrics (OI) |",
         "|---|---|---|---|---:|---|---|---|"]
    for _, r in t.iterrows():
        L.append(f"| {r['symbol']} | {r['listing_date_utc']} | {r['status']} | {r['delisting_date_utc'] or '-'} | "
                 f"{r['months_in_top10']} | {r['klines_1h_first']} .. {r['klines_1h_last']} | "
                 f"{(r['funding_first'] + ' .. ' + r['funding_last']) if r['funding_first'] else 'none'} | "
                 f"{(r['metrics_first'] + ' .. ' + r['metrics_last']) if r['metrics_first'] else 'none'} |")
    L.append("\nData windows are clipped to the research window (from 2022-11-01); a contract "
             "listed earlier shows 2022-11-01 as its first 1h bar.\n")
    L.append("## Borderline symbols NOT counted as memes\n")
    L.append("| symbol | reason |")
    L.append("|---|---|")
    for s, why in sorted(MEME_BORDERLINE_EXCLUDED.items()):
        L.append(f"| {s} | {why} |")
    new = cls[(cls["symbol_class"] == "crypto") & (~cls["is_meme"])
              & (cls["first_month"].fillna("2026-11") >= "2026-01")
              & (~cls["symbol"].isin(MEME_BORDERLINE_EXCLUDED))]
    L.append(f"\n**Not reviewed ({len(new)} crypto perps first listed in 2026):** these projects "
             "are newer than the reviewer's knowledge and were treated as non-meme. Owner review "
             "requested in `research/QUESTIONS.md`: " + ", ".join(sorted(new["symbol"])) + ".\n")
    L.append("## Hype proxies (exchange data only)\n")
    L.append("No free historical social-media or news archive exists, so hype is measured from "
             "what the exchange records. Code: `src/radar/hype.py`; all bars: "
             "`data/clean/meme_hype_1h.parquet`. Each value uses only data known at the 1h bar's "
             "close.\n")
    L.append("| proxy | column | one-sentence meaning |")
    L.append("|---|---|---|")
    L.append("| volume z-score vs 30d | `hype_volume_z30d` | How many standard deviations the last 24h of "
             "quote volume sits above the 30 previous daily volumes (taken at the same hour). |")
    L.append("| OI surge vs 7d | `hype_oi_surge_7d` | How much more money sits in open positions now than "
             "the median of the previous 7 days (0.5 = +50%). |")
    L.append("| funding jump | `hype_funding_jump_bp` | How much the latest settled funding rate (scaled to "
             "8h) exceeds the mean of the settlements in the 7 days before it, in basis points. |")
    L.append("| price move vs BTC | `hype_move_vs_btc_24h` | The coin's 24h return minus BTC's 24h return, "
             "i.e. how far it ran ahead of (or fell behind) the market. |")
    L.append("| days since listing | `days_since_listing` | Days since the contract's first Binance futures "
             "bar; fresh listings get the most attention and the steepest fades. |")
    L.append(f"\n### Snapshot at the latest bar ({latest_t:%Y-%m-%d %H:%M} UTC open), trading memes\n")
    L.append("Funding in the archive ends with the last complete month, so `funding jump` is stale "
             "for the current month (shown for reference only).\n")
    L.append("| symbol | 24h quote vol (M USDT) | volume z30d | OI surge 7d | funding jump (bp) | move vs BTC 24h | days since listing |")
    L.append("|---|---:|---:|---:|---:|---:|---:|")
    qv24 = hype[hype["open_time"] > latest_t - pd.Timedelta(hours=24)].groupby("symbol")["quote_volume"].sum()
    for s in sorted(trading["symbol"], key=lambda x: -qv24.get(x, 0)):
        if s not in snap.index:
            continue
        r = snap.loc[s]

        def f(v, nd=2, pct=False):
            return "n/a" if pd.isna(v) else (f"{100 * v:+.1f}%" if pct else f"{v:.{nd}f}")
        L.append(f"| {s} | {qv24.get(s, 0) / 1e6:,.1f} | {f(r['hype_volume_z30d'])} | "
                 f"{f(r['hype_oi_surge_7d'], pct=True)} | {f(r['hype_funding_jump_bp'], 1)} | "
                 f"{f(r['hype_move_vs_btc_24h'], pct=True)} | {f(r['days_since_listing'], 0)} |")
    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"wrote {OUT} and {CSV}")


if __name__ == "__main__":
    main()
