"""LLM role layer (design §7, AGENTS.md rule 4). Call sites name a role; configuration maps it to a
provider and model. Only this package imports pydantic_ai or a vendor SDK; a failed call raises
`RoleError` and is never retried on another model."""

from banyan.llm.client import RoleClient, RoleError, Tool, ToolCalled
from banyan.llm.config import RolesConfig, load_roles
from banyan.llm.roles import Role
from banyan.llm.usage import CallContext, UsageCollector, UsageRecord

__all__ = [
    "CallContext",
    "Role",
    "RoleClient",
    "RoleError",
    "RolesConfig",
    "Tool",
    "ToolCalled",
    "UsageCollector",
    "UsageRecord",
    "load_roles",
]
