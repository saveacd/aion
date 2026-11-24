"""Command line entry points for first-run setup and operational tasks.

Usage (inside the backend container or the venv)::

    python -m aion.cli create-admin --username admin
    python -m aion.cli init
    python -m aion.cli show-status
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from sqlalchemy import func, select

from aion.core.config import get_settings
from aion.core.db import dispose_engine, get_sessionmaker
from aion.core.security import check_password_strength, hash_password
from aion.models import (
    AccountSnapshot,
    ReconciliationRun,
    RiskPolicy,
    TradingControlState,
    User,
)


async def _create_admin(username: str, password: str) -> int:
    problems = check_password_strength(password)
    if problems:
        print("密码不符合要求：", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 2

    factory = get_sessionmaker()
    async with factory() as session:
        existing = await session.scalar(select(User).where(User.username == username))
        if existing is not None:
            print(f"用户 {username} 已存在，未做修改。", file=sys.stderr)
            return 1
        session.add(
            User(
                username=username,
                password_hash=hash_password(password),
                is_active=True,
                is_admin=True,
            )
        )
        await session.commit()
    print(f"已创建管理员 {username}")
    return 0


async def _init() -> int:
    factory = get_sessionmaker()
    async with factory() as session:
        created: list[str] = []

        policy = await session.scalar(select(func.count()).select_from(RiskPolicy))
        if not policy:
            session.add(RiskPolicy(name="default", is_active=True))
            created.append("risk_policies")

        control = await session.get(TradingControlState, 1)
        if control is None:
            session.add(TradingControlState(id=1))
            created.append("trading_control_state")

        await session.commit()

    settings = get_settings()
    print(f"环境: {settings.environment}  交易模式: {settings.resolve_trading_mode()}")
    print("初始化完成：" + (", ".join(created) if created else "无需变更（已存在）"))
    if settings.trading_mode.value == "MAINNET":
        print("警告：配置里要求 MAINNET，但必须 mainnet_enabled=true 才会生效。", file=sys.stderr)
    return 0


async def _show_status() -> int:
    factory = get_sessionmaker()
    async with factory() as session:
        users = await session.scalar(select(func.count()).select_from(User))
        policy = await session.scalar(select(RiskPolicy).where(RiskPolicy.is_active.is_(True)))
        control = await session.get(TradingControlState, 1)
        snap = await session.scalar(
            select(AccountSnapshot).order_by(AccountSnapshot.captured_at.desc()).limit(1)
        )
        blocking = await session.scalar(
            select(func.count())
            .select_from(ReconciliationRun)
            .where(ReconciliationRun.blocking.is_(True), ReconciliationRun.resolved.is_(False))
        )

    settings = get_settings()
    print(f"用户数: {users}")
    print(f"交易模式: {settings.resolve_trading_mode()} (mainnet_enabled={settings.mainnet_enabled})")
    print(f"风控策略: {'已配置' if policy else '未配置'}")
    if policy is not None:
        print(
            f"  杠杆上限 {policy.max_leverage}  单笔名义 {policy.max_position_notional_usd}  "
            f"日亏损上限 {policy.daily_loss_limit_usd}"
        )
    if control is not None:
        print(
            f"控制状态: stop_new_trades={control.stop_new_trades} pause_ai={control.pause_ai} "
            f"emergency_stop={control.emergency_stop}"
        )
    print(f"账户快照: {'有' if snap else '无（尚未从交易所同步）'}")
    print(f"未解决的对账阻塞: {blocking}")
    return 0


async def _run(args: argparse.Namespace) -> int:
    try:
        if args.command == "create-admin":
            password = args.password or getpass.getpass("密码: ")
            return await _create_admin(args.username, password)
        if args.command == "init":
            return await _init()
        if args.command == "show-status":
            return await _show_status()
        raise SystemExit(f"unknown command: {args.command}")
    finally:
        await dispose_engine()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aion", description="AION backend operations")
    sub = parser.add_subparsers(dest="command", required=True)

    p_admin = sub.add_parser("create-admin", help="创建管理员账号")
    p_admin.add_argument("--username", default="admin")
    p_admin.add_argument("--password", default="")

    sub.add_parser("init", help="初始化默认风控策略与控制状态")
    sub.add_parser("show-status", help="打印当前运行状态摘要")

    args = parser.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
