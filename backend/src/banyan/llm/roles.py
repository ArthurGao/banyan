"""The roles of design §7.1 and what each needs from its model."""

from enum import StrEnum


class Role(StrEnum):
    OUTLINE_GENERATE = "outline.generate"
    NODE_EXPLAIN = "node.explain"
    ANSWER_COMPOSE = "answer.compose"
    ANSWER_FROM_KB = "answer.from_kb"
    JUDGE_FRESHNESS = "judge.freshness"
    JUDGE_SUFFICIENCY = "judge.sufficiency"
    NODE_CLASSIFY = "node.classify"
    BRANCH_COMPRESS = "branch.compress"
    NOTES_UPDATE = "notes.update"
    KB_DRAFT_ENTRY = "kb.draft_entry"


# The answer loops get tools (kb_search, web_search, web_fetch) from B3/B4 on (design §7.2).
TOOL_ROLES = frozenset({Role.NODE_EXPLAIN, Role.ANSWER_COMPOSE})
STRUCTURED_ROLES = frozenset(
    {
        Role.OUTLINE_GENERATE,
        Role.JUDGE_FRESHNESS,
        Role.JUDGE_SUFFICIENCY,
        Role.NODE_CLASSIFY,
        Role.NOTES_UPDATE,
        Role.KB_DRAFT_ENTRY,
    }
)
# Required at startup (design §7.2): B1's three, plus notes.update from B2. Later stages add theirs.
REQUIRED_ROLES = frozenset(
    {Role.OUTLINE_GENERATE, Role.NODE_EXPLAIN, Role.ANSWER_COMPOSE, Role.NOTES_UPDATE}
)
