"""Request-ID correlation, access logging, and last-resort error handling.

Implemented as plain ASGI middleware (not ``BaseHTTPMiddleware``) so the request-ID
context variable is reliably visible to all code that runs for the request.
"""

from __future__ import annotations

import re
import time
import uuid

import structlog
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from apps.api.errors import internal_error_response

REQUEST_ID_HEADER = "X-Request-ID"
# Accept a caller-supplied ID only if it is short and boring. Anything else could be
# used for log injection, so we replace it with our own.
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

logger = structlog.get_logger("lekha.http")


def resolve_request_id(incoming: str | None) -> str:
    if incoming and _VALID_REQUEST_ID.fullmatch(incoming):
        return incoming
    return uuid.uuid4().hex


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = None
        for key, value in scope["headers"]:
            if key == b"x-request-id":
                incoming = value.decode("latin-1")
                break
        request_id = resolve_request_id(incoming)
        scope.setdefault("state", {})["request_id"] = request_id

        status_code = 500
        response_started = False

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        started = time.perf_counter()
        structlog.contextvars.clear_contextvars()
        # Log the path only: query strings may carry user input we must not persist.
        structlog.contextvars.bind_contextvars(
            request_id=request_id, method=scope["method"], path=scope["path"]
        )
        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            logger.exception("unhandled_exception")
            if response_started:
                raise  # Too late to send a clean error; let the server close the connection.
            response = internal_error_response(request_id)
            await response(scope, receive, send_with_request_id)
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.info("http_request", status=status_code, duration_ms=duration_ms)
            structlog.contextvars.clear_contextvars()
