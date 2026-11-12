# Banyan frontend — Agent Guide

Adds frontend rules to the root `AGENTS.md`. React + Vite + TypeScript, pnpm. Design §11 is the spec.

## Layout

```
src/
  api/generated/     @hey-api/openapi-ts output, types only — never edit (`pnpm gen:api`)
  api/http.ts        the only place requests are made: Bearer token, RFC 7807 → ApiError, 401 drops the token
  api/endpoints.ts   one function per route of design §9
  api/sse.ts         SSE parser and stream state (design §9): typed events, de-duplication, abortable
  app/               App (providers), router, Shell (top bar, quick ask)
  i18n/              zh (reference) and en strings; UI language switchable
  features/
    auth/            token gate
    topics/          topic list, new-topic dialog, topic settings (instructions)
    topic/           topic layout (ResponsiveSplit) and home (overview, explain mode, export)
    outline/         outline tree (numbering, keyboard, edit menu, drag reorder), draft editor
    node/            node page: explanation, branch list, ask here
    branch/          branch thread: messages, composer, re-answers, move, close
    message/         assistant message: source badge, citations, provenance, feedback; streaming view
    notes/           notes pane
    run/             generate all: estimate dialog, progress bar
    quickask/        quick ask dialog (classify → place → stream)
    memory/          memories (pin, delete) and KB cards (accept / reject)
    export/          HTML preview (sandboxed iframe), download, share
    usage/           KB hit rate, tokens per role, savings per topic
  components/ui/     shadcn/ui (added with the CLI; shadows removed, 44px targets on touch)
  components/        Markdown (GFM, KaTeX, Shiki on demand), SourceBadge, StatusLine, ResponsiveSplit, ConfirmDialog
  lib/               outline tree helpers, layout breakpoints, useStream, query keys and hooks
  test/              setup, fetch-level API mock, fixtures
```

## Rules

1. **Server state lives in TanStack Query**; local UI state in components. No global store unless a
   feature cannot do without one.
2. **Responsive by layout, not by duplicate screens.** One `ResponsiveSplit` shell: three panes ≥1200px
   (outline | node + branch | notes), two panes 768–1199px (outline in a drawer), one pane <768px with
   drill-down routes (topic → node → branch) and the outline in a bottom sheet.
3. **Every assistant message shows its source** (`kb` / `llm` / `web` / `mixed`) with a badge and its
   citations; feedback buttons ("wrong", "incomplete") are reachable with a thumb on a phone.
4. **Streaming:** answers render progressively from SSE `token` events; `path` events update the status
   line ("checking knowledge base…", "searching the web…"). Aborting a stream cancels the request.
5. **Theme:** CSS variables on `:root` (comp A values, design §11), light by default; dark values under `prefers-color-scheme: dark` apply only when the user picks "system"; a manual toggle
   overrides via `data-theme`.
6. **Accessibility:** keyboard navigation for the outline tree, visible focus, touch targets ≥44px.
7. **No PWA in v1** (owner decision 2026-11-13): the phone uses the browser over Tailscale; nothing is cached offline.
8. **No secrets in the bundle.** The API token is entered once and kept in `localStorage`.

## Testing

Vitest + Testing Library for components and the SSE parser (with recorded event fixtures); the API is
mocked only at `fetch` (`src/test/api.tsx`). Playwright (`pnpm e2e`) runs the main flow (create topic →
outline → explain node → ask in a branch → feedback → export) and generate-all + quick ask on the three
viewports against the real backend with fakes: `e2e/serve.sh` builds the frontend, drops and migrates the
throwaway schema `banyan_e2e` and serves both on 127.0.0.1:8812 (needs `BANYAN_DATABASE_URL`, from
`backend/.env` locally). Tests run one at a time (one backend, one schema).
