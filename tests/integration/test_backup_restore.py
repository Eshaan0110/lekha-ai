"""Restore drill: back up, destroy, restore, and prove everything came back.

Runs against the throwaway test database and bucket, plus a uniquely named Qdrant
collection that is removed afterwards.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from apps.api.main import create_app
from apps.scripts.backup import BackupError, create_backup, restore_backup, verify_backup
from apps.scripts.seed_dev_data import SeedResult, seed_synthetic_organisation
from db.engine import create_session_factory
from db.tenancy import tenant_transaction
from lekha.auth import user_badge
from lekha.core.config import AuthMode, Settings
from lekha.memory.s3_store import S3ObjectStore
from tests.integration.support import delete_tenant

pytestmark = pytest.mark.integration

PDF = b"%PDF-1.4\n% restore drill\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


@pytest.fixture
async def org(owner_engine: AsyncEngine) -> AsyncIterator[SeedResult]:
    factory = async_sessionmaker(owner_engine, expire_on_commit=False)
    async with factory.begin() as session:
        result = await seed_synthetic_organisation(session, uuid.uuid4())
    yield result
    await delete_tenant(owner_engine, result.organisation_id)


@pytest.fixture
def qdrant(settings: Settings) -> Iterator[tuple[httpx.Client, str]]:
    client = httpx.Client(
        base_url=str(settings.qdrant_url).rstrip("/"),
        headers={"api-key": settings.qdrant_api_key.get_secret_value()},
        timeout=30,
    )
    name = f"lekha_drill_{uuid.uuid4().hex[:8]}"
    client.put(
        f"/collections/{name}", json={"vectors": {"size": 4, "distance": "Cosine"}}
    ).raise_for_status()
    client.put(
        f"/collections/{name}/points",
        params={"wait": "true"},
        json={"points": [{"id": 1, "vector": [0.1, 0.2, 0.3, 0.4], "payload": {"tag": "drill"}}]},
    ).raise_for_status()
    yield client, name
    client.delete(f"/collections/{name}")
    client.close()


def _upload(settings: Settings, org: SeedResult) -> str:
    app_settings = settings.model_copy(
        update={"auth_mode": AuthMode.DEV, "dev_auth_organisation_id": org.organisation_id}
    )
    with TestClient(create_app(app_settings)) as client:
        response = client.post(
            "/api/v1/documents",
            headers={"X-Dev-User": "dev|chitra"},
            files={"file": ("drill.pdf", PDF, "application/pdf")},
            data={
                "metadata": json.dumps(
                    {
                        "title": "Restore drill",
                        "canonical_key": f"synthetic:drill-{uuid.uuid4().hex[:8]}",
                        "business_unit_id": str(org.business_units["bu-alpha"]),
                        "classification": "internal",
                        "acl_subjects": [user_badge(org.users["chitra"])],
                    }
                )
            },
        )
    assert response.status_code == 201, response.text
    document_id: str = response.json()["document_id"]
    return document_id


async def _storage_key(owner_engine: AsyncEngine, document_id: str) -> str:
    async with owner_engine.connect() as conn:
        key: str = (
            await conn.execute(
                text(
                    "SELECT f.storage_key FROM document d JOIN document_version v"
                    " ON v.id = d.current_version_id JOIN document_file f ON f.id = v.file_id"
                    " WHERE d.id = :d"
                ),
                {"d": document_id},
            )
        ).scalar_one()
    return key


async def test_full_restore_drill(
    settings: Settings,
    org: SeedResult,
    owner_engine: AsyncEngine,
    app_engine: AsyncEngine,
    qdrant: tuple[httpx.Client, str],
    tmp_path: Path,
) -> None:
    client, collection = qdrant
    document_id = _upload(settings, org)
    key = await _storage_key(owner_engine, document_id)
    store = S3ObjectStore.from_settings(settings)

    backup = await create_backup(settings, tmp_path)
    manifest = verify_backup(backup)
    assert collection in manifest["qdrant_collections"]
    assert f"objects/{key}" in {f["path"] for f in manifest["files"]}

    # Disaster: the organisation's rows, the stored file and the collection are gone.
    await delete_tenant(owner_engine, org.organisation_id)
    await store.delete(key)
    client.delete(f"/collections/{collection}").raise_for_status()

    await restore_backup(settings, backup)

    # Database rows are back, and the least-privilege app role can still read them.
    factory = create_session_factory(app_engine)
    async with tenant_transaction(factory, org.organisation_id) as session:
        title = (
            await session.execute(
                text("SELECT title FROM document WHERE id = :d"), {"d": document_id}
            )
        ).scalar_one()
    assert title == "Restore drill"
    # The stored file is back, byte for byte.
    data, _ = await store.get(key)
    assert hashlib.sha256(data).digest() == hashlib.sha256(PDF).digest()
    # The vector collection is back with its point.
    point = client.get(f"/collections/{collection}/points/1")
    point.raise_for_status()
    assert point.json()["result"]["payload"] == {"tag": "drill"}
    # The audit trail survived and is still append-only.
    async with owner_engine.connect() as conn:
        audited = (
            await conn.execute(
                text("SELECT count(*) FROM audit_event WHERE target_id = :d"), {"d": document_id}
            )
        ).scalar_one()
    assert audited >= 1


async def test_a_damaged_backup_is_refused_before_anything_changes(
    settings: Settings, org: SeedResult, owner_engine: AsyncEngine, tmp_path: Path
) -> None:
    backup = await create_backup(settings, tmp_path)
    (backup / "postgres.dump").write_bytes(b"tampered")

    await delete_tenant(owner_engine, org.organisation_id)  # current state: org deleted
    with pytest.raises(BackupError, match="checksum mismatch"):
        await restore_backup(settings, backup)
    async with owner_engine.connect() as conn:
        still_gone = (
            await conn.execute(
                text("SELECT count(*) FROM organisation WHERE id = :o"),
                {"o": org.organisation_id},
            )
        ).scalar_one()
    assert still_gone == 0  # nothing was restored from a damaged backup


@pytest.mark.parametrize("bad_path", ["../outside.txt", "/etc/passwd", "objects/../../escape", ""])
def test_manifest_paths_cannot_escape_the_backup_folder(tmp_path: Path, bad_path: str) -> None:
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "format_version": 1,
                "files": [{"path": bad_path, "sha256": "0" * 64, "size": 0}],
                "qdrant_collections": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(BackupError, match="unsafe path"):
        verify_backup(tmp_path)
