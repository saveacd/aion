"""Shared pytest fixtures.

The suite expects a throwaway PostgreSQL database (default: ``aion_test``) and
Redis. Override with ``AION_TEST_DATABASE_URL`` / ``AION_TEST_REDIS_URL``. The
schema is created at session start; every test truncates all tables.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os

# Configure the environment before any AION module reads settings.
if len(os.environ.get("AION_SECRET_KEY", "")) < 32:
    os.environ["AION_SECRET_KEY"] = "test-only-secret-key-0123456789abcdef0123456789"
os.environ.setdefault("AION_ENVIRONMENT", "dev")
os.environ["AION_LOGIN_RATE_LIMIT_PER_MINUTE"] = "1000"
os.environ["AION_TRADING_MODE"] = "PAPER"
os.environ["AION_MAINNET_ENABLED"] = "false"
os.environ["AION_DATABASE_URL"] = os.environ.get(
    "AION_TEST_DATABASE_URL",
    "postgresql+asyncpg://aion:aion_dev_pw@127.0.0.1:5432/aion_test",
)
os.environ.setdefault("AION_REDIS_URL", os.environ.get("AION_TEST_REDIS_URL", "redis://127.0.0.1:6379/15"))

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

import aion.models  # noqa: F401,E402  (registers all tables)
from aion.core.config import get_settings  # noqa: E402
from aion.core.db import dispose_engine, get_sessionmaker, init_engine  # noqa: E402
from aion.core.redis import close_client, init_client  # noqa: E402
from aion.core.security import hash_password  # noqa: E402
from aion.main import create_app  # noqa: E402
from aion.models.auth import User  # noqa: E402
from aion.models.base import Base  # noqa: E402

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "Aion-Test-Passw0rd!"
TRADER_USERNAME = "trader"
TRADER_PASSWORD = "Aion-Trader-Passw0rd!"


@pytest.fixture(scope="session")
def settings():
    get_settings.cache_clear()
    return get_settings()


@pytest.fixture(scope="session")
def _schema(settings):
    async def _create() -> None:
        engine = create_async_engine(settings.database_url, poolclass=NullPool)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
        finally:
            await engine.dispose()

    asyncio.run(_create())


@pytest.fixture
async def database(settings, _schema):
    engine = init_engine(settings)
    init_client(settings)
    try:
        yield engine
    finally:
        await dispose_engine()
        await close_client()


@pytest.fixture
async def clean_db(database):
    table_names = ", ".join(f'"{table.name}"' for table in reversed(Base.metadata.sorted_tables))
    async with get_sessionmaker()() as session:
        await session.execute(text(f"TRUNCATE {table_names} RESTART IDENTITY CASCADE"))  # noqa: S608
        await session.commit()
    yield


@pytest.fixture
async def db_session(clean_db):
    async with get_sessionmaker()() as session:
        yield session


@pytest.fixture
async def app(settings):
    return create_app(settings)


@pytest.fixture
async def client(clean_db, app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        yield http_client


async def _create_user(username: str, password: str, *, is_admin: bool) -> User:
    async with get_sessionmaker()() as session:
        user = User(
            username=username,
            password_hash=hash_password(password),
            is_admin=is_admin,
            is_active=True,
            totp_enabled=False,
            password_changed_at=dt.datetime.now(dt.UTC),
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


@pytest.fixture
async def admin_user(clean_db):
    return await _create_user(ADMIN_USERNAME, ADMIN_PASSWORD, is_admin=True)


@pytest.fixture
async def trader_user(clean_db):
    return await _create_user(TRADER_USERNAME, TRADER_PASSWORD, is_admin=False)


@pytest.fixture
def login(client):
    async def _login(username: str = ADMIN_USERNAME, password: str = ADMIN_PASSWORD) -> dict[str, str]:
        response = await client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": password},
        )
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    return _login


@pytest.fixture
async def auth_headers(admin_user, login):
    return await login()
