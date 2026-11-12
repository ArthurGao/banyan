"""HttpStewardClient against a mocked transport (the network boundary): what goes on the wire and
how answers map. The real contract is checked by tests/contract (`-m steward`)."""

import json
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from banyan.steward import HttpStewardClient, StewardRejected, StewardUnavailable

NOW = "2026-11-12T00:00:00Z"
Handler = Callable[[httpx.Request], httpx.Response]


def client(handler: Handler) -> HttpStewardClient:
    """Writer and reviewer on one mocked transport, told apart by their Authorization header."""

    def http(key: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="http://steward",
            headers={"Authorization": f"Bearer {key}"},
        )

    return HttpStewardClient(http("write-key"), http("review-key"))


def written_json(
    entry: dict[str, Any] | None = None, candidate: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {"entry": entry, "candidate": candidate}


def entry_json(entry_id: str, collection_id: str) -> dict[str, Any]:
    return {
        "author_type": "human", "collection_id": collection_id, "content": "c", "created_at": NOW,
        "created_by": None, "id": entry_id, "origin": "manual", "risk_level": "low",
        "source_refs": [], "status": "active", "tags": [], "title": "t", "updated_at": NOW,
        "valid_from": NOW, "valid_to": None, "verified_at": None, "verified_by": None,
        "version": 1,
    }  # fmt: skip


def candidate_json(candidate_id: str) -> dict[str, Any]:
    return {
        "created_at": NOW, "decided_at": None, "decided_by": None, "decision_note": None,
        "draft": {}, "expires_at": None, "id": candidate_id, "op": "ADD", "origin": "api",
        "reason": None, "requested_for_user": None, "risk_level": "high",
        "stage": "awaiting_review", "status": "pending", "target_id": None,
        "target_type": "kb_entry",
    }  # fmt: skip


def collection_json(collection_id: str, name: str) -> dict[str, Any]:
    return {
        "created_at": NOW, "default_risk": "medium", "id": collection_id, "name": name,
        "review_policy": {"low": "auto", "medium": "auto"},
    }  # fmt: skip


async def test_create_entry_sends_our_idempotency_key_and_no_user() -> None:
    seen: list[httpx.Request] = []
    entry_id, collection_id = str(uuid.uuid4()), str(uuid.uuid4())

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json=written_json(entry_json(entry_id, collection_id)))

    result = await client(handler).create_entry(
        collection_id, title="t", content="c", tags=["app:banyan", "node:n1"], risk_level="low",
        idempotency_key="kb_propose:m1",
    )  # fmt: skip

    assert (result.entry_id, result.candidate_id) == (entry_id, None)
    (request,) = seen
    assert (request.method, request.url.path) == ("POST", "/v1/kb/entries")
    assert request.headers["Idempotency-Key"] == "kb_propose:m1"
    assert json.loads(request.content) == {
        "collection_id": collection_id, "title": "t", "content": "c",
        "tags": ["app:banyan", "node:n1"], "risk_level": "low",
    }  # fmt: skip


async def test_create_entry_that_waits_for_review_returns_the_candidate() -> None:
    candidate_id = str(uuid.uuid4())

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json=written_json(candidate=candidate_json(candidate_id)))

    result = await client(handler).create_entry(
        str(uuid.uuid4()), title="t", content="c", tags=[], risk_level="low", idempotency_key="k"
    )
    assert (result.entry_id, result.candidate_id) == (None, candidate_id)


async def test_retrieve_maps_scope_tags_and_sources() -> None:
    seen: list[dict[str, Any]] = []
    retrieval_id, item_id = str(uuid.uuid4()), str(uuid.uuid4())

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={
            "retrieval_id": retrieval_id, "context": None, "degraded": ["memory"],
            "items": [{"id": item_id, "kind": "entry", "status": "active", "score": 0.4,
                       "title": "t", "content": "c", "pinned": False, "source": "kb",
                       "verified": False}],
        })  # fmt: skip

    result = await client(handler).retrieve(
        "q", user_id="owner", collections=["c1"], tags=["node:n1"], include_memory=False, top_k=5
    )

    assert seen == [{
        "query": "q", "scope": {"user_id": "owner"}, "sources": ["kb"], "format": "items",
        "collections": ["c1"], "filters": {"tags": ["node:n1"]}, "top_k": 5,
    }]  # fmt: skip
    assert result.retrieval_id == retrieval_id
    assert [(i.id, i.kind, i.score) for i in result.items] == [(item_id, "entry", 0.4)]
    assert result.degraded == ["memory"]


async def test_ensure_collection_reuses_one_with_the_same_name() -> None:
    seen: list[str] = []
    existing = str(uuid.uuid4())

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.method)
        return httpx.Response(200, json={"items": [collection_json(existing, "topic:t1")]})

    assert (
        await client(handler).ensure_collection("topic:t1", review_policy={}, idempotency_key="c")
        == existing
    )
    assert seen == ["GET"]


async def test_ensure_collection_creates_a_missing_one_keyed_by_name() -> None:
    created = str(uuid.uuid4())
    posts: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"items": [collection_json(str(uuid.uuid4()), "x")]})
        posts.append(request)
        return httpx.Response(201, json=collection_json(created, "topic:t1"))

    policy = {"low": "auto", "medium": "auto"}
    steward = client(handler)
    got = await steward.ensure_collection(
        "topic:t1", review_policy=policy, idempotency_key="collection_ensure:t1:2"
    )
    assert got == created
    (post,) = posts
    # The caller's key, so a re-run after a 4xx can use a fresh one (design §4).
    assert post.headers["Idempotency-Key"] == "collection_ensure:t1:2"
    assert json.loads(post.content) == {"name": "topic:t1", "review_policy": policy}


@pytest.mark.parametrize("status", [500, 502, 503])
async def test_a_server_error_is_unavailable(status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"detail": "boom"})

    with pytest.raises(StewardUnavailable):
        await client(handler).deprecate_entry(str(uuid.uuid4()), idempotency_key="k")


async def test_a_connection_error_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(StewardUnavailable):
        await client(handler).retrieve("q", user_id="owner")


async def test_a_client_error_is_rejected_with_its_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "needs review scope"})

    with pytest.raises(StewardRejected) as caught:
        await client(handler).deprecate_entry(str(uuid.uuid4()), idempotency_key="k")
    assert (caught.value.status, caught.value.detail) == (403, "needs review scope")


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (200, {"status": "ok"}, "ok"),
        (200, {"status": "degraded"}, "degraded"),
        (503, {"status": "error"}, "error"),
        (200, {"unexpected": True}, "error"),
    ],
)
async def test_health_maps_stewards_report(status: int, body: Any, expected: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        return httpx.Response(status, json=body)

    assert await client(handler).health() == expected


async def test_only_collection_creation_and_deprecation_carry_the_review_key() -> None:
    auth: list[tuple[str, str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        auth.append((request.method, path.rsplit("/", 1)[-1], request.headers.get("Authorization")))
        if path == "/v1/kb/collections":
            if request.method == "GET":
                return httpx.Response(200, json={"items": []})
            return httpx.Response(201, json=collection_json(str(uuid.uuid4()), "topic:t1"))
        if path.endswith("/deprecate"):
            return httpx.Response(200, json=entry_json(str(uuid.uuid4()), str(uuid.uuid4())))
        return httpx.Response(
            201, json=written_json(entry_json(str(uuid.uuid4()), str(uuid.uuid4())))
        )

    steward = client(handler)
    await steward.ensure_collection("topic:t1", review_policy={}, idempotency_key="c")
    await steward.create_entry(
        str(uuid.uuid4()), title="t", content="c", tags=[], risk_level="low", idempotency_key="k"
    )
    await steward.deprecate_entry(str(uuid.uuid4()), idempotency_key="d")

    assert auth == [
        ("GET", "collections", "Bearer write-key"),
        ("POST", "collections", "Bearer review-key"),
        ("POST", "entries", "Bearer write-key"),
        ("POST", "deprecate", "Bearer review-key"),
    ]


@pytest.mark.parametrize("bad", ["../tokens/user", "x/accept", "not-a-uuid"])
async def test_an_id_that_is_not_a_uuid_never_reaches_a_url(bad: str) -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={})

    with pytest.raises(ValueError):
        await client(handler).deprecate_entry(bad, idempotency_key="d")
    with pytest.raises(ValueError):
        await client(handler).feedback(bad, str(uuid.uuid4()), helpful=True, idempotency_key="f")
    assert sent == []
