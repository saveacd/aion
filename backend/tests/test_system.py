from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from aion.models.account import AuditLog, SystemEvent, TradingControlState

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_healthz_is_dependency_free(client):
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readyz_reports_dependencies(client):
    response = await client.get("/readyz")
    assert response.status_code in (200, 503)
    body = response.json()
    assert isinstance(body["database"], bool)
    assert isinstance(body["redis"], bool)


async def test_system_health_requires_auth_and_reports_mode(client, auth_headers):
    unauthenticated = await client.get("/api/v1/system/health")
    assert unauthenticated.status_code == 401

    response = await client.get("/api/v1/system/health", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["trading_mode"] == "PAPER"
    assert body["mainnet_unlocked"] is False


async def test_system_status_never_leaks_secrets(client, auth_headers):
    response = await client.get("/api/v1/system/status", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert {item["mode"] for item in body["exchange"]} == {"TESTNET", "MAINNET"}
    allowed = {
        "mode",
        "credentials_configured",
        "has_api_key",
        "has_api_secret",
        "account_row_active",
        "permissions_checked",
    }
    for item in body["exchange"]:
        assert set(item) <= allowed


async def test_controls_persist_and_audit(client, auth_headers, db_session):
    response = await client.post(
        "/api/v1/system/controls",
        json={"action": "STOP_NEW_TRADES", "enabled": True, "reason": "test halt"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["stop_new_trades"] is True

    state = await db_session.get(TradingControlState, 1)
    assert state is not None
    assert state.stop_new_trades is True

    audit_count = (
        await db_session.execute(
            select(func.count()).select_from(AuditLog).where(AuditLog.action == "system.control.stop_new_trades")
        )
    ).scalar_one()
    assert audit_count == 1

    event_count = (
        await db_session.execute(
            select(func.count()).select_from(SystemEvent).where(SystemEvent.category == "controls")
        )
    ).scalar_one()
    assert event_count == 1

    events = await client.get("/api/v1/system/events", headers=auth_headers)
    assert events.status_code == 200
    assert events.json()["total"] == 1


async def test_emergency_stop_blocks_new_trades(client, auth_headers):
    response = await client.post(
        "/api/v1/system/controls",
        json={"action": "EMERGENCY_STOP", "enabled": True, "reason": "manual emergency"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["emergency_stop"] is True
    assert body["stop_new_trades"] is True


async def test_trade_intent_is_rejected_without_market_data(client, auth_headers):
    response = await client.post(
        "/api/v1/trading/intents",
        json={
            "symbol": "BTCUSDT",
            "side": "BUY",
            "position_side": "LONG",
            "order_type": "MARKET",
            "notional_usd": "100",
            "requested_leverage": 2,
        },
        headers=auth_headers,
    )
    # The intent row is created, but the decision must be a rejection: there is
    # no market snapshot, so the engine cannot verify the price.
    assert response.status_code == 201
    body = response.json()
    assert body["accepted"] is False
    assert body["risk"]["approved"] is False
    codes = body["risk"]["reason_codes"]
    assert "STALE_MARKET_DATA" in codes

    intents = await client.get("/api/v1/trading/intents", headers=auth_headers)
    assert intents.status_code == 200
    listed = intents.json()
    assert listed["total"] == 1
    assert listed["items"][0]["status"] == "REJECTED"


async def test_cancel_unknown_order_is_404(client, auth_headers):
    missing = await client.post(
        f"/api/v1/trading/orders/{uuid.uuid4()}/cancel",
        json={"reason": "test"},
        headers=auth_headers,
    )
    assert missing.status_code == 404
