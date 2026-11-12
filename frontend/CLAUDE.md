@AGENTS.md

# Claude Code notes — frontend

- Regenerate the API client with `pnpm gen:api` after the backend's `openapi.json` changes; never edit
  files under `src/api/generated/`.
- Check every new screen at three viewports before reporting: 390×844 (phone), 820×1180 (tablet portrait),
  1366×1024 (tablet landscape / desktop). Playwright projects exist for all three.
- Use the shadcn/ui components already in `src/components/ui/`; add new ones with the shadcn CLI, not by hand.
