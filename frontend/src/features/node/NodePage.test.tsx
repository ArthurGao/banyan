import { screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { Outlet, Route, Routes } from 'react-router'
import { NodePage } from '@/features/node/NodePage'
import type { TopicContext } from '@/features/topic/context'
import { readingOrder } from '@/lib/outline'
import { json, mockApi, renderWithApp } from '@/test/api'
import { NODES, TOPIC } from '@/test/fixtures'

afterEach(() => vi.restoreAllMocks())

const context: TopicContext = { detail: { ...TOPIC, order: readingOrder(TOPIC.nodes) }, run: undefined }

function renderNode(id: string) {
  const node = NODES.find((n) => n.id === id)
  mockApi({ [`GET /v1/nodes/${id}`]: () => json({ node, explanation: null, explanation_status: 'failed', branches: [] }) })
  renderWithApp(
    <Routes>
      <Route path="/topics/:topicId" element={<Outlet context={context} />}>
        <Route path="nodes/:nodeId" element={<NodePage />} />
      </Route>
    </Routes>,
    `/topics/t1/nodes/${id}`,
  )
}

test('a node links to the one before and the one after it in reading order', async () => {
  renderNode('n2')
  const nav = await screen.findByRole('navigation', { name: 'Previous and next node' })
  const links = [...nav.querySelectorAll('a')]
  expect(links.map((a) => [a.getAttribute('href'), a.textContent])).toEqual([
    ['/topics/t1/nodes/n1', '1 Pods'],
    ['/topics/t1/nodes/n3', '2.1 ClusterIP'],
  ])
})

test('the first node has no previous link, the last no next', async () => {
  renderNode('n1')
  const nav = await screen.findByRole('navigation', { name: 'Previous and next node' })
  expect([...nav.querySelectorAll('a')].map((a) => a.getAttribute('href'))).toEqual(['/topics/t1/nodes/n2'])
})

test('a node page leads back to its topic overview', async () => {
  renderNode('n3')
  const back = await screen.findByRole('link', { name: 'Kubernetes' })
  expect(back).toHaveAttribute('href', '/topics/t1')
})
