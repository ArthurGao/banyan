"""B3's acceptance on real steward (design §12 B3): Banyan's API and worker with HttpStewardClient
against llm-steward's embedded mode, models faked. Opt-in: `uv run pytest -m steward`.

1. A question the KB cannot answer goes to compose and is proposed back (kb_propose).
2. The same question again is answered from the KB (path 1), citing the proposed entry.
3. "Wrong" on the first answer re-answers it and deprecates its entry, which leaves retrieval.
"""

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from llm_steward_client import AsyncSteward

from banyan.llm import Role
from banyan.llm.fakes import FakeLLM
from banyan.main import create_app
from banyan.models import JobKind, Message
from banyan.steward import HttpStewardClient, collection_name
from tests.conftest import (
    API_TOKEN,
    Sessions,
    app_client,
    create_node,
    create_topic,
    run_next,
    settings_for,
    sse_events,
    worker_ctx,
)
from tests.contract.fake_embedder import FakeEmbedder

pytestmark = pytest.mark.steward

QUESTION = "What does a Kubernetes Service give a set of pods?"
ANSWER = "A Kubernetes Service gives a set of pods a stable virtual IP and DNS name."
DRAFT = json.dumps(
    {"title": "What a Kubernetes Service gives pods", "content": ANSWER, "risk_level": "low"}
)
HIT = json.dumps({"sufficient": True, "coverage": 0.9, "missing": []})


@asynccontextmanager
async def embedded_steward(tmp_path: Path) -> AsyncIterator[HttpStewardClient]:
    """Opened and closed in the test's own task (anyio requires it of the embedded app)."""
    # Embedded mode has one caller identity: review scope covers collections and deprecation.
    embedded = await AsyncSteward.embedded(
        f"sqlite+aiosqlite:///{tmp_path}/steward.db",
        review=True,
        app_overrides={"embedder": FakeEmbedder()},
    )
    try:
        yield HttpStewardClient(embedded._http, embedded._http)
    finally:
        await embedded.aclose()


async def retrievable(steward: HttpStewardClient, topic_id: str, *, found: bool) -> None:
    deadline = time.monotonic() + 20
    while True:
        result = await steward.retrieve(
            QUESTION,
            user_id="owner",
            collections=[collection_name(topic_id)],
            include_memory=False,
        )
        if bool(result.items) == found:
            return
        assert time.monotonic() < deadline, f"entry never became {'' if found else 'un'}retrievable"
        await asyncio.sleep(0.2)


async def test_kb_first_round_trip_on_real_steward(
    db: Sessions, migrated_url: str, tmp_path: Path
) -> None:
    async with embedded_steward(tmp_path) as steward:
        await _round_trip(db, migrated_url, steward)


async def _round_trip(db: Sessions, migrated_url: str, steward: HttpStewardClient) -> None:
    llm = FakeLLM(
        {
            Role.ANSWER_COMPOSE: [ANSWER, "Corrected: " + ANSWER],
            Role.KB_DRAFT_ENTRY: [DRAFT],
            Role.JUDGE_SUFFICIENCY: [HIT],
            Role.ANSWER_FROM_KB: ["It gives them a stable virtual IP and DNS name [S1]."],
        },
        roles=[
            Role.ANSWER_COMPOSE,
            Role.KB_DRAFT_ENTRY,
            Role.JUDGE_SUFFICIENCY,
            Role.ANSWER_FROM_KB,
            Role.NOTES_UPDATE,
        ],
    )
    app = create_app(settings_for(migrated_url), steward=steward, llm=llm.client())
    worker = worker_ctx(db, migrated_url, llm, steward)
    async with app_client(app, token=API_TOKEN) as api:
        topic = await create_topic(api, title="Kubernetes networking")
        node = await create_node(api, topic["id"], "Services")
        assert await run_next(worker, [JobKind.COLLECTION_ENSURE.value]) == "done"

        # 1. Nothing in the KB yet: compose answers and the answer is proposed.
        first_branch = (
            await api.post(f"/v1/nodes/{node['id']}/branches", json={"title": "Q1"})
        ).json()
        first = sse_events(
            (
                await api.post(
                    f"/v1/branches/{first_branch['id']}/ask", json={"question": QUESTION}
                )
            ).text
        )
        assert (first[-1][0], first[-1][1]["source"]) == ("done", "llm")
        first_id = first[-1][1]["message_id"]
        assert await run_next(worker, [JobKind.KB_PROPOSE.value]) == "done"
        await retrievable(steward, topic["id"], found=True)

        # 2. Asked again: answered from the KB, citing the entry it proposed (path 1).
        second_branch = (
            await api.post(f"/v1/nodes/{node['id']}/branches", json={"title": "Q2"})
        ).json()
        second = sse_events(
            (
                await api.post(
                    f"/v1/branches/{second_branch['id']}/ask", json={"question": QUESTION}
                )
            ).text
        )
        done = second[-1][1]
        assert (second[-1][0], done["source"]) == ("done", "kb")
        assert {p["step"]: p["status"] for p in done["path"]}["judge"] == "hit"
        proposed = (await api.get(f"/v1/branches/{first_branch['id']}/messages/latest")).json()
        latest = (await api.get(f"/v1/branches/{second_branch['id']}/messages/latest")).json()
        (citation,) = latest["citations"]
        assert citation["entry_id"] in [str(e) for e in await _proposed(db, first_id)]
        assert proposed["id"] == first_id

        # 3. "Wrong" on the first answer: answered again, and its entry leaves the KB.
        reanswer = sse_events(
            (await api.post(f"/v1/messages/{first_id}/feedback", json={"feedback": "wrong"})).text
        )
        assert reanswer[-1][0] == "done"
        assert await run_next(worker, [JobKind.KB_DEPRECATE.value]) == "done"
        await retrievable(steward, topic["id"], found=False)


async def _proposed(db: Sessions, message_id: str) -> list[str]:
    async with db() as s:
        message = await s.get(Message, uuid.UUID(message_id))
        assert message is not None
        return list(message.proposed_entry_ids)
