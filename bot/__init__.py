"""FORGE paper-trading bot (PAPER ONLY).

Reuses the LAB signal code (src/lab/setups.py) and RADAR's universe rules (src/radar/) so the
paper trades follow the same rules as the backtest. There is no code path that sends a real
order: `bot.live` is a stub that raises NotImplementedError.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

__all__ = ["REPO", "SRC"]
