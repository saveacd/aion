"""WebSocket endpoint for live events.

Clients authenticate with a ``token`` query parameter or an initial
``{"type": "auth", "token": "..."}`` frame, then subscribe to symbol channels.
Events are fanned out from the in-process bus; nothing is fabricated here.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import json
import uuid
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from aion.core.config import Settings, get_settings
from aion.core.db import get_sessionmaker
from aion.core.errors import AppError
from aion.core.security import TokenService
from aion.models.auth import DeviceSession, User
from aion.services.events import bus

router = APIRouter(tags=["websocket"])

AUTH_TIMEOUT_SECONDS = 10.0
HEARTBEAT_SECONDS = 30.0
MAX_SUBSCRIPTIONS = 50


def _unauthorized(message: str) -> dict[str, Any]:
    return {"type": "error", "data": {"code": "UNAUTHORIZED", "message": message}}


async def _resolve_user(settings: Settings, token: str) -> User | None:
    try:
        claims = TokenService(settings).decode_access_token(token)
        user_id = uuid.UUID(str(claims.get("sub")))
        session_id = uuid.UUID(str(claims.get("sid")))
    except (AppError, TypeError, ValueError):
        return None

    async with get_sessionmaker()() as session:
        device = await session.get(DeviceSession, session_id)
        if device is None or device.user_id != user_id or device.revoked_at is not None:
            return None
        if device.expires_at <= dt.datetime.now(dt.UTC):
            return None
        user = await session.get(User, user_id)
        if user is None or not user.is_active:
            return None
        return user


async def _read_auth_token(websocket: WebSocket) -> str | None:
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=AUTH_TIMEOUT_SECONDS)
    except (TimeoutError, WebSocketDisconnect):
        return None
    try:
        message = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(message, dict) and message.get("type") == "auth":
        token = message.get("token")
        return token if isinstance(token, str) else None
    return None


def _matches(event: dict[str, Any], subscriptions: set[str]) -> bool:
    symbol = event.get("symbol")
    if not symbol:
        return True
    return "*" in subscriptions or str(symbol).upper() in subscriptions


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    settings = get_settings()
    await websocket.accept()

    token = websocket.query_params.get("token") or await _read_auth_token(websocket)
    if not token:
        await websocket.send_json(_unauthorized("Authentication required."))
        await websocket.close(code=4401)
        return

    user = await _resolve_user(settings, token)
    if user is None:
        await websocket.send_json(_unauthorized("Invalid or expired session."))
        await websocket.close(code=4401)
        return

    await websocket.send_json({"type": "auth_ok", "data": {"username": user.username}})

    subscriptions: set[str] = set()
    queue = await bus.subscribe()
    send_lock = asyncio.Lock()

    async def send(payload: dict[str, Any]) -> None:
        async with send_lock:
            await websocket.send_json(payload)

    async def receiver() -> None:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(message, dict):
                continue
            kind = message.get("type")
            if kind in {"ping", "heartbeat"}:
                await send({"type": "pong", "ts": dt.datetime.now(dt.UTC).isoformat()})
            elif kind == "subscribe":
                for channel in list(message.get("channels") or message.get("symbols") or []):
                    if isinstance(channel, str) and channel.strip():
                        subscriptions.add(channel.strip().upper())
                while len(subscriptions) > MAX_SUBSCRIPTIONS:
                    subscriptions.pop()
                await send({"type": "subscribed", "data": {"channels": sorted(subscriptions)}})
            elif kind == "unsubscribe":
                for channel in list(message.get("channels") or message.get("symbols") or []):
                    if isinstance(channel, str):
                        subscriptions.discard(channel.strip().upper())
                await send({"type": "unsubscribed", "data": {"channels": sorted(subscriptions)}})
            elif kind == "close":
                return

    async def sender() -> None:
        while True:
            event = await queue.get()
            if _matches(event, subscriptions):
                await send(event)

    async def heartbeat() -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            await send({"type": "ping", "ts": dt.datetime.now(dt.UTC).isoformat()})

    tasks = [
        asyncio.create_task(receiver()),
        asyncio.create_task(sender()),
        asyncio.create_task(heartbeat()),
    ]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            # A finished task only means the socket is gone; uvicorn raises
            # ClientDisconnected (not WebSocketDisconnect) on a dead send.
            with contextlib.suppress(Exception):
                task.result()
    finally:
        await bus.unsubscribe(queue)
        with contextlib.suppress(Exception):
            await websocket.close(code=1000)
