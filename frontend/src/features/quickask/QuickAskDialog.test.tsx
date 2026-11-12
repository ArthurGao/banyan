import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, test, vi } from 'vitest'
import { QuickAskDialog } from '@/features/quickask/QuickAskDialog'
import { json, mockApi, renderWithApp } from '@/test/api'
import { SUMMARY, TOPIC, sse } from '@/test/fixtures'

afterEach(() => vi.restoreAllMocks())

const answer = () =>
  sse([
    ['path', { step: 'scope', status: 'ok', detail: { message_id: 'm1', branch_id: 'b1', node_id: 'n1' } }],
    ['token', { text: 'Pods share a network namespace.' }],
    ['done', { message_id: 'm1', source: 'llm', usage: { input: 1, output: 2 }, path: [], warnings: [] }],
  ])

async function classifyWith(verdict: object) {
  const calls = mockApi({
    'GET /v1/topics': () => json([SUMMARY]),
    'GET /v1/topics/t1': () => json(TOPIC),
    'POST /v1/topics/t1/classify': () => json(verdict),
    'POST /v1/topics/t1/quick-ask': () => answer(),
  })
  renderWithApp(<QuickAskDialog open onOpenChange={() => undefined} topicId="t1" />)
  await userEvent.type(await screen.findByRole('textbox', { name: 'Quick ask' }), 'How do pods talk?')
  await userEvent.click(screen.getByRole('button', { name: 'Ask' }))
  return calls
}

test('a confident classification shows the node, which can be changed before asking', async () => {
  const calls = await classifyWith({ node_id: 'n3', confidence: 0.9, confident: true, suggested_node: null })
  expect(await screen.findByText('Filed under “ClusterIP”')).toBeInTheDocument()
  expect(calls.filter((c) => c.path.endsWith('/quick-ask'))).toEqual([]) // nothing asked yet

  await userEvent.click(screen.getByRole('button', { name: 'Pick another node' }))
  await userEvent.click(screen.getByRole('combobox', { name: 'Choose a node' }))
  await userEvent.click(await screen.findByRole('option', { name: '1 Pods' }))
  await userEvent.click(screen.getByRole('button', { name: 'Ask' }))

  expect(await screen.findByText('Pods share a network namespace.')).toBeInTheDocument()
  const ask = calls.find((c) => c.path === '/v1/topics/t1/quick-ask')
  expect(ask?.body).toEqual({ question: 'How do pods talk?', allow_web: true, node_id: 'n1' })
})

test('without a fitting node the learner decides whether to add the suggested one', async () => {
  const suggested = { title: 'NodePort', summary: 'From outside.', parent_id: 'n2', position: 1 }
  const calls = await classifyWith({ node_id: null, confidence: 0.2, confident: false, suggested_node: suggested })
  expect(await screen.findByText('The outline does not cover this. Add a node “NodePort” under “Services”?')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Add the node and ask' }))
  await waitFor(() => { expect(calls.find((c) => c.path === '/v1/topics/t1/quick-ask')?.body).toEqual({ question: 'How do pods talk?', allow_web: true, new_node: suggested }); })
})

test('a stream lost after the branch was made still leads to the branch, where the answer is recovered', async () => {
  mockApi({
    'GET /v1/topics': () => json([SUMMARY]),
    'GET /v1/topics/t1': () => json(TOPIC),
    'POST /v1/topics/t1/classify': () => json({ node_id: 'n1', confidence: 0.9, confident: true, suggested_node: null }),
    // The connection drops after the first event: the answer may still be committed.
    'POST /v1/topics/t1/quick-ask': () => sse([['path', { step: 'scope', status: 'ok', detail: { message_id: 'm1', branch_id: 'b9', node_id: 'n1' } }]]),
  })
  renderWithApp(<QuickAskDialog open onOpenChange={() => undefined} topicId="t1" />)
  await userEvent.type(await screen.findByRole('textbox', { name: 'Quick ask' }), 'How do pods talk?')
  await userEvent.click(screen.getByRole('button', { name: 'Ask' }))
  await userEvent.click(await screen.findByRole('button', { name: 'Ask' }))
  expect(await screen.findByRole('button', { name: 'Open the branch' })).toBeInTheDocument()
})
