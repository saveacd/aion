"""Typed errors for Binance USDⓈ-M Futures.

``classify`` turns an HTTP status plus Binance error payload into a
:class:`BinanceAPIError` with a human readable message and an explicit
``retryable`` flag. Nothing here ever contains the API secret.
"""

from __future__ import annotations

from typing import Any


class ExchangeError(Exception):
    """Base class for every adapter failure."""


class ExchangeNetworkError(ExchangeError):
    """The request never produced a usable HTTP response."""


class ExchangeTimeout(ExchangeNetworkError):
    """The request timed out; the order outcome is unknown."""


#: code -> (readable message, retryable)
_CODE_TABLE: dict[int, tuple[str, bool]] = {
    -1003: ("Request weight limit exceeded; back off before retrying.", True),
    -1021: ("Timestamp outside recvWindow; resynchronise server time and retry once.", True),
    -1022: ("Signature verification failed; check the API secret.", False),
    -1023: ("Request rejected: invalid API key, IP or permissions.", False),
    -2010: ("Order rejected: insufficient balance or margin.", False),
    -2011: ("Unknown order.", False),
    -2013: ("Order does not exist.", False),
    -2019: ("Margin is insufficient for this order.", False),
    -2027: ("Order quantity or price violates symbol precision filters.", False),
    -4131: ("Order price does not comply with the PERCENT_PRICE filter.", False),
    -4164: ("Order notional is below the symbol minimum.", False),
}


class BinanceAPIError(ExchangeError):
    """A structured exchange error.

    ``code`` is Binance's numeric error code (``None`` when the response was
    not a Binance payload), ``http_status`` the HTTP status.
    """

    def __init__(
        self,
        code: int | None,
        msg: str,
        http_status: int | None = None,
        retryable: bool = False,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.msg = msg
        self.http_status = http_status
        self.retryable = retryable
        self.payload = payload or {}
        super().__init__(self._describe())

    def _describe(self) -> str:
        parts = ["Binance API error"]
        if self.code is not None:
            parts.append(f"code={self.code}")
        if self.http_status is not None:
            parts.append(f"http={self.http_status}")
        parts.append(self.msg)
        return ": ".join([parts[0], ", ".join(parts[1:])])

    def __repr__(self) -> str:
        return (
            f"BinanceAPIError(code={self.code!r}, http_status={self.http_status!r}, "
            f"retryable={self.retryable!r}, msg={self.msg!r})"
        )


def _coerce_code(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def classify(http_status: int | None, payload: dict[str, Any] | None) -> BinanceAPIError:
    """Map an HTTP response to a :class:`BinanceAPIError`."""
    payload = payload or {}
    code = _coerce_code(payload.get("code"))
    raw_msg = payload.get("msg") or payload.get("message")
    message = str(raw_msg) if raw_msg else ""

    retryable = bool(http_status is not None and (http_status in (429, 418) or http_status >= 500))
    if http_status == 418:
        message = "IP has been auto-banned after repeated rate-limit violations. " + message
    elif http_status == 429:
        message = "Rate limit exceeded (HTTP 429). " + message

    if code is not None and code in _CODE_TABLE:
        readable, code_retryable = _CODE_TABLE[code]
        retryable = retryable or code_retryable
        message = f"{readable} {message}".strip()

    if not message:
        message = f"HTTP {http_status}" if http_status is not None else "Unknown exchange error"

    return BinanceAPIError(code=code, msg=message, http_status=http_status, retryable=retryable)
