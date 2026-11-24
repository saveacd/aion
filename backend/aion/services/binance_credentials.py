"""Optional live verification of a Binance Futures API key pair."""

from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import urlencode

import httpx

BASE_URLS = {
    "TESTNET": "https://testnet.binancefuture.com",
    "MAINNET": "https://fapi.binance.com",
}


REQUEST_TIMEOUT_SECONDS = 8.0


async def verify_binance_credentials(
    api_key: str, api_secret: str, mode: str
) -> tuple[bool, str]:
    """Call a signed endpoint to prove the key pair is valid.

    Returns ``(ok, message)``. Network failures are reported honestly instead of
    being swallowed so the client can show the real state.
    """
    base = BASE_URLS.get(mode, BASE_URLS["TESTNET"])
    params = {"timestamp": int(time.time() * 1000), "recvWindow": 5000}
    query = urlencode(params)
    signature = hmac.new(
        api_secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    url = f"{base}/fapi/v2/account?{query}&signature={signature}"

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.get(url, headers={"X-MBX-APIKEY": api_key})
    except httpx.HTTPError as exc:
        return False, f"无法连接 Binance：{exc.__class__.__name__}"

    if response.status_code == 200:
        return True, "已验证：Binance API Key 可用。"

    try:
        payload = response.json()
        detail = payload.get("msg") or payload
    except ValueError:
        detail = response.text[:200]
    return False, f"Binance 拒绝请求（HTTP {response.status_code}）：{detail}"
