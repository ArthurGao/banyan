import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ThemeToggle } from './ThemeToggle'

test('cycles system, light, dark and back, setting data-theme and remembering it', async () => {
  render(<ThemeToggle />)
  const root = document.documentElement
  const button = screen.getByRole('button')
  expect(button).toHaveTextContent('Theme: System')
  expect(root).not.toHaveAttribute('data-theme')

  await userEvent.click(button)
  expect(root).toHaveAttribute('data-theme', 'light')
  expect(localStorage.getItem('banyan.theme')).toBe('light')

  await userEvent.click(button)
  expect(root).toHaveAttribute('data-theme', 'dark')

  await userEvent.click(button)
  expect(root).not.toHaveAttribute('data-theme')
  expect(button).toHaveTextContent('Theme: System')
})

test('starts from the stored choice', () => {
  localStorage.setItem('banyan.theme', 'dark')
  render(<ThemeToggle />)
  expect(screen.getByRole('button')).toHaveTextContent('Theme: Dark')
})
