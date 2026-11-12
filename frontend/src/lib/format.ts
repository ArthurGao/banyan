import { useTranslation } from 'react-i18next'

/** Numbers in the UI language (1,234 / 1,234). */
export function useFormatNumber(): (n: number) => string {
  const { i18n } = useTranslation()
  return (n) => n.toLocaleString(i18n.resolvedLanguage)
}
