"""Validated application configuration.

All settings come from environment variables prefixed ``LEKHA_`` (or a local ``.env``
file). Invalid or unsafe configuration stops the process at startup instead of failing
later at runtime.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

from pydantic import AnyHttpUrl, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PLACEHOLDER_SECRET = "CHANGE_ME"  # noqa: S105 - the marker we refuse, not a credential
MIN_SECRET_LENGTH = 16


class Environment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class AuthMode(StrEnum):
    OIDC = "oidc"  # Group identity provider (pending Phase 0); the safe default
    DEV = "dev"  # local/test only: trusts an X-Dev-User header naming a synthetic user


class LogFormat(StrEnum):
    CONSOLE = "console"  # human-readable, for a developer terminal
    JSON = "json"  # one JSON object per line, for log aggregation (Loki)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LEKHA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    environment: Environment = Environment.LOCAL
    log_level: str = Field(default="INFO", pattern=r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")
    log_format: LogFormat = LogFormat.JSON

    postgres_host: str = "127.0.0.1"
    postgres_port: int = Field(default=15432, ge=1, le=65535)
    postgres_db: str = Field(default="lekha", min_length=1)
    # Owner account: used only by migrations and provisioning, never by the running app.
    postgres_user: str = Field(default="lekha", min_length=1)
    postgres_password: SecretStr
    # Least-privilege account the API connects as (no DDL, subject to row-level security).
    db_app_user: str = Field(default="lekha_app", pattern=r"^[a-z_][a-z0-9_]{0,62}$")
    db_app_password: SecretStr
    db_pool_size: int = Field(default=5, ge=1, le=50)
    db_statement_timeout_ms: int = Field(default=15_000, ge=100)

    redis_host: str = "127.0.0.1"
    redis_port: int = Field(default=16379, ge=1, le=65535)
    redis_password: SecretStr

    qdrant_url: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:16333")
    qdrant_api_key: SecretStr

    s3_endpoint_url: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:18333")
    s3_access_key: SecretStr
    s3_secret_key: SecretStr
    s3_bucket: str = Field(default="lekha-documents", pattern=r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
    s3_region: str = Field(default="ap-south-1", pattern=r"^[a-z]{2}-[a-z]+-\d$")

    upload_max_bytes: int = Field(default=25 * 1024 * 1024, ge=1024, le=200 * 1024 * 1024)
    # Release gate (plan section 4): confidential/restricted uploads stay blocked until the
    # Phase 2 security and legal gate is formally passed. Never enable this locally.
    allow_confidential_uploads: bool = False

    readiness_timeout_seconds: float = Field(default=2.0, gt=0, le=30)

    # Vector search (Phase 2). "fake" is a clearly labelled simulation; "minilm" is a small
    # real English-only model used until BGE-M3 is downloaded (Phase 2 plan, decision D3).
    # Each embedder needs its own collection: vectors from different models can't be compared.
    qdrant_collection: str = Field(default="lekha_chunks", pattern=r"^[a-z][a-z0-9_]{0,62}$")
    embedder: Literal["fake", "minilm", "bge-m3"] = "fake"
    embedder_model_dir: Path = Path("models/bge-m3")
    minilm_model_dir: Path = Path("models/all-MiniLM-L6-v2")
    embedder_device: Literal["cpu", "cuda"] = "cpu"

    auth_mode: AuthMode = AuthMode.OIDC
    # The synthetic organisation the dev adapter signs users into (see seed_dev_data).
    dev_auth_organisation_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _reject_unsafe_secrets(self) -> Self:
        secrets = {
            "postgres_password": self.postgres_password,
            "db_app_password": self.db_app_password,
            "redis_password": self.redis_password,
            "qdrant_api_key": self.qdrant_api_key,
            "s3_access_key": self.s3_access_key,
            "s3_secret_key": self.s3_secret_key,
        }
        for name, secret in secrets.items():
            value = secret.get_secret_value()
            if value == PLACEHOLDER_SECRET or len(value) < MIN_SECRET_LENGTH:
                # Never echo the value itself.
                raise ValueError(
                    f"LEKHA_{name.upper()} is a placeholder or shorter than "
                    f"{MIN_SECRET_LENGTH} characters; run ./dev.ps1 setup"
                )
        return self

    @model_validator(mode="after")
    def _app_must_not_use_owner_account(self) -> Self:
        if self.db_app_user == self.postgres_user:
            raise ValueError("LEKHA_DB_APP_USER must differ from the database owner account")
        return self

    @model_validator(mode="after")
    def _dev_auth_only_locally(self) -> Self:
        if self.auth_mode is AuthMode.DEV:
            if self.environment not in {Environment.LOCAL, Environment.TEST}:
                raise ValueError("LEKHA_AUTH_MODE=dev is only allowed in local or test")
            if self.dev_auth_organisation_id is None:
                raise ValueError("LEKHA_AUTH_MODE=dev requires LEKHA_DEV_AUTH_ORGANISATION_ID")
        return self

    @model_validator(mode="after")
    def _confidential_uploads_need_a_passed_gate(self) -> Self:
        if self.allow_confidential_uploads and self.environment in {
            Environment.LOCAL,
            Environment.TEST,
        }:
            raise ValueError(
                "LEKHA_ALLOW_CONFIDENTIAL_UPLOADS is for gated environments only; local and "
                "test use synthetic, non-confidential data"
            )
        return self

    @model_validator(mode="after")
    def _production_guards(self) -> Self:
        if self.environment in {Environment.STAGING, Environment.PRODUCTION}:
            if self.log_format is not LogFormat.JSON:
                raise ValueError("staging/production require LEKHA_LOG_FORMAT=json")
            if self.log_level == "DEBUG":
                raise ValueError("DEBUG logging is not allowed in staging/production")
        return self
