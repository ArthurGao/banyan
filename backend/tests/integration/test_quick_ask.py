"""Quick ask (design §5.3, §9): a question without a node is classified into the outline, or grows
it by a node the learner confirms; then it is asked in a new branch there. Two steps (owner
decision 2026-11-12): classify (JSON), quick-ask (SSE)."""

import json
from typing import Any

import httpx
import pytest

from banyan.llm import Role
from banyan.llm.fakes import FakeLLM
from banyan.models import Branch, Node, NodeOrigin, UsageLog
from banyan.services import answers
from tests.conftest import NOT_FRESH, Sessions, create_node, create_topic, rows, sse_events


async def outline(api: httpx.AsyncClient) -> tuple[str, dict[str, str]]:
    """A topic with Pods (N1), Services (N2) and its child ClusterIP (N3): (topic id, ids)."""
    topic = await create_topic(api)
    pods = await create_node(api, topic["id"], "Pods")
    services = await create_node(api, topic["id"], "Services")
    cluster_ip = await create_node(api, topic["id"], "ClusterIP", parent_id=services["id"])
    return topic["id"], {"N1": pods["id"], "N2": services["id"], "N3": cluster_ip["id"]}


def verdict(node: str | None, confidence: float, **suggested: Any) -> str:
    return json.dumps({"node": node, "confidence": confidence, "suggested_node": suggested or None})


async def classify(api: httpx.AsyncClient, topic_id: str, question: str) -> dict[str, Any]:
    response = await api.post(f"/v1/topics/{topic_id}/classify", json={"question": question})
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def test_a_confident_classification_names_the_node(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions
) -> None:
    topic_id, ids = await outline(api)
    fake_llm.script(Role.NODE_CLASSIFY, verdict("N3", 0.9))
    got = await classify(api, topic_id, "What IP does a ClusterIP Service get?")
    assert got == {
        "node_id": ids["N3"], "confidence": 0.9, "confident": True, "suggested_node": None,
    }  # fmt: skip
    (call,) = fake_llm.calls_of(Role.NODE_CLASSIFY)
    assert "N2: Services" in call.prompt and "  N3: ClusterIP" in call.prompt
    assert ids["N3"] not in call.prompt  # labels, not ids the model could mistype
    assert "What IP does a ClusterIP Service get?" in call.prompt
    assert [(u.role, u.ok) for u in await rows(db, UsageLog)] == [("node.classify", True)]


async def test_an_unknown_label_is_no_node(api: httpx.AsyncClient, fake_llm: FakeLLM) -> None:
    topic_id, _ = await outline(api)
    fake_llm.script(Role.NODE_CLASSIFY, verdict("N9", 0.95))
    got = await classify(api, topic_id, "Q?")
    assert (got["node_id"], got["confident"]) == (None, False)


async def test_below_the_threshold_comes_with_a_suggested_node(
    api: httpx.AsyncClient, fake_llm: FakeLLM
) -> None:
    topic_id, ids = await outline(api)
    fake_llm.script(
        Role.NODE_CLASSIFY,
        verdict(
            "N2",
            0.4,
            title="NodePort",
            summary="Reach a Service from outside.",
            parent="N2",
            position=1,
        ),
    )
    got = await classify(api, topic_id, "How do I reach a Service from outside the cluster?")
    assert (got["node_id"], got["confidence"], got["confident"]) == (ids["N2"], 0.4, False)
    assert got["suggested_node"] == {
        "title": "NodePort", "summary": "Reach a Service from outside.", "parent_id": ids["N2"],
        "position": 1,
    }  # fmt: skip


async def test_a_confident_answer_carries_no_suggestion(
    api: httpx.AsyncClient, fake_llm: FakeLLM
) -> None:
    topic_id, _ = await outline(api)
    fake_llm.script(Role.NODE_CLASSIFY, verdict("N1", 0.8, title="X", summary="", parent=None))
    assert (await classify(api, topic_id, "Q?"))["suggested_node"] is None


async def test_a_confirmed_new_node_is_inserted_and_the_question_asked_there(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions
) -> None:
    topic_id, ids = await outline(api)
    fake_llm.script(Role.JUDGE_FRESHNESS, NOT_FRESH)
    fake_llm.script(Role.ANSWER_COMPOSE, "NodePort opens a port on every node.")
    question = "How do I reach a Service from outside the cluster, say from my laptop or a VM?"
    new_node = {
        "title": "NodePort",
        "summary": "From outside.",
        "parent_id": ids["N2"],
        "position": 0,
    }
    response = await api.post(
        f"/v1/topics/{topic_id}/quick-ask", json={"question": question, "new_node": new_node}
    )
    events = sse_events(response.text)
    assert events[-1][0] == "done" and events[-1][1]["source"] == "llm"
    first = events[0][1]["detail"]
    node = next(n for n in await rows(db, Node) if n.title == "NodePort")
    assert (node.origin, str(node.parent_id), node.position) == (
        NodeOrigin.QUESTION, ids["N2"], 0,
    )  # fmt: skip
    (branch,) = [b for b in await rows(db, Branch) if b.node_id == node.id]
    assert (first["node_id"], first["branch_id"]) == (str(node.id), str(branch.id))
    assert branch.title == question[:80]
    siblings = sorted((n for n in await rows(db, Node) if str(n.parent_id) == ids["N2"]),
                      key=lambda n: n.position)  # fmt: skip
    assert [n.title for n in siblings] == ["NodePort", "ClusterIP"]


async def test_a_question_goes_into_the_node_the_learner_chose(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions
) -> None:
    topic_id, ids = await outline(api)
    fake_llm.script(Role.JUDGE_FRESHNESS, NOT_FRESH)
    fake_llm.script(Role.ANSWER_COMPOSE, "An answer.")
    response = await api.post(
        f"/v1/topics/{topic_id}/quick-ask", json={"question": "Q?", "node_id": ids["N1"]}
    )
    assert sse_events(response.text)[-1][0] == "done"
    (branch,) = await rows(db, Branch)
    assert str(branch.node_id) == ids["N1"]


async def test_quick_ask_needs_exactly_one_place_in_this_topic(
    api: httpx.AsyncClient, fake_llm: FakeLLM
) -> None:
    topic_id, ids = await outline(api)
    other, _ = await outline(api)
    url = f"/v1/topics/{topic_id}/quick-ask"
    new_node = {"title": "X", "summary": "", "parent_id": None, "position": 0}
    for body in (
        {"question": "Q?"},
        {"question": "Q?", "node_id": ids["N1"], "new_node": new_node},
    ):
        assert (await api.post(url, json=body)).status_code == 422
    elsewhere = await api.post(
        f"/v1/topics/{other}/quick-ask", json={"question": "Q?", "node_id": ids["N1"]}
    )
    assert elsewhere.status_code == 404
    # ClusterIP is on level 2; a node under its child would be a 4th level.
    third = await create_node(api, topic_id, "Third level", parent_id=ids["N3"])
    over = await api.post(
        url, json={"question": "Q?", "new_node": new_node | {"parent_id": third["id"]}}
    )
    assert over.status_code == 422


async def test_a_failed_classification_is_502_with_its_usage_kept(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions
) -> None:
    topic_id, _ = await outline(api)
    fake_llm.script(Role.NODE_CLASSIFY, ConnectionError("provider down"))
    response = await api.post(f"/v1/topics/{topic_id}/classify", json={"question": "Q?"})
    assert (response.status_code, response.json()["title"]) == (502, "role_provider")
    assert [(u.role, u.ok) for u in await rows(db, UsageLog)] == [("node.classify", False)]


async def test_a_quick_ask_that_cannot_start_leaves_no_node_or_branch(
    api: httpx.AsyncClient, db: Sessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    topic_id, ids = await outline(api)

    async def busy(s: object, branch_id: object) -> object:
        raise answers.AnswerStreaming("an answer is already streaming on this branch")

    monkeypatch.setattr(answers, "_placeholder", busy)
    new_node = {"title": "NodePort", "summary": "", "parent_id": ids["N2"], "position": 0}
    response = await api.post(
        f"/v1/topics/{topic_id}/quick-ask", json={"question": "Q?", "new_node": new_node}
    )
    assert response.status_code == 409
    assert "NodePort" not in [n.title for n in await rows(db, Node)]
    assert await rows(db, Branch) == []
