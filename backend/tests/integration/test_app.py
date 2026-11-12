"""The app against the real test database: migrations and /health."""

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
from alembic import command
from sqlalchemy import text

from banyan.cli import alembic_config
from banyan.main import create_app
from banyan.steward import FakeSteward
from banyan.storage.db import engine_scope
from tests.conftest import TEST_SCHEMA, app_client, settings_for


async def _version(url: str) -> str | None:
    async with engine_scope(url, schema=TEST_SCHEMA) as engine, engine.connect() as conn:
        exists = await conn.scalar(
            text("SELECT to_regclass(:t)"), {"t": f"{TEST_SCHEMA}.alembic_version"}
        )
        if exists is None:
            return None
        return await conn.scalar(text(f"SELECT version_num FROM {TEST_SCHEMA}.alembic_version"))


async def test_migrate_creates_the_schema_and_downgrades(pg_url: str) -> None:
    cfg = alembic_config(pg_url, TEST_SCHEMA)
    await asyncio.to_thread(command.upgrade, cfg, "head")
    assert await _version(pg_url) == "0001"
    await asyncio.to_thread(command.downgrade, cfg, "base")
    assert await _version(pg_url) is None


@pytest.fixture
async def http(pg_url: str) -> AsyncIterator[httpx.AsyncClient]:
    async with app_client(create_app(settings_for(pg_url), steward=FakeSteward())) as client:
        yield client


async def test_health_is_ok_on_the_real_database(http: httpx.AsyncClient) -> None:
    response = await http.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checks": {"db": "ok", "steward": "ok", "search": "not_configured", "roles": {}},
    }


async def test_health_is_down_with_503_when_the_database_is_unreachable() -> None:
    # Nothing listens on port 1: the connection is refused at once.
    app = create_app(settings_for("postgresql+asyncpg://u:secret-pw@127.0.0.1:1/x"))
    async with app_client(app) as client:
        response = await client.get("/health")
    assert response.status_code == 503
    assert response.json()["status"] == "down"
    assert response.json()["checks"]["db"] == "error"
    assert "secret-pw" not in response.text and "127.0.0.1" not in response.text
