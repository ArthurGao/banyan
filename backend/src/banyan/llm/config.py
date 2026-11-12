"""Roles configuration (design §7.1): providers, a capability table per model, role → model.

    providers:
      anthropic: {kind: anthropic, api_key_env: ANTHROPIC_API_KEY}
      deepseek:  {kind: openai_compatible, base_url: <url>, api_key_env: DEEPSEEK_API_KEY}
    models:
      - {provider: anthropic, name: <model>, tools: true, structured: true, context: 200000}
    roles:
      outline.generate: anthropic/<model>

Startup validates configuration only (design §7.2): every required role is mapped, every mapping
names a registered model, tool roles get tool-capable models, structured roles structured-output
ones, and each provider's key variable is set. Whether a model answers is /health's business.
"""

import os
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from banyan.llm.roles import REQUIRED_ROLES, STRUCTURED_ROLES, TOOL_ROLES, Role

ProviderKind = Literal["anthropic", "openai", "openai_compatible"]


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: ProviderKind
    base_url: str | None = None
    api_key_env: str | None = None


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    name: str = Field(min_length=1)
    tools: bool = False
    structured: bool = False
    context: int = Field(gt=0)

    @property
    def ref(self) -> str:
        return f"{self.provider}/{self.name}"


class RolesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    providers: dict[str, ProviderConfig]
    models: list[ModelConfig]
    roles: dict[Role, str]

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        problems: list[str] = []
        known = {m.ref: m for m in self.models}
        for model in self.models:
            if model.provider not in self.providers:
                problems.append(f"model {model.ref}: unknown provider {model.provider!r}")
        for role in sorted(REQUIRED_ROLES - self.roles.keys()):
            problems.append(f"role {role.value} has no model")
        for role, ref in self.roles.items():
            mapped = known.get(ref)
            if mapped is None:
                problems.append(f"role {role.value}: model {ref!r} is not registered under models")
            elif role in TOOL_ROLES and not mapped.tools:
                problems.append(f"role {role.value}: model {ref} does not support tool calls")
            elif role in STRUCTURED_ROLES and not mapped.structured:
                problems.append(f"role {role.value}: model {ref} has no structured output")
        if problems:
            raise ValueError("; ".join(problems))
        return self

    def model_for(self, role: Role) -> ModelConfig | None:
        ref = self.roles.get(role)
        return next((m for m in self.models if m.ref == ref), None) if ref else None

    def missing_keys(self) -> list[str]:
        used = {m.provider for m in self.models if m.ref in self.roles.values()}
        return sorted(
            p.api_key_env
            for name, p in self.providers.items()
            if name in used and p.api_key_env and not os.environ.get(p.api_key_env)
        )


def load_roles(path: Path) -> RolesConfig:
    config = RolesConfig.model_validate(yaml.safe_load(path.read_text()))
    if missing := config.missing_keys():
        raise ValueError(f"API key variables not set: {', '.join(missing)}")
    return config
