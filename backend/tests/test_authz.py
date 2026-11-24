from __future__ import annotations

import pytest
from conftest import TRADER_PASSWORD, TRADER_USERNAME

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_missing_token_is_rejected(client):
    response = await client.get("/api/v1/account/overview")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


async def test_invalid_token_is_rejected(client):
    response = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


async def test_system_control_requires_admin(client, trader_user, login):
    headers = await login(TRADER_USERNAME, TRADER_PASSWORD)
    response = await client.post(
        "/api/v1/system/controls",
        json={"action": "STOP_NEW_TRADES", "enabled": True},
        headers=headers,
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


async def test_read_endpoints_require_authentication(client):
    for path in (
        "/api/v1/system/status",
        "/api/v1/risk/policy",
        "/api/v1/strategies",
        "/api/v1/trading/orders",
    ):
        response = await client.get(path)
        assert response.status_code == 401, path
