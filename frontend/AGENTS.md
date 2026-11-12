# Banyan frontend — Agent Guide

Adds frontend rules to the root `AGENTS.md`. React + Vite + TypeScript, pnpm. Design §11 is the spec.

## Layout

```
src/
  api/generated/     @hey-api/openapi-ts output — never edit
  api/sse.ts         SSE client: typed events (design §9), abortable
  app/               router, providers (QueryClient, theme), layout shell
  features/
    topics/          topic list, new topic, topic settings (instructions)
    outline/         outline tree: progress, edit, reorder, insert prerequisite
    node/            node page: explanation, branch list, notes preview
    branch/          branch thread: messages, composer, source badges, feedback
    memory/          memory candidate cards (confirm / reject)
    export/          HTML preview (iframe), download, share
    usage/           token and path stats per topic
  components/ui/     shadcn/ui
  components/        Markdown (GFM, KaTeX, Shiki), SourceBadge, ResponsiveSplit, BottomSheet
  lib/               formatting, hooks
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
5. **Theme:** CSS variables on `:root` with dark values under `prefers-color-scheme: dark`; a manual toggle
   overrides via `data-theme`.
6. **Accessibility:** keyboard navigation for the outline tree, visible focus, touch targets ≥44px.
7. **PWA:** installable, app shell cached; API responses are not cached offline in v1.
8. **No secrets in the bundle.** The API token is entered once and kept in `localStorage`.

## Testing

Vitest + Testing Library for components and the SSE parser (with recorded event fixtures);
Playwright for the main flow (create topic → explain node → ask in a branch → feedback → export) on the
three viewports, against a backend running with fakes (`BANYAN_FAKES=1`).
