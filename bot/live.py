"""LIVE trading: deliberately NOT implemented.

There is no code anywhere in this bot that sends an order to an exchange, signs a request or
reads an API key. Setting `mode: live` in config.yaml stops the bot with this error.
"""
from __future__ import annotations

GO_LIVE_MESSAGE = "Real money needs the owner's 'go live'"


class LiveBroker:
    def __init__(self, *args, **kwargs):
        raise NotImplementedError(GO_LIVE_MESSAGE)

    def place_order(self, *args, **kwargs):  # pragma: no cover - unreachable by design
        raise NotImplementedError(GO_LIVE_MESSAGE)
