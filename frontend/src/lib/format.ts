import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'

/** Numbers in the UI language (1,234 / 1,234). */
export function useFormatNumber(): (n: number) => string {
  const { i18n } = useTranslation()
  return (n) => n.toLocaleString(i18n.resolvedLanguage)
}

/** Token counts in the top bar: 38k, 1.2m. Kept in the short Latin form in both languages. */
const compact = new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 })
export const formatCompact = (n: number): string => compact.format(n).toLowerCase()

/** A date as people say it: today, yesterday, 3 days ago, then the date (branch lists, comp A). */
export function useFormatDay(now: () => Date = () => new Date()): (iso: string) => string {
  const { i18n } = useTranslation()
  const lang = i18n.resolvedLanguage
  // Intl formatters are costly to build: once per language, not per date.
  const { relative, date } = useMemo(
    () => ({ relative: new Intl.RelativeTimeFormat(lang, { numeric: 'auto' }), date: new Intl.DateTimeFormat(lang, { month: 'short', day: 'numeric' }) }),
    [lang],
  )
  return (iso) => {
    const day = (d: Date) => Date.UTC(d.getFullYear(), d.getMonth(), d.getDate())
    const then = new Date(iso)
    const days = Math.round((day(now()) - day(then)) / 86_400_000)
    return days >= 0 && days < 7 ? relative.format(-days, 'day') : date.format(then)
  }
}
