// Every screen under a topic shares one ResponsiveSplit: outline | page | notes of the open node.
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, Outlet, useLocation, useParams } from 'react-router'
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
  const outline = (
    <div>
      <Link to={`/topics/${topicId}`} className="mb-3 block font-serif text-lg leading-snug font-semibold">
        {detail.topic.title}
      </Link>
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
        <p className="text-sm text-muted-foreground">{t('topics.draft')}</p>
      )}
    </div>
  )
  return (
    <ResponsiveSplit
      closeKey={location.pathname}
      outline={outline}
      main={<Outlet context={{ detail, run: run.data } satisfies TopicContext} />}
      notes={<NotesPane nodeId={nodeId} />}
    />
  )
}
