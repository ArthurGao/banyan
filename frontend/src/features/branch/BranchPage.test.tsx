import { screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { Outlet, Route, Routes } from 'react-router'
import { BranchPage } from '@/features/branch/BranchPage'
import type { TopicContext } from '@/features/topic/context'
import { readingOrder } from '@/lib/outline'
import { json, mockApi, renderWithApp } from '@/test/api'
import { TOPIC } from '@/test/fixtures'

afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
})

const context: TopicContext = { detail: { ...TOPIC, order: readingOrder(TOPIC.nodes) }, run: undefined }
const branch = { id: 'b1', node_id: 'n1', title: 'Why pods?', kind: 'question', created_at: '2026-11-01T00:00:00Z', closed_at: null }
const message = (id: string, role: 'user' | 'assistant', content: string, status = 'done') => ({
  id, branch_id: 'b1', role, content_md: content, status, source: role === 'assistant' ? 'llm' : null, path: [], citations: [],
  provenance: null, feedback: 'none', superseded_by: null, created_at: '2026-11-01T00:00:00Z',
})

test('an answer still being written (a dropped stream, another tab) appears once it is committed', async () => {
  const question = message('m1', 'user', 'Why pods?')
  let committed = false
  mockApi({
    'GET /v1/branches/b1': () => json({ branch, messages: committed ? [question, message('m2', 'assistant', 'Pods group containers.')] : [question] }),
    'GET /v1/branches/b1/messages/latest': () => {
      // Streaming on the first look; committed by the next poll.
      const latest = message('m2', 'assistant', committed ? 'Pods group containers.' : '', committed ? 'done' : 'streaming')
      committed = true
      return json(latest)
    },
  })
  renderWithApp(
    <Routes>
      <Route path="/topics/:topicId" element={<Outlet context={context} />}>
        <Route path="nodes/:nodeId/branches/:branchId" element={<BranchPage />} />
      </Route>
    </Routes>,
    '/topics/t1/nodes/n1/branches/b1',
  )
  expect(await screen.findByText('An answer is being written…')).toBeInTheDocument()
  expect(await screen.findByText('Pods group containers.', {}, { timeout: 5000 })).toBeInTheDocument()
  expect(screen.queryByText('An answer is being written…')).toBeNull()
})
