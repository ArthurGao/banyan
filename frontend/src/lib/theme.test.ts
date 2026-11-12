import { expect, test } from 'vitest'
import { nextTheme, setTheme, storedTheme } from './theme'

// Comp A is light; the owner chose it as the default (2026-11-13). Dark and system stay manual choices.
test('with nothing stored the theme is light, not the OS setting', () => {
  expect(storedTheme()).toBe('light')
})

test('a stored choice wins, system included', () => {
  localStorage.setItem('banyan.theme', 'system')
  expect(storedTheme()).toBe('system')
  localStorage.setItem('banyan.theme', 'dark')
  expect(storedTheme()).toBe('dark')
})

test('cycles light, dark, system and back', () => {
  expect([nextTheme('light'), nextTheme('dark'), nextTheme('system')]).toEqual(['dark', 'system', 'light'])
})

test('choosing a theme sets data-theme and remembers it; system removes the override', () => {
  const root = document.documentElement
  setTheme('dark')
  expect(root).toHaveAttribute('data-theme', 'dark')
  expect(localStorage.getItem('banyan.theme')).toBe('dark')
  setTheme('system')
  expect(root).not.toHaveAttribute('data-theme')
  expect(storedTheme()).toBe('system')
})
