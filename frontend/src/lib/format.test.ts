import { renderHook } from '@testing-library/react'
import { expect, test } from 'vitest'
import { useFormatDay } from './format'

const now = () => new Date('2026-11-13T10:00:00')

test('branch dates read as today, yesterday, days ago, then a date', () => {
  const { result } = renderHook(() => useFormatDay(now))
  const day = result.current
  expect(day('2026-11-13T08:00:00')).toBe('today')
  expect(day('2026-11-12T23:00:00')).toBe('yesterday')
  expect(day('2026-11-10T09:00:00')).toBe('3 days ago')
  expect(day('2026-10-28T09:00:00')).toBe('Oct 28')
})
