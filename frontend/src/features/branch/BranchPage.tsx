// A branch thread (design §5.3, §9): questions and answers with their provenance, the composer,
// stop, re-answers after feedback, moving the branch to another node, closing it.
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, MoreHorizontal } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useLocation, useNavigate, useParams } from 'react-router'
import { toast } from 'sonner'
import type { BranchPatch, MessageOut } from '@/api/generated'
import { api } from '@/api/endpoints'
import { QueryError, QueryLoading } from '@/components/QueryState'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { AskComposer, type PendingAsk } from '@/features/message/AskComposer'
import { AssistantMessage, type Reanswer } from '@/features/message/AssistantMessage'
import { StreamingMessage } from '@/features/message/StreamingMessage'
import { initialStream } from '@/api/sse'
import { StatusLine } from '@/components/StatusLine'
import { useTopicContext } from '@/features/topic/context'
import { toastError } from '@/lib/errors'
import { refreshAfterAnswer, useBranch, useLatestAnswer } from '@/lib/queries'
import { runStream, useStream, type StreamRunner } from '@/lib/useStream'

export function BranchPage() {
  const { branchId = '', nodeId = '' } = useParams()
  return <BranchView key={branchId} branchId={branchId} nodeId={nodeId} />
}

function BranchView({ branchId, nodeId }: { branchId: string; nodeId: string }) {
  const { t } = useTranslation()
  const { detail } = useTopicContext()
  const location = useLocation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const branch = useBranch(branchId)
  const { state, start, stop } = useStream()
  const latest = useLatestAnswer(branchId, state.phase === 'streaming')
  const [lastAsk, setLastAsk] = useState<PendingAsk | null>(null)
  const [moving, setMoving] = useState(false)
  const topicId = detail.topic.id

  // The node comes from the route: known before the branch has loaded.
  const refresh = () => refreshAfterAnswer(queryClient, { nodeId, branchId })
  const run = async (runner: StreamRunner) => {
    // A lost stream may still have committed (design §9): the refresh fetches it.
    await runStream(start, runner)
    await refresh()
  }
  const ask = (pending: PendingAsk) => {
    setLastAsk(pending)
    void run((on, signal) => api.ask(branchId, { question: pending.question, allow_web: pending.allowWeb }, on, signal))
  }
  const reanswer =
    (message: MessageOut): Reanswer =>
    (feedback, note) =>
      void run((on, signal) => api.feedbackReanswer(message.id, { feedback, note, reanswer: true }, on, signal))

  // Arriving from "ask here" with the question: ask it once and drop it from the history entry.
  const started = useRef(false)
  useEffect(() => {
    const pending = (location.state as { ask?: PendingAsk } | null)?.ask
    if (!pending || started.current) return
    started.current = true
    void navigate(location.pathname, { replace: true, state: null })
    ask(pending)
  })

  const update = useMutation({
    mutationFn: (body: BranchPatch) => api.updateBranch(branchId, body),
    onSuccess: async (b, body) => {
      await refreshAfterAnswer(queryClient, { nodeId: b.node_id, branchId })
      if (body.node_id) {
        // The node it left lists one branch fewer.
        await refreshAfterAnswer(queryClient, { nodeId })
        const title = detail.nodes.find((n) => n.id === b.node_id)?.title ?? ''
        toast.success(t('branch.moved', { title }))
        void navigate(`/topics/${topicId}/nodes/${b.node_id}/branches/${b.id}`, { replace: true })
      }
    },
    onError: toastError,
  })

  if (branch.isPending) return <QueryLoading />
  if (branch.error) return <QueryError error={branch.error} onRetry={() => void branch.refetch()} />
  const b = branch.data.branch
  const node = detail.nodes.find((n) => n.id === b.node_id)
  const messages = branch.data.messages.filter((m) => m.status === 'done')
  const live = state.phase !== 'idle' && !(state.phase === 'done' && messages.some((m) => m.id === state.messageId))

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6" data-branch={branchId}>
      <header className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <Link to={`/topics/${topicId}/nodes/${b.node_id}`} className="inline-flex min-h-9 items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
            <ArrowLeft className="size-4" aria-hidden />
            {node?.title}
          </Link>
          <h1 className="font-serif text-xl leading-snug font-semibold">{b.title}</h1>
          {b.closed_at && (
            <p className="text-xs text-muted-foreground">
              {t('node.closed')} · {t('branch.reopen')}
            </p>
          )}
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon" aria-label={t('app.edit')}>
              <MoreHorizontal aria-hidden />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onSelect={() => { setMoving(true) }}>{t('branch.move')}</DropdownMenuItem>
            {!b.closed_at && <DropdownMenuItem onSelect={() => { update.mutate({ closed: true }) }}>{t('branch.close')}</DropdownMenuItem>}
          </DropdownMenuContent>
        </DropdownMenu>
      </header>

      <ol className="flex flex-col gap-6">
        {messages.map((m) => {
          if (m.role === 'user') {
            return (
              <li key={m.id} className="self-end rounded-lg bg-secondary px-3 py-2 whitespace-pre-wrap md:max-w-[85%]">
                {m.content_md}
              </li>
            )
          }
          const message = <AssistantMessage message={m} onReanswer={reanswer(m)} onChanged={() => void refresh()} />
          return (
            <li key={m.id}>
              {m.superseded_by ? (
                <details className="text-sm text-muted-foreground">
                  <summary className="cursor-pointer">{t('branch.superseded')}</summary>
                  {message}
                </details>
              ) : (
                message
              )}
            </li>
          )
        })}
        {live && (
          <li>
            {lastAsk && !messages.some((m) => m.role === 'user' && m.content_md === lastAsk.question) && (
              <p className="mb-4 ml-auto w-fit rounded-lg bg-secondary px-3 py-2 whitespace-pre-wrap md:max-w-[85%]">{lastAsk.question}</p>
            )}
            <StreamingMessage state={state} onStop={stop} onRetry={lastAsk ? () => { ask(lastAsk) } : undefined} />
          </li>
        )}
      </ol>
      {!live && latest.data?.status === 'streaming' && <StatusLine state={{ ...initialStream, phase: 'streaming', step: 'answer' }} label={t('branch.writing')} />}

      <AskComposer
        className="sticky bottom-0 border-t bg-background pt-3 pb-2"
        label={t('branch.composer')}
        placeholder={t('branch.composer')}
        submitLabel={t('branch.send')}
        disabled={state.phase === 'streaming'}
        onAsk={ask}
      />

      <Dialog open={moving} onOpenChange={setMoving}>
        <DialogContent className="max-h-[80vh] overflow-auto">
          <DialogHeader>
            <DialogTitle>{t('branch.moveTitle')}</DialogTitle>
            <DialogDescription className="sr-only">{t('branch.moveTitle')}</DialogDescription>
          </DialogHeader>
          <ul className="text-sm">
            {detail.order.map((item) => (
              <li key={item.node.id}>
                <button
                  type="button"
                  disabled={item.node.id === b.node_id}
                  onClick={() => {
                    setMoving(false)
                    update.mutate({ node_id: item.node.id })
                  }}
                  className="flex min-h-11 w-full items-center gap-2 rounded-sm px-2 text-left hover:bg-accent disabled:opacity-50"
                  style={{ paddingLeft: `${0.5 + item.node.depth * 0.9}rem` }}
                >
                  <span className="w-10 shrink-0 font-mono text-xs text-muted-foreground">{item.number}</span>
                  {item.node.title}
                </button>
              </li>
            ))}
          </ul>
        </DialogContent>
      </Dialog>
    </div>
  )
}
