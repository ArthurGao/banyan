// Progress of "generate all" at the top of the topic page (design §11.2): done / total, tokens /
// budget, pause, resume, cancel, add budget; failed nodes are retried from the outline.
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { RunOut, RunPatch } from '@/api/generated'
import { api } from '@/api/endpoints'
import { QueryError } from '@/components/QueryState'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Progress } from '@/components/ui/progress'
import { UNFINISHED } from '@/features/run/useRun'
import { useFormatNumber } from '@/lib/format'
import { keys } from '@/lib/queries'

export function RunProgress({ run }: { run: RunOut }) {
  const { t } = useTranslation()
  const fmt = useFormatNumber()
  const queryClient = useQueryClient()
  const [adding, setAdding] = useState(false)
  const [amount, setAmount] = useState('')
  const change = useMutation({
    mutationFn: (body: RunPatch) => api.changeRun(run.id, body),
    onSuccess: (next) => {
      queryClient.setQueryData(keys.run(run.id), next)
      setAdding(false)
      setAmount('')
    },
  })
  const failed = run.nodes.filter((n) => n.status === 'failed').length
  if (!UNFINISHED.includes(run.status) && failed === 0) return null
  const done = run.nodes.filter((n) => n.status === 'done').length
  const label =
    run.status === 'budget_exhausted' ? t('run.status.budget_exhausted', { used: fmt(run.tokens_used) }) : t(`run.status.${run.status}`)
  return (
    <section aria-label={t('run.title')} data-run-status={run.status} className="mb-6 border-y py-3">
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2 text-sm">
        <span className={run.status === 'budget_exhausted' ? 'text-warn' : 'font-medium'}>{label}</span>
        <span className="text-muted-foreground tabular-nums">
          {t('run.progress', { done, total: run.nodes.length })} · {t('run.tokens', { used: fmt(run.tokens_used), budget: fmt(run.token_budget) })}
        </span>
      </div>
      <Progress value={run.nodes.length ? (done / run.nodes.length) * 100 : 0} aria-label={t('run.progress', { done, total: run.nodes.length })} />
      {UNFINISHED.includes(run.status) && (
        <div className="mt-3 flex flex-wrap gap-2">
          {run.status === 'running' && (
            <Button variant="outline" size="sm" onClick={() => { change.mutate({ action: 'pause' }); }}>
              {t('run.pause')}
            </Button>
          )}
          {run.status === 'paused' && (
            <Button variant="outline" size="sm" onClick={() => { change.mutate({ action: 'resume' }); }}>
              {t('run.resume')}
            </Button>
          )}
          <Button variant="outline" size="sm" onClick={() => { setAdding(!adding); }}>
            {t('run.addBudget')}
          </Button>
          <Button variant="ghost" size="sm" onClick={() => { change.mutate({ action: 'cancel' }); }}>
            {t('run.cancelRun')}
          </Button>
        </div>
      )}
      {adding && (
        <form
          className="mt-3 flex gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            if (Number(amount) > 0) change.mutate({ add_budget: Number(amount) })
          }}
        >
          <Input type="number" inputMode="numeric" min={1} aria-label={t('run.addAmount')} placeholder={t('run.addAmount')} value={amount} onChange={(e) => { setAmount(e.target.value); }} />
          <Button type="submit" disabled={!(Number(amount) > 0)}>
            {t('app.save')}
          </Button>
        </form>
      )}
      {change.error && <QueryError error={change.error} />}
    </section>
  )
}
