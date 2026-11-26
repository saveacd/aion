"""Routed market stream payloads, freshness and ordering."""

from __future__ import annotations

import datetime as dt

from aion.services import market_stream


def test_depth_trade_and_liquidation_samples_reject_stale_or_replayed_frames(monkeypatch):
    monkeypatch.setattr(market_stream, "_EVENTS", {})
    monkeypatch.setattr(market_stream, "_PRICES", {})
    ms = int(dt.datetime.now(dt.UTC).timestamp() * 1000)
    process = market_stream.process_market_event
    process({"e": "aggTrade", "s": "BTCUSDT", "E": ms, "a": 2, "p": "100", "q": "2"})
    process({"e": "aggTrade", "s": "BTCUSDT", "E": ms, "a": 1, "p": "999", "q": "2"})
    process({"e": "aggTrade", "s": "BTCUSDT", "E": ms - 90_000, "a": 3, "p": "999", "q": "2"})
    process({"e": "depthUpdate", "s": "BTCUSDT", "E": ms, "u": 3,
             "b": [["99", "1"]], "a": [["101", "1"]]})
    process({"e": "forceOrder", "E": ms, "o": {"s": "BTCUSDT", "S": "SELL", "ap": "101", "z": "1"}})
    events = market_stream.recent_market_events("BTCUSDT")
    assert events["aggTrade"]["price"] == "100"
    assert events["depthUpdate"]["event_id"] == 3
    assert events["forceOrder"]["side"] == "SELL"
