import type { NodeOut, TopicDetail, TopicSummary } from '@/api/generated'

export const node = (id: string, title: string, parent: string | null, position: number, depth: number): NodeOut => ({
  id, title, parent_id: parent, position, depth, summary: null, status: 'not_started', origin: 'outline',
})

export const NODES = [node('n1', 'Pods', null, 0, 0), node('n2', 'Services', null, 1, 0), node('n3', 'ClusterIP', 'n2', 0, 1)]

const topicOut = {
  id: 't1', title: 'Kubernetes', goal: null, level: 'intermediate', language: 'en', instructions: null, status: 'active',
  outline_status: 'confirmed', explain_mode: 'prefetch', created_at: '2026-11-01T00:00:00Z',
} as const

export const TOPIC: TopicDetail = { topic: topicOut, nodes: NODES, latest_run_id: null }
export const SUMMARY: TopicSummary = { ...topicOut, nodes: 3, done: 0 }

/** An SSE body as the backend writes it (api/sse.py). */
export function sse(events: [string, object][]): Response {
  const text = events.map(([event, data], i) => `id: ${i + 1}\r\nevent: ${event}\r\ndata: ${JSON.stringify(data)}\r\n\r\n`).join('')
  return new Response(text, { headers: { 'Content-Type': 'text/event-stream' } })
}
