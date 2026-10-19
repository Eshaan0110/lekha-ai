"""The common error envelope.

Every error response has the same shape so clients can handle errors uniformly::

    {"error": {"code": "not_found", "message": "...", "request_id": "...", "details": [...]}}

Internal exception text, stack traces, and submitted input values are never returned.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException


class FieldError(BaseModel):
    location: list[str | int]
    message: str


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str | None = None
    details: list[FieldError] | None = None


class ErrorEnvelope(BaseModel):
    error: ErrorBody


_STATUS_CODES = {
    400: "bad_request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    411: "length_required",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "rate_limited",
    503: "upstream_unavailable",
}


def error_response(
    status_code: int,
    code: str,
    message: str,
    request_id: str | None,
    details: list[FieldError] | None = None,
) -> JSONResponse:
    body = ErrorEnvelope(
        error=ErrorBody(code=code, message=message, request_id=request_id, details=details)
    )
    return JSONResponse(status_code=status_code, content=body.model_dump(exclude_none=True))


def internal_error_response(request_id: str | None) -> JSONResponse:
    return error_response(500, "internal_error", "An unexpected error occurred.", request_id)


def _request_id(request: Request) -> str | None:
    value = request.scope.get("state", {}).get("request_id")
    return value if isinstance(value, str) else None


async def _http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, StarletteHTTPException):  # guaranteed by registration below
        raise TypeError(type(exc).__name__)
    code = _STATUS_CODES.get(exc.status_code, "error")
    # Only our own handlers raise HTTPException with a detail string meant for clients.
    message = exc.detail if isinstance(exc.detail, str) else code.replace("_", " ")
    response = error_response(exc.status_code, code, message, _request_id(request))
    if exc.headers:
        response.headers.update(exc.headers)
    return response


async def _validation_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):  # guaranteed by registration below
        raise TypeError(type(exc).__name__)
    # Report where and why validation failed, but never echo the submitted value.
    details = [
        FieldError(
            location=[str(p) if not isinstance(p, int) else p for p in err["loc"]],
            message=str(err["msg"]),
        )
        for err in exc.errors()
    ]
    return error_response(
        422, "validation_error", "The request is invalid.", _request_id(request), details
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)
