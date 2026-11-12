import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, beforeAll } from 'vitest'
import i18n from '@/i18n'

// jsdom lacks what Radix Select calls on open.
Element.prototype.hasPointerCapture = () => false
Element.prototype.releasePointerCapture = () => undefined
Element.prototype.scrollIntoView = () => undefined
const noop = () => undefined
globalThis.ResizeObserver ??= class {
  observe = noop
  unobserve = noop
  disconnect = noop
}

// Tests read English strings; the app defaults to Chinese.
beforeAll(async () => {
  await i18n.changeLanguage('en')
})

afterEach(() => {
  cleanup()
  localStorage.clear()
})
