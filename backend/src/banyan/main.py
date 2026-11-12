"""The FastAPI application.

`build_app()` is its shape (routes, the OpenAPI contract); `create_app()` adds the runtime
resources from settings and is what `banyan serve` runs.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from banyan.api import errors, system, v1
from banyan.config import Settings
from banyan.llm import RoleClient, load_roles
from banyan.llm.fakes import FakeLLM, demo_answers
from banyan.steward import FakeSteward, HttpStewardClient, StewardClient
from banyan.storage.db import create_engine, session_factory
from banyan.worker.context import WorkerContext
from banyan.worker.run import run as run_worker
from banyan.worker.sweep import sweep_stale_streams

log = logging.getLogger("banyan")

Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]


def build_app(lifespan: Lifespan | None = None) -> FastAPI:
    app = FastAPI(
        title="Banyan",
        version="0.1.0",
        lifespan=lifespan,
        # operationId = the route function's name, so the frontend's generated client reads well.
        generate_unique_id_function=lambda route: route.name,
    )
    errors.install(app)
    app.include_router(system.router)
    app.include_router(v1.router)
    return app


def create_app(
    settings: Settings | None = None,
    *,
    steward: StewardClient | None = None,
    llm: RoleClient | None = None,
) -> FastAPI:
    """`steward` and `llm` replace the clients built from settings (tests pass fakes)."""
    resolved = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with worker_context(resolved, steward=steward, llm=llm) as (engine, ctx):
            app.state.settings = resolved
            app.state.engine = engine
            app.state.sessions = ctx.sessions
            app.state.llm = ctx.llm
            app.state.role_status = "fake" if resolved.fakes and llm is None else "ok"
            app.state.steward = ctx.steward
            stop = asyncio.Event()
            worker = asyncio.create_task(run_worker(ctx, stop)) if resolved.run_worker else None
            try:
                yield
            finally:
                stop.set()
                if worker is not None:
                    await worker

    return build_app(lifespan)


@asynccontextmanager
async def worker_context(
    settings: Settings, *, steward: StewardClient | None = None, llm: RoleClient | None = None
) -> AsyncIterator[tuple[AsyncEngine, WorkerContext]]:
    """The database, the role client and steward, for the API and for `banyan worker`. Answers
    left streaming by a process that died are failed first (design §9)."""
    engine = create_engine(settings.database_url, schema=settings.db_schema)
    owned = _steward_from(settings) if steward is None and not settings.fakes else None
    ctx = WorkerContext(
        sessions=session_factory(engine),
        llm=llm or _llm_from(settings),
        steward=steward or owned or (FakeSteward() if settings.fakes else None),
        settings=settings,
    )
    try:
        try:
            await sweep_stale_streams(ctx.sessions, older_than_s=settings.stream_stale_s)
        except Exception:
            # Best effort: an unreachable or unmigrated database must not stop the API from starting
            # (/health reports it); the worker sweeps again every minute.
            log.warning("could not sweep interrupted answers at startup", exc_info=True)
        yield engine, ctx
    finally:
        if owned is not None:
            await owned.aclose()
        await engine.dispose()


def _llm_from(settings: Settings) -> RoleClient:
    if settings.fakes:
        log.warning("BANYAN_FAKES is on: every role answers from FakeLLM and steward is a fake")
        fake = FakeLLM(default=demo_answers, delay_s=settings.fake_delay_s)
        return fake.client(timeout_s=settings.role_timeout_s)
    if settings.roles_file is None:
        raise RuntimeError("BANYAN_ROLES_FILE is not set (or set BANYAN_FAKES=1 for development)")
    config = load_roles(settings.roles_file)
    return RoleClient.from_config(config, timeout_s=settings.role_timeout_s)


def _steward_from(settings: Settings) -> HttpStewardClient | None:
    if settings.steward_url is None:
        return None
    # Settings guarantees both keys once the URL is set.
    assert settings.steward_api_key is not None and settings.steward_review_key is not None
    return HttpStewardClient.connect(
        settings.steward_url,
        api_key=settings.steward_api_key.get_secret_value(),
        review_key=settings.steward_review_key.get_secret_value(),
        timeout_s=settings.steward_timeout_s,
    )
