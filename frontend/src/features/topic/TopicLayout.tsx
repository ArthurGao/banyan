// Every screen under a topic shares one ResponsiveSplit: outline | page | notes of the open node.
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Outlet, useLocation, useParams } from 'react-router'
import { api } from '@/api/endpoints'
import { QueryError, QueryLoading } from '@/components/QueryState'
import { ResponsiveSplit } from '@/components/ResponsiveSplit'
import { NotesPane } from '@/features/notes/NotesPane'
import { OutlineTree } from '@/features/outline/OutlineTree'
import { explanationStates, useRun } from '@/features/run/useRun'
import type { TopicContext } from '@/features/topic/context'
import { toastError } from '@/lib/errors'
import { keys, useTopic } from '@/lib/queries'

export function TopicLayout() {
  const { t } = useTranslation()
  const { topicId = '', nodeId } = useParams()
  const location = useLocation()
  const queryClient = useQueryClient()
  const topic = useTopic(topicId)
  const run = useRun(topicId, topic.data?.latest_run_id)
  const explanation = useMemo(() => explanationStates(run.data), [run.data])
  const retry = useMutation({
    mutationFn: ({ runId, node }: { runId: string; node: string }) => api.retryRunNode(runId, node),
    onSuccess: (next) => queryClient.setQueryData(keys.run(next.id), next),
    onError: toastError,
  })

  if (topic.isPending) {
    return (
      <div className="p-6">
        <QueryLoading />
      </div>
    )
  }
  if (topic.error) {
    return (
      <div className="p-6">
        <QueryError error={topic.error} onRetry={() => void topic.refetch()} />
      </div>
    )
  }
  const detail = topic.data
  const confirmed = detail.topic.outline_status === 'confirmed'
  const runId = run.data?.id
  const done = detail.nodes.filter((n) => n.status === 'done').length
  const outline = (
    <div className="flex min-h-full flex-col">
      <div className="mb-2 flex items-center justify-between px-2.5 text-xs text-muted-foreground">
        <span className="eyebrow">{t('outline.title')}</span>
        {confirmed && <span className="tabular-nums">{t('outline.doneCount', { done, total: detail.nodes.length })}</span>}
      </div>
      {confirmed ? (
        <OutlineTree
          topicId={topicId}
          nodes={detail.nodes}
          activeNodeId={nodeId}
          explanation={explanation}
          onRetry={
            runId
              ? (node) => {
                  retry.mutate({ runId, node })
                }
              : undefined
          }
        />
      ) : (
        <p className="px-2.5 text-sm text-muted-foreground">{t('topics.draft')}</p>
      )}
    </div>
  )
  return (
    <ResponsiveSplit
      closeKey={location.pathname}
      outline={outline}
      main={<Outlet context={{ detail, run: run.data } satisfies TopicContext} />}
      notes={<NotesPane topicId={topicId} nodeId={nodeId} nodeTitle={detail.nodes.find((n) => n.id === nodeId)?.title} />}
    />
  )
}
