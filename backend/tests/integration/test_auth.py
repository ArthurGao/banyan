"""Default deny (web-authz §1): every /v1 route needs the token; the public list is written down."""

import uuid
from collections.abc import AsyncIterator

import httpx
import pytest

from banyan.main import build_app
from tests.conftest import API_TOKEN, client_for

PUBLIC = {"/health", "/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


@pytest.fixture
async def anonymous(migrated_url: str) -> AsyncIterator[httpx.AsyncClient]:
    async with client_for(migrated_url) as client:
        yield client


def _v1_routes() -> list[tuple[str, str]]:
    # From the OpenAPI spec: FastAPI mounts included routers lazily, so `app.routes` does not list
    # them; the spec is exactly what is served.
    paths = build_app().openapi()["paths"]
    assert {p for p in paths if not p.startswith("/v1")} <= PUBLIC
    routes = [(m.upper(), p) for p, ops in paths.items() if p.startswith("/v1") for m in ops]
    assert len(routes) >= 12, routes
    return routes


@pytest.mark.parametrize(("method", "path"), _v1_routes())
@pytest.mark.parametrize("token", [None, "wrong-token-0123456789"])
async def test_every_v1_route_refuses_a_missing_or_wrong_token(
    anonymous: httpx.AsyncClient, method: str, path: str, token: str | None
) -> None:
    url = path.replace("{", "").replace("}", "")
    for name in ("topic_id", "node_id", "branch_id"):
        url = url.replace(name, str(uuid.uuid4()))
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    response = await anonymous.request(method, url, headers=headers, json={})
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


async def test_the_right_token_is_let_through(anonymous: httpx.AsyncClient) -> None:
    response = await anonymous.get("/v1/topics", headers={"Authorization": f"Bearer {API_TOKEN}"})
    assert response.status_code == 200


async def test_health_and_the_spec_are_public(anonymous: httpx.AsyncClient) -> None:
    assert (await anonymous.get("/health")).status_code == 200
    assert (await anonymous.get("/openapi.json")).status_code == 200
