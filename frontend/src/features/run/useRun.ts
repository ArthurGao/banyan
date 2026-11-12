import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { RunOut, RunPatch } from '@/api/generated'
import { api } from '@/api/endpoints'
import type { ExplanationState } from '@/lib/outline'
import { keys } from '@/lib/queries'

export const UNFINISHED: RunOut['status'][] = ['running', 'paused', 'budget_exhausted']

export const doneCount = (run: RunOut | undefined) => run?.nodes.filter((n) => n.status === 'done').length ?? 0

/** The topic's latest run, polled while it is running (design §9: the client polls progress).
 * Each finished node also changed the outline (its status), so the topic is refreshed then. */
export function useRun(topicId: string, runId: string | null | undefined) {
  const queryClient = useQueryClient()
  return useQuery({
    queryKey: keys.run(runId ?? ''),
    queryFn: async () => {
      const id = runId ?? ''
      const before = queryClient.getQueryData<RunOut>(keys.run(id))
      const run = await api.run(id)
      if (before && doneCount(run) !== doneCount(before)) void queryClient.invalidateQueries({ queryKey: keys.topic(topicId) })
      return run
    },
    enabled: Boolean(runId),
    refetchInterval: (q) => (q.state.data?.status === 'running' ? 2000 : false),
  })
}

export function explanationStates(run: RunOut | undefined): Record<string, ExplanationState> {
  const out: Record<string, ExplanationState> = {}
  for (const n of run?.nodes ?? []) {
    if (n.status === 'failed') out[n.node_id] = 'failed'
    else if (n.status === 'generating' && run?.status === 'running') out[n.node_id] = 'generating'
  }
  return out
}

/** Pause, resume, cancel or add budget; the run query takes the server's answer. */
export function useChangeRun(runId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: RunPatch) => api.changeRun(runId, body),
    onSuccess: (next) => queryClient.setQueryData(keys.run(runId), next),
  })
}

/** The one toggle a running or paused run offers. */
export const pauseOrResume = (status: RunOut['status']) => (status === 'running' ? 'pause' : status === 'paused' ? 'resume' : null)
