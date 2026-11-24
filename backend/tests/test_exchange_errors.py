"""Error classification tests. Pure functions only."""

from __future__ import annotations

import pytest

from aion.exchange.errors import BinanceAPIError, classify


def _payload(code: int, msg: str = "boom") -> dict[str, object]:
    return {"code": code, "msg": msg}


def test_timestamp_error_is_retryable() -> None:
    error = classify(400, _payload(-1021, "Timestamp for this request is outside of the recvWindow."))
    assert error.code == -1021
    assert error.retryable is True
    assert "Timestamp" in error.msg


def test_signature_error_is_not_retryable() -> None:
    error = classify(400, _payload(-1022, "Signature for this request is not valid."))
    assert error.code == -1022
    assert error.retryable is False


def test_rate_limit_code_is_retryable() -> None:
    error = classify(400, _payload(-1003, "Too many requests."))
    assert error.code == -1003
    assert error.retryable is True


def test_margin_and_balance_errors_are_not_retryable() -> None:
    for code in (-2010, -2019):
        error = classify(400, _payload(code, "margin insufficient"))
        assert error.retryable is False
        assert "margin" in error.msg.lower()


def test_precision_errors_are_not_retryable() -> None:
    for code in (-4131, -2027):
        error = classify(400, _payload(code))
        assert error.retryable is False
        assert error.msg


@pytest.mark.parametrize("status", [429, 418, 500, 503])
def test_transport_errors_are_retryable(status: int) -> None:
    error = classify(status, {})
    assert error.retryable is True
    assert error.http_status == status
    assert error.msg


def test_unknown_code_is_not_retryable() -> None:
    error = classify(400, _payload(-9999, "something else"))
    assert error.code == -9999
    assert error.retryable is False


def test_repr_does_not_expose_payload() -> None:
    error = BinanceAPIError(code=-1022, msg="Signature invalid", http_status=401, retryable=False)
    assert "BinanceAPIError" in repr(error)
    assert "401" in repr(error)
    assert "Signature invalid" in str(error)
