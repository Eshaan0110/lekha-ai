"""Back up and restore the whole LOCAL stack: PostgreSQL, Qdrant and object storage.

    python -m apps.scripts.backup create [--into backups]
    python -m apps.scripts.backup verify  backups/<stamp>
    python -m apps.scripts.backup restore backups/<stamp> --yes

A backup is one folder:

    backups/<UTC stamp>/
        manifest.json         what was saved, with the SHA-256 and size of every file
        postgres.dump         pg_dump custom format (schema, data, grants, audit log)
        qdrant/<name>.snapshot one Qdrant snapshot per collection
        objects/<key>          every object in the document bucket, under its own key

Restore verifies every file against the manifest *before* changing anything, then restores
PostgreSQL, Qdrant and objects in that order. Objects or collections that exist now but
not in the backup are reported, not deleted.

Local development only. Production uses managed, continuous backups (RDS snapshots and
point-in-time recovery, Qdrant snapshots to S3, S3 versioning), set up with the
infrastructure. Backups contain document data: keep them as sensitive as the data itself.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import mimetypes
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import httpx

from lekha.core.config import Settings
from lekha.memory.s3_store import S3ObjectStore

FORMAT_VERSION = 1
ROOT = Path(__file__).resolve().parents[2]
COMPOSE = [
    "docker", "compose",
    "--env-file", str(ROOT / ".env"),
    "-f", str(ROOT / "infra" / "local" / "docker-compose.yml"),
]  # fmt: skip
CONTAINER_TMP = "/tmp/lekha-backup.dump"  # noqa: S108 - path inside the Postgres container
QDRANT_TIMEOUT = httpx.Timeout(60.0, connect=5.0)


class BackupError(RuntimeError):
    """The backup or restore could not be completed safely."""


@dataclass(frozen=True, slots=True)
class ManifestFile:
    path: str  # relative to the backup folder, always with "/" separators
    sha256: str
    size: int


# --- helpers -------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _record(folder: Path, path: Path) -> ManifestFile:
    return ManifestFile(
        path=path.relative_to(folder).as_posix(),
        sha256=_sha256_file(path),
        size=path.stat().st_size,
    )


def _compose(*args: str) -> None:
    """Run a docker compose command; never echo its output (it may contain data)."""
    result = subprocess.run([*COMPOSE, *args], capture_output=True, check=False, timeout=600)  # noqa: S603
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", "replace").strip().splitlines()
        raise BackupError(f"docker compose {args[0]} failed: {stderr[-1] if stderr else ''}")


def _safe_child(folder: Path, relative: str) -> Path:
    """Resolve a manifest path inside the backup folder; refuse anything that escapes it."""
    parts = PurePosixPath(relative).parts
    if not parts or any(part in ("..", "") for part in parts) or relative.startswith("/"):
        raise BackupError(f"unsafe path in manifest: {relative!r}")
    resolved = (folder / Path(*parts)).resolve()
    if folder.resolve() not in resolved.parents:
        raise BackupError(f"unsafe path in manifest: {relative!r}")
    return resolved


class _Qdrant:
    def __init__(self, settings: Settings) -> None:
        self.client = httpx.Client(
            base_url=str(settings.qdrant_url).rstrip("/"),
            headers={"api-key": settings.qdrant_api_key.get_secret_value()},
            timeout=QDRANT_TIMEOUT,
        )

    def collections(self) -> list[str]:
        response = self.client.get("/collections")
        response.raise_for_status()
        return sorted(c["name"] for c in response.json()["result"]["collections"])

    def snapshot_to(self, collection: str, target: Path) -> None:
        created = self.client.post(f"/collections/{collection}/snapshots", params={"wait": "true"})
        created.raise_for_status()
        name = created.json()["result"]["name"]
        try:
            with self.client.stream("GET", f"/collections/{collection}/snapshots/{name}") as resp:
                resp.raise_for_status()
                with target.open("wb") as handle:
                    for chunk in resp.iter_bytes():
                        handle.write(chunk)
        finally:  # do not leave snapshot files accumulating inside Qdrant
            self.client.delete(f"/collections/{collection}/snapshots/{name}")

    def restore_from(self, collection: str, source: Path) -> None:
        with source.open("rb") as handle:
            response = self.client.post(
                f"/collections/{collection}/snapshots/upload",
                params={"priority": "snapshot", "wait": "true"},
                files={"snapshot": (source.name, handle, "application/octet-stream")},
            )
        response.raise_for_status()

    def close(self) -> None:
        self.client.close()


# --- create --------------------------------------------------------------------------


async def create_backup(settings: Settings, into: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    final = into / stamp
    # Written under a ".partial" name and renamed only when complete, so an interrupted
    # backup can never be mistaken for a usable one.
    folder = into / f"{stamp}.partial"
    folder.mkdir(parents=True)
    files: list[ManifestFile] = []

    # 1. PostgreSQL first: the system of record. Objects added after this are harmless
    #    orphans; nothing references them.
    dump = folder / "postgres.dump"
    _compose(
        "exec", "-T", "postgres", "pg_dump", "-U", settings.postgres_user,
        "-d", settings.postgres_db, "--format=custom", f"--file={CONTAINER_TMP}",
    )  # fmt: skip
    _compose("cp", f"postgres:{CONTAINER_TMP}", str(dump))
    _compose("exec", "-T", "postgres", "rm", "-f", CONTAINER_TMP)
    files.append(_record(folder, dump))

    # 2. Qdrant: one snapshot per collection.
    qdrant = _Qdrant(settings)
    try:
        collections = qdrant.collections()
        for name in collections:
            target = folder / "qdrant" / f"{name}.snapshot"
            target.parent.mkdir(exist_ok=True)
            qdrant.snapshot_to(name, target)
            files.append(_record(folder, target))
    finally:
        qdrant.close()

    # 3. Object storage: every object, stored under its own key.
    store = S3ObjectStore.from_settings(settings)
    keys = await store.list_keys()
    for key in keys:
        data, _ = await store.get(key)
        target = _safe_child(folder / "objects", key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        files.append(_record(folder, target))

    manifest = {
        "format_version": FORMAT_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "postgres_database": settings.postgres_db,
        "bucket": settings.s3_bucket,
        "qdrant_collections": collections,
        "object_count": len(keys),
        "files": [asdict(f) for f in files],
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    folder.rename(final)
    return final


# --- verify and restore ----------------------------------------------------------------


def verify_backup(folder: Path) -> dict[str, Any]:
    """Check every file against the manifest. Raises BackupError on any problem."""
    manifest_path = folder / "manifest.json"
    if not manifest_path.is_file():
        raise BackupError("manifest.json is missing")
    manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format_version") != FORMAT_VERSION:
        raise BackupError("unsupported backup format")
    listed = set()
    for entry in manifest["files"]:
        path = _safe_child(folder, entry["path"])
        listed.add(path)
        if not path.is_file():
            raise BackupError(f"missing file: {entry['path']}")
        if path.stat().st_size != entry["size"] or _sha256_file(path) != entry["sha256"]:
            raise BackupError(f"checksum mismatch: {entry['path']}")
    if not any(PurePosixPath(e["path"]).name == "postgres.dump" for e in manifest["files"]):
        raise BackupError("the backup has no database dump")
    allowed = listed | {manifest_path.resolve()}
    extra = [p for p in folder.rglob("*") if p.is_file() and p.resolve() not in allowed]
    if extra:
        raise BackupError(f"{len(extra)} unexpected file(s) in the backup folder")
    return manifest


async def restore_backup(settings: Settings, folder: Path) -> list[str]:
    """Restore everything in ``folder``. Returns warnings (things present now, not in backup)."""
    manifest = verify_backup(folder)  # nothing is touched unless every file checks out
    warnings: list[str] = []

    _compose("cp", str(folder / "postgres.dump"), f"postgres:{CONTAINER_TMP}")
    try:
        _compose(
            "exec", "-T", "postgres", "pg_restore", "-U", settings.postgres_user,
            "-d", settings.postgres_db, "--clean", "--if-exists", "--single-transaction",
            CONTAINER_TMP,
        )  # fmt: skip
    finally:
        _compose("exec", "-T", "postgres", "rm", "-f", CONTAINER_TMP)

    qdrant = _Qdrant(settings)
    try:
        for name in manifest["qdrant_collections"]:
            qdrant.restore_from(name, folder / "qdrant" / f"{name}.snapshot")
        extra_collections = set(qdrant.collections()) - set(manifest["qdrant_collections"])
        if extra_collections:
            warnings.append(f"{len(extra_collections)} Qdrant collection(s) not in the backup")
    finally:
        qdrant.close()

    store = S3ObjectStore.from_settings(settings)
    restored: set[str] = set()
    objects_dir = folder / "objects"
    for entry in manifest["files"]:
        if not entry["path"].startswith("objects/"):
            continue
        key = entry["path"].removeprefix("objects/")
        data = _safe_child(objects_dir, key).read_bytes()
        media_type = mimetypes.guess_type(key)[0] or "application/octet-stream"
        await store.put(key, data, media_type=media_type, sha256=hashlib.sha256(data).digest())
        restored.add(key)
    extra_objects = set(await store.list_keys()) - restored
    if extra_objects:
        warnings.append(f"{len(extra_objects)} object(s) in storage are not in the backup")
    return warnings


# --- command line ----------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="backup", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", help="write a new backup folder")
    create.add_argument("--into", type=Path, default=ROOT / "backups")
    verify = sub.add_parser("verify", help="check a backup folder against its manifest")
    verify.add_argument("folder", type=Path)
    restore = sub.add_parser("restore", help="REPLACE current data with a backup")
    restore.add_argument("folder", type=Path)
    restore.add_argument("--yes", action="store_true", help="confirm replacing current data")
    args = parser.parse_args(argv)
    settings = Settings()
    try:
        if args.command == "create":
            folder = asyncio.run(create_backup(settings, args.into))
            print(f"Backup written to {folder}")
        elif args.command == "verify":
            manifest = verify_backup(args.folder)
            print(f"OK: {len(manifest['files'])} files match the manifest.")
        else:
            if not args.yes:
                print("Restore REPLACES the current local data. Re-run with --yes to confirm.")
                return 2
            for warning in asyncio.run(restore_backup(settings, args.folder)):
                print(f"WARNING: {warning}")
            print(f"Restored {args.folder}")
    except BackupError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
