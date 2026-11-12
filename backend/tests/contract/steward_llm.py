"""A scripted model for steward's own memory roles in embedded mode (steward's tests do the same,
in its `tests/fakes/llm.py`): `memory.extract` makes each user line one fact, `memory.resolve`
replaces the first related fact when the new one says "no longer". Banyan's models are FakeLLM;
this one stands in for steward's, which embedded mode needs to extract memories at all."""

import json
from typing import Any

from llm_steward.llm.roles import Provenance, Role
from pydantic import BaseModel

REPLACES = "no longer"


class StewardLLM:
    model = "banyan-fake-steward-model"

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def structured[T: BaseModel](
        self, role: Role, system: str, user: str, schema: type[T]
    ) -> tuple[T, Provenance]:
        payload: dict[str, Any] = json.loads(user)
        self.calls.append(role.value)
        if role == Role.MEMORY_EXTRACT:
            answer: dict[str, Any] = {
                "facts": [m["content"] for m in payload["MESSAGES"] if m["role"] == "user"]
            }
        elif role == Role.MEMORY_RESOLVE:
            existing = payload["EXISTING"]
            if REPLACES in payload["NEW"].lower() and existing:
                answer = {"action": "UPDATE", "target_id": existing[0]["id"], "reason": "changed"}
            else:
                answer = {"action": "ADD", "target_id": None, "reason": "new"}
        else:
            raise AssertionError(f"StewardLLM has no answer for {role.value!r}")
        return schema.model_validate(answer), Provenance(role.value, self.model, "fake")
