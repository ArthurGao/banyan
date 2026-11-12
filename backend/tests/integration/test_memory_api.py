"""The memory page's API (design §8.5, §9): memories listed, pinned ("always apply") and deleted,
KB candidate cards decided. Synchronous writes to steward (owner decision 2026-11-12, AGENTS.md
rule 6). FakeSteward; the wire contract is in tests/contract/test_memory_contract.py."""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest

from banyan.llm.fakes import FakeLLM
from banyan.main import create_app
from banyan.steward import Candidate, FakeSteward, Memory
from banyan.steward.fake import FakeMemory
from tests.conftest import API_TOKEN, app_client, client_for, settings_for

NOW = datetime(2026, 11, 12, tzinfo=UTC)
M1, M2, C1 = (str(uuid.uuid4()) for _ in range(3))
UNKNOWN = str(uuid.uuid4())


@pytest.fixture
def steward() -> FakeSteward:
    fake = FakeSteward()
    fake.memories = {
        M1: FakeMemory("Prefers Java analogies"),
        M2: FakeMemory("Learns at night"),
    }
    fake.candidates = {
        C1: Candidate(C1, "Generics erase types", "At runtime…", "reusable", "medium", NOW, None)
    }
    return fake


@pytest.fixture
async def api(migrated_url: str, steward: FakeSteward) -> AsyncIterator[httpx.AsyncClient]:
    async with client_for(migrated_url, FakeLLM(), token=API_TOKEN, steward=steward) as client:
        yield client


async def test_memories_are_listed_newest_first_and_paged(api: httpx.AsyncClient) -> None:
    page = (await api.get("/v1/memory", params={"limit": 1})).json()
    assert [(m["id"], m["content"], m["pinned"]) for m in page["items"]] == [
        (M2, "Learns at night", False)
    ]
    rest = (await api.get("/v1/memory", params={"cursor": page["next_cursor"]})).json()
    assert [m["id"] for m in rest["items"]] == [M1] and rest["next_cursor"] is None


async def test_a_memory_is_pinned_to_always_apply_and_unpinned(
    api: httpx.AsyncClient, steward: FakeSteward
) -> None:
    pinned = await api.patch(f"/v1/memory/{M1}", json={"pinned": True})
    assert (pinned.status_code, pinned.json()["pinned"]) == (200, True)
    assert steward.memories[M1].pinned is True
    await api.patch(f"/v1/memory/{M1}", json={"pinned": False})
    assert steward.memories[M1].pinned is False


async def test_deleting_a_memory_twice_is_still_deleted_and_an_unknown_one_is_404(
    api: httpx.AsyncClient, steward: FakeSteward
) -> None:
    assert (await api.delete(f"/v1/memory/{M1}")).status_code == 204
    assert (await api.delete(f"/v1/memory/{M1}")).status_code == 204  # steward said 409: gone
    assert [m["id"] for m in (await api.get("/v1/memory")).json()["items"]] == [M2]
    missing = await api.delete(f"/v1/memory/{UNKNOWN}")
    assert (missing.status_code, missing.json()["title"]) == (404, "not_found")
    assert (await api.delete("/v1/memory/../episodes")).status_code in (404, 422)
    assert (await api.delete("/v1/memory/not-a-uuid")).status_code == 422


async def test_a_card_is_listed_and_decided_once(
    api: httpx.AsyncClient, steward: FakeSteward
) -> None:
    (card,) = (await api.get("/v1/memory/candidates")).json()["items"]
    assert (card["id"], card["title"], card["risk_level"]) == (
        C1,
        "Generics erase types",
        "medium",
    )
    decided = await api.post(
        f"/v1/memory/candidates/{C1}", json={"decision": "accept", "note": "ok"}
    )
    assert decided.status_code == 204 and steward.decisions == {C1: True}
    assert (await api.get("/v1/memory/candidates")).json()["items"] == []
    again = await api.post(f"/v1/memory/candidates/{C1}", json={"decision": "reject"})
    assert again.status_code == 409
    unknown = await api.post(f"/v1/memory/candidates/{UNKNOWN}", json={"decision": "reject"})
    assert unknown.status_code == 404


async def test_an_unreachable_steward_is_503_not_an_empty_page(
    api: httpx.AsyncClient, steward: FakeSteward
) -> None:
    steward.unavailable = True
    for response in (
        await api.get("/v1/memory"),
        await api.patch(f"/v1/memory/{M1}", json={"pinned": True}),
        await api.get("/v1/memory/candidates"),
    ):
        assert (response.status_code, response.json()["title"]) == (503, "steward_unavailable")


async def test_without_steward_the_memory_page_is_503(migrated_url: str) -> None:
    app = create_app(settings_for(migrated_url), llm=FakeLLM().client())
    async with app_client(app, token=API_TOKEN) as api:
        response = await api.get("/v1/memory")
    assert (response.status_code, response.json()["title"]) == (503, "steward_not_configured")


async def test_each_write_carries_its_own_idempotency_key(
    api: httpx.AsyncClient, steward: FakeSteward
) -> None:
    keys: list[str] = []
    original = steward.set_memory_pinned

    async def recording(memory_id: str, pinned: bool, *, idempotency_key: str) -> Memory:
        keys.append(idempotency_key)
        return await original(memory_id, pinned, idempotency_key=idempotency_key)

    steward.set_memory_pinned = recording  # type: ignore[method-assign]
    for pinned in (True, False, True):
        await api.patch(f"/v1/memory/{M1}", json={"pinned": pinned})
    # A reused key would make steward replay the first answer and drop the third change.
    assert len(set(keys)) == 3 and all(uuid.UUID(k.split(":")[-1]) for k in keys)


async def test_a_rejected_card_is_rejected(api: httpx.AsyncClient, steward: FakeSteward) -> None:
    await api.post(f"/v1/memory/candidates/{C1}", json={"decision": "reject", "note": "too vague"})
    assert steward.decisions == {C1: False}
