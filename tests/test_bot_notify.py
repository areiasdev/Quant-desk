import json
from pathlib import Path

import pytest

from bot import notify as N
from bot.journal import Journal


class FakeResp:
    def __init__(self, code=200, body=None):
        self.status_code, self._b = code, body or {"ok": True}
        self.text = json.dumps(self._b)

    def json(self):
        return self._b


class FakeHTTP:
    def __init__(self, code=200, body=None, raise_exc=False):
        self.calls, self.code, self.body, self.raise_exc = [], code, body, raise_exc

    def post(self, url, data=None, timeout=None):
        self.calls.append((url, data))
        if self.raise_exc:
            raise ConnectionError("down")
        return FakeResp(self.code)

    def get(self, url, timeout=None):
        self.calls.append((url, None))
        return FakeResp(self.code, self.body)


OPEN = dict(event="open", symbol="ETHUSDT", side=1, entry=3000.0, stop=2900.0,
            notional=33.0, risk_usdt=1.1, reason="entry: negative funding in uptrend")
CLOSE = dict(event="close", symbol="ETHUSDT", side=1, entry=3000.0, exit=3100.0, pnl=1.2,
             R=1.09, exit_reason="trailing stop", equity_after=111.2)


def test_messages_for_trades_and_halts():
    o = N.format_record("trades", OPEN)
    assert "Entrada LONG ETHUSDT" in o and "SIMULAÇÃO" in o and "2900" in o
    c = N.format_record("trades", CLOSE)
    assert "Saída" in c and "+1.20 USDT" in c and "111.20" in c
    assert "SHIELD PAROU" in N.format_record("shield", dict(type="HALT", reason="3 losses"))
    assert N.format_record("shield", dict(type="RESET")) is not None
    blk = dict(type="BLOCK", rule="min_notional", symbol="BTCUSDT")
    assert N.format_record("shield", blk) is None
    assert N.format_record("shield", blk, notify_blocks=True) is not None
    for quiet in ("decisions", "equity", "system"):
        assert N.format_record(quiet, {"type": "x"}) is None


def test_notifier_sends_and_never_raises(monkeypatch):
    monkeypatch.setattr(N.time, "sleep", lambda s: None)
    http = FakeHTTP()
    n = N.TelegramNotifier("123:abc", "42", session=http, background=False)
    n.on_record("trades", OPEN)
    assert len(http.calls) == 1 and http.calls[0][1]["chat_id"] == "42"
    bad = N.TelegramNotifier("123:abc", "42", session=FakeHTTP(raise_exc=True),
                             background=False)
    bad.on_record("trades", OPEN)          # network down: no exception
    wrong = FakeHTTP(code=401)
    N.TelegramNotifier("123:abc", "42", session=wrong, background=False).on_record(
        "trades", OPEN)
    assert len(wrong.calls) == 1           # bad token: no pointless retries


def test_journal_hook_cannot_break_logging(tmp_path):
    j = Journal(tmp_path)
    seen = []
    j.on_append = lambda name, rec: seen.append(name)
    j.append("trades", OPEN)
    assert seen == ["trades"]

    def boom(name, rec):
        raise RuntimeError("telegram exploded")
    j.on_append = boom
    j.append("trades", CLOSE)              # must not raise
    assert len(j.read("trades")) == 2


def test_settings_file(tmp_path):
    assert N.load_settings(tmp_path / "missing.yaml") is None
    ex = Path(__file__).resolve().parent.parent / "telegram.example.yaml"
    with pytest.raises(N.SettingsError):
        N.load_settings(ex)                # example values are rejected
    f = tmp_path / "telegram.yaml"
    f.write_text('bot_token: "123:abc"\nchat_id: "42"\n', encoding="utf-8")
    s = N.load_settings(f)
    assert s["chat_id"] == "42" and s["notify_blocks"] is False


def test_find_chat():
    body = {"result": [{"message": {"chat": {"id": 42, "first_name": "Test"}}}]}
    assert N.find_chats("123:abc", session=FakeHTTP(body=body)) == [("42", "Test")]


def test_token_file_is_gitignored():
    gi = (Path(__file__).resolve().parent.parent / ".gitignore").read_text()
    assert "telegram.yaml" in gi.splitlines()
