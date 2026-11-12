import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, test, vi } from 'vitest'
import { Outlet, Route, Routes } from 'react-router'
import type { TopicContext } from '@/features/topic/context'
import { TopicSettings } from '@/features/topics/TopicSettings'
import { readingOrder } from '@/lib/outline'
import { json, mockApi, renderWithApp } from '@/test/api'
import { TOPIC } from '@/test/fixtures'

afterEach(() => vi.restoreAllMocks())

const context: TopicContext = { detail: { ...TOPIC, order: readingOrder(TOPIC.nodes) }, run: undefined }

function renderSettings() {
  renderWithApp(
    <Routes>
      <Route path="/" element={<p>Topic list</p>} />
      <Route path="/topics/:topicId" element={<Outlet context={context} />}>
        <Route path="settings" element={<TopicSettings />} />
      </Route>
    </Routes>,
    '/topics/t1/settings',
  )
}

test('deleting a topic asks first, can retire its KB entries, then goes back to the list', async () => {
  const calls = mockApi({ 'DELETE /v1/topics/t1': () => json({ proposed_entry_ids: ['e1'] }), 'GET /v1/topics': () => json([]) })
  renderSettings()
  await userEvent.click(screen.getByRole('button', { name: 'Delete topic' }))
  const dialog = screen.getByRole('dialog')
  expect(dialog).toHaveTextContent('Delete “Kubernetes”')
  await userEvent.click(screen.getByRole('checkbox', { name: 'Also deprecate the KB entries it wrote' }))
  await userEvent.click(screen.getByRole('button', { name: 'Delete' }))
  await waitFor(() => { expect(calls).toContainEqual({ method: 'DELETE', path: '/v1/topics/t1?deprecate_entries=true', body: undefined }) })
  expect(await screen.findByText('Topic list')).toBeInTheDocument()
})

test('cancelling the confirmation deletes nothing', async () => {
  const calls = mockApi({})
  renderSettings()
  await userEvent.click(screen.getByRole('button', { name: 'Delete topic' }))
  await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
  expect(calls.filter((c) => c.method === 'DELETE')).toEqual([])
  expect(screen.queryByRole('dialog')).toBeNull()
})
