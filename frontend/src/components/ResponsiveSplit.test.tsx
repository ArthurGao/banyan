import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ResponsiveSplit } from './ResponsiveSplit'
import { layoutFor } from '@/lib/layout'

function renderAt(width: number) {
  window.innerWidth = width
  render(<ResponsiveSplit outline="OUTLINE" main="MAIN" notes="NOTES" />)
  const panes = () => [...document.querySelectorAll('[data-pane]')].map((el) => el.getAttribute('data-pane'))
  return { panes }
}

test.each([
  [390, 'one'],
  [767, 'one'],
  [768, 'two'],
  [1199, 'two'],
  [1200, 'three'],
  [1366, 'three'],
])('width %i uses the %s-pane layout', (width, layout) => {
  expect(layoutFor(width)).toBe(layout)
})

test('three panes at desktop width', () => {
  const { panes } = renderAt(1366)
  expect(panes()).toEqual(['outline', 'main', 'notes'])
})

test('two panes with the outline in a drawer on a tablet', async () => {
  const { panes } = renderAt(820)
  expect(panes()).toEqual(['main', 'notes'])
  await userEvent.click(screen.getByRole('button', { name: 'Outline' }))
  expect(panes()).toEqual(['main', 'notes', 'outline'])
  expect(screen.getByRole('dialog', { name: 'Outline' })).toHaveTextContent('OUTLINE')
})

test('one pane with the outline in a bottom sheet on a phone', async () => {
  const { panes } = renderAt(390)
  expect(panes()).toEqual(['main'])
  await userEvent.click(screen.getByRole('button', { name: 'Outline' }))
  expect(panes()).toEqual(['main', 'outline'])
  await userEvent.keyboard('{Escape}')
  await userEvent.click(screen.getByRole('button', { name: 'Notes' }))
  expect(screen.getByRole('dialog', { name: 'Notes' })).toHaveTextContent('NOTES')
})

test('follows a resize across a breakpoint', () => {
  const { panes } = renderAt(1366)
  act(() => {
    window.innerWidth = 820
    window.dispatchEvent(new Event('resize'))
  })
  expect(panes()).toEqual(['main', 'notes'])
})
