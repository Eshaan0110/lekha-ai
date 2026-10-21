"""Audit middleware: one append-only event for every ``/api/`` request.

The event is written *before* the response starts. If it cannot be written, the caller
receives a generic 500 instead of the data: nothing is returned that was not recorded
(fail closed). Health and documentation endpoints are not audited.
"""

from __future__ import annotations

import uuid

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from apps.api.errors import internal_error_response
from lekha.audit import MAX_PATH_LENGTH, AuditEvent, AuditSink, outcome_for_status

logger = structlog.get_logger("lekha.audit")

AUDITED_PREFIX = "/api/"
# Path parameters that name an audit target, and the target type they name.
_TARGET_PARAMS = {
    "document_id": "document",
    "job_id": "ingestion_job",
    "request_id": "deletion_request",
    "chunk_id": "chunk",
    "query_id": "query_log",
    "meeting_id": "meeting",
    "minutes_id": "meeting_minutes",
}


def _uuid_or_none(value: object) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def build_event(scope: Scope, status_code: int) -> AuditEvent:
    state = scope.get("state", {})
    route = scope.get("route")
    template = getattr(route, "path", None) or "<unmatched>"
    target_type: str | None = None
    target_id: uuid.UUID | None = None
    if "audit_target" in state:  # set by handlers whose target is not in the path
        target_type, raw_id = state["audit_target"]
        target_id = _uuid_or_none(raw_id)
    for param, kind in _TARGET_PARAMS.items():
        if target_type is not None:
            break
        if param in scope.get("path_params", {}):
            target_type, target_id = kind, _uuid_or_none(scope["path_params"][param])
            break
    return AuditEvent(
        request_id=str(state.get("request_id", "unknown")),
        action=f"{scope['method']} {template}"[:300],
        method=scope["method"],
        path=scope["path"][:MAX_PATH_LENGTH],
        status_code=status_code,
        outcome=outcome_for_status(status_code),
        organisation_id=_uuid_or_none(state["organisation_id"])
        if "organisation_id" in state
        else None,
        actor_user_id=_uuid_or_none(state["user_id"]) if "user_id" in state else None,
        target_type=target_type,
        target_id=target_id,
    )


class AuditMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(AUDITED_PREFIX):
            await self.app(scope, receive, send)
            return
        sink: AuditSink = scope["app"].state.audit_sink
        recorded = False
        suppress_body = False

        async def send_after_audit(message: Message) -> None:
            nonlocal recorded, suppress_body
            if message["type"] == "http.response.start":
                recorded = True
                try:
                    await sink.record(build_event(scope, message["status"]))
                except Exception:  # any storage failure must block the response
                    logger.exception("audit_write_failed")
                    suppress_body = True
                    await internal_error_response()(scope, receive, send)
                    return
            elif suppress_body:
                return  # drop the original body: it was never audited
            await send(message)

        try:
            await self.app(scope, receive, send_after_audit)
        except Exception:
            if not recorded:
                try:
                    await sink.record(build_event(scope, 500))
                except Exception:
                    logger.exception("audit_write_failed")
            raise
