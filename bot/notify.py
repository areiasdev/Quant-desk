"""Telegram notifications for the paper bot (optional).

Sends a phone message when a paper trade opens or closes and when Shield halts or resets
the bot. Off unless a telegram.yaml file exists next to config.yaml. That file holds the
Telegram bot token, so it is gitignored and must never be committed or pasted in a chat.

    python -m bot.notify --find-chat   # after sending /start to your Telegram bot: shows chat_id
    python -m bot.notify --test        # sends a test message

A notification can never stop or slow the trading loop: messages go through a background
thread with short timeouts, and every error is only logged.
"""
from __future__ import annotations

import argparse
import logging
import queue
import sys
import threading
import time
from pathlib import Path

import requests
import yaml

log = logging.getLogger("bot.notify")

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FILE = ROOT / "telegram.yaml"
API = "https://api.telegram.org/bot{token}/{method}"


class SettingsError(Exception):
    pass


def load_settings(path: Path = DEFAULT_FILE, need_chat: bool = True) -> dict | None:
    """None when the file doesn't exist (notifications off)."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        d = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise SettingsError(f"{path.name} is not valid YAML: {e}") from e
    token = str(d.get("bot_token") or "").strip()
    chat = str(d.get("chat_id") or "").strip()
    if not token or ":" not in token or "COLA" in token.upper():
        raise SettingsError(f"{path.name}: bot_token is missing or still the example value")
    if need_chat and (not chat or "COLA" in chat.upper()):
        raise SettingsError(f"{path.name}: chat_id is missing (run: python -m bot.notify --find-chat)")
    return {"bot_token": token, "chat_id": chat,
            "notify_blocks": bool(d.get("notify_blocks", False))}


def _num(x, nd=4):
    try:
        return f"{float(x):.{nd}g}"
    except (TypeError, ValueError):
        return "-"


def format_record(name: str, rec: dict, notify_blocks: bool = False,
                  label: str = "") -> str | None:
    """Portuguese message for one journal record, or None if it should not be sent."""
    text = _format(name, rec, notify_blocks)
    return f"{label}\n{text}" if text and label else text


def _format(name: str, rec: dict, notify_blocks: bool) -> str | None:
    if name == "trades":
        side = "LONG" if rec.get("side", 1) == 1 else "SHORT"
        sym = rec.get("symbol", "?")
        if rec.get("event") == "open":
            return (f"🟢 [SIMULAÇÃO] Entrada {side} {sym}\n"
                    f"Preço {_num(rec.get('entry'))} · stop {_num(rec.get('stop'))}\n"
                    f"Tamanho {float(rec.get('notional', 0)):.2f} USDT · risco "
                    f"{float(rec.get('risk_usdt', 0)):.2f} USDT\n"
                    f"Razão: {rec.get('reason', '-')}")
        if rec.get("event") == "close":
            pnl = float(rec.get("pnl", 0) or 0)
            icon = "✅" if pnl > 0 else "🔴"
            return (f"{icon} [SIMULAÇÃO] Saída {side} {sym}\n"
                    f"{_num(rec.get('entry'))} → {_num(rec.get('exit'))} · "
                    f"{pnl:+.2f} USDT ({float(rec.get('R', 0) or 0):+.2f}R)\n"
                    f"Razão: {rec.get('exit_reason') or rec.get('reason', '-')}\n"
                    f"Saldo: {float(rec.get('equity_after', 0) or 0):.2f} USDT")
        return None
    if name == "shield":
        t = rec.get("type")
        if t == "HALT":
            return (f"⛔ SHIELD PAROU O BOT\nMotivo: {rec.get('reason', '-')}\n"
                    "Vê logs/shield.jsonl; para retomar apaga o ficheiro HALT.")
        if t == "RESET":
            return "▶️ Bot retomado (HALT apagado)."
        if t == "BLOCK" and notify_blocks:
            return (f"🛡️ Shield bloqueou {rec.get('symbol', '?')}: "
                    f"{rec.get('rule', '-')} {rec.get('reason', '')}".strip())
    return None


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, notify_blocks: bool = False,
                 session=None, label: str = "", timeout: float = 10.0, retries: int = 3,
                 background: bool = True):
        self.token, self.chat_id = token, chat_id
        self.notify_blocks = notify_blocks
        self.label = label
        self.http = session or requests.Session()
        self.timeout, self.retries = timeout, retries
        self.q: queue.Queue = queue.Queue(maxsize=200)
        self.background = background
        if background:
            threading.Thread(target=self._worker, name="telegram", daemon=True).start()

    # Journal hook: must never raise.
    def on_record(self, name: str, rec: dict) -> None:
        try:
            text = format_record(name, rec, self.notify_blocks, self.label)
            if text:
                self.send(text)
        except Exception as e:  # noqa: BLE001
            log.warning("notification skipped: %s", e)

    def send(self, text: str) -> None:
        if not self.background:
            self._deliver(text)
            return
        try:
            self.q.put_nowait(text)
        except queue.Full:
            log.warning("notification queue full; message dropped")

    def _worker(self) -> None:
        while True:
            self._deliver(self.q.get())

    def _deliver(self, text: str) -> bool:
        url = API.format(token=self.token, method="sendMessage")
        for attempt in range(self.retries):
            try:
                r = self.http.post(url, data={"chat_id": self.chat_id, "text": text},
                                   timeout=self.timeout)
                if r.status_code == 200:
                    return True
                # never log the URL: it contains the token
                log.warning("Telegram answered HTTP %s: %s", r.status_code, r.text[:200])
                if r.status_code in (400, 401, 403, 404):
                    return False  # wrong token/chat: retrying won't help
            except Exception as e:  # noqa: BLE001
                log.warning("Telegram send failed (%s)", type(e).__name__)
            time.sleep(min(2 ** attempt, 8))
        return False


def from_settings(path: Path = DEFAULT_FILE, label: str = "") -> TelegramNotifier | None:
    s = load_settings(path)
    if s is None:
        return None
    return TelegramNotifier(s["bot_token"], s["chat_id"], s["notify_blocks"], label=label)


def find_chats(token: str, session=None) -> list[tuple[str, str]]:
    http = session or requests.Session()
    r = http.get(API.format(token=token, method="getUpdates"), timeout=15)
    if r.status_code != 200:
        raise SettingsError(f"Telegram answered HTTP {r.status_code}: {r.text[:200]}")
    out = {}
    for u in r.json().get("result", []):
        chat = (u.get("message") or u.get("my_chat_member") or {}).get("chat") or {}
        if "id" in chat:
            name = chat.get("first_name") or chat.get("title") or chat.get("username") or ""
            out[str(chat["id"])] = name
    return list(out.items())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Telegram notifications for the paper bot")
    ap.add_argument("--find-chat", action="store_true")
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--file", default=str(DEFAULT_FILE))
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    path = Path(a.file)
    if not path.exists():
        print(f"Falta o ficheiro {path.name}. Copia telegram.example.yaml para telegram.yaml "
              "e cola lá o token do BotFather.")
        return 1
    try:
        if a.find_chat:
            s = load_settings(path, need_chat=False)
            chats = find_chats(s["bot_token"])
            if not chats:
                print("Nenhuma conversa encontrada. No Telegram, abre o teu bot, carrega em "
                      "Start (ou envia /start) e corre este comando outra vez.")
                return 1
            for cid, name in chats:
                print(f"chat_id: {cid}   ({name})")
            print("Cola esse número em chat_id no telegram.yaml.")
            return 0
        s = load_settings(path)
        n = TelegramNotifier(s["bot_token"], s["chat_id"], background=False)
        ok = n._deliver("✅ Quant desk: notificações ligadas (SÓ SIMULAÇÃO).")
        print("Mensagem enviada. Vê o Telegram." if ok else
              "Falhou. Confirma o bot_token e o chat_id no telegram.yaml.")
        return 0 if ok else 1
    except SettingsError as e:
        print(f"ERRO: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
