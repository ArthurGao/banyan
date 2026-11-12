"""Async PostgreSQL engine. Banyan's tables live in one schema (default `banyan`); extensions stay
in public."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.schema import CreateSchema

# Fail fast when the database is unreachable instead of hanging a health check.
CONNECT_TIMEOUT_SECONDS = 5


def create_engine(database_url: str | URL, *, schema: str) -> AsyncEngine:
    url = make_url(database_url)
    if url.get_backend_name() != "postgresql":
        raise ValueError(f"Banyan needs PostgreSQL, got {url.get_backend_name()!r}")
    return create_async_engine(
        url,
        pool_pre_ping=True,
        connect_args={
            "timeout": CONNECT_TIMEOUT_SECONDS,
            "server_settings": {"search_path": f"{schema}, public"},
        },
    )


@asynccontextmanager
async def engine_scope(database_url: str | URL, *, schema: str) -> AsyncIterator[AsyncEngine]:
    """A short-lived engine that is always disposed (CLI, migrations, tests)."""
    engine = create_engine(database_url, schema=schema)
    try:
        yield engine
    finally:
        await engine.dispose()


async def prepare_schema(engine: AsyncEngine, schema: str) -> None:
    async with engine.begin() as conn:
        await conn.execute(CreateSchema(schema, if_not_exists=True))


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Objects stay readable after commit, so services can return what they just wrote."""
    return async_sessionmaker(engine, expire_on_commit=False)


async def ping(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
