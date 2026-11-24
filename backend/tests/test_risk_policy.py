from __future__ import annotations

from decimal import Decimal

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_policy_is_absent_until_saved(client, auth_headers):
    response = await client.get("/api/v1/risk/policy", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["has_data"] is False
    assert body["policy"] is None
    assert body["absolute_limits"]["max_leverage"] == 20


async def test_policy_update_roundtrip(client, auth_headers):
    response = await client.put(
        "/api/v1/risk/policy",
        json={
            "max_leverage": 3,
            "max_position_notional_usd": "500",
            "daily_loss_limit_usd": "50",
        },
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    policy = response.json()["policy"]
    assert policy["max_leverage"] == 3
    assert Decimal(policy["max_position_notional_usd"]) == Decimal("500")
    assert policy["required_stop_loss"] is True

    fetched = await client.get("/api/v1/risk/policy", headers=auth_headers)
    assert fetched.json()["has_data"] is True
    assert Decimal(fetched.json()["policy"]["daily_loss_limit_usd"]) == Decimal("50")


async def test_policy_rejects_leverage_above_absolute_cap(client, auth_headers):
    response = await client.put(
        "/api/v1/risk/policy",
        json={"max_leverage": 50},
        headers=auth_headers,
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert "max_leverage" in error["details"]["violations"]


async def test_policy_rejects_notional_above_absolute_cap(client, auth_headers):
    response = await client.put(
        "/api/v1/risk/policy",
        json={"max_position_notional_usd": "100001"},
        headers=auth_headers,
    )
    assert response.status_code == 422
    assert "max_position_notional_usd" in response.json()["error"]["details"]["violations"]


async def test_policy_rejects_daily_loss_above_absolute_cap(client, auth_headers):
    response = await client.put(
        "/api/v1/risk/policy",
        json={"daily_loss_limit_usd": "999999"},
        headers=auth_headers,
    )
    assert response.status_code == 422
    assert "daily_loss_limit_usd" in response.json()["error"]["details"]["violations"]


async def test_policy_update_allows_regular_user(client, trader_user, login):
    headers = await login("trader", "Aion-Trader-Passw0rd!")
    response = await client.put(
        "/api/v1/risk/policy",
        json={"max_leverage": 2},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["policy"]["max_leverage"] == 2

    fetched = await client.get("/api/v1/risk/policy", headers=headers)
    assert fetched.json()["policy"]["max_leverage"] == 2


async def test_decisions_and_events_are_empty_without_data(client, auth_headers):
    decisions = await client.get("/api/v1/risk/decisions", headers=auth_headers)
    assert decisions.status_code == 200
    assert decisions.json()["items"] == []

    events = await client.get("/api/v1/risk/events", headers=auth_headers)
    assert events.status_code == 200
    assert events.json()["items"] == []
