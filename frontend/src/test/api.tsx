// Test helpers: the API mocked at the network boundary (fetch), a fresh QueryClient and router.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, type RenderResult } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router'
import { vi } from 'vitest'

export interface Call { method: string; path: string; body: unknown }
type Handler = (call: Call) => Response | Promise<Response>

export const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

/** Routes `METHOD /path` (query string ignored) to handlers; records every call. */
export function mockApi(routes: Record<string, Handler>) {
  const calls: Call[] = []
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = new URL(input instanceof Request ? input.url : input.toString(), 'http://test')
    const method = init?.method ?? 'GET'
    const call = { method, path: url.pathname + url.search, body: typeof init?.body === 'string' ? (JSON.parse(init.body) as unknown) : undefined }
    calls.push(call)
    const handler = routes[`${method} ${url.pathname}`]
    if (!handler) return json({ title: 'not mocked', detail: `${method} ${url.pathname}` }, 404)
    return handler(call)
  })
  return calls
}

export function renderWithApp(ui: ReactNode, path = '/'): RenderResult {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  )
}
