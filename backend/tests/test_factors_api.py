"""API smoke tests for the factor research endpoints."""

from __future__ import annotations

import pytest

pytest.importorskip("httpx", reason="API tests need httpx")

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_factor_endpoints_require_auth(client):
    r = await client.get("/api/v1/factors/library")
    assert r.status_code == 401
    r = await client.post(
        "/api/v1/factors/research",
        json={"symbol": "BTCUSDT", "interval": "15m", "horizon_bars": 8},
    )
    assert r.status_code == 401


async def test_factor_library_empty_for_authenticated_user(client, auth_headers):
    r = await client.get("/api/v1/factors/library", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"items": []}


async def test_factor_library_symbol_filter_roundtrip(client, auth_headers):
    r = await client.get(
        "/api/v1/factors/library?symbol=ETHUSDT", headers=auth_headers
    )
    assert r.status_code == 200
    assert r.json() == {"items": []}


async def test_research_without_provider_returns_503(client, auth_headers):
    r = await client.post(
        "/api/v1/factors/research",
        json={"symbol": "BTCUSDT", "interval": "15m", "horizon_bars": 8},
        headers=auth_headers,
    )
    assert r.status_code == 503
    body = r.json()
    assert body["error"]["code"] == "UPSTREAM_UNAVAILABLE"


async def test_research_rejects_bad_payload(client, auth_headers):
    r = await client.post(
        "/api/v1/factors/research",
        json={"symbol": "", "interval": "15m", "horizon_bars": 0},
        headers=auth_headers,
    )
    assert r.status_code == 422
