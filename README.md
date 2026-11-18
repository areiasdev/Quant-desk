# Quant desk

Binance USDT-M perps research and a **PAPER-ONLY** trading bot. Rules for every agent:
[RULES.md](RULES.md). No real orders exist anywhere in this repository.

| what | where |
|---|---|
| **Paper bot: install, run, logs, report, dashboard, HALT reset (owner guide, PT)** | [bot/README.md](bot/README.md) |
| Bot settings (the only file the owner edits) | [config.yaml](config.yaml) |
| Start the bot | Windows `run_bot.ps1`, macOS/Linux `run_bot.sh` |
| Monthly report / dashboard | `python -m bot.report --days 30` / `python -m bot.dashboard` |
| Backtest results (LAB) | [research/lab/REPORT.md](research/lab/REPORT.md), [top_setup.json](research/lab/top_setup.json) |
| Strategy card of the paper-traded setup | [research/cards/S16.md](research/cards/S16.md) |
| Universe and setup research (RADAR) | [research/radar/](research/radar/), [data/DATA_QUALITY.md](data/DATA_QUALITY.md) |
| Code | `src/radar/` (data, universe), `src/lab/` (backtest engine, setups), `bot/` (paper bot) |
| Tests | `.venv/bin/python -m pytest -q` (needs `requirements-dev.txt` and the local `data/clean` archive) |
| Open questions | [research/QUESTIONS.md](research/QUESTIONS.md) |
