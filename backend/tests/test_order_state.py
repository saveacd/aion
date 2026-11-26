"""Order state machine tests."""

from __future__ import annotations

import pytest

from aion.execution.state import (
    TERMINAL_STATES,
    InvalidTransition,
    OrderState,
    can_transition,
    ensure_transition,
    from_exchange_status,
    is_open,
    is_terminal,
)


def test_legal_chain_reaches_filled() -> None:
    chain = [
        OrderState.CREATED,
        OrderState.VALIDATED,
        OrderState.SUBMITTED,
        OrderState.ACKNOWLEDGED,
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
    ]
    for source, target in zip(chain[:-1], chain[1:], strict=True):
        assert can_transition(source, target), f"{source} -> {target}"
        assert ensure_transition(source, target) is target


def test_partial_fills_can_repeat() -> None:
    assert can_transition(OrderState.PARTIALLY_FILLED, OrderState.PARTIALLY_FILLED)


def test_illegal_transition_raises() -> None:
    assert not can_transition(OrderState.FILLED, OrderState.CANCELED)
    with pytest.raises(InvalidTransition, match="FILLED -> CANCELED"):
        ensure_transition(OrderState.FILLED, OrderState.CANCELED)
    with pytest.raises(InvalidTransition):
        ensure_transition(OrderState.CREATED, OrderState.SUBMITTED)


def test_terminal_states_have_no_exit() -> None:
    for state in TERMINAL_STATES:
        assert not any(can_transition(state, target) for target in OrderState)
        assert is_terminal(state)
        assert not is_open(state)


def test_unknown_is_recoverable() -> None:
    for target in (
        OrderState.ACKNOWLEDGED,
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
        OrderState.CANCELED,
        OrderState.REJECTED,
    ):
        assert can_transition(OrderState.UNKNOWN, target)
    assert is_open(OrderState.UNKNOWN)
    assert not is_terminal(OrderState.UNKNOWN)


def test_cancel_pending_can_still_fill() -> None:
    assert can_transition(OrderState.CANCEL_PENDING, OrderState.FILLED)
    assert can_transition(OrderState.CANCEL_PENDING, OrderState.CANCELED)
    assert not can_transition(OrderState.CANCEL_PENDING, OrderState.ACKNOWLEDGED)


def test_exchange_status_mapping() -> None:
    assert from_exchange_status("NEW") is OrderState.ACKNOWLEDGED
    assert from_exchange_status("PARTIALLY_FILLED") is OrderState.PARTIALLY_FILLED
    assert from_exchange_status("FILLED") is OrderState.FILLED
    assert from_exchange_status("EXPIRED") is OrderState.EXPIRED
    assert from_exchange_status("SOMETHING_NEW") is OrderState.UNKNOWN


def test_unknown_string_values_do_not_explode() -> None:
    assert can_transition("NOPE", "CREATED") is False
    assert is_open("NOPE") is False
    with pytest.raises(InvalidTransition):
        ensure_transition("NOPE", "CREATED")
