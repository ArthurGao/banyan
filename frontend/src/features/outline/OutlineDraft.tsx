// Steps 3–5 of a new topic (design §5.1): generate the outline (SSE), edit it, confirm it and
// choose how explanations are generated. Nothing is explained before the outline is confirmed.
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useLocation, useNavigate, useSearchParams } from 'react-router'
import type { ExplainMode, TopicDetail } from '@/api/generated'
import { api } from '@/api/endpoints'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { QueryError } from '@/components/QueryState'
import { StatusLine } from '@/components/StatusLine'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { OutlineTree } from '@/features/outline/OutlineTree'
import { EXPLAIN_MODES } from '@/lib/enums'
import { refreshTopic } from '@/lib/queries'
import { runStream, useStream, type StreamRunner } from '@/lib/useStream'
import { cn } from '@/lib/utils'


export function OutlineDraft({ detail, onConfirmed }: { detail: TopicDetail; onConfirmed: (mode: ExplainMode) => void }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const location = useLocation()
  const { state, start } = useStream()
  const [confirming, setConfirming] = useState(false)
  const [mode, setMode] = useState<ExplainMode>('prefetch')
  const [regenerating, setRegenerating] = useState(false)
  const topicId = detail.topic.id
  const empty = detail.nodes.length === 0

  const run = async (runner: StreamRunner) => {
    await runStream(start, runner)
    await refreshTopic(queryClient, topicId)
  }
  const generate = (replace: boolean) => run((on, signal) => api.generateOutline(topicId, replace, on, signal))

  // Coming from the new-topic dialog: generate once, then drop the flag so a reload does not repeat it.
  const started = useRef(false)
  useEffect(() => {
    if (params.get('generate') !== '1' || started.current) return
    started.current = true
    void navigate(location.pathname, { replace: true })
    if (empty) void generate(false)
  })

  const confirm = useMutation({
    mutationFn: () => api.confirmOutline(topicId, mode),
    onSuccess: async () => {
      setConfirming(false)
      await refreshTopic(queryClient, topicId)
      onConfirmed(mode)
    },
  })

  const generating = state.phase === 'streaming'
  return (
    <div>
      <p className="mb-4 text-sm text-muted-foreground">{t('outline.confirmBody')}</p>
      {generating && (
        <div className="mb-4">
          <StatusLine state={state} />
        </div>
      )}
      {state.phase === 'error' && state.error && <p role="alert" className="mb-4 text-sm text-destructive">{state.error.message}</p>}
      {state.done?.warnings.map((w) => (
        <p key={w} className="mb-4 text-sm text-warn">
          {t(`message.warnings.${w}`, { defaultValue: w })}
        </p>
      ))}

      {!generating && <OutlineTree topicId={topicId} nodes={detail.nodes} editable />}

      <div className="mt-6 flex flex-wrap gap-2">
        {empty ? (
          <Button onClick={() => void generate(false)} disabled={generating}>
            {t('outline.generate')}
          </Button>
        ) : (
          <>
            <Button onClick={() => { setConfirming(true); }} disabled={generating}>
              {t('outline.confirm')}
            </Button>
            <Button
              variant="outline"
              disabled={generating}
              onClick={() => { setRegenerating(true); }}
            >
              {t('outline.regenerate')}
            </Button>
          </>
        )}
      </div>

      <Dialog open={confirming} onOpenChange={setConfirming}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('outline.confirmTitle')}</DialogTitle>
            <DialogDescription>{t('outline.confirmBody')}</DialogDescription>
          </DialogHeader>
          <div role="radiogroup" aria-label={t('explainMode.label')} className="flex flex-col gap-2">
            {EXPLAIN_MODES.map((m) => (
              <button
                key={m}
                type="button"
                role="radio"
                aria-checked={mode === m}
                onClick={() => { setMode(m); }}
                className={cn('rounded-md border p-3 text-left', mode === m ? 'border-foreground' : 'border-border hover:bg-accent/50')}
              >
                <span className="block text-sm font-medium">{t(`outline.mode.${m}`)}</span>
                <span className="block text-xs text-muted-foreground">{t(`outline.mode.${m}Hint`)}</span>
              </button>
            ))}
          </div>
          {confirm.error && <QueryError error={confirm.error} />}
          <DialogFooter>
            <Button variant="outline" onClick={() => { setConfirming(false); }}>
              {t('app.cancel')}
            </Button>
            <Button onClick={() => { confirm.mutate(); }} disabled={confirm.isPending}>
              {t('outline.confirm')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <ConfirmDialog
        open={regenerating}
        onOpenChange={setRegenerating}
        title={t('outline.regenerate')}
        body={t('outline.regenerateConfirm')}
        confirmLabel={t('outline.regenerate')}
        destructive
        onConfirm={() => void generate(true)}
      />
    </div>
  )
}
