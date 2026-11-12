import { useSyncExternalStore } from 'react'

// Theme choice (frontend/AGENTS.md rule 5). Light is the default (comp A, owner 2026-11-13); `system`
// removes the override so `prefers-color-scheme` decides. index.html applies the stored choice before
// the first paint. The choice is a per-device convenience, so storage may fail.

export type ThemeChoice = 'system' | 'light' | 'dark'

const KEY = 'banyan.theme'
const ORDER: ThemeChoice[] = ['light', 'dark', 'system']

export function nextTheme(current: ThemeChoice): ThemeChoice {
  return ORDER[(ORDER.indexOf(current) + 1) % ORDER.length] ?? 'light'
}

/** Sets the attribute only: main.tsx applies the stored choice at start without making it explicit. */
export function applyTheme(choice: ThemeChoice, root: HTMLElement = document.documentElement) {
  if (choice === 'system') root.removeAttribute('data-theme')
  else root.setAttribute('data-theme', choice)
}

export function storedTheme(): ThemeChoice {
  try {
    const value = localStorage.getItem(KEY)
    return value === 'dark' || value === 'system' ? value : 'light'
  } catch {
    return 'light'
  }
}

// The one store of the choice: the toggle, the phone menu and the toaster all read it.
const listeners = new Set<() => void>()

export function setTheme(choice: ThemeChoice): void {
  applyTheme(choice)
  try {
    localStorage.setItem(KEY, choice)
  } catch {
    // private mode or blocked storage: the choice lasts for this page only
  }
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
