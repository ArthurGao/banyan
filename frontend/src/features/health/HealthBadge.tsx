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

const TONE: Record<Exclude<Shown, 'ok'>, string> = { degraded: 'bg-warn', down: 'bg-destructive', unreachable: 'bg-destructive' }

export function HealthBadge() {
  const { t } = useTranslation()
  const { data } = useQuery({ queryKey: keys.health, queryFn: fetchStatus, refetchInterval: 30_000 })
  // Quiet while the API answers (comp A has no health light); it shows up only when something is wrong.
  if (!data || data === 'ok') return null
  const text = t(`app.health.${data}`)
  return (
    <span role="status" title={text} data-health={data} className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
      <span aria-hidden className={cn('size-2 rounded-full', TONE[data])} />
      <span className="sr-only md:not-sr-only">{text}</span>
    </span>
  )
}
