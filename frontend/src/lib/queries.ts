// Query keys, hooks and the refreshes that follow a change: server state lives in TanStack Query
// (frontend/AGENTS.md rule 1). Each domain event refreshes what it touches, in one place.
import { useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import type { MessageOut, TopicDetail } from '@/api/generated'
import { api } from '@/api/endpoints'
import { readingOrder } from '@/lib/outline'

export const keys = {
  topics: ['topics'] as const,
  topic: (id: string) => ['topic', id] as const,
  node: (id: string) => ['node', id] as const,
  notes: (id: string) => ['notes', id] as const,
  branch: (id: string) => ['branch', id] as const,
  latest: (branchId: string) => ['latest', branchId] as const,
  run: (id: string) => ['run', id] as const,
  memories: ['memories'] as const,
  candidates: ['candidates'] as const,
  usage: ['usage'] as const,
  health: ['health'] as const,
}

export const useTopics = () => useQuery({ queryKey: keys.topics, queryFn: api.topics })

/** The topic with its outline in reading order (numbered), computed once per response. */
// Stable selectors: TanStack re-runs an inline one on every render of every observer.
const withOrder = (detail: TopicDetail) => ({ ...detail, order: readingOrder(detail.nodes) })
const titleOf = (detail: TopicDetail) => detail.topic.title
const latestRunOf = (detail: TopicDetail) => detail.latest_run_id

export const useTopic = (id: string) => useQuery({ queryKey: keys.topic(id), queryFn: () => api.topic(id), select: withOrder })
/** The top bar needs one field each; same cache entry, no reading-order work. */
export const useTopicTitle = (id: string) => useQuery({ queryKey: keys.topic(id), queryFn: () => api.topic(id), select: titleOf })
export const useLatestRunId = (id: string) => useQuery({ queryKey: keys.topic(id), queryFn: () => api.topic(id), select: latestRunOf })

/** A node; its outline entry in the topic cache is updated from it (the server flips a node to
 * `learning` when its explanation is recorded). Polls while its explanation is made elsewhere,
 * unless this page is streaming it itself. */
export function useNode(id: string, topicId: string, streaming: boolean) {
  const queryClient = useQueryClient()
  return useQuery({
    queryKey: keys.node(id),
    queryFn: async () => {
      const detail = await api.node(id)
      queryClient.setQueryData<TopicDetail>(keys.topic(topicId), (topic) =>
        topic && { ...topic, nodes: topic.nodes.map((n) => (n.id === detail.node.id ? detail.node : n)) },
      )
      return detail
    },
    refetchInterval: (q) => (!streaming && q.state.data?.explanation_status === 'generating' ? 3000 : false),
  })
}
export const useNotes = (id: string) => useQuery({ queryKey: keys.notes(id), queryFn: () => api.notes(id) })
export const useBranch = (id: string) => useQuery({ queryKey: keys.branch(id), queryFn: () => api.branch(id) })
/** The branch's newest answer in any state. While it is `streaming` and this page is not the one
 * streaming it (a dropped stream, another tab), poll; once it settles, the branch shows it. */
export function useLatestAnswer(branchId: string, streaming: boolean) {
  const queryClient = useQueryClient()
  return useQuery({
    queryKey: keys.latest(branchId),
    queryFn: async () => {
      const before = queryClient.getQueryData<MessageOut | null>(keys.latest(branchId))
      const latest = await api.latest(branchId)
      if (before?.status === 'streaming' && latest?.status !== 'streaming') void queryClient.invalidateQueries({ queryKey: keys.branch(branchId) })
      return latest
    },
    enabled: !streaming,
    refetchInterval: (q) => (q.state.data?.status === 'streaming' ? 2000 : false),
  })
}
/** KB cards waiting for a decision (the top-bar badge and the memory page). */
export const useCandidates = () => useQuery({ queryKey: keys.candidates, queryFn: api.candidates, refetchInterval: 300_000 })

/** After an answer, an explanation or feedback: the node, its notes, the branch and the outline. */
export function refreshAfterAnswer(qc: QueryClient, at: { topicId?: string; nodeId?: string; branchId?: string }) {
  const keysToRefresh = [
    at.nodeId && keys.node(at.nodeId),
    at.nodeId && keys.notes(at.nodeId),
    at.branchId && keys.branch(at.branchId),
    at.branchId && keys.latest(at.branchId),
    at.topicId && keys.topic(at.topicId),
  ].filter((k) => k !== undefined && k !== '')
  return Promise.all(keysToRefresh.map((queryKey) => qc.invalidateQueries({ queryKey })))
}

/** After a topic or its outline changed: the topic and the topic list. */
export const refreshTopic = (qc: QueryClient, topicId: string) =>
  Promise.all([qc.invalidateQueries({ queryKey: keys.topic(topicId) }), qc.invalidateQueries({ queryKey: keys.topics })])
