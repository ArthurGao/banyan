// Types generated from the backend's OpenAPI spec, the only contract between backend and frontend
// (AGENTS.md). The backend writes ./openapi.json (`uv run banyan openapi`); regenerate with
// `pnpm gen:api`. Only types are generated: requests go through the small hand-written client in
// src/api/http.ts. Never edit the output.
import { defineConfig } from '@hey-api/openapi-ts'

export default defineConfig({
  input: './openapi.json',
  output: { path: 'src/api/generated' },
  plugins: ['@hey-api/typescript'],
})
