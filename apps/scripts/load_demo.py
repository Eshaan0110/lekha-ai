"""Load the SYNTHETIC demo corpus into the dev organisation, through the running API.

Usage (API and worker running, dev organisation seeded):

    uv run python -m apps.scripts.load_demo

Everything goes through the public API as the synthetic curators (chitra for Alpha, bina
for Beta), exactly like a real upload: validation, permissions, audit, ingestion. Guest
lists name users directly: Alpha policies are shared with asha and meera, restricted ones
with meera only, Beta ones with ravi. Safe to re-run: documents already loaded are skipped.
"""

from __future__ import annotations

import io
import json
import sys
import time
import zipfile
from datetime import UTC, datetime
from typing import Any

import docx
import httpx

from apps.scripts.demo_corpus import DOCS, GLOSSARY, MEETINGS, GoldenDoc
from lekha.auth import user_badge

API = "http://127.0.0.1:8000/api/v1"
GUESTS = {"alpha": ("asha", "meera"), "meera": ("meera",), "beta": ("ravi",)}
WAIT_SECONDS = 600


def _as(user: str) -> dict[str, str]:
    return {"X-Dev-User": f"dev|{user}"}


def _docx(doc: GoldenDoc) -> bytes:
    """Byte-identical on every run, so re-running the loader skips documents already loaded
    (an identical file is refused as a duplicate version)."""
    document = docx.Document()
    fixed = datetime(2026, 1, 1, tzinfo=UTC)
    document.core_properties.created = fixed
    document.core_properties.modified = fixed
    document.core_properties.last_printed = fixed
    document.add_heading(doc.heading, level=1)
    for sentence in doc.sentences:
        document.add_paragraph(sentence)
    buffer = io.BytesIO()
    document.save(buffer)
    return _fixed_zip_times(buffer.getvalue())


def _fixed_zip_times(data: bytes) -> bytes:
    """A .docx is a zip whose entries carry the time they were written; pin them."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, "w") as target:
        for item in source.infolist():
            pinned = zipfile.ZipInfo(item.filename, date_time=(2026, 1, 1, 0, 0, 0))
            pinned.compress_type = zipfile.ZIP_DEFLATED
            target.writestr(pinned, source.read(item.filename))
    return out.getvalue()


def _job_reader(key: str) -> str:
    """Someone allowed to see the job's status: a curator for documents, the organiser for
    meetings (curators can't see meetings they weren't in)."""
    if key.startswith("meeting:"):
        return next(m.organiser for m in MEETINGS if f"meeting:{m.key}" == key)
    return "bina" if key.startswith("B") else "chitra"


def _me(client: httpx.Client, user: str) -> dict[str, Any]:  # Any: JSON body
    response = client.get(f"{API}/me", headers=_as(user))
    response.raise_for_status()
    body: dict[str, Any] = response.json()
    return body


def main() -> int:
    client = httpx.Client(timeout=60)
    try:
        client.get("http://127.0.0.1:8000/health/live").raise_for_status()
    except httpx.HTTPError:
        print("The API is not running on 127.0.0.1:8000. Start it with ./dev.ps1 run.")
        return 1
    user_ids = {name: _me(client, name)["user_id"] for name in ("asha", "meera", "ravi")}
    units = {
        "bu-alpha": _me(client, "chitra")["business_units"][0]["business_unit_id"],
        "bu-beta": _me(client, "bina")["business_units"][0]["business_unit_id"],
    }

    jobs: dict[str, str] = {}
    skipped = 0
    for doc in DOCS:
        uploader = "bina" if doc.business_unit == "bu-beta" else "chitra"
        metadata = {
            "title": doc.title,
            "canonical_key": f"synthetic:demo-{doc.key.lower()}",
            "business_unit_id": units[doc.business_unit],
            "classification": "internal",
            "acl_subjects": [user_badge(user_ids[name]) for name in GUESTS[doc.readers]],
        }
        response = client.post(
            f"{API}/documents",
            headers=_as(uploader),
            files={"file": (f"{doc.key}.docx", _docx(doc))},
            data={"metadata": json.dumps(metadata)},
        )
        if response.status_code == 409:  # identical file already loaded
            skipped += 1
            continue
        response.raise_for_status()
        jobs[doc.key] = response.json()["ingestion_job_id"]
    print(f"Uploaded {len(jobs)} documents ({skipped} already loaded).")

    all_ids = {name: _me(client, name)["user_id"] for name in ("asha", "meera", "ravi", "bina")}
    meetings = 0
    for meeting in MEETINGS:
        organiser_unit = _me(client, meeting.organiser)["business_units"][0]["business_unit_id"]
        metadata = {
            "title": meeting.title,
            "business_unit_id": organiser_unit,
            "started_at": meeting.started_at,
            "participant_user_ids": [all_ids[p] for p in meeting.participants],
            "external_id": f"demo:{meeting.key}",
        }
        response = client.post(
            f"{API}/meetings",
            headers=_as(meeting.organiser),
            files={"file": (meeting.filename, meeting.transcript.encode("utf-8"))},
            data={"metadata": json.dumps(metadata)},
        )
        if response.status_code == 409:
            continue
        response.raise_for_status()
        jobs[f"meeting:{meeting.key}"] = response.json()["ingestion_job_id"]
        meetings += 1
    print(f"Imported {meetings} meetings.")

    terms = 0
    for unit, curator, shared, canonical, aliases, description in GLOSSARY:
        response = client.post(
            f"{API}/glossary",
            headers=_as(curator),
            json={
                "business_unit_id": units[unit],
                "canonical": canonical,
                "aliases": list(aliases),
                "description": description,
                "shared": shared,
            },
        )
        if response.status_code == 409:  # already loaded
            continue
        response.raise_for_status()
        terms += 1
    print(f"Added {terms} glossary entries.")

    deadline = time.monotonic() + WAIT_SECONDS
    pending = dict(jobs)
    while pending and time.monotonic() < deadline:
        for key, job_id in list(pending.items()):
            uploader = _job_reader(key)
            status = client.get(f"{API}/ingestion-jobs/{job_id}", headers=_as(uploader)).json()
            if status["status"] in {"succeeded", "failed"}:
                if status["status"] == "failed":
                    print(f"  {key}: failed ({status['error_code']})")
                del pending[key]
        if pending:
            time.sleep(2)
    if pending:
        print(f"{len(pending)} still processing. Is the worker running (./dev.ps1 worker)?")
        return 1
    print("All documents processed. Open the web app and sign in as asha, meera or ravi.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
