"""Shared fixtures.

Integration tests run against `BANYAN_TEST_DATABASE_URL` in the throwaway schema `banyan_test`,
dropped before and after each test. Settings are always built with the env file switched off, so a
test never depends on the developer's `.env` (KB testing-and-ci §14). Without the URL the run stops:
there is no default database (KB testing-and-ci §8).
"""

import asyncio
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from alembic import command
from fastapi import FastAPI
from pydantic import Field
from pydantic_ai import models
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.schema import DropSchema

from banyan.cli import alembic_config
from banyan.config import DEFAULT_DB_SCHEMA, TEST_SCHEMA_SUFFIX, Settings
from banyan.jobs.runner import run_one
from banyan.llm import Role
from banyan.llm.fakes import FakeLLM
from banyan.main import create_app
from banyan.models import Base
from banyan.steward import FakeSteward
from banyan.storage.db import engine_scope, session_factory
from banyan.worker.context import WorkerContext
from banyan.worker.handlers import handlers
from banyan.worker.run import queue_for

TEST_SCHEMA = DEFAULT_DB_SCHEMA + TEST_SCHEMA_SUFFIX

# No test reaches a real model (AGENTS.md rule 8): Pydantic AI refuses any non-test model.
models.ALLOW_MODEL_REQUESTS = False


class _TestTarget(BaseSettings):
    # Reads backend/.env for convenience, but only this variable, never the app's database URL.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    url: str | None = Field(default=None, validation_alias="BANYAN_TEST_DATABASE_URL")


def require_test_database_url() -> str:
    url = _TestTarget().url
    if not url:
        pytest.fail("BANYAN_TEST_DATABASE_URL is not set; integration tests need a PostgreSQL URL")
    if not url.startswith("postgresql"):
        pytest.fail("BANYAN_TEST_DATABASE_URL must be a PostgreSQL URL")
    return url


Sessions = async_sessionmaker[AsyncSession]

API_TOKEN = "test-token-0123456789"


def settings_for(url: str) -> Settings:
    """App settings for tests: no env file, schema `banyan_test` (which the validator refuses for
    real deployments, hence the copy)."""
    settings = Settings(_env_file=None, database_url=url, api_token=API_TOKEN, run_worker=False)  # type: ignore[arg-type]
    return settings.model_copy(update={"db_schema": TEST_SCHEMA})


@asynccontextmanager
async def app_client(app: FastAPI, *, token: str | None = None) -> AsyncIterator[httpx.AsyncClient]:
    """The app with its lifespan running, called in-process; `token` goes in as a Bearer token."""
    headers = {"Authorization": f"Bearer {token}"} if token else None
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t", headers=headers
        ) as client,
    ):
        yield client


async def _drop_test_schema(url: str) -> None:
    async with engine_scope(url, schema=TEST_SCHEMA) as engine, engine.begin() as conn:
        await conn.execute(DropSchema(TEST_SCHEMA, cascade=True, if_exists=True))


@pytest.fixture
async def pg_url() -> AsyncIterator[str]:
    url = require_test_database_url()
    await _drop_test_schema(url)
    yield url
    await _drop_test_schema(url)


@pytest.fixture
async def migrated_url(pg_url: str) -> str:
    """The test schema migrated to head."""
    await asyncio.to_thread(command.upgrade, alembic_config(pg_url, TEST_SCHEMA), "head")
    return pg_url


@pytest.fixture
def fake_llm() -> FakeLLM:
    """Scripted per test with `fake_llm.script(role, ...)`; an unscripted call fails."""
    return FakeLLM()


@pytest.fixture
async def api(migrated_url: str, fake_llm: FakeLLM) -> AsyncIterator[httpx.AsyncClient]:
    """The app on the migrated test schema with fakes, authenticated."""
    async with client_for(migrated_url, fake_llm, token=API_TOKEN) as client:
        yield client


@asynccontextmanager
async def client_for(
    url: str, llm: FakeLLM | None = None, *, token: str | None = None
) -> AsyncIterator[httpx.AsyncClient]:
    """An in-process client of the app on `url`, with FakeSteward and `llm` (default: FakeLLM)."""
    app = create_app(settings_for(url), steward=FakeSteward(), llm=(llm or FakeLLM()).client())
    async with app_client(app, token=token) as client:
        yield client


@pytest.fixture
async def db(migrated_url: str) -> AsyncIterator[Sessions]:
    """Sessions on the migrated test schema, for arranging and checking rows directly."""
    async with engine_scope(migrated_url, schema=TEST_SCHEMA) as engine:
        yield session_factory(engine)


async def rows[M: Base](db: Sessions, model: type[M]) -> list[M]:
    async with db() as s:
        return list(await s.scalars(select(model).order_by(model.created_at)))


async def create_topic(api: httpx.AsyncClient, **fields: Any) -> dict[str, Any]:
    response = await api.post("/v1/topics", json={"title": "Kubernetes", **fields})
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def create_node(
    api: httpx.AsyncClient, topic_id: str, title: str, **fields: Any
) -> dict[str, Any]:
    response = await api.post(f"/v1/topics/{topic_id}/nodes", json={"title": title, **fields})
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


NOT_FRESH = '{"needs_web": false}'


async def explained_node(
    api: httpx.AsyncClient, fake_llm: FakeLLM, explanation: str
) -> tuple[str, str, str]:
    """A topic and node "Services" with a committed explanation (source llm):
    (topic id, node id, explanation message id)."""
    fake_llm.script(Role.JUDGE_FRESHNESS, NOT_FRESH)
    fake_llm.script(Role.NODE_EXPLAIN, explanation)
    topic = await create_topic(api)
    node = await create_node(api, topic["id"], "Services")
    await api.post(f"/v1/nodes/{node['id']}/explain")
    message_id: str = (await api.get(f"/v1/nodes/{node['id']}")).json()["explanation"]["id"]
    return topic["id"], node["id"], message_id


def sse_events(body: str) -> list[tuple[str, dict[str, Any]]]:
    """(event, data) pairs of an SSE body; pings and comments are skipped."""
    events: list[tuple[str, dict[str, Any]]] = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "event" in fields:
            events.append((fields["event"], json.loads(fields["data"])))
    return events


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """In CI a skipped test fails the run (KB testing-and-ci §1): skips hide what matters."""
    if not os.environ.get("CI"):
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    skipped = reporter.stats.get("skipped", []) if reporter else []
    if skipped:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
        if reporter:
            reporter.write_line(f"CI: {len(skipped)} skipped test(s) fail the run", red=True)


@pytest.fixture
def no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in [k for k in os.environ if k.startswith("BANYAN_")]:
        monkeypatch.delenv(key)


def worker_ctx(
    db: Sessions, url: str, llm: FakeLLM | None = None, steward: FakeSteward | None = None,
    **settings: Any,
) -> WorkerContext:  # fmt: skip
    """A worker on the test schema; `settings` override the test settings."""
    resolved = settings_for(url).model_copy(update=settings)
    return WorkerContext(db, (llm or FakeLLM()).client(), steward, resolved)


async def run_next(ctx: WorkerContext, kinds: list[str] | None = None) -> str | None:
    """Claims and runs one job (of `kinds`, default: every kind with a handler); its new state."""
    queue = queue_for(ctx)
    registry = handlers(ctx)
    claimed = await queue.claim(kinds or list(registry))
    return None if claimed is None else await run_one(queue, registry, claimed)
