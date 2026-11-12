// One function per route of design §9. Request and response types come from the generated
// OpenAPI types; streaming routes go through `postStream`.
import type {
  AskBody,
  BranchDetail,
  BranchOut,
  BranchPatch,
  CandidatePageOut,
  ClassifyOut,
  EstimateOut,
  ExplainMode,
  ExportOut,
  FeedbackIn,
  HealthOut,
  MemoryOut,
  MemoryPageOut,
  MessageOut,
  NodeCreate,
  NodeDeleted,
  TopicDeleted,
  NodeDetail,
  NodeMove,
  NodeOut,
  NodePatch,
  NotesOut,
  QuickAskIn,
  RunOut,
  RunPatch,
  TopicCreate,
  TopicDetail,
  TopicOut,
  TopicPatch,
  TopicSummary,
  TopicUsageOut,
} from '@/api/generated'
import { ApiError, request, requestText } from '@/api/http'
import { postStream, type StreamEvent } from '@/api/sse'

type OnEvent = (e: StreamEvent) => void
const q = (params: Record<string, string | number | boolean | null | undefined>) => {
  const s = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined) s.set(k, String(v))
  const text = s.toString()
  return text ? `?${text}` : ''
}

export const api = {
  health: () => request<HealthOut>('GET', '/health'),

  topics: () => request<TopicSummary[]>('GET', '/v1/topics'),
  topic: (id: string) => request<TopicDetail>('GET', `/v1/topics/${id}`),
  createTopic: (body: TopicCreate) => request<TopicOut>('POST', '/v1/topics', body),
  updateTopic: (id: string, body: TopicPatch) => request<TopicOut>('PATCH', `/v1/topics/${id}`, body),
  deleteTopic: (id: string, deprecateEntries: boolean) =>
    request<TopicDeleted>('DELETE', `/v1/topics/${id}${q({ deprecate_entries: deprecateEntries || null })}`),
  generateOutline: (id: string, replace: boolean, onEvent: OnEvent, signal: AbortSignal) =>
    postStream(`/v1/topics/${id}/outline${q({ replace: replace || null })}`, undefined, onEvent, signal),
  confirmOutline: (id: string, explain_mode: ExplainMode) =>
    request<TopicOut>('POST', `/v1/topics/${id}/outline/confirm`, { explain_mode }),

  estimate: (topicId: string) => request<EstimateOut>('GET', `/v1/topics/${topicId}/generation/estimate`),
  startRun: (topicId: string, token_budget: number | null) =>
    request<RunOut>('POST', `/v1/topics/${topicId}/generation-runs`, token_budget === null ? {} : { token_budget }),
  run: (id: string) => request<RunOut>('GET', `/v1/generation-runs/${id}`),
  changeRun: (id: string, body: RunPatch) => request<RunOut>('PATCH', `/v1/generation-runs/${id}`, body),
  retryRunNode: (id: string, nodeId: string) =>
    request<RunOut>('POST', `/v1/generation-runs/${id}/nodes/${nodeId}/retry`),

  addNode: (topicId: string, body: NodeCreate) => request<NodeOut>('POST', `/v1/topics/${topicId}/nodes`, body),
  node: (id: string) => request<NodeDetail>('GET', `/v1/nodes/${id}`),
  updateNode: (id: string, body: NodePatch) => request<NodeOut>('PATCH', `/v1/nodes/${id}`, body),
  moveNode: (id: string, body: NodeMove) => request<NodeOut>('POST', `/v1/nodes/${id}/move`, body),
  deleteNode: (id: string, deprecateEntries: boolean) =>
    request<NodeDeleted>('DELETE', `/v1/nodes/${id}${q({ deprecate_entries: deprecateEntries || null })}`),
  explain: (nodeId: string, onEvent: OnEvent, signal: AbortSignal) =>
    postStream(`/v1/nodes/${nodeId}/explain`, undefined, onEvent, signal),
  notes: (nodeId: string) => request<NotesOut>('GET', `/v1/nodes/${nodeId}/notes`),

  createBranch: (nodeId: string, title: string) =>
    request<BranchOut>('POST', `/v1/nodes/${nodeId}/branches`, { title }),
  branch: (id: string) => request<BranchDetail>('GET', `/v1/branches/${id}`),
  updateBranch: (id: string, body: BranchPatch) => request<BranchOut>('PATCH', `/v1/branches/${id}`, body),
  ask: (branchId: string, body: AskBody, onEvent: OnEvent, signal: AbortSignal) =>
    postStream(`/v1/branches/${branchId}/ask`, body, onEvent, signal),
  /** The newest answer in any state, null before the first (design §9: recovery after a dropped stream). */
  latest: (branchId: string) =>
    request<MessageOut>('GET', `/v1/branches/${branchId}/messages/latest`).catch((e: unknown) => {
      if (e instanceof ApiError && e.status === 404) return null
      throw e
    }),
  /** `good`, or `reanswer: false`, answers with JSON; `wrong` / `incomplete` with a re-answer stream. */
  feedback: (messageId: string, body: FeedbackIn) =>
    request<MessageOut>('POST', `/v1/messages/${messageId}/feedback`, body),
  feedbackReanswer: (messageId: string, body: FeedbackIn, onEvent: OnEvent, signal: AbortSignal) =>
    postStream(`/v1/messages/${messageId}/feedback`, body, onEvent, signal),

  classify: (topicId: string, question: string) =>
    request<ClassifyOut>('POST', `/v1/topics/${topicId}/classify`, { question }),
  quickAsk: (topicId: string, body: QuickAskIn, onEvent: OnEvent, signal: AbortSignal) =>
    postStream(`/v1/topics/${topicId}/quick-ask`, body, onEvent, signal),

  exportTopic: (topicId: string) => request<ExportOut>('POST', `/v1/topics/${topicId}/export`),
  exportHtml: (exportId: string) => requestText(`/v1/exports/${exportId}`),

  memories: (cursor: string | null) => request<MemoryPageOut>('GET', `/v1/memory${q({ cursor, limit: 20 })}`),
  pinMemory: (id: string, pinned: boolean) => request<MemoryOut>('PATCH', `/v1/memory/${id}`, { pinned }),
  deleteMemory: (id: string) => request<undefined>('DELETE', `/v1/memory/${id}`),
  candidates: () => request<CandidatePageOut>('GET', `/v1/memory/candidates${q({ limit: 50 })}`),
  decideCandidate: (id: string, decision: 'accept' | 'reject', note: string | null) =>
    request<undefined>('POST', `/v1/memory/candidates/${id}`, { decision, note }),

  usage: () => request<TopicUsageOut[]>('GET', '/v1/usage'),
}
