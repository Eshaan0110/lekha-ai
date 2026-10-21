"""Uploads end to end: real database, real object store, real permission rules."""

from __future__ import annotations

import hashlib
import io
import json
import uuid
import zipfile
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from apps.api.main import create_app
from apps.scripts.seed_dev_data import SeedResult, seed_synthetic_organisation
from lekha.auth import user_badge
from lekha.core.config import AuthMode, Settings
from lekha.memory import ObjectStore, ObjectStoreError
from lekha.memory.s3_store import S3ObjectStore
from tests.integration.support import delete_tenant

pytestmark = pytest.mark.integration

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def _docx(*, macros: bool = False) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<w:document/>")
        if macros:
            archive.writestr("word/vbaProject.bin", b"macro")
    return buffer.getvalue()


class RecordingStore:
    """Wraps the real store and remembers what was written and deleted."""

    def __init__(self, inner: ObjectStore) -> None:
        self.inner = inner
        self.puts: list[str] = []
        self.deletes: list[str] = []

    async def put(self, key: str, data: bytes, *, media_type: str, sha256: bytes) -> None:
        await self.inner.put(key, data, media_type=media_type, sha256=sha256)
        self.puts.append(key)

    async def delete(self, key: str) -> None:
        await self.inner.delete(key)
        self.deletes.append(key)

    async def check(self) -> None:
        await self.inner.check()

    async def get(self, key: str) -> tuple[bytes, str]:
        return await self.inner.get(key)


class BrokenStore(RecordingStore):
    async def put(self, key: str, data: bytes, *, media_type: str, sha256: bytes) -> None:
        raise ObjectStoreError("object store put failed: ServiceUnavailable")


@pytest.fixture
async def org(owner_engine: AsyncEngine) -> AsyncIterator[SeedResult]:
    factory = async_sessionmaker(owner_engine, expire_on_commit=False)
    async with factory.begin() as session:
        result = await seed_synthetic_organisation(session, uuid.uuid4())
    yield result
    await delete_tenant(owner_engine, result.organisation_id)


@pytest.fixture
def store(settings: Settings) -> RecordingStore:
    return RecordingStore(S3ObjectStore.from_settings(settings))


def _app_settings(settings: Settings, org: SeedResult, **overrides: object) -> Settings:
    return settings.model_copy(
        update={
            "auth_mode": AuthMode.DEV,
            "dev_auth_organisation_id": org.organisation_id,
            **overrides,
        }
    )


@pytest.fixture
def client(settings: Settings, org: SeedResult, store: RecordingStore) -> Iterator[TestClient]:
    app = create_app(_app_settings(settings, org), object_store=store)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def _upload(
    client: TestClient,
    org: SeedResult,
    *,
    user: str = "chitra",
    name: str = "handbook.pdf",
    data: bytes = PDF,
    metadata: dict[str, object] | None = None,
) -> tuple[int, dict[str, Any]]:  # Any: an arbitrary JSON response body
    body: dict[str, object] = {
        "title": "Synthetic handbook",
        "canonical_key": f"synthetic:{uuid.uuid4().hex[:10]}",
        "business_unit_id": str(org.business_units["bu-alpha"]),
        "classification": "internal",
        "acl_subjects": [user_badge(org.users["asha"])],
    }
    body.update(metadata or {})
    response = client.post(
        "/api/v1/documents",
        headers={"X-Dev-User": f"dev|{user}"},
        files={"file": (name, data, "application/octet-stream")},
        data={"metadata": json.dumps(body)},
    )
    return response.status_code, response.json()


async def test_curator_upload_is_stored_recorded_and_visible_to_its_guests(
    client: TestClient, org: SeedResult, store: RecordingStore, owner_engine: AsyncEngine
) -> None:
    status, body = _upload(client, org)
    assert status == 201, body
    document_id = body["document_id"]
    assert body["file"] == {
        "filename": "handbook.pdf",
        "media_type": "application/pdf",
        "size_bytes": len(PDF),
        "sha256": hashlib.sha256(PDF).hexdigest(),
    }
    assert uuid.UUID(body["ingestion_job_id"])  # a real job, handed to the worker

    # Stored under the organisation / business-unit prefix, nothing else.
    [key] = store.puts
    assert key.startswith(f"{org.organisation_id}/{org.business_units['bu-alpha']}/{document_id}/")

    async with owner_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT f.storage_key, f.sha256, v.version_number, d.current_version_id = v.id"
                    " FROM document d JOIN document_version v ON v.document_id = d.id"
                    " JOIN document_file f ON f.id = v.file_id WHERE d.id = :d"
                ),
                {"d": document_id},
            )
        ).one()
        audit = (
            await conn.execute(
                text(
                    "SELECT outcome::text, target_type, target_id::text FROM audit_event"
                    " WHERE action = 'POST /api/v1/documents' AND target_id = :d"
                ),
                {"d": document_id},
            )
        ).one()
    assert (row[0], row[1], row[2], row[3]) == (key, hashlib.sha256(PDF).digest(), 1, True)
    assert tuple(audit) == ("success", "document", document_id)

    # The guest list decides who sees it: asha is on it, ravi (other unit) is not.
    asha = client.get(f"/api/v1/documents/{document_id}", headers={"X-Dev-User": "dev|asha"})
    ravi = client.get(f"/api/v1/documents/{document_id}", headers={"X-Dev-User": "dev|ravi"})
    assert (asha.status_code, ravi.status_code) == (200, 404)


@pytest.mark.parametrize(
    ("case", "user", "name", "data", "metadata", "status"),
    [
        ("viewer may not upload", "asha", "a.pdf", PDF, {}, 403),
        ("curator only in alpha", "chitra", "a.pdf", PDF, {"business_unit_id": "beta"}, 403),
        ("confidential gate", "chitra", "a.pdf", PDF, {"classification": "confidential"}, 403),
        ("macro-enabled Word", "chitra", "m.docx", _docx(macros=True), {}, 415),
        ("program renamed .pdf", "chitra", "x.pdf", b"MZ\x90\x00program", {}, 415),
        ("text file", "chitra", "notes.txt", b"hello", {}, 415),
        ("empty file", "chitra", "e.pdf", b"", {}, 422),
        ("unknown guest", "chitra", "a.pdf", PDF, {"acl_subjects": ["group:none"]}, 422),
        ("bad metadata", "chitra", "a.pdf", PDF, {"classification": "top-secret"}, 422),
        ("extra metadata field", "chitra", "a.pdf", PDF, {"owner": "me"}, 422),
    ],
    ids=lambda value: value if isinstance(value, str) and " " in value else "",
)
async def test_rejected_uploads_store_nothing(
    client: TestClient,
    org: SeedResult,
    store: RecordingStore,
    owner_engine: AsyncEngine,
    case: str,
    user: str,
    name: str,
    data: bytes,
    metadata: dict[str, object],
    status: int,
) -> None:
    if metadata.get("business_unit_id") == "beta":
        metadata = {**metadata, "business_unit_id": str(org.business_units["bu-beta"])}
    got, body = _upload(client, org, user=user, name=name, data=data, metadata=metadata)
    assert got == status, (case, body)
    assert store.puts == [], case
    async with owner_engine.connect() as conn:
        count = (
            await conn.execute(
                text("SELECT count(*) FROM document WHERE organisation_id = :o"),
                {"o": org.organisation_id},
            )
        ).scalar_one()
    assert count == len(org.documents), case


def test_duplicate_canonical_key_is_a_conflict(client: TestClient, org: SeedResult) -> None:
    assert _upload(client, org, metadata={"canonical_key": "synthetic:dup"})[0] == 201
    status, body = _upload(client, org, metadata={"canonical_key": "synthetic:dup"})
    assert status == 409
    assert body["error"]["code"] == "conflict"


def test_oversized_upload_is_refused_before_the_body_is_read(
    settings: Settings, org: SeedResult, store: RecordingStore
) -> None:
    app = create_app(_app_settings(settings, org, upload_max_bytes=1024), object_store=store)
    with TestClient(app, raise_server_exceptions=False) as small_client:
        status, body = _upload(small_client, org, data=PDF + b" " * (200 * 1024))
    assert status == 413
    assert body["error"]["code"] == "payload_too_large"
    assert store.puts == []


async def test_storage_outage_fails_cleanly_with_no_rows(
    settings: Settings, org: SeedResult, owner_engine: AsyncEngine
) -> None:
    broken = BrokenStore(S3ObjectStore.from_settings(settings))
    with TestClient(
        create_app(_app_settings(settings, org), object_store=broken),
        raise_server_exceptions=False,
    ) as broken_client:
        status, body = _upload(broken_client, org, metadata={"canonical_key": "synthetic:outage"})
    assert status == 503
    assert body["error"]["code"] == "upstream_unavailable"
    async with owner_engine.connect() as conn:
        found = (
            await conn.execute(
                text("SELECT count(*) FROM document WHERE canonical_key = 'synthetic:outage'")
            )
        ).scalar_one()
    assert found == 0


class RacingStore(RecordingStore):
    """After storing, a concurrent upload claims the same canonical_key first."""

    def __init__(self, inner: ObjectStore, owner_engine: AsyncEngine, org: SeedResult) -> None:
        super().__init__(inner)
        self.owner_engine = owner_engine
        self.org = org

    async def put(self, key: str, data: bytes, *, media_type: str, sha256: bytes) -> None:
        await super().put(key, data, media_type=media_type, sha256=sha256)
        async with self.owner_engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO document (organisation_id, business_unit_id, source_system_id,"
                    " canonical_key, title, classification) SELECT organisation_id,"
                    " :bu, id, 'synthetic:race', 'Winner', 'internal' FROM source_system"
                    " WHERE organisation_id = :o"
                ),
                {"o": self.org.organisation_id, "bu": self.org.business_units["bu-alpha"]},
            )


async def test_losing_a_race_removes_the_stored_file(
    settings: Settings, org: SeedResult, owner_engine: AsyncEngine
) -> None:
    racing = RacingStore(S3ObjectStore.from_settings(settings), owner_engine, org)
    with TestClient(
        create_app(_app_settings(settings, org), object_store=racing),
        raise_server_exceptions=False,
    ) as race_client:
        status, _ = _upload(race_client, org, metadata={"canonical_key": "synthetic:race"})
    assert status == 409
    assert racing.puts == racing.deletes  # the orphaned file was cleaned up
    assert len(racing.puts) == 1


def test_readiness_checks_object_storage_credentials(settings: Settings, org: SeedResult) -> None:
    wrong = _app_settings(settings, org, s3_bucket="no-such-bucket-lekha")
    with TestClient(create_app(wrong)) as test_client:
        checks = test_client.get("/health/ready").json()["checks"]
    assert checks["object_storage"] == "unavailable"
