"""`StewardClient` over steward's REST `/v1` (design §8.3).

Requests go out on httpx directly rather than through the SDK's client: the SDK makes up a fresh
`Idempotency-Key` for every write and has no way to pass one in, while Banyan derives its keys from
the origin (`kb_propose:<message_id>`, backend/AGENTS.md rule 4) so that a worker retry hours later
replays the first answer. Responses are parsed with the SDK's generated models, so a contract change
in steward fails here. No retries: a request degrades (design §6.2), a worker job retries itself.
"""

import uuid
from collections.abc import Mapping
from typing import Any, Self

import httpx
from llm_steward_client import models as m

from banyan.steward.client import (
    ProposedEntry,
    RetrievalItem,
    RetrievalResult,
    Risk,
    StewardHealth,
    StewardRejected,
    StewardUnavailable,
)


class HttpStewardClient:
    """Two identities, two connections: `writer` carries the read/write key and does everything
    except the two calls steward reserves for reviewers, creating a collection and deprecating an
    entry, which go through `reviewer`. A key with review scope makes every entry it writes
    `verified`, so Banyan's model-written entries never go out with it (design §8.1)."""

    def __init__(self, writer: httpx.AsyncClient, reviewer: httpx.AsyncClient) -> None:
        self._writer = writer
        self._reviewer = reviewer

    @classmethod
    def connect(cls, base_url: str, *, api_key: str, review_key: str, timeout_s: float) -> Self:
        def http(key: str) -> httpx.AsyncClient:
            auth = {"Authorization": f"Bearer {key}"}
            return httpx.AsyncClient(base_url=base_url, timeout=timeout_s, headers=auth)

        return cls(http(api_key), http(review_key))

    async def aclose(self) -> None:
        await self._writer.aclose()
        await self._reviewer.aclose()

    async def health(self) -> StewardHealth:
        # The SDK has no health call; steward's /health needs no key, and a 503 carries its report.
        response = await _request(self._writer, "GET", "/health")
        try:
            status = response.json().get("status")
        except (ValueError, AttributeError):
            status = None
        return status if status in ("ok", "degraded") else "error"

    async def ensure_collection(
        self, name: str, *, review_policy: dict[str, str], idempotency_key: str
    ) -> str:
        listed = await _json(self._writer, "GET", "/v1/kb/collections")
        for collection in m.CollectionPage.model_validate(listed).items:
            if collection.name == name:
                return str(collection.id)
        # The caller's key (e.g. `collection_ensure:<topic_id>`): a retry replays one answer instead
        # of making two collections, and a re-run after a 4xx comes with a fresh key (design §4).
        created = await _json(
            self._reviewer,
            "POST",
            "/v1/kb/collections",
            json={"name": name, "review_policy": review_policy},
            idempotency_key=idempotency_key,
        )
        return str(m.CollectionOut.model_validate(created).id)

    async def create_entry(
        self,
        collection_id: str,
        *,
        title: str,
        content: str,
        tags: list[str],
        risk_level: Risk,
        idempotency_key: str,
    ) -> ProposedEntry:
        # No user_id: steward would ask that user about medium risk (design §8.1).
        body = {
            "collection_id": collection_id,
            "title": title,
            "content": content,
            "tags": tags,
            "risk_level": risk_level,
        }
        written = m.Written.model_validate(
            await _json(
                self._writer, "POST", "/v1/kb/entries", json=body, idempotency_key=idempotency_key
            )
        )
        return ProposedEntry(
            entry_id=str(written.entry.id) if written.entry else None,
            candidate_id=str(written.candidate.id) if written.candidate else None,
        )

    async def retrieve(
        self,
        query: str,
        *,
        user_id: str,
        collections: list[str] | None = None,
        tags: list[str] | None = None,
        include_memory: bool = True,
        top_k: int | None = None,
        token_budget: int | None = None,
        as_context: bool = False,
    ) -> RetrievalResult:
        optional = {
            "collections": collections,
            "filters": {"tags": tags} if tags else None,
            "top_k": top_k,
            "token_budget": token_budget,
        }
        body = {
            "query": query,
            "scope": {"user_id": user_id},
            "sources": ["memory", "kb"] if include_memory else ["kb"],
            "format": "context" if as_context else "items",
            **{k: v for k, v in optional.items() if v is not None},
        }
        out = m.RetrieveOut.model_validate(
            await _json(self._writer, "POST", "/v1/retrieve", json=body)
        )
        return RetrievalResult(
            retrieval_id=str(out.retrieval_id),
            items=[
                RetrievalItem(
                    id=str(item.id),
                    kind=item.kind,
                    status=item.status,
                    score=item.score,
                    title=item.title,
                    content=item.content,
                )
                for item in out.items
            ],
            context=out.context,
            degraded=list(out.degraded),
        )

    async def feedback(
        self,
        retrieval_id: str,
        item_id: str,
        *,
        helpful: bool,
        note: str | None = None,
        idempotency_key: str,
    ) -> None:
        body = {"item_id": _id(item_id), "helpful": helpful}
        if note is not None:
            body["note"] = note
        path = f"/v1/retrieve/{_id(retrieval_id)}/feedback"
        await _json(self._writer, "POST", path, json=body, idempotency_key=idempotency_key)

    async def deprecate_entry(self, entry_id: str, *, idempotency_key: str) -> None:
        # Steward takes no reason here; Banyan keeps it on its own job.
        path = f"/v1/kb/entries/{_id(entry_id)}/deprecate"
        await _json(self._reviewer, "POST", path, idempotency_key=idempotency_key)


def _id(value: str) -> str:
    """An id placed in a URL path is always a UUID, so it can never turn into another route."""
    return str(uuid.UUID(value))


async def _request(
    http: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    json: Any = None,
    idempotency_key: str | None = None,
) -> httpx.Response:
    headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
    try:
        return await http.request(method, path, json=json, headers=headers)
    except httpx.TransportError as exc:
        raise StewardUnavailable(f"{method} {path}: {type(exc).__name__}") from exc


async def _json(
    http: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    json: Any = None,
    idempotency_key: str | None = None,
) -> Any:
    """5xx is `StewardUnavailable` (retry later); 4xx is `StewardRejected` (final, design §4)."""
    response = await _request(http, method, path, json=json, idempotency_key=idempotency_key)
    if response.status_code >= 500:
        raise StewardUnavailable(f"{method} {path}: HTTP {response.status_code}")
    if response.status_code >= 400:
        raise StewardRejected(response.status_code, _detail(response))
    return response.json() if response.content else None


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(body, Mapping):
        return str(body.get("detail") or body.get("title") or body)[:200]
    return str(body)[:200]
