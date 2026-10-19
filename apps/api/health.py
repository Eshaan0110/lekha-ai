"""Liveness and readiness endpoints for orchestrators and load balancers.

These are operational endpoints, so they are unversioned and live outside ``/api/v1``.
They reveal only coarse per-dependency status, never error messages. In production they
must be reachable only from inside the network (see docs/security.md).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from lekha.core.health import CheckStatus, run_readiness_checks

router = APIRouter(prefix="/health", tags=["operations"])


class LivenessResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, CheckStatus]


@router.get("/live", response_model=LivenessResponse)
async def live() -> LivenessResponse:
    return LivenessResponse()


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={503: {"model": ReadinessResponse, "description": "A dependency is unavailable"}},
)
async def ready(request: Request) -> JSONResponse:
    state = request.app.state
    report = await run_readiness_checks(
        state.readiness_checks, timeout_seconds=state.settings.readiness_timeout_seconds
    )
    body = ReadinessResponse(status="ready" if report.ready else "not_ready", checks=report.checks)
    return JSONResponse(status_code=200 if report.ready else 503, content=body.model_dump())
