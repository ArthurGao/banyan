// The API token is entered once and kept in localStorage (frontend/AGENTS.md rule 8); a 401 drops it
// and brings this form back.
import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useState, type SyntheticEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '@/api/endpoints'
import { ApiError, TOKEN_CLEARED, getToken, setToken } from '@/api/http'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

export function TokenGate({ children }: { children: ReactNode }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [hasToken, setHasToken] = useState(() => getToken() !== null)
  const [refused, setRefused] = useState(false)
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    const onCleared = () => {
      setHasToken(false)
      setRefused(true)
      queryClient.clear()
    }
    window.addEventListener(TOKEN_CLEARED, onCleared)
    return () => { window.removeEventListener(TOKEN_CLEARED, onCleared); }
  }, [queryClient])

  if (hasToken) return <>{children}</>

  const submit = async (e: SyntheticEvent) => {
    e.preventDefault()
    setBusy(true)
    setToken(value.trim())
    try {
      await api.topics()
      setRefused(false)
      setHasToken(true)
    } catch (err) {
      if (!(err instanceof ApiError && err.status === 401)) {
        setHasToken(true) // the token may be right; the screens report the outage
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="mx-auto flex min-h-full max-w-sm flex-col justify-center gap-4 p-6">
      <h1 className="font-serif text-2xl font-semibold">{t('token.title')}</h1>
      <p className="text-sm text-muted-foreground">{t('token.body')}</p>
      <form onSubmit={(e) => void submit(e)} className="flex flex-col gap-3">
        <Label htmlFor="token">{t('token.label')}</Label>
        <Input id="token" type="password" autoComplete="current-password" value={value} onChange={(e) => { setValue(e.target.value); }} required />
        {refused && <p role="alert" className="text-sm text-destructive">{t('token.invalid')}</p>}
        <Button type="submit" disabled={busy || !value.trim()}>
          {t('token.submit')}
        </Button>
      </form>
    </main>
  )
}
