// UI language: Chinese / English, switchable (owner decision 2026-11-12). Technical terms (KB, token,
// model names) stay English in both. The choice is kept per device.
import i18n from 'i18next'
import LanguageDetector from 'i18next-browser-languagedetector'
import { initReactI18next } from 'react-i18next'
import { en } from '@/i18n/en'
import { zh } from '@/i18n/zh'

export const LANGUAGES = ['zh', 'en'] as const
export type UiLanguage = (typeof LANGUAGES)[number]

void i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: { zh: { translation: zh }, en: { translation: en } },
    fallbackLng: 'zh',
    supportedLngs: LANGUAGES,
    nonExplicitSupportedLngs: true,
    interpolation: { escapeValue: false },
    detection: { order: ['localStorage', 'navigator'], lookupLocalStorage: 'banyan.lang', caches: ['localStorage'] },
  })

export function currentLanguage(): UiLanguage {
  return i18n.resolvedLanguage === 'en' ? 'en' : 'zh'
}

export default i18n
