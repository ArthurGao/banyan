"""Steward contract spike (design §12 B0): the calls Banyan depends on, through HttpStewardClient,
against llm-steward running in this process (the SDK's embedded mode: SQLite, native engines, a fake
embedder; no server, no model download).
Opt-in: `uv sync --group contract && uv run pytest -m steward`.

What steward does here is recorded in docs/banyan-design.md §8 and the project KB's lessons.

Embedded mode has one caller identity and ignores `Authorization`: these tests prove request and
response shapes, idempotency and scope rules, not which key Banyan sends. Key routing is covered by
tests/unit/test_steward_http.py, and by test_steward_live.py against a real steward.
"""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from llm_steward_client import AsyncSteward

from banyan.steward import HttpStewardClient, RetrievalResult, StewardRejected
from tests.contract.fake_embedder import FakeEmbedder

pytestmark = pytest.mark.steward

POLICY = {"low": "auto", "medium": "auto"}
TAGS = ["app:banyan", "topic:t1", "node:n1", "source:llm", "author:ai", "origin_message:m1"]
TITLE = "What does a Kubernetes Service do"
CONTENT = (
    "A Kubernetes Service gives a stable virtual IP and DNS name to a changing set of pods, "
    "selected by labels, and load-balances traffic across them."
)
SYNC_DEADLINE_S = 20.0
COLLECTION = "topic:t1"
QUERY = "Kubernetes Service virtual IP pods"


@asynccontextmanager
async def embedded(db: Path, *, review: bool) -> AsyncIterator[AsyncSteward]:
    """Steward in this process. `review=False` is Banyan's read/write key, `review=True` its
    review key (design §8.1); one at a time on the same database."""
    client = await AsyncSteward.embedded(
        f"sqlite+aiosqlite:///{db}", review=review, app_overrides={"embedder": FakeEmbedder()}
    )
    try:
        yield client
    finally:
        await client.aclose()


def banyan(steward: AsyncSteward) -> HttpStewardClient:
    # The SDK's embedded transport, driven by Banyan's own client (it sends our idempotency keys).
    return HttpStewardClient(steward._http, steward._http)


async def _retrieve_until(
    client: HttpStewardClient, *, found: bool
) -> tuple[RetrievalResult, float]:
    """Steward syncs entries through its outbox (design §8.4): poll until a deadline."""
    start = time.monotonic()
    while True:
        result = await client.retrieve(
            QUERY, user_id="owner", collections=[COLLECTION], tags=["node:n1"],
            include_memory=False, as_context=True,
        )  # fmt: skip
        if bool(result.items) == found:
            return result, time.monotonic() - start
        if time.monotonic() - start > SYNC_DEADLINE_S:
            pytest.fail(f"retrieve did not {'find' if found else 'drop'} the entry in time")
        await asyncio.sleep(0.2)


async def test_the_knowledge_round_trip(tmp_path: Path) -> None:
    db = tmp_path / "steward.db"
    # Creating a collection needs review scope (Banyan's review key).
    async with embedded(db, review=True) as reviewer:
        client = banyan(reviewer)
        collection_id = await client.ensure_collection(
            COLLECTION, review_policy=POLICY, idempotency_key="collection_ensure:t1"
        )
        again = await client.ensure_collection(
            COLLECTION, review_policy=POLICY, idempotency_key="collection_ensure:t1"
        )
        assert again == collection_id

    async with embedded(db, review=False) as steward:
        client = banyan(steward)
        # Without review scope, creating one is refused.
        with pytest.raises(StewardRejected) as refused:
            await client.ensure_collection(
                "topic:t2", review_policy=POLICY, idempotency_key="collection_ensure:t2"
            )
        assert refused.value.status == 403

        # A low-risk answer without user_id goes live at once (review_policy low -> auto) as
        # `active`: written without review scope, so not `verified`.
        proposed = await client.create_entry(
            collection_id, title=TITLE, content=CONTENT, tags=TAGS, risk_level="low",
            idempotency_key="kb_propose:m1",
        )  # fmt: skip
        assert proposed.candidate_id is None
        assert proposed.entry_id is not None
        entry_id = proposed.entry_id

        # Same Idempotency-Key: the first answer is replayed, no second entry.
        replay = await client.create_entry(
            collection_id, title=TITLE, content=CONTENT, tags=TAGS, risk_level="low",
            idempotency_key="kb_propose:m1",
        )  # fmt: skip
        assert replay.entry_id == entry_id
        page = await steward.kb.entries.list(collection_id=collection_id)
        assert [str(e.id) for e in page.items] == [entry_id]

        # Stored as written, but recorded as human-authored (design §8.1, change request §13).
        entry = await steward.kb.entries.get(entry_id)
        assert (entry.status, entry.author_type, entry.tags) == ("active", "human", TAGS)

        # Retrieval by collection NAME and node tag finds it once the outbox has synced; the
        # item id is the entry id.
        result, delay = await _retrieve_until(client, found=True)
        print(f"\nsync delay until retrievable: {delay:.2f}s")
        (item,) = result.items
        assert (item.id, item.kind, item.status) == (entry_id, "entry", "active")
        assert result.context is not None and "stable virtual IP" in result.context
        assert result.degraded == []

        # Trap: a collection ID is not matched and gives an empty result, not an error.
        by_id = await client.retrieve(
            QUERY, user_id="owner", collections=[collection_id], include_memory=False
        )
        assert by_id.items == []

        # Tags filter with AND: a second node tag finds nothing.
        both = await client.retrieve(
            QUERY, user_id="owner", collections=[COLLECTION], tags=["node:n1", "node:n2"],
            include_memory=False,
        )  # fmt: skip
        assert both.items == []

        # Feedback on that retrieval's item is counted on the entry.
        await client.feedback(
            result.retrieval_id, item.id, helpful=False, note="wrong",
            idempotency_key="kb_feedback:m1",
        )  # fmt: skip
        entry = await steward.kb.entries.get(entry_id)
        assert entry.unhelpful_count == 1

        # Without review scope, deprecation is refused.
        with pytest.raises(StewardRejected) as refused:
            await client.deprecate_entry(entry_id, idempotency_key="kb_deprecate:m1")
        assert refused.value.status == 403

    async with embedded(db, review=True) as reviewer:
        client = banyan(reviewer)
        # A 4xx is stored under its Idempotency-Key and replayed for 24 h (only a 5xx releases the
        # key). Embedded mode has one caller identity, so the reviewer gets the writer's 403 back:
        # a job that got a 4xx must not retry with the same key (design §4).
        with pytest.raises(StewardRejected) as replayed:
            await client.deprecate_entry(entry_id, idempotency_key="kb_deprecate:m1")
        assert replayed.value.status == 403

        # With review scope and a fresh key it is deprecated and leaves retrieval.
        await client.deprecate_entry(entry_id, idempotency_key="kb_deprecate:m1:2")
        entry = await reviewer.kb.entries.get(entry_id)
        assert entry.status == "deprecated"
        gone, _ = await _retrieve_until(client, found=False)
        assert gone.items == []
