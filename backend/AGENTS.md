# Banyan backend — Agent Guide

Adds backend rules to the root `AGENTS.md`. Python 3.12, uv, FastAPI.

## Layout

```
src/banyan/
  main.py            create_app(): mounts /v1, /health, serves the built frontend in production
  cli.py             banyan serve | worker | migrate | openapi | export <topic>
  config.py          Settings (pydantic-settings, env prefix BANYAN_), roles config
  errors.py          domain errors
  api/v1/            routers: topics, nodes, branches, ask, feedback, notes, export, memory, usage
  api/sse.py         SSE event types (design §9) — the only place they are defined
  api/errors.py      domain errors → RFC 7807 problem responses
  services/          TopicService, OutlineService, AskService, NotesService, ExportService,
                     MemoryService, UsageService, JobService (enqueue only)
  pipeline/          ask pipeline (design §6): steps.py (the seven steps), run.py (orchestrator), record.py
                     (the commit point), events.py (what a stream reports), sources.py (per-call nonce
                     tags around retrieved and fetched text, §6.2), types.py (step I/O, Deps)
  llm/               role layer: roles.py (Role enum, resolve), client.py (Pydantic AI wrapper),
                     fakes.py (FakeLLM), usage.py (token accounting)
  tools/             agent tools: kb_search, web_search, web_fetch; search/ providers (Protocol + adapters)
  steward/           StewardClient Protocol, HttpStewardClient (httpx + the SDK's models), FakeSteward, mapping.py
  jobs/              generic job runtime: claim (SKIP LOCKED), lease recovery, retry with backoff,
                     dead-letter, idempotency and dedupe keys; imports nothing else from banyan
  worker/            handlers by kind (collection_ensure, node_explain, notes_update; kb_* from B3), the
                     stale-stream sweeper, run.py; `banyan worker` runs it alone, `banyan serve` runs it
                     in-process unless BANYAN_RUN_WORKER=false
  export/            the HTML study guide renderer, its template and the vendored KaTeX assets
  models/            SQLAlchemy tables, enums
  storage/           engine, session, migrations/
  prompts/           *.j2 — one template per role
tests/
  unit/ pipeline/ api/ worker/ contract/ (opt-in markers)
```

## Layers (enforced by import-linter)

`api` → `services` → `pipeline` | `export` → `llm` | `tools` | `steward` → `models` → `storage`.
`services`, `pipeline`, `llm`, `tools`, `steward`, `export`, `models`, `storage` must not import FastAPI or
Starlette. Only `llm/` imports `pydantic_ai` or any vendor SDK. Only `steward/` knows steward's wire format and imports `llm_steward_client`
(its generated models; requests go out on httpx so Banyan's own idempotency keys are sent).
Only `tools/search/` knows a search vendor's wire format. `jobs/` is standalone: it may import only
`storage` primitives it is given and the standard library, never `services`, `pipeline` or `models`, so
it can be moved to its own package later (design §2). `worker/` sits beside `api/` at the top layer.

Pipeline steps keep the activity shape (design §6.0): `async def step(inp, deps) -> out` with
serializable Pydantic input and output, no shared mutable state, all I/O through injected `deps`; the
orchestrating function does no I/O itself.

## Rules

1. **Routes are thin.** Validate, call one service method, return a schema or an SSE stream.
2. **Async everywhere.** No blocking I/O in request paths; CPU-bound work goes to a thread.
3. **One transaction per state change.** A new assistant message, its usage rows and its follow-up jobs
   (KB proposal, notes update, episode) are written in one transaction.
4. **Jobs are idempotent and leased.** Every job carries an idempotency key derived from its origin
   (e.g. `kb_propose:<message_id>`); steward writes send it as `Idempotency-Key`, which steward keeps for
   24 hours, so all retries of a job must finish within 24 hours of the first attempt. Workers claim with
   `FOR UPDATE SKIP LOCKED` and a lease (`locked_at`/`locked_by`); an expired lease makes the job
   claimable again (design §4). The job's retry and backoff are the only retry layer (the steward
   client has none), and every attempt reuses the job's key. A steward 4xx (`StewardRejected`) is final:
   steward replays it under that key for 24 hours, so the job goes `dead` at once instead of retrying.
5. **Budgets are config, not literals.** `BANYAN_ANSWER_MAX_STEPS`, `BANYAN_ANSWER_MAX_TOKENS`,
   `BANYAN_JUDGE_THRESHOLD`, retrieval `top_k` and the context token budget live in `Settings`.
6. **Token usage is recorded for every LLM provider attempt**, including judges, failed calls, timeouts
   and structured-output validation retries, as one `usage_log` row each with role, provider, model,
   attempt, input/output tokens (null when the provider returned none), `ok`, `error_kind` and the
   pipeline path.
7. **Errors and streams:** an SSE stream reports a failure as an `error` event, then closes; the
   assistant message is committed only at the `done` commit point, and a client disconnect cancels the
   LLM call and marks the placeholder message `aborted` (design §9).
8. **Migrations:** every schema change is an Alembic revision; never edit an applied revision.

## Testing

- `FakeLLM` returns scripted outputs per role and records calls; `FakeSearch` returns fixed results;
  `FakeSteward` keeps entries, memories and candidates in memory and applies the same scope filters.
- `tests/pipeline/` holds the path table (design §6.4): one test per row asserting the path taken,
  the roles called and the jobs enqueued.
- Integration tests use a throwaway schema `banyan_test` on the dev PostgreSQL
  (`BANYAN_TEST_DATABASE_URL`), never the app schema.
- Opt-in markers: `-m llm` (real models via configured roles), `-m search` (real search API),
  `-m steward` (contract tests on steward's embedded mode, in-process; needs `uv sync --group contract`),
  `-m steward_live` (against a running steward: sync latency, rerank scores, risk grading).
- Retrieved or fetched text never reaches a template unwrapped: tests check that a forged closing tag in
  a source is escaped and that the nonce differs per call (design §6.2).
