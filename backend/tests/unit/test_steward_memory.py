"""Episodes, memories and KB candidates on the wire (design §8.2, §8.3, §8.5): what
HttpStewardClient sends and how answers map. The real contract: tests/contract (`-m steward`)."""

import json
import uuid
from typing import Any

import httpx
import pytest

from banyan.steward import Candidate, EpisodeMessage, Memory, StewardRejected
from tests.unit.test_steward_http import NOW, client

MEMORY_ID, CANDIDATE_ID = str(uuid.uuid4()), str(uuid.uuid4())


def memory_json(
    memory_id: str = MEMORY_ID, *, pinned: bool = False, **changes: Any
) -> dict[str, Any]:
    return {
        "agent_id": None, "confidence": 0.9, "content": "Prefers Java analogies",
        "created_at": NOW, "id": memory_id, "importance": 0.5, "memory_type": "preference",
        "metadata": {}, "origin": "extraction", "pinned": pinned, "session_id": None,
        "source_refs": [], "status": "active", "sync_status": "synced", "tags": [],
        "updated_at": NOW, "user_id": "owner", "valid_from": NOW, "valid_to": None, **changes,
    }  # fmt: skip


def candidate_json(candidate_id: str = CANDIDATE_ID) -> dict[str, Any]:
    return {
        "created_at": NOW, "decided_at": None, "decided_by": None, "decision_note": None,
        "draft": {"title": "Generics erase types", "content": "At runtime…", "collection_id": "c"},
        "expires_at": NOW, "id": candidate_id, "op": "ADD", "origin": "extraction",
        "reason": "reusable", "requested_for_user": "owner", "risk_level": "low",
        "stage": "awaiting_user", "status": "pending", "target_id": None, "target_type": "kb_entry",
    }  # fmt: skip


class Recorder:
    def __init__(self, status: int, body: Any) -> None:
        self.status, self.body, self.seen = status, body, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        return httpx.Response(self.status, json=self.body)

    @property
    def one(self) -> httpx.Request:
        (request,) = self.seen
        return request


async def test_an_episode_is_user_wide_and_synchronous() -> None:
    rec = Recorder(200, {"episode_id": str(uuid.uuid4()), "status": "processed"})
    messages = [
        EpisodeMessage("user", "Explain with Java analogies"),
        EpisodeMessage("system", "s"),
    ]
    episode_id = await client(rec).submit_episode(
        user_id="owner", messages=messages, idempotency_key="episode:b1:m9"
    )
    assert episode_id == rec.body["episode_id"]
    assert (rec.one.method, rec.one.url.path) == ("POST", "/v1/episodes")
    assert rec.one.headers["Idempotency-Key"] == "episode:b1:m9"
    # Extraction runs inside the request: a short timeout would retry it, and pay for it again.
    assert rec.one.extensions["timeout"]["read"] == 60.0
    # No agent_id or session_id: steward would hide the memories from every other topic.
    assert json.loads(rec.one.content) == {
        "user_id": "owner", "mode": "sync",
        "messages": [
            {"role": "user", "content": "Explain with Java analogies"},
            {"role": "system", "content": "s"},
        ],
    }  # fmt: skip


async def test_memories_are_listed_active_for_the_user_with_a_cursor() -> None:
    rec = Recorder(200, {"items": [memory_json(pinned=True)], "next_cursor": MEMORY_ID})
    page = await client(rec).list_memories("owner", cursor=MEMORY_ID, limit=20)
    assert rec.one.url.path == "/v1/memories"
    assert dict(rec.one.url.params) == {
        "user_id": "owner", "status": "active", "limit": "20", "cursor": MEMORY_ID,
    }  # fmt: skip
    (memory,) = page.items
    assert memory == Memory(
        id=MEMORY_ID, content="Prefers Java analogies", memory_type="preference", pinned=True,
        created_at=memory.created_at, updated_at=memory.updated_at,
    )  # fmt: skip
    assert page.next_cursor == MEMORY_ID


async def test_pinning_patches_the_memory_with_our_key() -> None:
    rec = Recorder(200, memory_json(pinned=True))
    memory = await client(rec).set_memory_pinned(MEMORY_ID, True, idempotency_key="k1")
    assert (rec.one.method, rec.one.url.path) == ("PATCH", f"/v1/memories/{MEMORY_ID}")
    assert json.loads(rec.one.content) == {"pinned": True}
    assert rec.one.headers["Idempotency-Key"] == "k1"
    assert memory.pinned is True


async def test_deleting_a_memory_and_a_refusal_keeps_its_status() -> None:
    rec = Recorder(200, memory_json(status="deleted"))
    await client(rec).delete_memory(MEMORY_ID, idempotency_key="k2")
    assert (rec.one.method, rec.one.url.path) == ("DELETE", f"/v1/memories/{MEMORY_ID}")
    gone = Recorder(409, {"detail": "already deleted"})
    with pytest.raises(StewardRejected) as caught:
        await client(gone).delete_memory(MEMORY_ID, idempotency_key="k3")
    assert caught.value.status == 409


async def test_candidates_awaiting_the_user_come_with_their_draft() -> None:
    rec = Recorder(200, {"items": [candidate_json()], "next_cursor": None})
    page = await client(rec).list_candidates("owner")
    assert dict(rec.one.url.params) == {
        "stage": "awaiting_user", "status": "pending", "user_id": "owner", "limit": "50",
    }  # fmt: skip
    (card,) = page.items
    assert card == Candidate(
        id=CANDIDATE_ID, title="Generics erase types", content="At runtime…", reason="reusable",
        risk_level="low", created_at=card.created_at, expires_at=card.expires_at,
    )  # fmt: skip
    assert page.next_cursor is None


@pytest.mark.parametrize(("accept", "path"), [(True, "accept"), (False, "reject")])
async def test_a_candidate_is_decided_as_the_user_into_the_team_kb(accept: bool, path: str) -> None:
    rec = Recorder(200, candidate_json() | {"status": "accepted" if accept else "rejected"})
    await client(rec).decide_candidate(
        CANDIDATE_ID, accept=accept, user_id="owner", note="ok", idempotency_key="k4"
    )
    assert rec.one.url.path == f"/v1/candidates/{CANDIDATE_ID}/{path}"
    assert json.loads(rec.one.content) == {"user_id": "owner", "scope": "team", "note": "ok"}
    assert rec.one.headers["Idempotency-Key"] == "k4"


@pytest.mark.parametrize("bad", ["../episodes", "not-a-uuid"])
async def test_a_memory_or_candidate_id_must_be_a_uuid(bad: str) -> None:
    rec = Recorder(200, {})
    with pytest.raises(ValueError):
        await client(rec).delete_memory(bad, idempotency_key="k")
    with pytest.raises(ValueError):
        await client(rec).decide_candidate(bad, accept=True, user_id="o", idempotency_key="k")
    assert rec.seen == []
