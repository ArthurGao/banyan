"""The built frontend served by the API process in production (`BANYAN_FRONTEND_DIST`, design §11):
files as they are, every other non-API path the app shell (a single-page app), the API untouched."""

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from banyan.llm.fakes import FakeLLM
from banyan.main import create_app
from banyan.steward import FakeSteward
from tests.conftest import API_TOKEN, app_client, settings_for

INDEX = "<!doctype html><title>Banyan</title><div id=root></div>"


@pytest.fixture
async def served(migrated_url: str, tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(INDEX)
    (dist / "assets" / "app.js").write_text("console.log('banyan')")
    (tmp_path / "secret.txt").write_text("outside the dist")
    settings = settings_for(migrated_url).model_copy(update={"frontend_dist": dist})
    app = create_app(settings, steward=FakeSteward(), llm=FakeLLM().client())
    async with app_client(app, token=API_TOKEN) as client:
        yield client


async def test_the_app_shell_answers_every_page(served: httpx.AsyncClient) -> None:
    # Only whole API segments are the API's: /healthy or /docs-help are pages of the app.
    for path in ("/", "/topics/0f0e", "/nodes/abc/branches", "/healthy", "/docs-help"):
        response = await served.get(path)
        assert (response.status_code, response.text) == (200, INDEX), path
        assert response.headers["content-type"].startswith("text/html")


async def test_built_files_are_served_as_they_are(served: httpx.AsyncClient) -> None:
    response = await served.get("/assets/app.js")
    assert (response.status_code, response.text) == (200, "console.log('banyan')")
    assert "javascript" in response.headers["content-type"]


async def test_the_api_keeps_its_paths_and_its_404s(served: httpx.AsyncClient) -> None:
    assert (await served.get("/health")).json()["status"] in ("ok", "degraded")
    assert (await served.get("/v1/topics")).status_code == 200
    unknown = await served.get("/v1/no-such-route")
    assert unknown.status_code == 404 and INDEX not in unknown.text
    assert "paths" in (await served.get("/openapi.json")).json()


async def test_nothing_outside_the_dist_is_served(served: httpx.AsyncClient) -> None:
    for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/assets/../../secret.txt"):
        assert "outside the dist" not in (await served.get(path)).text, path


async def test_without_a_dist_there_is_no_shell(api: httpx.AsyncClient) -> None:
    assert (await api.get("/")).status_code == 404
