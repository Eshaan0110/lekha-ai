from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from lekha.core.config import Environment, Settings


def test_valid_settings_load(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings()
    assert settings.environment is Environment.TEST
    assert settings.postgres_port == 15432


@pytest.mark.parametrize("bad_value", ["CHANGE_ME", "short"])
def test_placeholder_or_short_secret_is_rejected(
    make_settings: Callable[..., Settings], bad_value: str
) -> None:
    with pytest.raises(ValidationError, match="LEKHA_REDIS_PASSWORD"):
        make_settings(redis_password=bad_value)


def test_rejection_message_does_not_echo_the_secret(
    make_settings: Callable[..., Settings],
) -> None:
    with pytest.raises(ValidationError) as excinfo:
        make_settings(qdrant_api_key="leakyvalue")
    assert "leakyvalue" not in str(excinfo.value)


def test_missing_secret_fails_closed() -> None:
    with pytest.raises(ValidationError, match="postgres_password"):
        Settings(_env_file=None)


def test_secrets_are_masked_in_repr(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings(postgres_password="super-secret-password-123")
    assert "super-secret-password-123" not in repr(settings)
    assert "super-secret-password-123" not in str(settings.model_dump())


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_production_like_environments_require_json_logs(
    make_settings: Callable[..., Settings], environment: str
) -> None:
    with pytest.raises(ValidationError, match="LOG_FORMAT=json"):
        make_settings(environment=environment, log_format="console")


def test_production_rejects_debug_logging(make_settings: Callable[..., Settings]) -> None:
    with pytest.raises(ValidationError, match="DEBUG"):
        make_settings(environment="production", log_level="DEBUG")


def test_invalid_log_level_is_rejected(make_settings: Callable[..., Settings]) -> None:
    with pytest.raises(ValidationError):
        make_settings(log_level="LOUD")


def test_settings_are_immutable(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings()
    with pytest.raises(ValidationError):
        settings.postgres_port = 1  # type: ignore[misc]
