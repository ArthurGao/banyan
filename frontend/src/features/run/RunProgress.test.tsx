import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, test, vi } from 'vitest'
import type { RunOut } from '@/api/generated'
import { RunProgress } from '@/features/run/RunProgress'
import { json, mockApi, renderWithApp } from '@/test/api'

afterEach(() => vi.restoreAllMocks())

const run = (status: RunOut['status'], nodes: RunOut['nodes']): RunOut => ({
  id: 'r1', topic_id: 't1', status, token_budget: 60_000, tokens_used: 61_000, created_at: '2026-11-01T00:00:00Z', nodes,
  estimate: { nodes: 3, tokens_per_node: 20_000, total: 60_000, basis: 'default' },
})

test('a run out of budget says so and takes more budget', async () => {
  const calls = mockApi({ 'PATCH /v1/generation-runs/r1': () => json(run('running', [])) })
  renderWithApp(<RunProgress run={run('budget_exhausted', [{ node_id: 'a', status: 'done' }, { node_id: 'b', status: 'none' }])} />)
  expect(screen.getByText('Budget used up: 61,000 used; add budget to continue')).toBeInTheDocument()
  expect(screen.getByText(/1 \/ 2 nodes/)).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Add budget' }))
  await userEvent.type(screen.getByRole('spinbutton', { name: 'Tokens to add' }), '40000')
  await userEvent.click(screen.getByRole('button', { name: 'Save' }))
  await waitFor(() => { expect(calls).toContainEqual({ method: 'PATCH', path: '/v1/generation-runs/r1', body: { add_budget: 40000 } }); })
})

test('a running run pauses; a finished run without failures shows nothing', () => {
  const { unmount } = renderWithApp(<RunProgress run={run('running', [{ node_id: 'a', status: 'generating' }])} />)
  expect(screen.getByRole('button', { name: 'Pause' })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Resume' })).toBeNull()
  unmount()
  const { container } = renderWithApp(<RunProgress run={run('done', [{ node_id: 'a', status: 'done' }])} />)
  expect(container).toBeEmptyDOMElement()
})

test('a finished run with a failed node stays visible', () => {
  renderWithApp(<RunProgress run={run('done', [{ node_id: 'a', status: 'failed' }])} />)
  expect(screen.getByText('Done')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Pause' })).toBeNull()
})
