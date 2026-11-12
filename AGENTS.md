# Banyan — Agent Guide

Banyan is a **personal AI learning system**: topic → editable outline → per-node explanation → branch Q&A
under any node → per-node notes → one self-contained HTML study guide. Questions are answered
**knowledge-base first**: Banyan retrieves what was already learned through llm-steward, a cheap model
judges whether that is enough, and only then does a stronger model answer, with web search. New answers
go back to the KB through steward's governance. Banyan is the learning app and the orchestrator; it does
**not** store or govern knowledge itself and does **not** talk to lite-rag or the memory engine (lite-mem by default, or mem0) directly.

This file is the shared guide for every coding agent. `CLAUDE.md` imports it. `backend/` and `frontend/`
each have their own guide that adds folder-specific rules.

Naming: repo `banyan`, Python package `banyan`, CLI `banyan`, env prefix `BANYAN_`, DB schema `banyan`.

## Repository layout

```
backend/     FastAPI service: learning tree, ask pipeline, LLM role layer, search tools, steward client,
             notes, HTML export, job worker (Python, uv)
frontend/    React + Vite + TypeScript SPA (pnpm), responsive phone / tablet / desktop (no PWA in v1)
docs/        design — read before writing code
```

The only contract between backend and frontend is the backend's OpenAPI spec plus the SSE event schema
(design §9); the frontend generates its client with `@hey-api/openapi-ts`.

## Sources of truth (highest first)

1. `docs/banyan-design.md` — the design. §3 architecture, §4 data model, §5 learning flow, §6 ask pipeline,
   §7 LLM role layer and models, §8 steward integration, §9 API and SSE, §10 notes and HTML export,
   §11 frontend, §12 stages and acceptance, §13 open questions.
2. llm-steward (`~/Code/AI/llm-steward`): its OpenAPI spec and `docs/llm-steward-design.md` decide every
   steward request and response. lite-rag (`~/Code/AI/lite-rag`) is reached only through steward.
3. KB at `/Users/arthurgao/Docs/Knowledgebase` (write with the `kb-maintain` skill): project KB
   `projects/banyan/`; cross-project facts in `global/`.

If a change must contradict the design, update the design in the same change and say why in the commit.

## Current stage

**B6 — quick ask, branch compression, deployment — backend done** (the screens are another
session's; no PWA in v1, owner decision 2026-11-13). v1's backend stages are done; open: owner-run
acceptance (`-m search`, `-m llm`, `-m steward_live` on lite-rag + lite-mem, the phone browser through
Tailscale) and the full image once the frontend builds. Track the checklist in design §12; ticked
items are done and verified. Open questions are in design §13 — do not decide them silently; ask, or
implement the conservative option behind config.

## Non-negotiable rules

1. **Knowledge and memory go through steward only.** Banyan never imports `lite_rag`, `lite_mem` or `mem0`,
   never calls lite-rag's or lite-mem's HTTP API, and never writes vectors anywhere. The steward SDK is imported only
   inside `banyan.steward`. All KB reads, KB writes (proposals), feedback,
   episodes and memory candidates use the `StewardClient` (design §8).
2. **Two sources of truth, linked by id.** Banyan's DB owns the learning tree, branches, messages, notes,
   usage and jobs. Steward owns governed knowledge and memory. Banyan stores steward ids
   (`steward_collection_id`, `steward_retrieval_ids`, `cited_entry_ids`, `proposed_entry_ids`) and never copies steward's governance state.
3. **The ask pipeline is deterministic code.** Routing (KB / LLM / web) is decided by code and judge roles,
   never by a free-running agent. Only the `answer.compose` step is an agent loop, with tools
   `kb_search`, `web_search`, `web_fetch`, a hard step budget and a token budget (design §6).
4. **Role layer, no fallback chain.** Call sites name a role; configuration maps the role to provider and
   model. No business code imports a vendor SDK or names a model. A failed call raises. A role with no
   model is a startup error.
5. **Provenance on every answer.** Each assistant message records its `source` (`kb` | `llm` | `web` |
   `mixed`), the pipeline `path`, `role:provider:model`, citations (steward entry ids, URLs) and token
   usage. The UI shows the source. Web-derived KB proposals carry `url`, `fetched_at` and `valid_to`.
6. **Writes to steward tied to Banyan's own state never happen in a request handler.** KB proposals,
   episodes and feedback are enqueued in Banyan's `jobs` table in the same transaction as the message,
   and pushed by the worker with retries. (Reads — retrieval — happen in the request.) The one
   exception (owner decision 2026-11-12): the learner's direct management of steward state on the memory
   page — deleting or pinning a memory, deciding a KB card — is synchronous, because nothing of Banyan's
   changes with it and the learner expects the result; it fails with 503 when steward is down.
7. **HTML export is deterministic.** It renders stored notes through a Jinja2 template; no LLM call during
   rendering. Output is one self-contained file (inline CSS/JS, no network requests), light and dark.
8. **Tests never call real LLMs, search APIs or steward.** Use `FakeLLM`, `FakeSearch`, `FakeSteward`.
   Real services run only under opt-in markers (`-m steward`, `-m search`, `-m llm`). Never call
   llm-gateway subscription profiles from tests, and in development only when the user has started the
   gateway and asked for it.
9. **Prompts live in `backend/src/banyan/prompts/*.j2`**, never inline strings.
10. **Secrets only via environment variables.** Never commit `.env*` except `.env.example`.
11. **Single user in v1.** One API token from the environment; one constant `BANYAN_USER_ID` passed to
    steward. Do not build multi-user features, but keep `user_id` out of hard-coded string literals.

## Tech stack

- Backend: Python 3.12, uv, FastAPI + Pydantic v2, SQLAlchemy 2 async + Alembic, PostgreSQL 18 + pgvector
  (shared instance, Banyan uses schema `banyan`), Pydantic AI (provider adapters and the
  `answer.compose` agent loop), the llm-steward Python SDK's generated models (git dependency, pinned commit), httpx, Jinja2, sse-starlette, Typer, pytest, ruff, mypy (strict),
  import-linter.
- Frontend: React 18, Vite, TypeScript (strict), Tailwind, shadcn/ui, TanStack Query, React Router,
  `@hey-api/openapi-ts`, react-markdown + remark-gfm + rehype-katex + Shiki, Vitest,
  Playwright (mobile and tablet viewports).
- Services: llm-steward (memory: lite-mem in-process by default; KB: lite-rag in-process; namespace config
  `deploy/steward/banyan.yaml`), Ollama on the host (`bge-m3:567m` embeddings, `qwen3:8b` for steward's
  extraction and optionally Banyan's cheap roles), PostgreSQL + pgvector in Docker.

## Commands

```
docker compose up -d db                         # PostgreSQL + pgvector
cd backend && uv run banyan migrate             # Alembic upgrade head
cd backend && uv run banyan serve               # API on 127.0.0.1:8800
cd backend && uv run banyan worker              # job worker alone (serve also runs one unless BANYAN_RUN_WORKER=false)
cd backend && uv run pytest                     # unit + integration, fakes only
cd backend && uv run ruff check && uv run ruff format --check && uv run mypy && uv run lint-imports
cd frontend && pnpm dev | pnpm build | pnpm test | pnpm lint | pnpm typecheck | pnpm gen:api
```

## Workflow

1. Read the stage in design §12 and the sections it names. Start a stage in plan mode.
2. Write tests with fakes first for anything in the ask pipeline, the role layer or the steward client.
3. Implement in small commits; keep the import-linter layers green.
4. Run every check (backend and frontend) before reporting.
5. **Close out every stage, in this order, once its code is done and every check passes** (details and
   the exact Codex command in `CLAUDE.md` § Stage close-out):
   1. `/simplify` on the stage's whole diff; apply its fixes; re-run every check.
   2. Codex review (`codex exec -s read-only …`) on the stage's whole diff.
   3. Judge every finding against the code: confirmed, or rejected with a reason.
   4. Fix every confirmed finding (a bug fix starts with a failing test); re-run Codex on the fix diff
      when the fixes are large or touch design §4, §6 or §8.
   5. Re-run every check and the stage's acceptance list.
   6. Tick the stage in design §12 with a close-out note (what simplify and Codex changed, what was
      rejected and why), record decisions and lessons in the project KB, then propose the commit and wait.
   Do not start or plan the next stage before this is done.
