"""B5's acceptance on real steward (design §12 B5): Banyan's API and worker with HttpStewardClient
against llm-steward's embedded mode, with memory extraction on and steward's model scripted
(tests/contract/steward_llm.py); Banyan's models are FakeLLM. Opt-in: `uv run pytest -m steward`.

1. A preference said in a question branch becomes a memory once the branch closes (an episode).
2. Pinned ("always apply"), it reaches an explanation in another topic, about something else.
3. Deleted, it no longer does.
4. Said differently later ("no longer …"), the new preference supersedes the old one, which leaves
   retrieval.
Memories come asynchronously in general (design §12): the test polls with a deadline.
"""

from pathlib import Path

import httpx
import pytest

from banyan.llm import Role
from banyan.llm.fakes import FakeLLM, demo_answers
from banyan.main import create_app
from banyan.models import JobKind
from banyan.worker.context import WorkerContext
from tests.conftest import (
    API_TOKEN,
    Sessions,
    app_client,
    create_branch,
    create_node,
    create_topic,
    run_next,
    settings_for,
    worker_ctx,
)
from tests.contract.embedded import embedded_steward, until

pytestmark = pytest.mark.steward

PREFERENCE = "Please explain everything with Java analogies"


async def say_and_close(
    api: httpx.AsyncClient, worker: WorkerContext, node_id: str, *said: str
) -> None:
    """A question branch where the learner says `said`, then closes it: an episode goes out."""
    branch_id = await create_branch(api, node_id)
    for text in said:
        await api.post(f"/v1/branches/{branch_id}/ask", json={"question": text})
    await api.patch(f"/v1/branches/{branch_id}", json={"closed": True})
    while await run_next(worker, [JobKind.EPISODE_SUBMIT.value]) is not None:
        pass


async def memories(api: httpx.AsyncClient) -> list[dict[str, object]]:
    items: list[dict[str, object]] = (await api.get("/v1/memory")).json()["items"]
    return items


async def explain_prompt(api: httpx.AsyncClient, llm: FakeLLM, topic_id: str, title: str) -> str:
    node = await create_node(api, topic_id, title)
    await api.post(f"/v1/nodes/{node['id']}/explain")
    return llm.calls_of(Role.NODE_EXPLAIN)[-1].prompt


async def test_a_learners_preference_is_remembered_applied_and_forgotten(
    db: Sessions, migrated_url: str, tmp_path: Path
) -> None:
    async with embedded_steward(tmp_path / "steward.db", memory=True) as steward:
        llm = FakeLLM(default=demo_answers)
        app = create_app(settings_for(migrated_url), steward=steward, llm=llm.client())
        worker = worker_ctx(db, migrated_url, llm, steward)
        async with app_client(app, token=API_TOKEN) as api:
            java = await create_topic(api, title="Java generics")
            generics = await create_node(api, java["id"], "Type erasure")
            python = await create_topic(api, title="Python")

            # 1. Said in a branch, remembered once the branch closes.
            await say_and_close(api, worker, generics["id"], PREFERENCE)
            (memory,) = await until(
                lambda: memories(api), lambda ms: len(ms) == 1, "the preference as a memory"
            )
            assert memory["content"] == PREFERENCE

            # 2. Pinned: in an explanation of something else, in another topic.
            pinned = await api.patch(f"/v1/memory/{memory['id']}", json={"pinned": True})
            assert pinned.json()["pinned"] is True
            prompt = await explain_prompt(api, llm, python["id"], "Decorators")
            assert PREFERENCE in prompt and 'kind="memory"' in prompt

            # 3. Deleted: gone from the page and from the next explanation.
            assert (await api.delete(f"/v1/memory/{memory['id']}")).status_code == 204
            assert await memories(api) == []
            assert PREFERENCE not in await explain_prompt(api, llm, python["id"], "Generators")


async def test_a_changed_preference_supersedes_the_old_one(
    db: Sessions, migrated_url: str, tmp_path: Path
) -> None:
    async with embedded_steward(tmp_path / "steward.db", memory=True) as steward:
        llm = FakeLLM(default=demo_answers)
        app = create_app(settings_for(migrated_url), steward=steward, llm=llm.client())
        worker = worker_ctx(db, migrated_url, llm, steward)
        async with app_client(app, token=API_TOKEN) as api:
            topic = await create_topic(api, title="Java generics")
            node = await create_node(api, topic["id"], "Type erasure")
            old = "I like Java analogies"
            await say_and_close(api, worker, node["id"], old)

            async def recalled(query: str) -> list[str]:
                found = await steward.retrieve(query, user_id="owner", include_memory=True)
                return [i.content for i in found.items if i.kind == "memory"]

            # In steward's engine first, so the next fact can be related to it.
            await until(lambda: recalled(old), lambda got: old in got, "the old preference")
            new = "No longer use Java analogies, use Python ones"
            await say_and_close(api, worker, node["id"], new)

            assert [m["content"] for m in await memories(api)] == [new]
            await until(lambda: recalled(old), lambda got: old not in got, "the old one leaving")
            await until(lambda: recalled(new), lambda got: new in got, "the new one recalled")
