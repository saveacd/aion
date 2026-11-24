"""Alembic environment (async engine, asyncpg driver)."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from aion.core.config import get_settings
from aion.models import Base  # noqa: F401  (registers all tables)
from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
config.set_main_option(
    "sqlalchemy.url", settings.database_url.replace("%", "%%").replace("+asyncpg", "")
)
ASYNC_URL = settings.database_url

target_metadata = Base.metadata


def _include_object(obj, name, type_, reflected, compare_to) -> bool:
    # Market candles are high-volume operational data; never drop them implicitly.
    if type_ == "table" and name in {"market_candles_archive"}:
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=ASYNC_URL.replace("+asyncpg", ""),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        {"sqlalchemy.url": ASYNC_URL},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
