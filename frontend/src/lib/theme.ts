import { useSyncExternalStore } from 'react'

// Manual theme choice (frontend/AGENTS.md rule 5). `system` removes the override so
// `prefers-color-scheme` decides. The choice is a per-device convenience, so storage may fail.

export type ThemeChoice = 'system' | 'light' | 'dark'

const KEY = 'banyan.theme'
const ORDER: ThemeChoice[] = ['system', 'light', 'dark']

export function nextTheme(current: ThemeChoice): ThemeChoice {
  return ORDER[(ORDER.indexOf(current) + 1) % ORDER.length] ?? 'system'
}

export function applyTheme(choice: ThemeChoice, root: HTMLElement = document.documentElement) {
  if (choice === 'system') root.removeAttribute('data-theme')
  else root.setAttribute('data-theme', choice)
  try {
    localStorage.setItem(KEY, choice)
  } catch {
    // private mode or blocked storage: the choice lasts for this page only
  }
}

export function storedTheme(): ThemeChoice {
  try {
    const value = localStorage.getItem(KEY)
    return value === 'light' || value === 'dark' ? value : 'system'
  } catch {
    return 'system'
  }
}

// The one store of the choice: the toggle, the phone menu and the toaster all read it.
const listeners = new Set<() => void>()

export function setTheme(choice: ThemeChoice): void {
  applyTheme(choice)
  for (const l of listeners) l()
}

export function useTheme(): { choice: ThemeChoice; cycle: () => void } {
  const choice = useSyncExternalStore(
    (l) => {
      listeners.add(l)
      return () => listeners.delete(l)
    },
    storedTheme,
  )
  return { choice, cycle: () => { setTheme(nextTheme(choice)) } }
}
