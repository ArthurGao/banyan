// "Generate all" shows the estimate first and starts only on confirmation (design §5.1.1).
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { TopicDetail } from '@/api/generated'
import { api } from '@/api/endpoints'
import { QueryError } from '@/components/QueryState'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useFormatNumber } from '@/lib/format'
import { keys } from '@/lib/queries'

export function RunStartDialog({ topicId, open, onOpenChange }: { topicId: string; open: boolean; onOpenChange: (o: boolean) => void }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const estimate = useQuery({ queryKey: ['estimate', topicId], queryFn: () => api.estimate(topicId), enabled: open, staleTime: 0 })
  const [budget, setBudget] = useState('')
  const start = useMutation({
    mutationFn: () => api.startRun(topicId, budget.trim() ? Number(budget) : null),
    onSuccess: (run) => {
      queryClient.setQueryData(keys.run(run.id), run)
      queryClient.setQueryData<TopicDetail>(keys.topic(topicId), (d) => d && { ...d, latest_run_id: run.id })
      onOpenChange(false)
    },
  })
  const fmt = useFormatNumber()
  const e = estimate.data
  const failure = start.error ?? estimate.error
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('run.estimateTitle')}</DialogTitle>
          <DialogDescription>
            {e ? t('run.estimateBody', { nodes: e.nodes, perNode: fmt(e.tokens_per_node), total: fmt(e.total) }) : t('app.loading')}
          </DialogDescription>
        </DialogHeader>
        {e && <p className="text-sm text-muted-foreground">{t(`run.basis.${e.basis}`)}</p>}
        {e?.nodes === 0 && <p className="text-sm">{t('run.nothing')}</p>}
        {e && e.nodes > 0 && (
          <div className="flex flex-col gap-2">
            <Label htmlFor="budget">{t('run.budget')}</Label>
            <p className="text-xs text-muted-foreground">{t('run.budgetHint')}</p>
            <Input id="budget" type="number" inputMode="numeric" min={1} value={budget} onChange={(ev) => { setBudget(ev.target.value); }} />
          </div>
        )}
        {failure && <QueryError error={failure} />}
        <DialogFooter>
          <Button variant="outline" onClick={() => { onOpenChange(false); }}>
            {t('app.cancel')}
          </Button>
          <Button onClick={() => { start.mutate(); }} disabled={!e || e.nodes === 0 || start.isPending}>
            {t('run.confirm')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
