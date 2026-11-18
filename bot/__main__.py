"""Start the paper bot:  python -m bot   (Ctrl+C or close the window to stop).

  --once        run one cycle and exit (a quick check that everything works)
  --config F    another config file (default: config.yaml in the repository folder)
Exit codes: 0 normal, 2 config error, 3 'mode: live' (refused). run_bot.ps1/.sh do not restart
the bot after exit codes 2 and 3, because a restart cannot fix them.
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import sys

from . import config as C
from .datasource import Clock, RestDataSource
from .live import GO_LIVE_MESSAGE


def setup_logging(logs_dir) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger("bot")
    root.setLevel(logging.INFO)
    root.handlers.clear()
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(fmt)
    f = logging.handlers.RotatingFileHandler(logs_dir / "bot.log", maxBytes=5_000_000,
                                             backupCount=3, encoding="utf-8")
    f.setFormatter(fmt)
    root.addHandler(h)
    root.addHandler(f)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="PAPER-ONLY trading bot")
    ap.add_argument("--config", default=None)
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args(argv)
    try:
        cfg = C.load(a.config)
    except C.ConfigError as e:
        print(f"CONFIG ERROR: {e}", file=sys.stderr)
        return 2
    setup_logging(cfg.logs_dir)
    from .runner import Bot

    clock = Clock()
    try:
        src = RestDataSource(cfg.base_url, clock=clock,
                             max_weight_per_minute=cfg.max_weight_per_minute,
                             timeout=cfg.http_timeout_seconds, retries=cfg.http_retries)
        bot = Bot(cfg, src, clock)
    except NotImplementedError as e:
        print(f"STOPPED: {e}. Set 'mode: paper' in config.yaml.", file=sys.stderr)
        return 3
    if a.once:
        print(bot.cycle())
        return 0
    bot.run_forever()
    return 0


if __name__ == "__main__":
    assert GO_LIVE_MESSAGE
    raise SystemExit(main())
