import json
from pathlib import Path

from typer.testing import CliRunner

from banyan.cli import app

FRONTEND_SPEC = Path(__file__).parents[3] / "frontend" / "openapi.json"


def test_committed_openapi_spec_matches_the_app() -> None:
    result = CliRunner().invoke(app, ["openapi"])
    assert result.exit_code == 0, result.output
    assert FRONTEND_SPEC.exists(), "run: uv run banyan openapi > ../frontend/openapi.json"
    assert json.loads(FRONTEND_SPEC.read_text()) == json.loads(result.output)
