"""Shared by the path-table tests (design §6.4): inputs, steward items, a run of the pipeline."""

import json
import uuid
from dataclasses import dataclass
from typing import Any

from banyan.llm.fakes import FakeLLM
from banyan.pipeline.events import Event, PathEntry, PathEvent
from banyan.pipeline.run import run_ask
from banyan.pipeline.types import AnswerOut, AskInput, Deps, JobSpec
from banyan.steward import FakeSteward, RetrievalItem, collection_name
from banyan.tools.web import Web

ANSWER = "Services give pods a stable virtual IP."


def verdict(sufficient: bool, coverage: float, missing: list[str] | None = None) -> str:
    return json.dumps({"sufficient": sufficient, "coverage": coverage, "missing": missing or []})


HIT, MISS = verdict(True, 0.9), verdict(False, 0.3, ["NodePort details"])


def ask_input(**changes: Any) -> AskInput:
    topic = uuid.uuid4()
    base = AskInput(
        topic_id=topic,
        node_id=uuid.uuid4(),
        branch_id=uuid.uuid4(),
        message_id=uuid.uuid4(),
        mode="ask",
        question="What does a Service do?",
        topic_title="Kubernetes",
        goal=None,
        level="beginner",
        language_name="English",
        instructions=None,
        node_title="Services",
        node_summary="Stable addresses",
        collection=collection_name(topic),
        all_collections=[collection_name(topic)],
    )
    return base.model_copy(update=changes)


@dataclass(frozen=True)
class Outcome:
    answer: AnswerOut
    jobs: list[JobSpec]
    retrieval_ids: list[str]
    path: list[PathEntry]

    def steps(self) -> list[tuple[str, str]]:
        return [(p.step, p.status) for p in self.path]

    def detail(self, step: str) -> dict[str, Any]:
        return next(p.detail for p in self.path if p.step == step)


class Run:
    def __init__(self, fake: FakeLLM, steward: FakeSteward | None, web: Web | None = None) -> None:
        self.fake = fake
        self.events: list[Event] = []

        async def emit(event: Event) -> None:
            self.events.append(event)

        self.deps = Deps(llm=fake.client(), steward=steward, emit=emit, user_id="owner", web=web)

    async def __call__(self, inp: AskInput) -> Outcome:
        result = await run_ask(inp, self.deps)
        return Outcome(result.answer, result.jobs, result.retrieval_ids, self.deps.path)

    def path_events(self) -> list[tuple[str, str]]:
        return [(e.entry.step, e.entry.status) for e in self.events if isinstance(e, PathEvent)]


def entry(i: str, kind: str = "entry") -> RetrievalItem:
    return RetrievalItem(
        id=i,
        kind=kind,
        status="active",
        score=0.5,
        title=f"t{i}",  # type: ignore[arg-type]
        content=f"content {i}",
    )
