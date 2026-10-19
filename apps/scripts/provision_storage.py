"""Create the document bucket in the local S3-compatible store if it does not exist.

Run by ``./dev.ps1 migrate``. In production the bucket (with encryption, versioning and
access policies) is created by infrastructure tooling, not by this script.
"""

from __future__ import annotations

import asyncio

from lekha.core.config import Settings
from lekha.memory.s3_store import S3ObjectStore


async def provision(settings: Settings) -> None:
    created = await S3ObjectStore.from_settings(settings).ensure_bucket()
    state = "created" if created else "already exists"
    print(f"Bucket '{settings.s3_bucket}' {state}.")


def main() -> None:
    asyncio.run(provision(Settings()))


if __name__ == "__main__":
    main()
