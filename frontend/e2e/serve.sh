#!/usr/bin/env bash
# The backend with fakes (BANYAN_FAKES=1: FakeLLM, FakeSteward, no search) serving the production
# build on 127.0.0.1:8812, for Playwright (frontend/AGENTS.md § Testing). Its data lives in the
# throwaway schema `banyan_e2e` of the dev database, dropped and migrated on every start.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
cd "$here/.."
pnpm build >/dev/null

cd ../backend
export BANYAN_FAKES=1
export BANYAN_DB_SCHEMA=banyan_e2e
export BANYAN_API_TOKEN=e2e-token-not-a-secret-0123
export BANYAN_FRONTEND_DIST="$here/../dist"
export BANYAN_FAKE_DELAY_S=0.01
uv run python - <<'PY'
import asyncio
from sqlalchemy import text
from banyan.config import Settings
from banyan.storage.db import create_engine

async def main() -> None:
    settings = Settings()
    assert settings.db_schema == "banyan_e2e", settings.db_schema  # never another schema
    engine = create_engine(settings.database_url, schema=settings.db_schema)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA IF EXISTS banyan_e2e CASCADE"))
    await engine.dispose()

asyncio.run(main())
PY
uv run banyan migrate >/dev/null
exec uv run banyan serve --port 8812
