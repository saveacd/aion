"""Order state machine.

Illegal transitions raise :class:`InvalidTransition`; nothing else may mutate
an order status. ``UNKNOWN`` is a *recoverable* state: it means the exchange
outcome could not be confirmed, never that the order failed.
"""

from __future__ import annotations

from enum import StrEnum


class OrderState(StrEnum):
    CREATED = "CREATED"
    VALIDATED = "VALIDATED"
    SUBMITTED = "SUBMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


TERMINAL_STATES = frozenset({OrderState.FILLED, OrderState.CANCELED, OrderState.REJECTED})
OPEN_STATES = frozenset(
    {
        OrderState.CREATED,
        OrderState.VALIDATED,
        OrderState.SUBMITTED,
        OrderState.ACKNOWLEDGED,
        OrderState.PARTIALLY_FILLED,
        OrderState.CANCEL_PENDING,
        OrderState.UNKNOWN,
    }
)

_TRANSITIONS: dict[OrderState, frozenset[OrderState]] = {
    OrderState.CREATED: frozenset({OrderState.VALIDATED, OrderState.REJECTED, OrderState.CANCELED}),
    OrderState.VALIDATED: frozenset(
        {OrderState.SUBMITTED, OrderState.REJECTED, OrderState.UNKNOWN}
    ),
    OrderState.SUBMITTED: frozenset(
        {
            OrderState.ACKNOWLEDGED,
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.CANCEL_PENDING,
            OrderState.CANCELED,
            OrderState.REJECTED,
            OrderState.UNKNOWN,
        }
    ),
    OrderState.ACKNOWLEDGED: frozenset(
        {
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.CANCEL_PENDING,
            OrderState.CANCELED,
            OrderState.REJECTED,
            OrderState.UNKNOWN,
        }
    ),
    OrderState.PARTIALLY_FILLED: frozenset(
        {
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.CANCEL_PENDING,
            OrderState.CANCELED,
            OrderState.UNKNOWN,
        }
    ),
    OrderState.CANCEL_PENDING: frozenset(
        {
            OrderState.CANCELED,
            OrderState.FILLED,
            OrderState.PARTIALLY_FILLED,
            OrderState.UNKNOWN,
        }
    ),
    OrderState.FILLED: frozenset(),
    OrderState.CANCELED: frozenset(),
    OrderState.REJECTED: frozenset(),
    # Recovery: after a successful post-hoc query we may land on any real state.
    OrderState.UNKNOWN: frozenset(
        {
            OrderState.VALIDATED,
            OrderState.SUBMITTED,
            OrderState.ACKNOWLEDGED,
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.CANCEL_PENDING,
            OrderState.CANCELED,
            OrderState.REJECTED,
        }
    ),
}

#: Exchange order status -> local state.
_EXCHANGE_STATUS_MAP = {
    "NEW": OrderState.ACKNOWLEDGED,
    "PARTIALLY_FILLED": OrderState.PARTIALLY_FILLED,
    "FILLED": OrderState.FILLED,
    "CANCELED": OrderState.CANCELED,
    "PENDING_CANCEL": OrderState.CANCEL_PENDING,
    "REJECTED": OrderState.REJECTED,
    "EXPIRED": OrderState.CANCELED,
    "EXPIRED_IN_MATCH": OrderState.CANCELED,
}


class InvalidTransition(Exception):
    def __init__(self, source: OrderState | str, target: OrderState | str) -> None:
        self.source = source
        self.target = target
        source_label = source.value if isinstance(source, OrderState) else str(source)
        target_label = target.value if isinstance(target, OrderState) else str(target)
        super().__init__(f"illegal order transition {source_label} -> {target_label}")


def can_transition(source: OrderState | str, target: OrderState | str) -> bool:
    try:
        src = OrderState(source)
        dst = OrderState(target)
    except ValueError:
        return False
    return dst in _TRANSITIONS[src]


def ensure_transition(source: OrderState | str, target: OrderState | str) -> OrderState:
    """Validate and return ``target``, raising :class:`InvalidTransition`."""
    try:
        src = OrderState(source)
        dst = OrderState(target)
    except ValueError as exc:
        raise InvalidTransition(source, target) from exc
    if not can_transition(src, dst):
        raise InvalidTransition(src, dst)
    return dst


def from_exchange_status(status: str | None, *, fallback: OrderState = OrderState.UNKNOWN) -> OrderState:
    if not status:
        return fallback
    return _EXCHANGE_STATUS_MAP.get(status.upper(), fallback)


def is_terminal(state: OrderState | str) -> bool:
    try:
        return OrderState(state) in TERMINAL_STATES
    except ValueError:
        return False


def is_open(state: OrderState | str) -> bool:
    try:
        return OrderState(state) in OPEN_STATES
    except ValueError:
        return False
