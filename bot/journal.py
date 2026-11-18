"""Append-only JSONL logs in logs/ and the atomic state file.

logs/decisions.jsonl  every signal evaluated (inputs, taken or skipped and why)
logs/trades.jsonl     paper trade opens and closes (exactly once, via the state outbox)
logs/shield.jsonl     every Shield block, halt and reset, with its reason
logs/equity.jsonl     paper equity, once per hourly cycle
logs/system.jsonl     start/stop, config, exchangeInfo (ESTIMATE vs fetched), universe updates
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import time
from pathlib import Path

NAMES = ("decisions", "trades", "shield", "equity", "system")


def _clean(x):
    if isinstance(x, float):
        return None if not math.isfinite(x) else x
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if hasattr(x, "item") and not isinstance(x, (str, bytes)):
        try:
            return _clean(x.item())
        except Exception:
            return str(x)
    return x


class Journal:
    def __init__(self, logs_dir: Path):
        self.dir = Path(logs_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.on_append = None   # optional hook(name, rec), e.g. Telegram; must not raise

    def path(self, name: str) -> Path:
        assert name in NAMES, name
        return self.dir / f"{name}.jsonl"

    def append(self, name: str, rec: dict, fsync: bool = False) -> None:
        line = json.dumps(_clean(rec), ensure_ascii=False, sort_keys=False)
        with open(self.path(name), "a", encoding="utf-8") as f:
            f.write(line + "\n")
            if fsync:
                f.flush()
                os.fsync(f.fileno())
        if self.on_append is not None:
            try:
                self.on_append(name, rec)
            except Exception:  # noqa: BLE001  a notification must never stop the bot
                pass

    def read(self, name: str) -> list[dict]:
        return read_jsonl(self.path(name))


def read_jsonl(p: Path) -> list[dict]:
    p = Path(p)
    if not p.exists():
        return []
    out = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a half-written last line after a power cut: ignored
    return out


def save_state(path: Path, state: dict) -> None:
    """Atomic write: temp file in the same folder, fsync, then os.replace."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".state.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(_clean(state), f, indent=1, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(10):     # Windows: another program may hold the file briefly
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.2 * (attempt + 1))
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def load_state(path: Path) -> dict | None:
    path = Path(path)
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)
