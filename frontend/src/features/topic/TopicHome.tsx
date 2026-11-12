// The topic page: while the outline is a draft, the outline editor (design §5.1); afterwards the
// overview with generation progress, the explanation mode, the outline (on narrow screens, where
// the outline pane is hidden) and the export.
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Settings } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import type { ExplainMode } from '@/api/generated'
import { api } from '@/api/endpoints'
import { useLayout } from '@/lib/layout'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { ExportPanel } from '@/features/export/ExportPanel'
import { OutlineDraft } from '@/features/outline/OutlineDraft'
import { OutlineTree } from '@/features/outline/OutlineTree'
import { RunProgress } from '@/features/run/RunProgress'
import { RunStartDialog } from '@/features/run/RunStartDialog'
import { explanationStates } from '@/features/run/useRun'
import { useTopicContext } from '@/features/topic/context'
import { EXPLAIN_MODES } from '@/lib/enums'
import { keys } from '@/lib/queries'


export function TopicHome() {
  const { t } = useTranslation()
  const { detail, run } = useTopicContext()
  const layout = useLayout()
  const queryClient = useQueryClient()
  const [estimating, setEstimating] = useState(false)
  const [editing, setEditing] = useState(false)
  const topic = detail.topic
  const setMode = useMutation({
    mutationFn: (mode: ExplainMode) => api.updateTopic(topic.id, { explain_mode: mode }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.topic(topic.id) }),
  })

  const header = (
    <header className="mb-6">
      <div className="flex items-start justify-between gap-2">
        <h1 className="title-display">{topic.title}</h1>
        <Button variant="ghost" size="icon" asChild>
          <Link to={`/topics/${topic.id}/settings`} aria-label={t('topicSettings.title')} title={t('topicSettings.title')}>
            <Settings aria-hidden />
          </Link>
        </Button>
      </div>
      {topic.goal && <p className="mt-2 text-muted-foreground">{topic.goal}</p>}
      <p className="mt-1 text-xs text-muted-foreground">
        {t(`level.${topic.level}`)} · {t(`topicLanguage.${topic.language}`)}
      </p>
    </header>
  )

  if (topic.outline_status === 'draft') {
    return (
      <div className="mx-auto max-w-3xl">
        {header}
        <OutlineDraft detail={detail} onConfirmed={(mode) => {
            if (mode === 'all') setEstimating(true)
          }} />
        <RunStartDialog topicId={topic.id} open={estimating} onOpenChange={setEstimating} />
      </div>
    )
  }

  const next = detail.order.find((n) => n.node.status !== 'done')
  return (
    <div className="mx-auto max-w-3xl">
      {header}
      {run && <RunProgress run={run} />}
      <div className="mb-6 flex flex-wrap items-center gap-3">
        {next && (
          <Button asChild>
            <Link to={`/topics/${topic.id}/nodes/${next.node.id}`}>
              {next.number} {next.node.title}
            </Link>
          </Button>
        )}
        <label className="flex items-center gap-2 text-sm text-muted-foreground">
          <span>{t('explainMode.label')}</span>
          <Select value={topic.explain_mode} onValueChange={(v) => { setMode.mutate(v as ExplainMode); }}>
            <SelectTrigger className="w-40" aria-label={t('explainMode.label')}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {EXPLAIN_MODES.map((m) => (
                <SelectItem key={m} value={m}>
                  {t(`explainMode.${m}`)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>
        <Button variant="outline" onClick={() => { setEstimating(true); }}>
          {t('run.start')}
        </Button>
      </div>

      {/* On a wide screen the outline has its own pane: here it shows only while editing. */}
      <section aria-label={t('outline.title')} className="mb-6">
        <div className="mb-2 flex items-center justify-between gap-2">
          {(editing || layout !== 'three') && <h2 className="font-serif text-lg font-medium">{t('outline.title')}</h2>}
          <Button
            variant={editing ? 'default' : 'outline'}
            size="sm"
            onClick={() => {
              setEditing(!editing)
            }}
          >
            {editing ? t('app.done') : t('outline.edit')}
          </Button>
        </div>
        {(editing || layout !== 'three') && (
          <OutlineTree topicId={topic.id} nodes={detail.nodes} editable={editing} explanation={explanationStates(run)} />
        )}
      </section>

      <ExportPanel topicId={topic.id} title={topic.title} />
      <RunStartDialog topicId={topic.id} open={estimating} onOpenChange={setEstimating} />
    </div>
  )
}
