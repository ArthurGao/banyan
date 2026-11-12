"""Episodes, memories and KB candidates through HttpStewardClient against embedded steward
(design §8.2, §8.3, §8.5): memory extraction on (`memory_llm`) with a scripted steward model.
Opt-in: `uv sync --group contract && uv run pytest -m steward`."""

from functools import partial
from pathlib import Path

import pytest

from banyan.steward import EpisodeMessage, HttpStewardClient, StewardRejected
from tests.contract.embedded import embedded_steward, until

pytestmark = pytest.mark.steward

USER = "owner"
embedded = partial(embedded_steward, review=False, memory=True)


async def memories(client: HttpStewardClient) -> list[str]:
    return [m.content for m in (await client.list_memories(USER)).items]


async def retrieved_until(client: HttpStewardClient, wanted: str, *, present: bool) -> None:
    async def recalled() -> list[str]:
        found = await client.retrieve(wanted, user_id=USER, collections=[], include_memory=True)
        return [i.content for i in found.items if i.kind == "memory"]

    state = "found" if present else "dropped"
    await until(recalled, lambda got: (wanted in got) == present, f"{wanted!r} {state}")


async def test_an_episode_becomes_memories_to_pin_retrieve_and_delete(tmp_path: Path) -> None:
    async with embedded(tmp_path / "steward.db") as client:
        said = "Explain things to me with Java analogies"
        messages = [
            EpisodeMessage("user", said),
            EpisodeMessage("assistant", "Sure."),
            EpisodeMessage(
                "system", "Learning state (computed by Banyan): node Generics, 2 questions"
            ),
        ]
        first = await client.submit_episode(user_id=USER, messages=messages, idempotency_key="ep:1")
        again = await client.submit_episode(user_id=USER, messages=messages, idempotency_key="ep:1")
        assert again == first  # replayed, not extracted twice
        assert await memories(client) == [said]  # sync: extracted in the request

        # Unpinned, recalled by similarity: only if stored user-wide (a session_id on the episode
        # would hide it from every retrieval that does not name that session, design §8.2).
        await retrieved_until(client, said, present=True)

        (memory,) = (await client.list_memories(USER)).items
        pinned = await client.set_memory_pinned(memory.id, True, idempotency_key="pin:1")
        assert pinned.pinned is True
        assert (await client.list_memories(USER)).items[0].pinned is True
        # Pinned: retrieved for any question, however unrelated (owner decision, design §8.5).
        found = await client.retrieve("Python generators", user_id=USER, include_memory=True)
        assert said in [i.content for i in found.items if i.kind == "memory"]

        await client.delete_memory(memory.id, idempotency_key="del:1")
        assert await memories(client) == []
        await retrieved_until(client, said, present=False)
        with pytest.raises(StewardRejected) as twice:
            await client.delete_memory(memory.id, idempotency_key="del:2")
        assert twice.value.status == 409


async def test_a_changed_preference_supersedes_the_old_one(tmp_path: Path) -> None:
    async with embedded(tmp_path / "steward.db") as client:
        old = "I like Java analogies"
        await client.submit_episode(
            user_id=USER, messages=[EpisodeMessage("user", old)], idempotency_key="ep:1"
        )
        await retrieved_until(client, old, present=True)  # in the engine, so it can be related
        new = "No longer use Java analogies, use Python ones"
        await client.submit_episode(
            user_id=USER, messages=[EpisodeMessage("user", new)], idempotency_key="ep:2"
        )
        assert await memories(client) == [new]
        await retrieved_until(client, old, present=False)


async def test_a_card_awaiting_the_learner_is_listed_and_decided_once(tmp_path: Path) -> None:
    db = tmp_path / "steward.db"
    async with embedded(db, review=True) as reviewer:
        collection_id = await reviewer.ensure_collection(
            "topic-cards", review_policy={"low": "auto"}, idempotency_key="c:1"
        )
    async with embedded(db) as client:
        # A medium-risk entry written for a user asks that user (steward's default route).
        raw = client._writer
        made = await raw.post(
            "/v1/kb/entries",
            json={
                "collection_id": collection_id, "title": "Generics erase types",
                "content": "At runtime a List<String> is a List.", "risk_level": "medium",
                "tags": [], "user_id": USER,
            },
        )  # fmt: skip
        assert made.status_code in (200, 201, 202), made.text
        (card,) = (await client.list_candidates(USER)).items
        assert (card.title, card.risk_level) == ("Generics erase types", "medium")

        await client.decide_candidate(card.id, accept=True, user_id=USER, idempotency_key="d:1")
        assert (await client.list_candidates(USER)).items == []
        with pytest.raises(StewardRejected) as twice:
            await client.decide_candidate(
                card.id, accept=False, user_id=USER, idempotency_key="d:2"
            )
        assert twice.value.status == 409
