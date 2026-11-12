"""Alembic environment (async, PostgreSQL only).

`banyan migrate` and the tests pass `database_url` and `schema` through `config.attributes`; plain
`uv run alembic ...` falls back to `Settings`. The schema is created first in its own transaction;
tables land in it through `search_path` (set by the engine), and `alembic_version` is pinned to
it so a stray `public.alembic_version` is never picked up instead.
"""

import asyncio

from alembic import context
from sqlalchemy.engine import Connection

from banyan.config import Settings
from banyan.models import Base
from banyan.storage.db import engine_scope, prepare_schema

config = context.config
target_metadata = Base.metadata


def _target() -> tuple[str, str]:
    url = config.attributes.get("database_url")
    schema = config.attributes.get("schema")
    if url is None or schema is None:
        settings = Settings()
        url = url or settings.database_url
        schema = schema or settings.db_schema
    return url, schema


def _run(connection: Connection, schema: str) -> None:
    context.configure(
        connection=connection, target_metadata=target_metadata, version_table_schema=schema
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_online() -> None:
    url, schema = _target()
    async with engine_scope(url, schema=schema) as engine:
        await prepare_schema(engine, schema)
        async with engine.connect() as connection:
            await connection.run_sync(_run, schema)


if context.is_offline_mode():
    raise SystemExit("Offline migrations are not supported; run against a database.")
asyncio.run(_run_online())
