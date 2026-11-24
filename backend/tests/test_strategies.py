from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _create_strategy(client, headers, name: str = "Momentum") -> dict:
    response = await client.post(
        "/api/v1/strategies",
        json={
            "name": name,
            "description": "test strategy",
            "symbols": ["BTCUSDT"],
            "interval": "15m",
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_create_and_list_strategies(client, auth_headers):
    created = await _create_strategy(client, auth_headers)
    assert created["state"] == "RESEARCH"
    assert created["symbols"] == ["BTCUSDT"]

    listing = await client.get("/api/v1/strategies", headers=auth_headers)
    assert listing.status_code == 200
    assert listing.json()["total"] == 1


async def test_duplicate_name_conflicts(client, auth_headers):
    await _create_strategy(client, auth_headers)
    response = await client.post(
        "/api/v1/strategies",
        json={"name": "Momentum", "symbols": ["ETHUSDT"], "interval": "15m"},
        headers=auth_headers,
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"


async def test_version_creation_increments(client, auth_headers):
    strategy = await _create_strategy(client, auth_headers)
    for expected in (1, 2):
        response = await client.post(
            f"/api/v1/strategies/{strategy['id']}/versions",
            json={"params": {"fast": 12}, "change_note": f"v{expected}"},
            headers=auth_headers,
        )
        assert response.status_code == 201, response.text
        assert response.json()["version"] == expected


async def test_illegal_transition_is_conflict(client, auth_headers):
    strategy = await _create_strategy(client, auth_headers)
    response = await client.post(
        f"/api/v1/strategies/{strategy['id']}/state",
        json={"state": "ACTIVE"},
        headers=auth_headers,
    )
    assert response.status_code == 409
    assert "allowed" in response.json()["error"]["details"]


async def test_lifecycle_reaches_active_with_version(client, auth_headers):
    strategy = await _create_strategy(client, auth_headers)
    strategy_id = strategy["id"]

    version = await client.post(
        f"/api/v1/strategies/{strategy_id}/versions",
        json={"params": {"fast": 9}},
        headers=auth_headers,
    )
    version_id = version.json()["id"]

    for state in ("BACKTEST", "PAPER", "SHADOW", "APPROVED"):
        response = await client.post(
            f"/api/v1/strategies/{strategy_id}/state",
            json={"state": state, **({"version_id": version_id} if state == "APPROVED" else {})},
            headers=auth_headers,
        )
        assert response.status_code == 200, response.text

    blocked = await client.post(
        f"/api/v1/strategies/{strategy_id}/state",
        json={"state": "ACTIVE"},
        headers=auth_headers,
    )
    assert blocked.status_code == 409

    activated = await client.post(
        f"/api/v1/strategies/{strategy_id}/state",
        json={"state": "ACTIVE", "version_id": version_id},
        headers=auth_headers,
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["state"] == "ACTIVE"
    assert activated.json()["active_version_id"] == version_id

    versions = await client.get(f"/api/v1/strategies/{strategy_id}/versions", headers=auth_headers)
    assert versions.status_code == 200
    assert versions.json()[0]["is_approved"] is True


async def test_backtests_empty_when_none_exist(client, auth_headers):
    strategy = await _create_strategy(client, auth_headers)
    response = await client.get(
        f"/api/v1/strategies/{strategy['id']}/backtests",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 0
    assert body["items"] == []


async def test_unknown_strategy_is_404(client, auth_headers):
    response = await client.get(
        "/api/v1/strategies/00000000-0000-0000-0000-000000000000",
        headers=auth_headers,
    )
    assert response.status_code == 404
