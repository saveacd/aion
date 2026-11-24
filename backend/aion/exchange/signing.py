"""Request signing for Binance USDⓈ-M Futures.

Binance authenticates signed endpoints with ``HMAC-SHA256(secret, query)``.
The signature is computed over the *raw* query string, so ``timestamp`` and
``recvWindow`` must be present before signing, keys must be sorted, and the
same values must be sent on the wire.

The API secret is never stored on the result objects and never appears in
``repr`` output or error messages.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlencode


def _render(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def canonical_query(params: Mapping[str, Any]) -> str:
    """Return the exact query string Binance signs: sorted keys, no ``None``."""
    items = [(_render(k), _render(v)) for k, v in params.items() if v is not None]
    items.sort(key=lambda kv: kv[0])
    return urlencode(items)


def sign_query(params: Mapping[str, Any], secret: str) -> str:
    """HMAC-SHA256 hex digest of the canonical query string."""
    if not secret:
        raise ValueError("API secret is required for signing")
    query = canonical_query(params)
    return hmac.new(secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()


def build_signed_params(
    params: Mapping[str, Any],
    secret: str,
    recv_window: int,
    timestamp_ms: int,
) -> dict[str, str]:
    """Inject ``timestamp``/``recvWindow`` *before* signing and return the payload."""
    payload: dict[str, str] = {str(k): _render(v) for k, v in params.items() if v is not None}
    payload["timestamp"] = str(int(timestamp_ms))
    payload["recvWindow"] = str(int(recv_window))
    payload["signature"] = sign_query(payload, secret)
    return payload


def auth_headers(api_key: str) -> dict[str, str]:
    return {"X-MBX-APIKEY": api_key}


_SENSITIVE_KEYS = frozenset({"signature", "apiKey", "api_key", "secret", "apiSecret"})


def redact_params(params: Mapping[str, Any]) -> dict[str, Any]:
    """Copy of ``params`` safe to persist or log."""
    return {k: ("***" if k in _SENSITIVE_KEYS else v) for k, v in params.items()}
