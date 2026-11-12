"""The SSE events of design §9 — the only place their wire format is defined.

    path      {step, status, detail}
    token     {text}
    citation  {kind, entry_id?, url?, title?}     after the answer, before done
    outline   {nodes: [...]}
    done      {message_id, source, usage: {input, output}, path, warnings}
    error     {code, message}

Every event has an `id`, a counter within its stream, for client-side de-duplication only: there is
no `Last-Event-ID` resume (design §9). A client that loses a stream fetches
`GET /v1/branches/{id}/messages/latest`.
"""

import uuid
from collections.abc import AsyncIterator
from typing import Literal

from pydantic import BaseModel
from sse_starlette import EventSourceResponse, ServerSentEvent

from banyan.api.schemas import NodeOut
from banyan.models import Source
from banyan.pipeline.events import (
    CitationEvent,
    DoneEvent,
    ErrorEvent,
    Event,
    OutlineEvent,
    PathEntry,
    PathEvent,
    TokenEvent,
)


class TokenData(BaseModel):
    text: str


class CitationData(BaseModel):
    kind: Literal["kb", "web"]
    entry_id: str | None = None
    url: str | None = None
    title: str | None = None


class OutlineData(BaseModel):
    nodes: list[NodeOut]


class UsageData(BaseModel):
    input: int
    output: int


class DoneData(BaseModel):
    message_id: uuid.UUID | None
    source: Source | None
    usage: UsageData
    path: list[PathEntry]
    warnings: list[str]


class ErrorData(BaseModel):
    code: str
    message: str


EventName = Literal["path", "token", "citation", "outline", "done", "error"]


def _wire(event: Event) -> tuple[EventName, BaseModel]:
    match event:
        case PathEvent():
            return "path", event.entry
        case TokenEvent():
            return "token", TokenData(text=event.text)
        case CitationEvent():
            c = event.citation
            return "citation", CitationData(
                kind=c.kind, entry_id=c.entry_id, url=c.url, title=c.title
            )
        case OutlineEvent():
            return "outline", OutlineData(nodes=[NodeOut.model_validate(n) for n in event.nodes])
        case DoneEvent():
            usage = UsageData(input=event.input_tokens, output=event.output_tokens)
            return "done", DoneData(
                message_id=event.message_id, source=event.source, usage=usage, path=event.path,
                warnings=event.warnings,
            )  # fmt: skip
        case ErrorEvent():
            return "error", ErrorData(code=event.code, message=event.message)


def stream(events: AsyncIterator[Event]) -> EventSourceResponse:
    async def encoded() -> AsyncIterator[ServerSentEvent]:
        n = 0
        async for event in events:
            n += 1
            name, data = _wire(event)
            yield ServerSentEvent(data=data.model_dump_json(), event=name, id=str(n))

    return EventSourceResponse(encoded(), ping=15)
