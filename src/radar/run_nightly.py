"""Nightly RADAR data run (idempotent; already-verified files are not downloaded again).

Usage (from the repo root):
    cd src && ../.venv/bin/python -m radar.run_nightly
Single steps: cd src && ../.venv/bin/python -m radar.<module> [args]  (e.g. radar.universe final)

Order:
 1 list_symbols   S3 listing of every USDT-M symbol (live + delisted) -> data/symbols_all.csv
 2 screen_volume  1d klines for every symbol (volume screen)      -> data/clean/screen_1d_all_symbols.parquet
 3 classify       crypto / stablecoin / index / tradfi + meme flag -> data/symbol_classes.csv
 4 universe A     1d trailing-30d ranking, top-30 candidates       -> data/universe_screen_by_month.csv
 5 download 1h    1h klines for candidates + memes
 6 universe B     1h re-ranking, top-10 per month                  -> data/universe_by_month.csv
 7 download       funding (monthly) + metrics (daily)
 8 clean          parquet in data/clean/, quality facts in data/quality/
 9 quality        data/DATA_QUALITY.md
10 ranking        research/radar/ranking_latest.md
11 meme           research/radar/meme_universe.md (+ hype proxies)
"""
from __future__ import annotations

import time

from . import classify, clean, download, list_symbols, meme, quality, ranking, screen_volume, universe
from .archive import rewrite_manifest_sorted


def main() -> None:
    steps = [
        ("list_symbols", list_symbols.main),
        ("screen_volume", screen_volume.main),
        ("classify", classify.main),
        ("universe_screen", universe.screen),
        ("download_klines", lambda: download.main("klines")),
        ("universe_final", universe.final),
        ("download_funding_metrics", lambda: download.main("funding_metrics")),
        ("clean", clean.main),
        ("manifest_sort", rewrite_manifest_sorted),
        ("quality", quality.main),
        ("ranking", ranking.main),
        ("meme", meme.main),
    ]
    for name, fn in steps:
        t0 = time.time()
        print(f"=== {name}", flush=True)
        fn()
        print(f"=== {name} done in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
