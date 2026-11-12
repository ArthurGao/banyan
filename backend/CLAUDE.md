@AGENTS.md

# Claude Code notes — backend

- Before touching the ask pipeline, re-read design §6 and the tests under `tests/pipeline/`; the path
  table there is the spec.
- Before any steward call, open steward's OpenAPI spec (`http://127.0.0.1:8600/openapi.json` when it is
  running, otherwise the steward repo) and match field names exactly. Add the call to `FakeSteward` and
  to the contract test in the same change.
- Never start the real LLM, search or steward in a test run unless the user asked for that marker.
- After changing a route or a schema, run `uv run banyan openapi > ../frontend/openapi.json` so the
  frontend can regenerate its client.
