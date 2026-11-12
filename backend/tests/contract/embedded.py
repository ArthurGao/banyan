"""Shared by the contract tests: llm-steward in this process (the SDK's embedded mode), seen
through Banyan's own HttpStewardClient, and a poll for what steward does asynchronously."""

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from llm_steward_client import AsyncSteward

from banyan.steward import HttpStewardClient
from tests.contract.fake_embedder import FakeEmbedder

DEADLINE_S = 20.0


@asynccontextmanager
async def embedded_steward(
    db: Path, *, review: bool = True, memory: bool = False
) -> AsyncIterator[HttpStewardClient]:
    """Opened and closed in the test's own task (anyio requires it of the embedded app).
    `review`: the review key's scope (embedded mode has one caller identity); `memory`: memory
    extraction on, with steward's model scripted (tests/contract/steward_llm.py)."""
    overrides: dict[str, object] = {"embedder": FakeEmbedder()}
    if memory:
        # Imported here: it needs llm-steward itself (`uv sync --group contract`), which the
        # backend job does not install, and every test module is imported before markers deselect.
        from tests.contract.steward_llm import StewardLLM

        overrides["llm"] = StewardLLM()
    steward = await AsyncSteward.embedded(
        f"sqlite+aiosqlite:///{db}", review=review,
        settings={"memory_llm": True} if memory else None, app_overrides=overrides,
    )  # fmt: skip
    try:
        # The SDK's embedded transport, driven by Banyan's client (it sends our idempotency keys).
        yield HttpStewardClient(steward._http, steward._http)
    finally:
        await steward.aclose()


async def until[T](
    check: Callable[[], Awaitable[T]], ok: Callable[[T], bool], what: str,
    deadline_s: float = DEADLINE_S,
) -> T:  # fmt: skip
    """Steward syncs through its outbox: poll `check` until `ok`, or fail after the deadline."""
    deadline = time.monotonic() + deadline_s
    while True:
        value = await check()
        if ok(value):
            return value
        assert time.monotonic() < deadline, f"{what} did not happen in time"
        await asyncio.sleep(0.2)
