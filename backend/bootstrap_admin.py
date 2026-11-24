"""创建或提升一个管理员账号。

用法：
    python bootstrap_admin.py [username] [password]

未传 password 时：账号不存在则随机生成并打印一次；账号已存在则只提升权限，不改密码。
也可用环境变量 ADMIN_USERNAME / ADMIN_PASSWORD 传入。
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
import secrets
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from aion.core.config import Settings
from aion.core.security import hash_password
from aion.models.auth import User


async def create_admin(username: str, password: str | None) -> None:
    settings = Settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = dt.datetime.now(dt.UTC)
    generated = False

    async with factory() as db:
        user = (
            await db.execute(select(User).where(User.username == username))
        ).scalar_one_or_none()

        if user is None:
            if not password:
                password = secrets.token_urlsafe(12)
                generated = True
            db.add(
                User(
                    username=username,
                    password_hash=hash_password(password),
                    is_active=True,
                    is_admin=True,
                    password_changed_at=now,
                    last_login_at=now,
                )
            )
            action = "已创建"
        else:
            user.is_admin = True
            user.is_active = True
            if password:
                user.password_hash = hash_password(password)
                user.password_changed_at = now
                action = "已提升为管理员并重置密码"
            else:
                action = "已提升为管理员（密码未改动）"

        await db.commit()

    await engine.dispose()

    print(f"[aion] 管理员{action}：{username}")
    if generated:
        print(f"[aion] 初始密码：{password}（仅显示这一次，请立即保存）")


def main() -> None:
    username = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("ADMIN_USERNAME", "admin"))
    password = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("ADMIN_PASSWORD", "")
    asyncio.run(create_admin(username.strip() or "admin", password.strip() or None))


if __name__ == "__main__":
    main()
