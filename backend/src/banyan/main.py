"""The FastAPI application.

`build_app()` is its shape (routes, the OpenAPI contract); `create_app()` adds the runtime
resources from settings and is what `banyan serve` runs.
"""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import FastAPI

from banyan.api import system
from banyan.config import Settings
from banyan.services.health import StewardProbe
from banyan.steward import HttpStewardClient
from banyan.storage.db import create_engine

Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]


def build_app(lifespan: Lifespan | None = None) -> FastAPI:
    app = FastAPI(
        title="Banyan",
        version="0.1.0",
        lifespan=lifespan,
        # operationId = the route function's name, so the frontend's generated client reads well.
        generate_unique_id_function=lambda route: route.name,
    )
    app.include_router(system.router)
    return app


def create_app(settings: Settings | None = None, *, steward: StewardProbe | None = None) -> FastAPI:
    """`steward` replaces the client built from settings (tests pass a fake)."""
    resolved = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.engine = create_engine(resolved.database_url, schema=resolved.db_schema)
        owned = _steward_from(resolved) if steward is None else None
        app.state.steward = steward or owned
        try:
            yield
        finally:
            if owned is not None:
                await owned.aclose()
            await app.state.engine.dispose()

    return build_app(lifespan)


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
