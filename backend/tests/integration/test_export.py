"""Exporting a topic over the API (design §10.2) — the B2 acceptance case."""

import re
import uuid

import httpx

from banyan.models import Export, NodeNotes
from tests.conftest import Sessions, create_node, create_topic, rows
from tests.unit.test_export_render import NOTES, external_references


async def five_node_topic(api: httpx.AsyncClient) -> tuple[str, list[dict[str, str]]]:
    topic = await create_topic(api, title="Kubernetes networking")
    a = await create_node(api, topic["id"], "Pods")
    a1 = await create_node(api, topic["id"], "Pod IPs", parent_id=a["id"])
    await create_node(api, topic["id"], "Pause container", parent_id=a1["id"])
    await create_node(api, topic["id"], "Services")
    await create_node(api, topic["id"], "Ingress")
    nodes = (await api.get(f"/v1/topics/{topic['id']}")).json()["nodes"]
    return topic["id"], nodes


async def test_a_five_node_topic_exports_offline_with_its_outline(
    api: httpx.AsyncClient, db: Sessions
) -> None:
    topic_id, nodes = await five_node_topic(api)
    titled = {n["title"]: n["id"] for n in nodes}
    async with db() as s:
        for title in ("Pods", "Services"):
            s.add(NodeNotes(node_id=uuid.UUID(titled[title]), content=NOTES, version=2))
        await s.commit()

    created = await api.post(f"/v1/topics/{topic_id}/export")
    assert created.status_code == 201
    export = created.json()
    assert (export["status"], export["error"]) == ("done", None)
    assert export["notes_versions"] == {titled["Pods"]: 2, titled["Services"]: 2}

    page = await api.get(f"/v1/exports/{export['id']}")
    assert page.headers["content-type"].startswith("text/html")
    html = page.text
    assert external_references(html) == []
    assert re.findall(r"<h([234])>([^<]+)</h\1>", html) == [
        ("2", "Pods"), ("3", "Pod IPs"), ("4", "Pause container"), ("2", "Services"),
        ("2", "Ingress"),
    ]  # fmt: skip
    assert html.count("No notes yet.") == 3
    saved = await api.get(f"/v1/exports/{export['id']}?download=true")
    assert saved.headers["content-disposition"].startswith("attachment;")


async def test_a_broken_note_fails_the_export_without_a_file(
    api: httpx.AsyncClient, db: Sessions
) -> None:
    topic_id, nodes = await five_node_topic(api)
    async with db() as s:
        s.add(NodeNotes(node_id=uuid.UUID(nodes[0]["id"]), content={"key_points": 7}, version=1))
        await s.commit()
    export = (await api.post(f"/v1/topics/{topic_id}/export")).json()
    assert export["status"] == "failed" and "TypeError" in export["error"]
    (stored,) = await rows(db, Export)
    assert stored.html is None
    assert (await api.get(f"/v1/exports/{export['id']}")).status_code == 409
