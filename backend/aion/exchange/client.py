"""Async HTTP client for Binance USDⓈ-M Futures.

* Signing is delegated to :mod:`aion.exchange.signing`; the secret is held in
  a private attribute and never appears in ``repr``/log output.
* Rate limits are tracked from ``X-MBX-USED-WEIGHT-1M`` and 429/418/5xx are
  retried with bounded exponential backoff (1s → 2s → 4s, at most 3 retries).
* ``-1021`` (timestamp outside recvWindow) triggers one server-time resync and
  a single re-signed retry.
* A ``transport`` can be injected so tests run entirely offline.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import httpx

from aion.exchange.account import AccountAPI
from aion.exchange.errors import (
    BinanceAPIError,
    ExchangeNetworkError,
    ExchangeTimeout,
    classify,
)
from aion.exchange.market import MarketAPI
from aion.exchange.orders import OrdersAPI
from aion.exchange.signing import auth_headers, build_signed_params

TESTNET_BASE_URL = "https://testnet.binancefuture.com"
MAINNET_BASE_URL = "https://fapi.binance.com"

_SleepFn = Callable[[float], Awaitable[None]]


def _now_ms() -> int:
    return int(time.time() * 1000)


def _mask(value: str, keep: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= keep:
        return "*" * len(value)
    return f"{value[:keep]}{'*' * 8}"


class BinanceFuturesClient(MarketAPI, AccountAPI, OrdersAPI):
    """Thin, signed REST client. One instance per exchange account/mode."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str = TESTNET_BASE_URL,
        timeout: float = 10.0,
        recv_window: int = 5000,
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        max_weight_1m: int = 1100,
        max_retries: int = 3,
        backoff_base_seconds: float = 1.0,
        sleep_fn: _SleepFn | None = None,
    ) -> None:
        if not api_key or not api_secret:
            raise ValueError("Binance API key and secret are required")
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        self._api_key = api_key
        self._api_secret = api_secret
        self._base_url = base_url
        self._recv_window = int(recv_window)
        self._max_weight_1m = int(max_weight_1m)
        self._max_retries = int(max_retries)
        self._backoff_base_seconds = float(backoff_base_seconds)
        self._sleep_fn: _SleepFn = sleep_fn or asyncio.sleep
        self._http = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            transport=transport,
        )
        self.time_offset_ms = 0
        self.used_weight_1m = 0

    # ------------------------------------------------------------------ repr
    def __repr__(self) -> str:
        return (
            f"BinanceFuturesClient(base_url={self._base_url!r}, "
            f"api_key={_mask(self._api_key)!r}, recv_window={self._recv_window})"
        )

    __str__ = __repr__

    # ------------------------------------------------------------------ time
    def timestamp_ms(self) -> int:
        return _now_ms() + self.time_offset_ms

    async def sync_time(self) -> int:
        """Synchronise against ``/fapi/v1/time`` and return the offset in ms."""
        payload = await self._public_request("GET", "/fapi/v1/time")
        server_time = payload.get("serverTime")
        if server_time is None:
            raise ExchangeNetworkError("time endpoint returned no serverTime")
        self.time_offset_ms = int(server_time) - _now_ms()
        return self.time_offset_ms

    # ------------------------------------------------------------- requests
    async def _public_request(
        self,
        method: str,
        path: str,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        return await self._request(method, path, params, signed=False)

    async def _signed_request(
        self,
        method: str,
        path: str,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        return await self._request(method, path, params, signed=True)

    async def _api_key_request(
        self,
        method: str,
        path: str,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        """For endpoints that need ``X-MBX-APIKEY`` but no signature (listen keys)."""
        return await self._request(method, path, params, signed=False, api_key_only=True)

    async def _request(
        self,
        method: str,
        path: str,
        params: Mapping[str, Any] | None,
        *,
        signed: bool,
        api_key_only: bool = False,
    ) -> Any:
        base_params = dict(params or {})
        headers = auth_headers(self._api_key) if (signed or api_key_only) else {}
        attempt = 0
        resynced = False

        while True:
            request_params: dict[str, Any]
            if signed:
                request_params = build_signed_params(
                    base_params,
                    self._api_secret,
                    self._recv_window,
                    self.timestamp_ms(),
                )
            else:
                request_params = base_params

            self._guard_weight()
            try:
                response = await self._http.request(
                    method, path, params=request_params or None, headers=headers
                )
            except httpx.TimeoutException as exc:
                raise ExchangeTimeout(f"Binance request timed out: {method} {path}") from exc
            except httpx.HTTPError as exc:
                raise ExchangeNetworkError(
                    f"Binance request failed: {method} {path} ({exc.__class__.__name__})"
                ) from exc

            self._record_weight(response)
            payload = self._decode(response, method, path)
            # Order placement must never be blindly retried: if the first
            # request actually reached the exchange, resending the same order
            # can double the position.surface errors instead.
            order_post = (
                method.upper() == "POST"
                and path in {"/fapi/v1/order", "/fapi/v1/batchOrders"}
            )

            if response.status_code < 400 and not self._is_error_payload(payload):
                return payload

            error_payload = payload if isinstance(payload, dict) else {}
            error = classify(response.status_code, error_payload)

            if error.code == -1021 and signed and not resynced:
                resynced = True
                await self.sync_time()
                continue

            if error.retryable and attempt < self._max_retries and not order_post:
                delay = self._backoff_base_seconds * (2**attempt)
                attempt += 1
                await self._sleep_fn(delay)
                continue

            raise error

    @staticmethod
    def _decode(response: httpx.Response, method: str, path: str) -> Any:
        text = response.text
        if not text:
            return {}
        try:
            return json.loads(text)
        except ValueError as exc:
            if response.status_code >= 400:
                return {}
            raise ExchangeNetworkError(f"Binance returned invalid JSON: {method} {path}") from exc

    @staticmethod
    def _is_error_payload(payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        code = payload.get("code")
        return isinstance(code, int) and not isinstance(code, bool) and code < 0

    # --------------------------------------------------------------- weight
    def _record_weight(self, response: httpx.Response) -> None:
        raw = response.headers.get("x-mbx-used-weight-1m")
        if raw is None:
            return
        try:
            self.used_weight_1m = int(raw)
        except ValueError:
            return

    def _guard_weight(self) -> None:
        if self.used_weight_1m < self._max_weight_1m:
            return
        raise BinanceAPIError(
            code=-1003,
            msg=(
                f"Used request weight {self.used_weight_1m} reached the configured limit "
                f"{self._max_weight_1m}; refusing to send until the window resets."
            ),
            http_status=None,
            retryable=True,
        )

    # --------------------------------------------------------------- teardown
    async def aclose(self) -> None:
        await self._http.aclose()
