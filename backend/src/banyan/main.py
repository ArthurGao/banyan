"""The FastAPI application.

`build_app()` is its shape (routes, the OpenAPI contract); `create_app()` adds the runtime
resources from settings and is what `banyan serve` runs.
"""

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import FastAPI

from banyan.api import errors, system, v1
from banyan.config import Settings
from banyan.llm import RoleClient, load_roles
from banyan.llm.fakes import FakeLLM, demo_answers
from banyan.services.health import CheckStatus, StewardProbe
from banyan.steward import FakeSteward, HttpStewardClient
from banyan.storage.db import create_engine, session_factory

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
    steward: StewardProbe | None = None,
    llm: RoleClient | None = None,
) -> FastAPI:
    """`steward` and `llm` replace the clients built from settings (tests pass fakes)."""
    resolved = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = resolved
        app.state.engine = create_engine(resolved.database_url, schema=resolved.db_schema)
        app.state.sessions = session_factory(app.state.engine)
        app.state.llm, app.state.role_status = _llm_from(resolved) if llm is None else (llm, "ok")
        owned = _steward_from(resolved) if steward is None and not resolved.fakes else None
        app.state.steward = steward or owned or (FakeSteward() if resolved.fakes else None)
        try:
            yield
        finally:
            if owned is not None:
                await owned.aclose()
            await app.state.engine.dispose()

    return build_app(lifespan)


def _llm_from(settings: Settings) -> tuple[RoleClient, CheckStatus]:
    if settings.fakes:
        log.warning("BANYAN_FAKES is on: every role answers from FakeLLM and steward is a fake")
        fake = FakeLLM(default=demo_answers, delay_s=settings.fake_delay_s)
        return fake.client(timeout_s=settings.role_timeout_s), "fake"
    if settings.roles_file is None:
        raise RuntimeError("BANYAN_ROLES_FILE is not set (or set BANYAN_FAKES=1 for development)")
    config = load_roles(settings.roles_file)
    return RoleClient.from_config(config, timeout_s=settings.role_timeout_s), "ok"


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
