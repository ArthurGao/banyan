"""Enumerations of design §4. Values are what the database and the API carry."""

from enum import StrEnum


class Level(StrEnum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


class Language(StrEnum):
    ZH = "zh"
    EN = "en"
    MIXED = "mixed"

    @property
    def prompt_name(self) -> str:
        """How prompts name the language the learner wants answers in."""
        return {
            Language.ZH: "Chinese",
            Language.EN: "English",
            Language.MIXED: "the learner's mix of Chinese and English",
        }[self]


class TopicStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class OutlineStatus(StrEnum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"


class ExplainMode(StrEnum):
    ALL = "all"
    PREFETCH = "prefetch"
    MANUAL = "manual"


class NodeStatus(StrEnum):
    NOT_STARTED = "not_started"
    LEARNING = "learning"
    DONE = "done"


class NodeOrigin(StrEnum):
    OUTLINE = "outline"
    USER = "user"
    PREREQUISITE = "prerequisite"
    QUESTION = "question"


class BranchKind(StrEnum):
    MAIN = "main"
    QUESTION = "question"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class MessageStatus(StrEnum):
    STREAMING = "streaming"
    DONE = "done"
    ABORTED = "aborted"
    FAILED = "failed"


class Source(StrEnum):
    KB = "kb"
    LLM = "llm"
    WEB = "web"
    MIXED = "mixed"


class Feedback(StrEnum):
    NONE = "none"
    GOOD = "good"
    WRONG = "wrong"
    INCOMPLETE = "incomplete"


class ErrorKind(StrEnum):
    TIMEOUT = "timeout"
    PROVIDER = "provider"
    VALIDATION = "validation"
    BUDGET = "budget"
    ABORTED = "aborted"  # the client went away mid-stream (design §9)


class JobKind(StrEnum):
    NODE_EXPLAIN = "node_explain"
    COLLECTION_ENSURE = "collection_ensure"
    KB_PROPOSE = "kb_propose"
    KB_FEEDBACK = "kb_feedback"
    KB_DEPRECATE = "kb_deprecate"
    NOTES_UPDATE = "notes_update"
    EPISODE_SUBMIT = "episode_submit"
    EXPORT_RENDER = "export_render"


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    DEAD = "dead"


class RunStatus(StrEnum):
    RUNNING = "running"
    PAUSED = "paused"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CANCELLED = "cancelled"
    DONE = "done"


ACTIVE_RUN = (RunStatus.RUNNING, RunStatus.PAUSED, RunStatus.BUDGET_EXHAUSTED)


class ExportStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"
