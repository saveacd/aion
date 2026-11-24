"""Signing tests. Fixed parameters and a fixed (fake) secret: no network, no keys."""

from __future__ import annotations

import hashlib
import hmac

import pytest

from aion.exchange.signing import (
    auth_headers,
    build_signed_params,
    canonical_query,
    redact_params,
    sign_query,
)

SECRET = "aion-test-secret"
TIMESTAMP = 1700000000000
RECV_WINDOW = 5000

PARAMS = {
    "symbol": "BTCUSDT",
    "side": "BUY",
    "type": "LIMIT",
    "quantity": "0.01",
    "price": "20000",
    "timeInForce": "GTC",
    "timestamp": TIMESTAMP,
    "recvWindow": RECV_WINDOW,
}

# Expected values derived independently with the documented formula:
# urlencode(sorted(params)) -> HMAC-SHA256(secret, query).hexdigest()
EXPECTED_QUERY = (
    "price=20000&quantity=0.01&recvWindow=5000&side=BUY&symbol=BTCUSDT"
    "&timeInForce=GTC&timestamp=1700000000000&type=LIMIT"
)
EXPECTED_SIGNATURE = "83da393c6d6a3ded6a66f9032853ec3072c2101cf671b2f886c3d07c00db1f14"
SIGNATURE_WITHOUT_TIMESTAMP = "2c36e8b58b8d0793d23b4d2eb1b8f8cbc3a86206411b114693b2c4946b43ceb4"


def test_canonical_query_sorts_and_encodes() -> None:
    assert canonical_query(PARAMS) == EXPECTED_QUERY
    shuffled = dict(reversed(list(PARAMS.items())))
    assert canonical_query(shuffled) == EXPECTED_QUERY


def test_sign_query_matches_known_hmac() -> None:
    assert sign_query(PARAMS, SECRET) == EXPECTED_SIGNATURE
    # Recompute from the formula to prove the constant is not just a snapshot.
    recomputed = hmac.new(
        SECRET.encode(), EXPECTED_QUERY.encode(), hashlib.sha256
    ).hexdigest()
    assert recomputed == EXPECTED_SIGNATURE


def test_timestamp_participates_in_signature() -> None:
    without_timestamp = {k: v for k, v in PARAMS.items() if k != "timestamp"}
    assert sign_query(without_timestamp, SECRET) == SIGNATURE_WITHOUT_TIMESTAMP
    assert sign_query(without_timestamp, SECRET) != EXPECTED_SIGNATURE


def test_build_signed_params_injects_timestamp_before_signing() -> None:
    payload = build_signed_params(
        {"symbol": "BTCUSDT", "side": "BUY"}, SECRET, RECV_WINDOW, TIMESTAMP
    )
    assert payload["timestamp"] == str(TIMESTAMP)
    assert payload["recvWindow"] == str(RECV_WINDOW)
    expected = hmac.new(
        SECRET.encode(),
        b"recvWindow=5000&side=BUY&symbol=BTCUSDT&timestamp=1700000000000",
        hashlib.sha256,
    ).hexdigest()
    assert payload["signature"] == expected
    assert len(payload["signature"]) == 64


def test_none_values_are_dropped() -> None:
    payload = build_signed_params({"symbol": "BTCUSDT", "reduceOnly": None}, SECRET, 5000, TIMESTAMP)
    assert "reduceOnly" not in payload


def test_booleans_render_lowercase() -> None:
    assert canonical_query({"reduceOnly": True, "closePosition": False}) == (
        "closePosition=false&reduceOnly=true"
    )


def test_auth_headers_and_redaction() -> None:
    headers = auth_headers("public-key")
    assert headers == {"X-MBX-APIKEY": "public-key"}
    redacted = redact_params({"symbol": "BTCUSDT", "signature": EXPECTED_SIGNATURE})
    assert redacted == {"symbol": "BTCUSDT", "signature": "***"}


def test_secret_is_required() -> None:
    with pytest.raises(ValueError, match="secret"):
        sign_query({"symbol": "BTCUSDT"}, "")
