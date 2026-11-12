// The node page (design §11.2): the explanation first, generated on open when there is none
// (§5.2); then the branches, and "ask here", which opens a new branch so the main line stays clean.
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ArrowRight } from 'lucide-react'
import { useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useParams } from 'react-router'
import type { NodeStatus } from '@/api/generated'
import { api } from '@/api/endpoints'
import { QueryError, QueryLoading } from '@/components/QueryState'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { AskComposer, type PendingAsk } from '@/features/message/AskComposer'
import { AssistantMessage, type Reanswer } from '@/features/message/AssistantMessage'
import { StreamingMessage } from '@/features/message/StreamingMessage'
import { useTopicContext } from '@/features/topic/context'
import { toastError } from '@/lib/errors'
import { useFormatDay } from '@/lib/format'
import { refreshAfterAnswer, useNode } from '@/lib/queries'
import { runStream, useStream, type StreamRunner } from '@/lib/useStream'

export function NodePage() {
  const { nodeId = '' } = useParams()
  // Keyed so a different node starts with fresh stream state.
  return <NodeView key={nodeId} nodeId={nodeId} />
}

function NodeView({ nodeId }: { nodeId: string }) {
  const { t } = useTranslation()
  const { detail } = useTopicContext()
  const queryClient = useQueryClient()
  const { state, start, stop } = useStream()
  const formatDay = useFormatDay()
  const topicId = detail.topic.id
  const streaming = state.phase === 'streaming'
  const node = useNode(nodeId, topicId, streaming)
  const confirmed = detail.topic.outline_status === 'confirmed'
  const here = detail.order.findIndex((n) => n.node.id === nodeId)
  const item = detail.order[here]
  const next = detail.order[here + 1]

  const refresh = () => refreshAfterAnswer(queryClient, { topicId, nodeId })
  // A 409 means a background job is writing the explanation: the refresh shows it as generating
  // and the node query polls until it lands.
  const run = async (runner: StreamRunner) => {
    await runStream(start, runner, () => undefined)
    await refresh()
  }
  const explain = () => run((on, signal) => api.explain(nodeId, on, signal))
  const reanswer: Reanswer = (feedback, note) => {
    const id = node.data?.explanation?.id
    if (id) void run((on, signal) => api.feedbackReanswer(id, { feedback, note, reanswer: true }, on, signal))
  }

  const status = node.data?.explanation_status
  const busyElsewhere = !streaming && status === 'generating'

  // Open → explain, once, when there is nothing yet (design §5.2).
  const asked = useRef(false)
  useEffect(() => {
    if (asked.current || !confirmed || status !== 'none') return
    asked.current = true
    void explain()
  })

  const setStatus = useMutation({
    mutationFn: (s: NodeStatus) => api.updateNode(nodeId, { status: s }),
    onSuccess: refresh,
    onError: toastError,
  })

  if (node.isPending) return <QueryLoading />
  if (node.error) return <QueryError error={node.error} onRetry={() => void node.refetch()} />
  const { explanation } = node.data
  const n = node.data.node
  const questions = node.data.branches.filter((b) => b.kind === 'question')
  const markAs: NodeStatus = n.status === 'done' ? 'learning' : 'done'

  return (
    <article className="mx-auto max-w-3xl" data-node={nodeId}>
      <header className="mb-5">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <p className="eyebrow">
            {item?.number} · {t(`outline.status.${n.status}`)}
          </p>
          <Button variant="ghost" size="xs" className="text-muted-foreground" onClick={() => { setStatus.mutate(markAs) }}>
            {t(markAs === 'done' ? 'node.markDone' : 'node.markLearning')}
          </Button>
        </div>
        <h1 className="title-display mt-1.5">{n.title}</h1>
        {n.summary && <p className="mt-2 text-muted-foreground">{n.summary}</p>}
      </header>

      <section aria-label={t('node.explanation')} className="mb-10">
        {!confirmed && <p className="text-sm text-muted-foreground">{t('node.notConfirmed')}</p>}
        {/* The stored explanation once committed; the live stream until then (or when it failed). */}
        {explanation && (state.phase === 'idle' || state.phase === 'done') ? (
          <AssistantMessage message={explanation} onReanswer={reanswer} onChanged={() => void refresh()} />
        ) : (
          <StreamingMessage state={state} onStop={stop} onRetry={() => void explain()} />
        )}
        {busyElsewhere && <p className="text-sm text-muted-foreground">{t('node.generatingElsewhere')}</p>}
        {confirmed && status === 'failed' && state.phase === 'idle' && (
          <div className="flex items-center gap-3 text-sm">
            <Badge variant="warn" className="h-6 px-2.5">
              {t('node.explainFailed')}
            </Badge>
            <Button variant="outline" size="sm" onClick={() => void explain()}>
              {t('app.retry')}
            </Button>
          </div>
        )}
      </section>

      <section aria-label={t('node.branches')} className="mb-8 border-t pt-5">
        <h2 className="mb-3.5 font-serif text-[1.375rem] font-medium">
          {t('node.branches')} {questions.length > 0 && <span className="font-sans text-[15px] text-muted-foreground tabular-nums">{questions.length}</span>}
        </h2>
        {questions.length === 0 ? (
          <p className="mb-4 text-sm text-muted-foreground">{t('node.noBranches')}</p>
        ) : (
          <ul className="mb-5 flex flex-col gap-2.5">
            {questions.map((b) => (
              <li key={b.id}>
                <Link
                  to={`/topics/${topicId}/nodes/${nodeId}/branches/${b.id}`}
                  className="flex min-h-12 items-center gap-3 rounded-lg border bg-card px-3.5 py-3 hover:border-input"
                >
                  <span className="min-w-0 flex-1 truncate text-[15px]">{b.title}</span>
                  <span className="shrink-0 text-xs text-muted-foreground">
                    {b.closed_at ? `${t('node.closed')} · ` : ''}
                    {formatDay(b.created_at)}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
        <AskHere topicId={topicId} nodeId={nodeId} />
      </section>

      {next && (
        <Link to={`/topics/${topicId}/nodes/${next.node.id}`} className="inline-flex min-h-11 items-center gap-2 text-sm text-muted-foreground hover:text-foreground">
          <span className="font-mono">{next.number}</span> {next.node.title}
          <ArrowRight className="size-4" aria-hidden />
        </Link>
      )}
    </article>
  )
}

/** Opens a branch for the question; the branch page asks it (handed over in the history entry). */
function AskHere({ topicId, nodeId }: { topicId: string; nodeId: string }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const create = useMutation({
    mutationFn: (ask: PendingAsk) => api.createBranch(nodeId, ask.question.slice(0, 80)),
    onSuccess: (branch, ask) => {
      void navigate(`/topics/${topicId}/nodes/${nodeId}/branches/${branch.id}`, { state: { ask } })
    },
    onError: toastError,
  })
  return (
    <AskComposer
      label={t('node.askHere')}
      placeholder={t('node.askPlaceholder')}
      submitLabel={t('node.askHere')}
      disabled={create.isPending}
      onAsk={(ask) => {
        create.mutate(ask)
      }}
    />
  )
}
