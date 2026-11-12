// UI language: Chinese / English, switchable (owner decision 2026-11-12). Chinese is the default
// whatever the browser says (owner 2026-11-13); English only when chosen, and that choice is kept per
// device. Technical terms (KB, token, model names) stay English in both.
import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import { en } from '@/i18n/en'
import { zh } from '@/i18n/zh'

export const LANGUAGES = ['zh', 'en'] as const
export type UiLanguage = (typeof LANGUAGES)[number]

// A new key: the old browser detector wrote `banyan.lang` on its own from the browser language.
const KEY = 'banyan.language'

export function storedLanguage(): UiLanguage {
  try {
    return localStorage.getItem(KEY) === 'en' ? 'en' : 'zh'
  } catch {
    return 'zh'
  }
}

export async function setLanguage(language: UiLanguage): Promise<void> {
  try {
    localStorage.setItem(KEY, language)
  } catch {
    // private mode or blocked storage: the choice lasts for this page only
  }
  await i18n.changeLanguage(language)
}

void i18n.use(initReactI18next).init({
  resources: { zh: { translation: zh }, en: { translation: en } },
  lng: storedLanguage(),
  fallbackLng: 'zh',
  supportedLngs: LANGUAGES,
  interpolation: { escapeValue: false },
})

export function currentLanguage(): UiLanguage {
  return i18n.resolvedLanguage === 'en' ? 'en' : 'zh'
}

export default i18n
