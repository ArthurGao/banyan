"""`banyan` command line: serve, worker, migrate, openapi."""

import json
from pathlib import Path

import typer
import uvicorn
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from banyan.config import Settings

app = typer.Typer(no_args_is_help=True, add_completion=False)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def alembic_config(database_url: str, schema: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    # Passed as objects, not ini options: no ConfigParser escaping; read by migrations/env.py.
    cfg.attributes["database_url"] = database_url
    cfg.attributes["schema"] = schema
    return cfg


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8800, reload: bool = False) -> None:
    """Run the HTTP API."""
    uvicorn.run("banyan.main:create_app", factory=True, host=host, port=port, reload=reload)


@app.command()
def worker() -> None:
    """Run the job worker on its own (set BANYAN_RUN_WORKER=false on `banyan serve`)."""
    import asyncio
    import signal

    from banyan.main import worker_context
    from banyan.worker.run import run

    async def main() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        async with worker_context(Settings()) as (_, ctx):
            await run(ctx, stop)

    asyncio.run(main())


@app.command()
def migrate(revision: str = "head") -> None:
    """Apply database migrations up to REVISION."""
    settings = Settings()
    command.upgrade(alembic_config(settings.database_url, settings.db_schema), revision)
    typer.echo(f"Migrated {make_url(settings.database_url).render_as_string()} to {revision}")


@app.command()
def openapi() -> None:
    """Print the OpenAPI spec (sorted keys) for the frontend's generated client."""
    from banyan.main import build_app

    spec = build_app().openapi()
    typer.echo(json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False))
