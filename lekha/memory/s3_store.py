"""S3 implementation of ObjectStore (AWS S3 in production, SeaweedFS locally; ADR-0005).

boto3 is synchronous, so calls run in a worker thread. Timeouts and a bounded retry policy
apply to every call. Errors are re-raised as ObjectStoreError with the S3 error *code* only:
messages can contain bucket names, keys, or endpoint details.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
from typing import TYPE_CHECKING, TypeVar

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from lekha.core.config import Settings
from lekha.memory.storage import ObjectStoreError

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

T = TypeVar("T")

_CLIENT_CONFIG = Config(
    connect_timeout=2,
    read_timeout=15,
    retries={"max_attempts": 3, "mode": "standard"},
    s3={"addressing_style": "path"},  # works for AWS and S3-compatible local stores
    # Only send/validate checksums when S3 requires them; we compute our own SHA-256.
    request_checksum_calculation="when_required",
    response_checksum_validation="when_required",
)


def _error_code(exc: Exception) -> str:
    if isinstance(exc, ClientError):
        return str(exc.response.get("Error", {}).get("Code", "ClientError"))
    return type(exc).__name__


class S3ObjectStore:
    def __init__(
        self,
        *,
        endpoint_url: str | None,
        region: str,
        bucket: str,
        access_key: str,
        secret_key: str,
    ) -> None:
        self._bucket = bucket
        self._client: S3Client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=_CLIENT_CONFIG,
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> S3ObjectStore:
        return cls(
            endpoint_url=str(settings.s3_endpoint_url),
            region=settings.s3_region,
            bucket=settings.s3_bucket,
            access_key=settings.s3_access_key.get_secret_value(),
            secret_key=settings.s3_secret_key.get_secret_value(),
        )

    async def put(self, key: str, data: bytes, *, media_type: str, sha256: bytes) -> None:
        def _put() -> None:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType=media_type,
                # S3 verifies the body against this and rejects a corrupted upload.
                ChecksumSHA256=base64.b64encode(sha256).decode(),
            )

        await self._call("put", _put)

    async def delete(self, key: str) -> None:
        await self._call("delete", lambda: self._client.delete_object(Bucket=self._bucket, Key=key))

    async def check(self) -> None:
        await self._call("check", lambda: self._client.head_bucket(Bucket=self._bucket))

    async def list_keys(self) -> list[str]:
        """Every key in the bucket (used by backups)."""

        def _list() -> list[str]:
            keys: list[str] = []
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self._bucket):
                keys.extend(item["Key"] for item in page.get("Contents", []) if "Key" in item)
            return keys

        return await self._call("list", _list)

    async def get(self, key: str) -> tuple[bytes, str]:
        """The object's bytes and content type (used by backups)."""

        def _get() -> tuple[bytes, str]:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            return response["Body"].read(), response.get("ContentType", "application/octet-stream")

        return await self._call("get", _get)

    async def ensure_bucket(self) -> bool:
        """Create the bucket if it does not exist. Returns True if it was created."""
        try:
            await self.check()
            return False
        except ObjectStoreError:
            pass
        await self._call("create_bucket", lambda: self._client.create_bucket(Bucket=self._bucket))
        return True

    async def _call(self, operation: str, func: Callable[[], T]) -> T:
        try:
            return await asyncio.to_thread(func)
        except (BotoCoreError, ClientError) as exc:
            raise ObjectStoreError(f"object store {operation} failed: {_error_code(exc)}") from None
