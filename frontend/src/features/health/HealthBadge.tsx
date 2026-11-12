import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from '@/api/endpoints'
import { ApiError } from '@/api/http'
import { keys } from '@/lib/queries'
import { cn } from '@/lib/utils'

type Shown = 'ok' | 'degraded' | 'down' | 'unreachable'

// A 503 is the API reporting itself down; only a failed request is `unreachable`.
async function fetchStatus(): Promise<Shown> {
  try {
    return (await api.health()).status
  } catch (e) {
    return e instanceof ApiError && e.status === 503 ? 'down' : 'unreachable'
  }
}

const TONE: Record<Shown, string> = { ok: 'bg-muted-foreground', degraded: 'bg-warn', down: 'bg-destructive', unreachable: 'bg-destructive' }

export function HealthBadge() {
  const { t } = useTranslation()
  const { data } = useQuery({ queryKey: keys.health, queryFn: fetchStatus, refetchInterval: 30_000 })
  const text = t(`app.health.${data ?? 'checking'}`)
  return (
    <span role="status" title={text} data-health={data ?? 'checking'} className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
      <span aria-hidden className={cn('size-2 rounded-full', data ? TONE[data] : 'bg-border')} />
      <span className={cn(data === 'ok' && 'sr-only md:not-sr-only')}>{text}</span>
    </span>
  )
}
