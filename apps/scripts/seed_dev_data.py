"""Seed a SYNTHETIC organisation for local development and tests. No real data.

Run with ``./dev.ps1 seed``. Sign in as any user below by sending ``X-Dev-User: dev|<name>``.

The data is a permission test matrix: every rule in permissions-v1 §3 has a case that
must allow and a case that must deny.

| User           | Roles                              | Can read      |
|----------------|------------------------------------|---------------|
| dev|asha       | viewer @ alpha                     | A1            |
| dev|meera      | viewer + legal @ alpha             | A1, A2, A5    |
| dev|ravi       | viewer @ beta                      | B1            |
| dev|chitra     | curator @ alpha (may upload)       | nothing       |
| dev|bina       | curator @ beta (may upload)        | nothing       |
| dev|tara       | audit_reader @ alpha               | nothing; reads Alpha's audit log |
| dev|hari       | approver @ alpha                   | nothing; approves Alpha's tickets |
| dev|uma        | approver @ beta                    | nothing; approves Beta's tickets |
| dev|kiran      | viewer @ alpha, grant EXPIRED      | nothing       |
| dev|nobody     | none                               | nothing       |
| dev|inactive   | viewer @ alpha, account inactive   | cannot sign in|
"""

from __future__ import annotations

import asyncio
import hashlib
import sys
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from db.engine import database_url
from db.schema import (
    AppUser,
    BusinessUnit,
    Document,
    DocumentAcl,
    DocumentFile,
    DocumentVersion,
    Organisation,
    PermissionSubject,
    Role,
    SourceSystem,
    UserRoleGrant,
)
from db.tenancy import delete_organisation_rows
from lekha.auth import (
    AccessLevel,
    AclSource,
    Classification,
    SubjectKind,
    role_badge,
    user_badge,
)
from lekha.core.config import Settings

ROLE_CLEARANCE = {
    "viewer": Classification.INTERNAL,
    "curator": Classification.INTERNAL,
    "admin": Classification.INTERNAL,
    "audit_reader": Classification.INTERNAL,
    "approver": Classification.INTERNAL,
    "legal": Classification.CONFIDENTIAL,
}


@dataclass
class SeedResult:
    organisation_id: uuid.UUID
    business_units: dict[str, uuid.UUID] = field(default_factory=dict)
    users: dict[str, uuid.UUID] = field(default_factory=dict)
    documents: dict[str, uuid.UUID] = field(default_factory=dict)
    # "viewer@bu-alpha" -> role badge key, for building guest lists in tests and demos
    role_badges: dict[str, str] = field(default_factory=dict)


class _Seeder:
    def __init__(self, session: AsyncSession, organisation_id: uuid.UUID, now: datetime) -> None:
        self.s = session
        self.org = organisation_id
        self.now = now
        self.result = SeedResult(organisation_id)
        self.roles: dict[str, uuid.UUID] = {}
        self.badges: dict[str, uuid.UUID] = {}  # badge key -> permission_subject id
        self.source_id = uuid.uuid4()
        self.current_versions: dict[uuid.UUID, uuid.UUID] = {}

    def add(self, obj: object) -> None:
        self.s.add(obj)

    def business_unit(self, code: str, name: str) -> uuid.UUID:
        bu_id = uuid.uuid4()
        self.add(BusinessUnit(id=bu_id, organisation_id=self.org, code=code, name=name))
        self.result.business_units[code] = bu_id
        return bu_id

    def user(self, name: str, *, active: bool = True) -> uuid.UUID:
        user_id = uuid.uuid4()
        self.add(
            AppUser(
                id=user_id,
                organisation_id=self.org,
                idp_subject=f"dev|{name}",
                display_name=f"{name.title()} (synthetic)",
                is_active=active,
            )
        )
        key = user_badge(user_id)
        self.badges[key] = uuid.uuid4()
        self.add(
            PermissionSubject(
                id=self.badges[key],
                organisation_id=self.org,
                kind=SubjectKind.USER,
                key=key,
                user_id=user_id,
            )
        )
        self.result.users[name] = user_id
        return user_id

    def grant(self, user: str, role: str, bu: str, *, expired: bool = False) -> None:
        self.add(
            UserRoleGrant(
                organisation_id=self.org,
                user_id=self.result.users[user],
                role_id=self.roles[role],
                business_unit_id=self.result.business_units[bu],
                expires_at=self.now - timedelta(days=1) if expired else None,
            )
        )

    def role_badge(self, role: str, bu: str) -> str:
        bu_id, role_id = self.result.business_units[bu], self.roles[role]
        key = role_badge(bu_id, role_id)
        if key not in self.badges:
            self.badges[key] = uuid.uuid4()
            self.add(
                PermissionSubject(
                    id=self.badges[key],
                    organisation_id=self.org,
                    kind=SubjectKind.ROLE,
                    key=key,
                    role_id=role_id,
                    business_unit_id=bu_id,
                )
            )
        self.result.role_badges[f"{role}@{bu}"] = key
        return key

    async def document(
        self,
        label: str,
        title: str,
        bu: str,
        classification: Classification,
        versions: list[list[tuple[str, datetime | None]]],
        *,
        deleted: bool = False,
    ) -> None:
        """``versions``: one guest list per version, oldest first; the last is current."""
        if not versions:
            raise ValueError("a document needs at least one version")
        doc_id = uuid.uuid4()
        self.add(
            Document(
                id=doc_id,
                organisation_id=self.org,
                business_unit_id=self.result.business_units[bu],
                source_system_id=self.source_id,
                canonical_key=f"synthetic:{label.lower()}",
                title=title,
                classification=classification,
                deleted_at=self.now if deleted else None,
            )
        )
        # document and document_version reference each other, so the ORM cannot order
        # these inserts by itself: write each layer explicitly before the next.
        await self.s.flush()
        for number, acl in enumerate(versions, start=1):
            file_id, version_id = uuid.uuid4(), uuid.uuid4()
            body = f"{title} v{number} (synthetic)".encode()
            self.add(
                DocumentFile(
                    id=file_id,
                    organisation_id=self.org,
                    storage_key=f"synthetic/{self.org}/{doc_id}/v{number}.txt",
                    sha256=hashlib.sha256(body).digest(),
                    size_bytes=len(body),
                    media_type="text/plain",
                    original_filename=f"{label.lower()}-v{number}.txt",
                )
            )
            self.add(
                DocumentVersion(
                    id=version_id,
                    organisation_id=self.org,
                    document_id=doc_id,
                    version_number=number,
                    file_id=file_id,
                    published_at=self.now,
                )
            )
            await self.s.flush()
            for key, expires_at in acl:
                self.add(
                    DocumentAcl(
                        organisation_id=self.org,
                        document_version_id=version_id,
                        subject_id=self.badges[key],
                        access_level=AccessLevel.READ,
                        source=AclSource.EXPLICIT,
                        expires_at=expires_at,
                    )
                )
        self.result.documents[label] = doc_id
        self.current_versions[doc_id] = version_id


async def seed_synthetic_organisation(
    session: AsyncSession, organisation_id: uuid.UUID, *, now: datetime | None = None
) -> SeedResult:
    """Insert the matrix in one transaction (caller commits). Requires the owner account."""
    sd = _Seeder(session, organisation_id, now or datetime.now(UTC))
    sd.add(
        Organisation(id=organisation_id, name=f"{SYNTHETIC_NAME_PREFIX}{str(organisation_id)[:8]}")
    )
    await session.flush()
    sd.business_unit("bu-alpha", "Alpha Unit (synthetic)")
    sd.business_unit("bu-beta", "Beta Unit (synthetic)")
    for name, clearance in ROLE_CLEARANCE.items():
        sd.roles[name] = uuid.uuid4()
        sd.add(
            Role(
                id=sd.roles[name],
                organisation_id=organisation_id,
                name=name,
                max_classification=clearance,
            )
        )
    sd.add(
        SourceSystem(id=sd.source_id, organisation_id=organisation_id, code="upload", name="Upload")
    )
    for name in (
        "asha",
        "meera",
        "ravi",
        "chitra",
        "bina",
        "tara",
        "hari",
        "uma",
        "kiran",
        "nobody",
    ):
        sd.user(name)
    sd.user("inactive", active=False)
    await session.flush()

    sd.grant("asha", "viewer", "bu-alpha")
    sd.grant("meera", "viewer", "bu-alpha")
    sd.grant("meera", "legal", "bu-alpha")
    sd.grant("ravi", "viewer", "bu-beta")
    sd.grant("kiran", "viewer", "bu-alpha", expired=True)
    sd.grant("chitra", "curator", "bu-alpha")
    sd.grant("bina", "curator", "bu-beta")
    sd.grant("tara", "audit_reader", "bu-alpha")
    sd.grant("hari", "approver", "bu-alpha")
    sd.grant("uma", "approver", "bu-beta")
    sd.grant("inactive", "viewer", "bu-alpha")
    alpha_viewer = sd.role_badge("viewer", "bu-alpha")
    alpha_legal = sd.role_badge("legal", "bu-alpha")
    beta_viewer = sd.role_badge("viewer", "bu-beta")
    sd.role_badge("curator", "bu-alpha")
    sd.role_badge("curator", "bu-beta")
    await session.flush()

    yesterday = sd.now - timedelta(days=1)
    asha = user_badge(sd.result.users["asha"])
    internal, confidential = Classification.INTERNAL, Classification.CONFIDENTIAL
    await sd.document("A1", "Alpha leave policy", "bu-alpha", internal, [[(alpha_viewer, None)]])
    # Viewers are on the guest list but lack clearance: only legal may read.
    await sd.document(
        "A2",
        "Alpha legal memo",
        "bu-alpha",
        confidential,
        [[(alpha_viewer, None), (alpha_legal, None)]],
    )
    await sd.document(
        "A3", "Alpha draft (expired share)", "bu-alpha", internal, [[(asha, yesterday)]]
    )
    await sd.document(
        "A4", "Alpha withdrawn policy", "bu-alpha", internal, [[(alpha_viewer, None)]], deleted=True
    )
    # Version 1 was for viewers; the current version 2 is restricted to legal.
    await sd.document(
        "A5",
        "Alpha travel policy",
        "bu-alpha",
        internal,
        [[(alpha_viewer, None)], [(alpha_legal, None)]],
    )
    await sd.document("B1", "Beta leave policy", "bu-beta", internal, [[(beta_viewer, None)]])
    # Mis-tagged with an alpha badge: alpha viewers must still not see a beta document.
    await sd.document("B2", "Beta mis-tagged notice", "bu-beta", internal, [[(alpha_viewer, None)]])
    await session.flush()

    for doc_id, version_id in sd.current_versions.items():
        await session.execute(
            update(Document).where(Document.id == doc_id).values(current_version_id=version_id)
        )
    return sd.result


SYNTHETIC_NAME_PREFIX = "Synthetic Dev Org "


async def main_async(settings: Settings, *, reset: bool = False) -> None:
    if settings.dev_auth_organisation_id is None:
        raise SystemExit("LEKHA_DEV_AUTH_ORGANISATION_ID is not set; run ./dev.ps1 setup")
    engine = create_async_engine(
        database_url(settings, owner=True), poolclass=NullPool, hide_parameters=True
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    org_id = settings.dev_auth_organisation_id
    try:
        async with factory.begin() as session:
            existing = await session.scalar(
                select(Organisation.name).where(Organisation.id == org_id)
            )
            if existing is not None and not reset:
                print(f"Synthetic organisation {org_id} already exists; nothing to do.")
                return
            if existing is not None:
                # Guard: never delete an organisation this script did not create.
                if not existing.startswith(SYNTHETIC_NAME_PREFIX):
                    raise SystemExit(f"Refusing to reset non-synthetic organisation {org_id}.")
                await delete_organisation_rows(await session.connection(), org_id)
            await seed_synthetic_organisation(session, org_id)
    finally:
        await engine.dispose()
    print(f"Seeded synthetic organisation {org_id}. Sign in with X-Dev-User: dev|asha")


def main() -> None:
    asyncio.run(main_async(Settings(), reset="--reset" in sys.argv[1:]))


if __name__ == "__main__":
    main()
