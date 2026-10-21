"""Qdrant adapter for chunk vectors (REST over httpx; no extra SDK).

Every point carries the plan's payload: tenant, business unit, classification (name and
rank), ``allowed_subjects`` copied from the version's guest list, language, current-version
flag and IDs. ``search`` refuses any filter that does not constrain the tenant and the
allowed subjects (the plan's "server-side guard that rejects any search request missing
tenant_id and allowed_subjects").
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from lekha.retrieval.filters import filter_is_permission_scoped

PAYLOAD_INDEXES = {
    "tenant_id": "keyword",
    "business_unit_id": "keyword",
    "allowed_subjects": "keyword",
    "is_current_version": "bool",
    "classification_rank": "integer",
    "language": "keyword",
    "document_version_id": "keyword",
}


class UnscopedSearchError(PermissionError):
    """A search was attempted without the mandatory permission filter."""


class VectorIndexError(RuntimeError):
    """The vector index could not complete the operation."""


@dataclass(frozen=True, slots=True)
class IndexedPoint:
    id: uuid.UUID
    vector: list[float]
    payload: dict[str, Any]  # Any: JSON payload


@dataclass(frozen=True, slots=True)
class Hit:
    chunk_id: uuid.UUID
    score: float
    payload: dict[str, Any]  # Any: JSON payload


class QdrantIndex:
    def __init__(self, client: httpx.AsyncClient, collection: str, dimensions: int) -> None:
        self._client = client
        self._collection = collection
        self._dimensions = dimensions

    @classmethod
    def connect(cls, url: str, api_key: str, collection: str, dimensions: int) -> QdrantIndex:
        client = httpx.AsyncClient(
            base_url=url.rstrip("/"),
            headers={"api-key": api_key},
            timeout=httpx.Timeout(15.0, connect=3.0),
        )
        return cls(client, collection, dimensions)

    async def close(self) -> None:
        await self._client.aclose()

    async def ensure_collection(self) -> None:
        """Create the collection if missing; refuse one sized for another embedding model."""
        response = await self._send(self._client.get(f"/collections/{self._collection}"))
        if response.status_code == 404:
            created = await self._send(
                self._client.put(
                    f"/collections/{self._collection}",
                    json={"vectors": {"size": self._dimensions, "distance": "Cosine"}},
                )
            )
            if created.status_code == 409:
                # Another worker created it a moment ago (seen live with parallel uploads).
                response = await self._ok(self._client.get(f"/collections/{self._collection}"))
            else:
                _raise_for_status(created)
                for field, schema in PAYLOAD_INDEXES.items():
                    await self._ok(
                        self._client.put(
                            f"/collections/{self._collection}/index",
                            params={"wait": "true"},
                            json={"field_name": field, "field_schema": schema},
                        )
                    )
                return
        _raise_for_status(response)
        size = response.json()["result"]["config"]["params"]["vectors"]["size"]
        if size != self._dimensions:
            # Vectors from different embedding models can't be compared; never mix them.
            raise VectorIndexError(
                f"Collection {self._collection} holds {size}-dimension vectors but the "
                f"embedder makes {self._dimensions}; use a separate collection per model."
            )

    async def upsert(self, points: Sequence[IndexedPoint]) -> None:
        if not points:
            return
        body = {
            "points": [{"id": str(p.id), "vector": p.vector, "payload": p.payload} for p in points]
        }
        await self._ok(
            self._client.put(
                f"/collections/{self._collection}/points", params={"wait": "true"}, json=body
            )
        )

    async def delete_version(self, document_version_id: uuid.UUID) -> None:
        await self._delete_where(_match("document_version_id", document_version_id))

    async def delete_document(self, document_id: uuid.UUID) -> None:
        """Remove every point of every version of a document (deletion workflow)."""
        await self._delete_where(_match("document_id", document_id))

    async def mark_current_version(
        self, document_id: uuid.UUID, current_version_id: uuid.UUID
    ) -> None:
        """Flag one version's points as current and every other version's as not.
        Idempotent, so a retried job converges on the database's answer."""
        others = {
            "must": [_match("document_id", document_id)],
            "must_not": [_match("document_version_id", current_version_id)],
        }
        await self._set_payload(others, {"is_current_version": False})
        await self._set_payload(
            {"must": [_match("document_version_id", current_version_id)]},
            {"is_current_version": True},
        )

    async def _set_payload(self, points_filter: dict[str, Any], payload: dict[str, Any]) -> None:
        await self._ok(
            self._client.post(
                f"/collections/{self._collection}/points/payload",
                params={"wait": "true"},
                json={"payload": payload, "filter": points_filter},
            )
        )

    async def _delete_where(self, condition: dict[str, Any]) -> None:
        await self._ok(
            self._client.post(
                f"/collections/{self._collection}/points/delete",
                params={"wait": "true"},
                json={"filter": {"must": [condition]}},
            )
        )

    async def search(
        self, vector: Sequence[float], query_filter: dict[str, Any], *, limit: int
    ) -> list[Hit]:
        if not filter_is_permission_scoped(query_filter):
            raise UnscopedSearchError("refusing a search without tenant and permission filters")
        response = await self._send(
            self._client.post(
                f"/collections/{self._collection}/points/search",
                json={
                    "vector": list(vector),
                    "filter": query_filter,
                    "limit": limit,
                    "with_payload": True,
                },
            )
        )
        if response.status_code == 404:
            return []  # nothing indexed yet: the collection is created by the first upload
        _raise_for_status(response)
        return [
            Hit(chunk_id=uuid.UUID(str(item["id"])), score=item["score"], payload=item["payload"])
            for item in response.json()["result"]
        ]

    @staticmethod
    async def _ok(call: Any) -> httpx.Response:  # Any: a pending httpx request coroutine
        response = await QdrantIndex._send(call)
        _raise_for_status(response)
        return response

    @staticmethod
    async def _send(call: Any) -> httpx.Response:  # Any: a pending httpx request coroutine
        """Await a request; network failures become VectorIndexError, statuses are kept."""
        try:
            response: httpx.Response = await call
        except httpx.HTTPError as exc:
            raise VectorIndexError(f"vector index request failed: {type(exc).__name__}") from None
        return response


def _match(key: str, value: uuid.UUID) -> dict[str, Any]:
    return {"key": key, "match": {"value": str(value)}}


def _raise_for_status(response: httpx.Response) -> None:
    if response.is_error:
        raise VectorIndexError(f"vector index request failed: HTTP {response.status_code}")
