"""The authentication dependency every protected route uses.

Fails closed at each step: no identity provider configured, no or invalid credentials, or
an unknown/deactivated user all produce ``401 unauthenticated``. The response never says
which step failed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

import structlog
from fastapi import Depends, HTTPException, Request

from db.access import load_auth_context
from db.tenancy import tenant_transaction
from lekha.auth import AuthContext, IdentityProvider

logger = structlog.get_logger("lekha.auth")

_UNAUTHENTICATED = HTTPException(status_code=401, detail="Sign-in required.")


async def require_auth_context(request: Request) -> AuthContext:
    provider: IdentityProvider | None = request.app.state.identity_provider
    if provider is None:
        logger.warning("auth_rejected", reason="no_identity_provider")
        raise _UNAUTHENTICATED
    identity = provider.authenticate(request.headers)
    if identity is None:
        logger.info("auth_rejected", reason="no_valid_credentials")
        raise _UNAUTHENTICATED
    async with tenant_transaction(
        request.app.state.session_factory, identity.organisation_id
    ) as session:
        ctx = await load_auth_context(session, identity, now=datetime.now(UTC))
    if ctx is None:
        logger.info("auth_rejected", reason="unknown_or_inactive_user")
        raise _UNAUTHENTICATED
    # Log the internal user ID only, never names or IdP subjects.
    state = request.scope.setdefault("state", {})
    state["user_id"] = str(ctx.user_id)
    state["organisation_id"] = str(ctx.organisation_id)
    structlog.contextvars.bind_contextvars(user_id=str(ctx.user_id))
    return ctx


CurrentUser = Annotated[AuthContext, Depends(require_auth_context)]
