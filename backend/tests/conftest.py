"""Shared fixtures.

Integration tests run against `BANYAN_TEST_DATABASE_URL` in the throwaway schema `banyan_test`,
dropped before and after each test. Settings are always built with the env file switched off, so a
test never depends on the developer's `.env` (KB testing-and-ci §14). Without the URL the run stops:
there is no default database (KB testing-and-ci §8).
"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.schema import DropSchema

from banyan.config import DEFAULT_DB_SCHEMA, TEST_SCHEMA_SUFFIX, Settings
from banyan.storage.db import engine_scope

TEST_SCHEMA = DEFAULT_DB_SCHEMA + TEST_SCHEMA_SUFFIX


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


def settings_for(url: str) -> Settings:
    """App settings for tests: no env file, schema `banyan_test` (which the validator refuses for
    real deployments, hence the copy)."""
    return Settings(_env_file=None, database_url=url).model_copy(update={"db_schema": TEST_SCHEMA})


@asynccontextmanager
async def app_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """The app with its lifespan running, called in-process."""
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client,
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
