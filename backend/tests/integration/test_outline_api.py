"""Topics, the outline and branches over /v1 (design §5.1, §9), on the real test database."""

import uuid

import httpx

from banyan.models import Branch, BranchKind, Job, JobKind, Message, MessageRole, MessageStatus
from tests.conftest import Sessions, create_node, create_topic, rows


async def _outline(api: httpx.AsyncClient, topic_id: str) -> list[tuple[str | None, int, int, str]]:
    nodes = (await api.get(f"/v1/topics/{topic_id}")).json()["nodes"]
    titles = {n["id"]: n["title"] for n in nodes}
    rows = [(titles.get(n["parent_id"]), n["depth"], n["position"], n["title"]) for n in nodes]
    return sorted(rows, key=lambda r: (r[1], r[0] or "", r[2]))


async def test_creating_a_topic_queues_its_steward_collection(
    api: httpx.AsyncClient, db: Sessions
) -> None:
    topic = await create_topic(api, goal="pass the CKA", level="intermediate", language="en")
    assert (topic["outline_status"], topic["explain_mode"], topic["level"]) == (
        "draft", "manual", "intermediate",
    )  # fmt: skip
    jobs = await rows(db, Job)
    assert [(j.kind, j.idempotency_key, j.payload) for j in jobs] == [
        (JobKind.COLLECTION_ENSURE, f"collection_ensure:{topic['id']}", {"topic_id": topic["id"]})
    ]


async def test_topics_list_with_progress(api: httpx.AsyncClient) -> None:
    first = await create_topic(api, title="First")
    second = await create_topic(api, title="Second")
    node = await create_node(api, first["id"], "Pods")
    await create_node(api, first["id"], "Services")
    await api.patch(f"/v1/nodes/{node['id']}", json={"status": "done"})

    listed = (await api.get("/v1/topics")).json()
    assert [(t["id"], t["nodes"], t["done"]) for t in listed] == [
        (second["id"], 0, 0),
        (first["id"], 2, 1),
    ]


async def test_nodes_insert_at_a_position_and_siblings_renumber(api: httpx.AsyncClient) -> None:
    topic = await create_topic(api)
    pods = await create_node(api, topic["id"], "Pods")
    await create_node(api, topic["id"], "Services")
    await create_node(api, topic["id"], "Linux namespaces", position=0, origin="prerequisite")
    await create_node(api, topic["id"], "Pod IPs", parent_id=pods["id"])

    assert await _outline(api, topic["id"]) == [
        (None, 0, 0, "Linux namespaces"),
        (None, 0, 1, "Pods"),
        (None, 0, 2, "Services"),
        ("Pods", 1, 0, "Pod IPs"),
    ]


async def test_the_outline_is_at_most_three_levels(api: httpx.AsyncClient) -> None:
    topic = await create_topic(api)
    a = await create_node(api, topic["id"], "A")
    b = await create_node(api, topic["id"], "B", parent_id=a["id"])
    c = await create_node(api, topic["id"], "C", parent_id=b["id"])
    response = await api.post(
        f"/v1/topics/{topic['id']}/nodes", json={"title": "D", "parent_id": c["id"]}
    )
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    # Moving a two-level subtree under a depth-1 node would make it four levels deep.
    other = await create_node(api, topic["id"], "X")
    x1 = await create_node(api, topic["id"], "X1", parent_id=other["id"])
    moved = await api.post(f"/v1/nodes/{b['id']}/move", json={"parent_id": x1["id"], "position": 0})
    assert moved.status_code == 422


async def test_moving_a_node_carries_its_subtree_and_renumbers(api: httpx.AsyncClient) -> None:
    topic = await create_topic(api)
    a = await create_node(api, topic["id"], "A")
    b = await create_node(api, topic["id"], "B")
    a1 = await create_node(api, topic["id"], "A1", parent_id=a["id"])
    await create_node(api, topic["id"], "A1a", parent_id=a1["id"])

    moved = await api.post(f"/v1/nodes/{a1['id']}/move", json={"parent_id": None, "position": 1})
    assert moved.status_code == 200, moved.text
    assert await _outline(api, topic["id"]) == [
        (None, 0, 0, "A"),
        (None, 0, 1, "A1"),
        (None, 0, 2, "B"),
        ("A1", 1, 0, "A1a"),
    ]
    under_itself = await api.post(
        f"/v1/nodes/{a['id']}/move", json={"parent_id": a["id"], "position": 0}
    )
    assert under_itself.status_code == 422
    assert b["id"]


async def test_deleting_a_node_returns_its_proposed_entries_and_renumbers(
    api: httpx.AsyncClient, db: Sessions
) -> None:
    topic = await create_topic(api)
    a = await create_node(api, topic["id"], "A")
    await create_node(api, topic["id"], "B")
    child = await create_node(api, topic["id"], "A1", parent_id=a["id"])
    async with db() as s:
        branch = Branch(node_id=uuid.UUID(child["id"]), kind=BranchKind.MAIN, title="A1")
        s.add(branch)
        await s.flush()
        s.add(
            Message(
                branch_id=branch.id,
                role=MessageRole.ASSISTANT,
                status=MessageStatus.DONE,
                proposed_entry_ids=["e2", "e1"],
            )
        )
        await s.commit()

    deleted = await api.delete(f"/v1/nodes/{a['id']}")
    assert deleted.json() == {"proposed_entry_ids": ["e1", "e2"]}
    assert await _outline(api, topic["id"]) == [(None, 0, 0, "B")]
    assert (await api.get(f"/v1/nodes/{child['id']}")).status_code == 404


async def test_confirming_the_outline(api: httpx.AsyncClient) -> None:
    topic = await create_topic(api)
    url = f"/v1/topics/{topic['id']}/outline/confirm"
    assert (await api.post(url, json={})).status_code == 422  # empty outline
    await create_node(api, topic["id"], "A")
    confirmed = await api.post(url, json={"explain_mode": "prefetch"})
    assert (confirmed.json()["outline_status"], confirmed.json()["explain_mode"]) == (
        "confirmed", "prefetch",
    )  # fmt: skip
    assert (await api.post(url, json={})).status_code == 409


async def test_branches_are_created_moved_closed_and_read(api: httpx.AsyncClient) -> None:
    topic = await create_topic(api)
    a = await create_node(api, topic["id"], "A")
    b = await create_node(api, topic["id"], "B")
    created = await api.post(f"/v1/nodes/{a['id']}/branches", json={"title": "Why?"})
    branch = created.json()
    assert (created.status_code, branch["kind"]) == (201, "question")

    patched = await api.patch(
        f"/v1/branches/{branch['id']}", json={"node_id": b["id"], "closed": True}
    )
    assert patched.json()["node_id"] == b["id"]
    assert patched.json()["closed_at"] is not None
    detail = (await api.get(f"/v1/nodes/{b['id']}")).json()
    assert [x["id"] for x in detail["branches"]] == [branch["id"]]
    assert detail["explanation"] is None
    assert (await api.get(f"/v1/branches/{branch['id']}")).json()["messages"] == []
    assert (await api.get(f"/v1/branches/{branch['id']}/messages/latest")).status_code == 404


async def test_unknown_ids_are_404_problems(api: httpx.AsyncClient) -> None:
    for url in (f"/v1/topics/{uuid.uuid4()}", f"/v1/nodes/{uuid.uuid4()}",
                f"/v1/branches/{uuid.uuid4()}"):  # fmt: skip
        response = await api.get(url)
        assert response.status_code == 404
        assert response.json()["title"] == "not_found"
