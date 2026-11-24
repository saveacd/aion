"""BUG-14: side/position_side combinations on manual intents."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aion.schemas.trading import TradeIntentIn


def _payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "position_side": "LONG",
        "quantity": "0.1",
    }
    base.update(overrides)
    return base


def test_buy_long_and_sell_short_are_valid() -> None:
    assert TradeIntentIn(**_payload()).position_side == "LONG"
    assert TradeIntentIn(**_payload(side="SELL", position_side="SHORT")).position_side == "SHORT"


@pytest.mark.parametrize(
    "side,position_side",
    [("SELL", "LONG"), ("BUY", "SHORT")],
)
def test_mismatched_combinations_rejected(side: str, position_side: str) -> None:
    with pytest.raises(ValidationError):
        TradeIntentIn(**_payload(side=side, position_side=position_side))
