"""Explanations and answers over SSE (design §5.2, §5.3, §9).

`start_*` runs in the request, before the stream opens: it writes the user message (ask) and the
`streaming` placeholder in one transaction, so a second stream on the branch is a plain 409. The
stream then runs the pipeline; the answer is committed with its usage and jobs at `done`. A client
that disconnects cancels the model call and the placeholder becomes `aborted`, unless the answer was
already being committed, which then completes (design §9).
"""

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from banyan.config import Settings
from banyan.errors import Conflict
from banyan.llm import RoleClient, RoleError
from banyan.models import (
    Branch,
    BranchKind,
    Message,
    MessageRole,
    MessageStatus,
    Node,
    Topic,
    TopicStatus,
)
from banyan.pipeline import record
from banyan.pipeline.events import DoneEvent, ErrorEvent, Event
from banyan.pipeline.run import run_ask
from banyan.pipeline.types import AskInput, Deps, Mode, Turn
from banyan.services import branches, outline, topics
from banyan.steward import StewardReader, collection_name

log = logging.getLogger("banyan.answers")


class AlreadyExplained(Conflict):
    pass


class AnswerStreaming(Conflict):
    pass


CHARS_PER_TOKEN = 4  # a rough estimate, enough to keep prompts inside a budget
EXPLANATION_CONTEXT_CHARS = 3000


@dataclass(frozen=True)
class Runtime:
    llm: RoleClient
    steward: StewardReader | None
    sessions: async_sessionmaker[AsyncSession]
    user_id: str
    history_budget: int
    # The sweeper fails `streaming` answers untouched for this long; a running one touches its
    # placeholder well within it, so only abandoned ones are swept (design §9).
    stale_s: float = 600.0

    @classmethod
    def from_settings(
        cls,
        llm: RoleClient,
        steward: StewardReader | None,
        sessions: async_sessionmaker[AsyncSession],
        settings: Settings,
    ) -> "Runtime":
        return cls(
            llm, steward, sessions, settings.user_id, settings.history_budget,
            settings.stream_stale_s,
        )  # fmt: skip


async def start_explain(s: AsyncSession, node_id: uuid.UUID) -> AskInput:
    node = await outline.get_node(s, node_id)
    main = await s.scalar(
        select(Branch).where(Branch.node_id == node_id, Branch.kind == BranchKind.MAIN)
    )
    if main is None:
        main = Branch(node_id=node_id, kind=BranchKind.MAIN, title=node.title)
        s.add(main)
        await s.flush()
    elif await branches.explanation(s, node_id) is not None:
        raise AlreadyExplained("the node is already explained")
    placeholder = await _placeholder(s, main.id)
    topic = await topics.get_topic(s, node.topic_id)
    nodes = await outline.topic_nodes(s, node.topic_id)
    return await _input(
        s, topic, node, main.id, placeholder, mode="explain",
        question=f'Explain "{node.title}"', outline_text=outline.outline_text(nodes),
    )  # fmt: skip


async def start_ask(
    s: AsyncSession, branch_id: uuid.UUID, question: str, *, history_budget: int
) -> AskInput:
    branch = await branches.get_branch(s, branch_id)
    if branch.closed_at is not None:
        raise Conflict("the branch is closed")
    node = await outline.get_node(s, branch.node_id)
    topic = await topics.get_topic(s, node.topic_id)
    history = _trim(await branches.done_messages(s, branch_id), history_budget)
    explained = await branches.explanation(s, node.id)
    s.add(
        Message(
            branch_id=branch_id,
            role=MessageRole.USER,
            status=MessageStatus.DONE,
            content_md=question,
        )
    )
    placeholder = await _placeholder(s, branch_id)
    return await _input(
        s, topic, node, branch_id, placeholder, mode="ask", question=question, history=history,
        explanation=explained.content_md[:EXPLANATION_CONTEXT_CHARS] if explained else None,
    )  # fmt: skip


async def stream_answer(inp: AskInput, rt: Runtime) -> AsyncIterator[Event]:
    """Runs the pipeline in a task and forwards its events. The task owns every outcome; leaving
    the stream cancels it (design §9)."""
    queue: asyncio.Queue[Event | None] = asyncio.Queue()
    deps = Deps(llm=rt.llm, steward=rt.steward, emit=queue.put, user_id=rt.user_id)
    task = asyncio.create_task(_answer(inp, rt, deps, queue))
    try:
        while (event := await queue.get()) is not None:
            yield event
    finally:
        if not task.done():  # the client went away mid-answer
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


async def _answer(
    inp: AskInput, rt: Runtime, deps: Deps, queue: asyncio.Queue[Event | None]
) -> None:
    async def end(status: MessageStatus) -> None:
        await record.ended(rt.sessions, inp.message_id, status, deps.usage, deps.path)

    alive = asyncio.create_task(_keep_alive(rt, inp.message_id))
    try:
        result = await run_ask(inp, deps)
        commit = asyncio.ensure_future(record.done(rt.sessions, inp, result, deps.usage, deps.path))
        try:
            await asyncio.shield(commit)
        except asyncio.CancelledError:
            await commit  # the commit point: once started, it completes (design §9)
            raise
        await queue.put(
            DoneEvent(
                inp.message_id,
                result.answer.source,
                deps.usage.input_tokens,
                deps.usage.output_tokens,
                list(deps.path),
                result.answer.warnings,
            )
        )
    except asyncio.CancelledError:
        await asyncio.shield(end(MessageStatus.ABORTED))  # a no-op if it was committed
        raise
    except RoleError as exc:
        await end(MessageStatus.FAILED)
        await queue.put(ErrorEvent(f"role_{exc.kind}", str(exc)))
    except Exception:  # a Banyan bug: the role layer turns every model failure into RoleError
        log.exception("answer %s failed", inp.message_id)
        await end(MessageStatus.FAILED)
        await queue.put(ErrorEvent("internal", "the answer failed; try again"))
    finally:
        alive.cancel()
        queue.put_nowait(None)


async def _keep_alive(rt: Runtime, message_id: uuid.UUID) -> None:
    """Touches the placeholder while the answer runs, so the stale-stream sweeper (which fails
    placeholders of processes that died) never takes a live one."""
    while True:
        await asyncio.sleep(rt.stale_s / 3)
        async with rt.sessions() as s:
            await s.execute(
                update(Message)
                .where(Message.id == message_id, Message.status == MessageStatus.STREAMING)
                .values(updated_at=func.now())
            )
            await s.commit()


async def _placeholder(s: AsyncSession, branch_id: uuid.UUID) -> uuid.UUID:
    message = Message(
        branch_id=branch_id, role=MessageRole.ASSISTANT, status=MessageStatus.STREAMING
    )
    s.add(message)
    try:
        await s.commit()
    except IntegrityError as exc:
        await s.rollback()
        raise AnswerStreaming("an answer is already streaming on this branch") from exc
    return message.id


async def _input(
    s: AsyncSession,
    topic: Topic,
    node: Node,
    branch_id: uuid.UUID,
    message_id: uuid.UUID,
    *,
    mode: Mode,
    question: str,
    outline_text: str = "",
    history: list[Turn] | None = None,
    explanation: str | None = None,
) -> AskInput:
    active = await s.scalars(select(Topic.id).where(Topic.status == TopicStatus.ACTIVE))
    return AskInput(
        topic_id=topic.id, node_id=node.id, message_id=message_id,
        mode=mode, question=question, topic_title=topic.title, goal=topic.goal,
        level=topic.level.value, language_name=topic.language.prompt_name,
        instructions=topic.instructions, node_title=node.title, node_summary=node.summary,
        outline=outline_text, explanation=explanation, history=history or [],
        collection=collection_name(topic.id),
        all_collections=[collection_name(t) for t in active],
    )  # fmt: skip


def _trim(messages: list[Message], budget_tokens: int) -> list[Turn]:
    """The newest turns that fit the budget, oldest first (compression arrives in B6)."""
    kept: list[Turn] = []
    used = 0
    for m in reversed(messages):
        cost = len(m.content_md) // CHARS_PER_TOKEN + 1
        if used + cost > budget_tokens:
            break
        used += cost
        kept.append(Turn(role=m.role.value, content=m.content_md))
    return list(reversed(kept))
