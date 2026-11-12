"""Against a running llm-steward with deploy/steward/banyan.yaml applied (opt-in: -m steward_live).

Needs BANYAN_STEWARD_URL, BANYAN_STEWARD_API_KEY (read, write) and BANYAN_STEWARD_REVIEW_KEY
(review); without them the tests fail rather than skip. They create entries in steward: point them
at a disposable namespace, never at one holding real knowledge. Engine-specific behaviour the
embedded spike cannot show: real sync latency, rerank scores, risk grading.
"""

import asyncio
import os
import time
import uuid
from collections.abc import AsyncIterator

import pytest

from banyan.steward import HttpStewardClient

pytestmark = pytest.mark.steward_live

POLICY = {"low": "auto", "medium": "auto"}
DEADLINE_S = 60.0


@pytest.fixture
async def live() -> AsyncIterator[HttpStewardClient]:
    try:
        url = os.environ["BANYAN_STEWARD_URL"]
        key = os.environ["BANYAN_STEWARD_API_KEY"]
        review = os.environ["BANYAN_STEWARD_REVIEW_KEY"]
    except KeyError as missing:
        pytest.fail(f"{missing} is not set; -m steward_live needs a running steward")
    client = HttpStewardClient.connect(url, api_key=key, review_key=review, timeout_s=30)
    try:
        yield client
    finally:
        await client.aclose()


async def _propose(client: HttpStewardClient, collection: str, title: str, content: str) -> str:
    collection_id = await client.ensure_collection(
        collection, review_policy=POLICY, idempotency_key=f"live:{uuid.uuid4()}"
    )
    proposed = await client.create_entry(
        collection_id, title=title, content=content, tags=["app:banyan", "node:live"],
        risk_level="low", idempotency_key=f"live:{uuid.uuid4()}",
    )  # fmt: skip
    assert proposed.entry_id is not None, f"went to review as candidate {proposed.candidate_id}"
    return proposed.entry_id


async def test_sync_latency_and_rerank_scores(live: HttpStewardClient) -> None:
    collection = f"banyan-live-{uuid.uuid4().hex[:8]}"
    entry_id = await _propose(
        live, collection, "Raft leader election",
        "In Raft a follower that hears no heartbeat before its election timeout becomes a "
        "candidate and requests votes; a majority makes it leader.",
    )  # fmt: skip
    start = time.monotonic()
    while True:
        result = await live.retrieve(
            "how does Raft elect a leader", user_id="banyan-live", collections=[collection],
            include_memory=False,
        )  # fmt: skip
        if any(item.id == entry_id for item in result.items):
            break
        assert time.monotonic() - start < DEADLINE_S, "entry never became retrievable"
        await asyncio.sleep(0.5)
    print(
        f"\nsync delay: {time.monotonic() - start:.2f}s, scores: {[i.score for i in result.items]}"
    )
    # Written with the read/write key, so `active`: a review key would have made it `verified`.
    (hit,) = [item for item in result.items if item.id == entry_id]
    assert hit.status == "active"
    # With retrieval.rerank on, scores are the cross-encoder's relevance in [0, 1] (design §8.1).
    assert all(0.0 <= item.score <= 1.0 for item in result.items)
    await live.deprecate_entry(entry_id, idempotency_key=f"live:{uuid.uuid4()}")


async def test_ordinary_learning_words_do_not_send_entries_to_review(
    live: HttpStewardClient,
) -> None:
    # Learning content mentions price and safety all the time; with high_risk_keywords: [] they
    # must still go live, or the KB never fills (design §8.1).
    collection = f"banyan-live-{uuid.uuid4().hex[:8]}"
    entry_id = await _propose(
        live, collection, "EC2 Spot pricing and safety margins",
        "Spot instances cost less than on-demand price; keep a safety margin of capacity "
        "because AWS can reclaim them with two minutes notice.",
    )  # fmt: skip
    await live.deprecate_entry(entry_id, idempotency_key=f"live:{uuid.uuid4()}")
