"""Version 1 API router (mounted under ``settings.api_prefix``)."""

from __future__ import annotations

from fastapi import APIRouter

from aion.api.v1 import (
    account,
    agents,
    auth,
    autopilot,
    factors,
    learning,
    market,
    models,
    notifications,
    risk,
    research_lab,
    strategies,
    system,
    trading,
    users,
    ws,
)
from aion.api.v1 import (
    settings as settings_routes,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(models.router)
api_router.include_router(system.router)
api_router.include_router(account.router)
api_router.include_router(market.router)
api_router.include_router(trading.router)
api_router.include_router(strategies.router)
api_router.include_router(research_lab.router)
api_router.include_router(risk.router)
api_router.include_router(agents.router)
api_router.include_router(factors.router)
api_router.include_router(autopilot.router)
api_router.include_router(learning.router)
api_router.include_router(notifications.router)
api_router.include_router(settings_routes.router)
api_router.include_router(ws.router)

__all__ = ["api_router"]
