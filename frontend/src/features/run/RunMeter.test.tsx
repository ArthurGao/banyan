import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, test, vi } from 'vitest'
import type { RunOut } from '@/api/generated'
import { RunMeter } from '@/features/run/RunMeter'
import { json, mockApi, renderWithApp } from '@/test/api'
import { TOPIC } from '@/test/fixtures'

afterEach(() => vi.restoreAllMocks())

const run = (status: RunOut['status']): RunOut => ({
  id: 'r1', topic_id: 't1', status, token_budget: 60_000, tokens_used: 38_200, created_at: '2026-11-01T00:00:00Z',
  nodes: [{ node_id: 'n1', status: 'done' }, { node_id: 'n2', status: 'generating' }, { node_id: 'n3', status: 'none' }],
  estimate: { nodes: 3, tokens_per_node: 20_000, total: 60_000, basis: 'default' },
})

test('the top bar shows an unfinished run and pauses it', async () => {
  const calls = mockApi({
    'GET /v1/topics/t1': () => json({ ...TOPIC, latest_run_id: 'r1' }),
    'GET /v1/generation-runs/r1': () => json(run('running')),
    'PATCH /v1/generation-runs/r1': () => json(run('paused')),
  })
  renderWithApp(<RunMeter topicId="t1" />)
  const bar = await screen.findByRole('progressbar', { name: 'Generate all 1 / 3' })
  expect(bar).toHaveAttribute('aria-valuenow', String((1 / 3) * 100)) // percent of nodes done
  expect(screen.getByText('38.2k / 60k')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Pause' }))
  await waitFor(() => { expect(calls).toContainEqual({ method: 'PATCH', path: '/v1/generation-runs/r1', body: { action: 'pause' } }) })
  expect(await screen.findByRole('button', { name: 'Resume' })).toBeInTheDocument()
})

test('a finished run leaves the top bar alone', async () => {
  mockApi({ 'GET /v1/topics/t1': () => json({ ...TOPIC, latest_run_id: 'r1' }), 'GET /v1/generation-runs/r1': () => json(run('done')) })
  const { container } = renderWithApp(<RunMeter topicId="t1" />)
  await waitFor(() => { expect(container.querySelector('[data-run-meter]')).toBeNull() })
  await new Promise((r) => setTimeout(r, 50))
  expect(container.querySelector('[data-run-meter]')).toBeNull()
})
