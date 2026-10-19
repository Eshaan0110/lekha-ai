"""The dev-only sign-in adapter and the configuration guards around it."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from apps.api.main import create_app
from lekha.auth import DevAuthNotAllowedError, DevIdentityProvider
from lekha.core.config import AuthMode, Settings
from tests.conftest import InMemoryAuditSink

ORG = uuid.uuid4()


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_dev_adapter_refuses_to_exist_outside_local_and_test(environment: str) -> None:
    with pytest.raises(DevAuthNotAllowedError):
        DevIdentityProvider(environment=environment, organisation_id=ORG)


def test_dev_adapter_accepts_only_dev_subjects() -> None:
    provider = DevIdentityProvider(environment="local", organisation_id=ORG)
    identity = provider.authenticate({"x-dev-user": "dev|asha"})
    assert identity is not None
    assert (identity.organisation_id, identity.idp_subject) == (ORG, "dev|asha")


@pytest.mark.parametrize(
    "header",
    [None, "", "asha", "admin", "dev|", "dev|Asha", "dev|a b", "dev|" + "a" * 64, "sso|asha"],
)
def test_dev_adapter_rejects_anything_else(header: str | None) -> None:
    provider = DevIdentityProvider(environment="test", organisation_id=ORG)
    headers = {} if header is None else {"x-dev-user": header}
    assert provider.authenticate(headers) is None


def test_default_sign_in_mode_is_oidc(make_settings: Callable[..., Settings]) -> None:
    assert make_settings().auth_mode is AuthMode.OIDC


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_settings_refuse_dev_sign_in_outside_local_and_test(
    make_settings: Callable[..., Settings], environment: str
) -> None:
    with pytest.raises(ValidationError, match="only allowed in local or test"):
        make_settings(environment=environment, auth_mode="dev", dev_auth_organisation_id=ORG)


def test_settings_refuse_dev_sign_in_without_organisation(
    make_settings: Callable[..., Settings],
) -> None:
    with pytest.raises(ValidationError, match="DEV_AUTH_ORGANISATION_ID"):
        make_settings(auth_mode="dev")


@pytest.mark.parametrize("path", ["/api/v1/me", "/api/v1/documents"])
def test_protected_routes_refuse_everyone_while_oidc_is_unavailable(
    make_settings: Callable[..., Settings], audit_sink: InMemoryAuditSink, path: str
) -> None:
    # OIDC mode with no implementation must fail closed, even with a dev header present.
    app = create_app(make_settings(), readiness_checks=[], audit_sink=audit_sink)
    with TestClient(app) as client:
        response = client.get(path, headers={"X-Dev-User": "dev|asha"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


def test_invalid_dev_header_is_rejected_before_any_database_call(
    make_settings: Callable[..., Settings], audit_sink: InMemoryAuditSink
) -> None:
    app = create_app(
        make_settings(auth_mode="dev", dev_auth_organisation_id=ORG),
        readiness_checks=[],
        audit_sink=audit_sink,
    )
    with TestClient(app) as client:
        response = client.get("/api/v1/me", headers={"X-Dev-User": "root"})
    assert response.status_code == 401


def test_health_endpoints_stay_public(make_settings: Callable[..., Settings]) -> None:
    with TestClient(create_app(make_settings(), readiness_checks=[])) as client:
        assert client.get("/health/live").status_code == 200
