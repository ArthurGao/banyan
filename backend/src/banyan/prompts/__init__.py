"""Prompt templates (AGENTS.md rule 9): one Jinja2 file per role, never inline strings.

Each template renders two blocks: `instructions` (the system side) and `prompt` (the user side).
Retrieved and fetched text reaches a template already wrapped by pipeline/sources.py (design §6.2).
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent),
    undefined=StrictUndefined,  # a missing variable is an error, not an empty string
    autoescape=False,  # plain text for a model, not HTML
    keep_trailing_newline=False,
    trim_blocks=True,
    lstrip_blocks=True,
)


@dataclass(frozen=True)
class Prompt:
    instructions: str
    prompt: str


def render(name: str, **variables: Any) -> Prompt:
    template = _env.get_template(f"{name}.j2")
    context = template.new_context(variables)
    blocks = {key: "".join(block(context)).strip() for key, block in template.blocks.items()}
    return Prompt(instructions=blocks["instructions"], prompt=blocks["prompt"])
