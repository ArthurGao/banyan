"""What a streaming operation (outline, explain, ask) reports while it runs (design §9).

The API turns these into SSE events (api/sse.py, the only place the wire format is defined); a
worker (B2) ignores the tokens.
"""

import uuid
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from banyan.models import Node, Source


class PathEntry(BaseModel):
    """One step of a message's `path` (design §6.2): stored on it and sent as SSE `path`."""

    step: str
    status: str
    detail: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class PathEvent:
    entry: PathEntry


@dataclass(frozen=True)
class TokenEvent:
    text: str


@dataclass(frozen=True)
class OutlineEvent:
    nodes: list[Node]


@dataclass(frozen=True)
class DoneEvent:
    message_id: uuid.UUID | None
    source: Source | None
    input_tokens: int
    output_tokens: int
    path: list[PathEntry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ErrorEvent:
    code: str
    message: str


Event = PathEvent | TokenEvent | OutlineEvent | DoneEvent | ErrorEvent
