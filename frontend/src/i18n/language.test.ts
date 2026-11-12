import { afterEach, expect, test, vi } from 'vitest'
import i18n, { setLanguage, storedLanguage } from '@/i18n'

afterEach(async () => {
  vi.restoreAllMocks()
  await i18n.changeLanguage('en') // the suite reads English (test/setup.ts)
})

// Chinese is the default whatever the browser says (owner 2026-11-13); English only by choice.
test('an English browser with nothing stored gets Chinese', () => {
  vi.spyOn(navigator, 'language', 'get').mockReturnValue('en-US')
  vi.spyOn(navigator, 'languages', 'get').mockReturnValue(['en-US'])
  expect(storedLanguage()).toBe('zh')
})

test('the value the old detector cached on its own is ignored', () => {
  localStorage.setItem('banyan.lang', 'en')
  expect(storedLanguage()).toBe('zh')
})

test('choosing English switches and remembers it', async () => {
  await setLanguage('en')
  expect(i18n.resolvedLanguage).toBe('en')
  expect(localStorage.getItem('banyan.language')).toBe('en')
  expect(storedLanguage()).toBe('en')
  await setLanguage('zh')
  expect(i18n.resolvedLanguage).toBe('zh')
  expect(storedLanguage()).toBe('zh')
})
