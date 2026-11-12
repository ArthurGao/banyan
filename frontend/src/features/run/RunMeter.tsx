// The top bar's view of "generate all" while it is unfinished (comp A): done / total, a thin bar,
// tokens / budget and pause or resume. The full control (budget, cancel) stays on the topic page.
import { useTranslation } from 'react-i18next'
import type { RunOut } from '@/api/generated'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { UNFINISHED, doneCount, pauseOrResume, useChangeRun, useRun } from '@/features/run/useRun'
import { formatCompact } from '@/lib/format'
import { useLatestRunId } from '@/lib/queries'

export function RunMeter({ topicId }: { topicId: string }) {
  const run = useRun(topicId, useLatestRunId(topicId).data).data
  if (!run || !UNFINISHED.includes(run.status)) return null
  return <Meter run={run} />
}

function Meter({ run }: { run: RunOut }) {
  const { t } = useTranslation()
  const change = useChangeRun(run.id)
  const toggle = pauseOrResume(run.status)
  const done = doneCount(run)
  const total = run.nodes.length
  const label = t('run.meter', { done, total })
  return (
    <div className="hidden items-center gap-2 text-[13px] text-muted-foreground lg:flex" data-run-meter={run.status}>
      <span className="tabular-nums">{label}</span>
      <Progress value={total ? (done / total) * 100 : 0} aria-label={label} className="h-1.5 w-30 bg-border [&>*]:bg-kb" />
      <span className="font-mono text-xs tabular-nums">
        {formatCompact(run.tokens_used)} / {formatCompact(run.token_budget)}
      </span>
      {toggle && (
        <Button variant="outline" size="xs" disabled={change.isPending} onClick={() => { change.mutate({ action: toggle }) }}>
          {t(`run.${toggle}`)}
        </Button>
      )}
    </div>
  )
}
