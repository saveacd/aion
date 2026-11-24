from __future__ import annotations

import pyotp
import pytest
from conftest import ADMIN_PASSWORD, ADMIN_USERNAME, TRADER_PASSWORD, TRADER_USERNAME

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_login_returns_tokens_and_user(client, admin_user):
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD, "device_name": "pytest"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token_type"] == "Bearer"
    assert body["user"]["username"] == ADMIN_USERNAME
    assert "factors" in body["user"]["features"]
    assert body["totp_enabled"] is False
    assert body["access_token"] and body["refresh_token"]
    assert "password_hash" not in response.text.lower()


async def test_me_requires_and_accepts_token(client, admin_user, login):
    headers = await login()
    response = await client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 200
    assert response.json()["username"] == ADMIN_USERNAME


async def test_login_wrong_password_is_401(client, admin_user):
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": ADMIN_USERNAME, "password": "Wrong-Password-1!"},
    )
    assert response.status_code == 401
    error = response.json()["error"]
    assert error["code"] == "UNAUTHORIZED"
    assert error["request_id"]


async def test_login_locks_after_repeated_failures(client, admin_user):
    for _ in range(5):
        response = await client.post(
            "/api/v1/auth/login",
            json={"username": ADMIN_USERNAME, "password": "Wrong-Password-1!"},
        )
        assert response.status_code == 401

    response = await client.post(
        "/api/v1/auth/login",
        json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
    )
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "RATE_LIMITED"


async def test_refresh_rotates_token(client, admin_user):
    login = await client.post(
        "/api/v1/auth/login",
        json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
    )
    refresh_token = login.json()["refresh_token"]

    response = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert response.status_code == 200
    new_refresh = response.json()["refresh_token"]
    assert new_refresh != refresh_token

    reused = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert reused.status_code == 401


async def test_logout_revokes_session(client, admin_user, auth_headers):
    response = await client.post(
        "/api/v1/auth/logout",
        json={"all_devices": True},
        headers=auth_headers,
    )
    assert response.status_code == 200

    me = await client.get("/api/v1/auth/me", headers=auth_headers)
    assert me.status_code == 401


async def test_totp_setup_and_enable(client, admin_user, auth_headers):
    setup = await client.post("/api/v1/auth/totp/setup", headers=auth_headers)
    assert setup.status_code == 200, setup.text
    secret = setup.json()["secret"]
    assert "otpauth://" in setup.json()["provisioning_uri"]

    code = pyotp.TOTP(secret).now()
    enable = await client.post("/api/v1/auth/totp/enable", json={"code": code}, headers=auth_headers)
    assert enable.status_code == 200, enable.text

    no_code = await client.post(
        "/api/v1/auth/login",
        json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
    )
    assert no_code.status_code == 401

    with_code = await client.post(
        "/api/v1/auth/login",
        json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD, "totp_code": code},
    )
    assert with_code.status_code == 200, with_code.text


async def test_change_password_requires_current(client, admin_user, auth_headers):
    response = await client.post(
        "/api/v1/auth/password",
        json={"current_password": "not-the-password", "new_password": "New-Aion-Passw0rd!"},
        headers=auth_headers,
    )
    assert response.status_code == 401


async def test_trader_login_includes_factor_menu_feature(client, trader_user):
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": TRADER_USERNAME, "password": TRADER_PASSWORD, "device_name": "pytest"},
    )
    assert response.status_code == 200, response.text
    features = response.json()["user"]["features"]
    assert "factors" in features
