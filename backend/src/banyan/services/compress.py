"""A long branch's early turns, compressed (design §5.3): run by the worker after an answer whose
history no longer fit, so asking never waits for it. The summary covers everything up to
`summary_through`; the newest whole turns that fit in half the budget stay verbatim
(`branches.history`, the same rule asking uses), which leaves room for several questions before
the next compression."""

import uuid

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from banyan.llm import CallContext, Role, RoleClient, UsageCollector
from banyan.models import Branch
from banyan.pipeline.record import save_usage
from banyan.pipeline.sources import SourceText, Wrapper
from banyan.prompts import render
from banyan.services import branches, outline, topics

SUMMARY_WORDS = 300


class Compressed(BaseModel):
    summary: str = Field(min_length=1, max_length=8000)


async def compress(
    sessions: async_sessionmaker[AsyncSession], llm: RoleClient, branch_id: uuid.UUID,
    *, budget: int,
) -> None:  # fmt: skip
    async with sessions() as s:
        branch = await s.get(Branch, branch_id)
        if branch is None:
            return
        # The newest whole turns that fit in half the budget stay verbatim; the rest is folded.
        fold = (await branches.history(s, branch, budget // 2)).dropped
        if not fold:
            return
        node = await outline.get_node(s, branch.node_id)
        topic = await topics.get_topic(s, node.topic_id)
        before, mark = branch.summary_md, branch.summary_through
    wrapper = Wrapper()
    material = [SourceText("summary", "kb", before)] if before and mark else []
    material += [
        SourceText(f"T{i}", "kb", f"{m.role.value}: {m.content_md}") for i, m in enumerate(fold, 1)
    ]
    prompt = render(
        "branch_compress", language_name=topic.language.prompt_name, topic_title=topic.title,
        node_title=node.title, material=wrapper.wrap(material), sources_rule=wrapper.rule,
        words=SUMMARY_WORDS,
    )  # fmt: skip
    usage = UsageCollector()
    try:
        compressed = await llm.structured(
            Role.BRANCH_COMPRESS, Compressed, instructions=prompt.instructions,
            prompt=prompt.prompt, usage=usage,
            context=CallContext(topic_id=topic.id, path_kind="branch_compress"),
        )  # fmt: skip
    finally:
        await save_usage(sessions, usage)  # kept whatever happened
    async with sessions() as s:
        await branches.advance_mark(
            s, branch_id, "summary_through", read=mark, to=fold[-1].id,
            summary_md=compressed.summary,
        )  # fmt: skip
