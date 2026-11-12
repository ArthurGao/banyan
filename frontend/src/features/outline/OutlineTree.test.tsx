import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, test, vi } from 'vitest'
import { OutlineTree } from '@/features/outline/OutlineTree'
import { json, mockApi, renderWithApp } from '@/test/api'
import { NODES, TOPIC } from '@/test/fixtures'

afterEach(() => vi.restoreAllMocks())

test('numbers the tree and moves focus with the arrow keys', async () => {
  renderWithApp(<OutlineTree topicId="t1" nodes={NODES} activeNodeId="n3" />)
  const links = screen.getAllByRole('link')
  expect(links.map((l) => l.textContent)).toEqual(['1PodsNot started', '2ServicesNot started', '2.1ClusterIPNot started'])
  expect(screen.getAllByRole('treeitem')[2]).toHaveAttribute('aria-current', 'page')
  links[0]!.focus()
  await userEvent.keyboard('{ArrowDown}')
  expect(links[1]).toHaveFocus()
  await userEvent.keyboard('{End}')
  expect(links[2]).toHaveFocus()
  await userEvent.keyboard('{ArrowUp}{ArrowUp}{ArrowUp}')
  expect(links[0]).toHaveFocus()
})

test('a failed background explanation offers a retry', async () => {
  const onRetry = vi.fn()
  renderWithApp(<OutlineTree topicId="t1" nodes={NODES} explanation={{ n2: 'failed', n1: 'generating' }} onRetry={onRetry} />)
  expect(screen.getByText('Generating')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Failed, retry' }))
  expect(onRetry).toHaveBeenCalledWith('n2')
})

test('in edit mode, "move up" moves a node one place among its siblings', async () => {
  const calls = mockApi({ 'POST /v1/nodes/n2/move': () => json(NODES[1]), 'GET /v1/topics/t1': () => json(TOPIC) })
  renderWithApp(<OutlineTree topicId="t1" nodes={NODES} editable />)
  await userEvent.click(screen.getByRole('button', { name: 'Edit: Services' }))
  await userEvent.click(await screen.findByRole('menuitem', { name: 'Move up' }))
  await waitFor(() => { expect(calls).toContainEqual({ method: 'POST', path: '/v1/nodes/n2/move', body: { parent_id: null, position: 0 } }); })
})

test('a prerequisite is inserted before the node, at its position', async () => {
  const calls = mockApi({ 'POST /v1/topics/t1/nodes': () => json(NODES[0], 201), 'GET /v1/topics/t1': () => json(TOPIC) })
  renderWithApp(<OutlineTree topicId="t1" nodes={NODES} editable />)
  await userEvent.click(screen.getByRole('button', { name: 'Edit: ClusterIP' }))
  await userEvent.click(await screen.findByRole('menuitem', { name: 'Insert a prerequisite before' }))
  await userEvent.type(screen.getByLabelText('Node title'), 'IP addressing')
  await userEvent.click(screen.getByRole('button', { name: 'Save' }))
  await waitFor(() => { expect(calls).toContainEqual({
      method: 'POST', path: '/v1/topics/t1/nodes',
      body: { title: 'IP addressing', summary: null, parent_id: 'n2', position: 0, origin: 'prerequisite' },
    }); },
  )
})

test('each node shows its progress as a dot, and the open node offers a prerequisite before it', async () => {
  const calls = mockApi({ 'POST /v1/topics/t1/nodes': () => json(NODES[0], 201), 'GET /v1/topics/t1': () => json(TOPIC) })
  const nodes = [{ ...NODES[0]!, status: 'done' as const }, { ...NODES[1]!, status: 'learning' as const }, NODES[2]!]
  const { container } = renderWithApp(<OutlineTree topicId="t1" nodes={nodes} activeNodeId="n3" />)
  expect([...container.querySelectorAll('[data-status]')].map((d) => d.getAttribute('data-status'))).toEqual(['done', 'learning', 'not_started'])
  await userEvent.click(screen.getByRole('button', { name: '+ Insert a prerequisite' }))
  await userEvent.type(screen.getByLabelText('Node title'), 'IP addressing')
  await userEvent.click(screen.getByRole('button', { name: 'Save' }))
  await waitFor(() => {
    expect(calls).toContainEqual({
      method: 'POST', path: '/v1/topics/t1/nodes',
      body: { title: 'IP addressing', summary: null, parent_id: 'n2', position: 0, origin: 'prerequisite' },
    })
  })
})
