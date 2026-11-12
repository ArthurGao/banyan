"""Usage rows (design §4): one per provider attempt, failed and retried ones included.

The role layer only collects them; the caller writes them in the transaction of what they belong to
(the answer at its commit point, design §9), so a usage row and its message never disagree.
"""

import uuid
from dataclasses import dataclass, field
from typing import Literal

ErrorKind = Literal["timeout", "provider", "validation", "budget", "aborted"]


@dataclass(frozen=True)
class CallContext:
    topic_id: uuid.UUID | None = None
    message_id: uuid.UUID | None = None
    path_kind: str | None = None


@dataclass(frozen=True)
class UsageRecord:
    role: str
    provider: str
    model: str
    attempt: int
    input_tokens: int | None
    output_tokens: int | None
    ok: bool
    error_kind: ErrorKind | None
    latency_ms: int
    context: CallContext


@dataclass
class UsageCollector:
    records: list[UsageRecord] = field(default_factory=list)

    def add(self, record: UsageRecord) -> None:
        self.records.append(record)

    @property
    def input_tokens(self) -> int:
        return sum(r.input_tokens or 0 for r in self.records)

    @property
    def output_tokens(self) -> int:
        return sum(r.output_tokens or 0 for r in self.records)
