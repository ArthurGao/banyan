import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, test, vi } from 'vitest'
import { MemoryPage } from '@/features/memory/MemoryPage'
import { json, mockApi, renderWithApp } from '@/test/api'

afterEach(() => vi.restoreAllMocks())

const memory = (id: string, pinned = false) => ({
  id, content: `memory ${id}`, memory_type: 'preference', pinned, created_at: '2026-11-01T00:00:00Z', updated_at: '2026-11-02T00:00:00Z',
})
const candidate = { id: 'c1', title: 'ClusterIP range', content: 'From `--service-cluster-ip-range`.', reason: 'asked twice', risk_level: 'low', created_at: '2026-11-01T00:00:00Z', expires_at: null }

test('a steward outage says so instead of an empty page', async () => {
  const down = () => json({ title: 'steward_unavailable', detail: 'connection refused' }, 503)
  mockApi({ 'GET /v1/memory': down, 'GET /v1/memory/candidates': down })
  renderWithApp(<MemoryPage />)
  expect(await screen.findByRole('alert')).toHaveTextContent('Steward is unavailable')
  expect(screen.queryByText('No memories yet.')).toBeNull()
})

test('pages through memories, pins one and deletes one after confirming', async () => {
  const calls = mockApi({
    'GET /v1/memory': ({ path }) =>
      path.includes('cursor=p2') ? json({ items: [memory('m3')], next_cursor: null }) : json({ items: [memory('m1'), memory('m2', true)], next_cursor: 'p2' }),
    'GET /v1/memory/candidates': () => json({ items: [], next_cursor: null }),
    'PATCH /v1/memory/m1': () => json(memory('m1', true)),
    'DELETE /v1/memory/m2': () => new Response(null, { status: 204 }),
  })
  renderWithApp(<MemoryPage />)
  expect(await screen.findByText('memory m1')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Load more' }))
  expect(await screen.findByText('memory m3')).toBeInTheDocument()

  await userEvent.click(screen.getAllByRole('button', { name: 'Pin' })[0]!)
  await waitFor(() => { expect(calls).toContainEqual({ method: 'PATCH', path: '/v1/memory/m1', body: { pinned: true } }); })

  const row = screen.getByText('memory m2').closest('li')!
  await userEvent.click(row.querySelector('button[aria-label="Delete"]')!)
  expect(calls.some((c) => c.method === 'DELETE')).toBe(false) // not before confirming
  await userEvent.click(screen.getByRole('dialog').querySelector('button.bg-destructive, button[data-variant="destructive"]')!)
  await waitFor(() => { expect(calls.some((c) => c.method === 'DELETE' && c.path === '/v1/memory/m2')).toBe(true); })
})

test('a KB card is accepted with the note', async () => {
  const calls = mockApi({
    'GET /v1/memory': () => json({ items: [], next_cursor: null }),
    'GET /v1/memory/candidates': () => json({ items: [candidate], next_cursor: null }),
    'POST /v1/memory/candidates/c1': () => new Response(null, { status: 204 }),
  })
  renderWithApp(<MemoryPage />)
  expect(await screen.findByText('ClusterIP range')).toBeInTheDocument()
  expect(screen.getByText('Low risk')).toBeInTheDocument()
  await userEvent.type(screen.getByRole('textbox', { name: 'Note (optional)' }), 'checked the docs')
  await userEvent.click(screen.getByRole('button', { name: 'Add to the KB' }))
  await waitFor(() => { expect(calls).toContainEqual({ method: 'POST', path: '/v1/memory/candidates/c1', body: { decision: 'accept', note: 'checked the docs' } }); },
  )
})
