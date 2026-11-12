// SSE client for the streaming endpoints (design §9; wire format in backend `api/sse.py`).
// EventSource cannot POST or send a Bearer token, so streams are read from fetch's body. Every
// event carries an `id` used only for de-duplication: there is no resume. A client that loses a
// stream fetches `GET /v1/branches/{id}/messages/latest`.
import type { NodeOut, PathEntry, Source } from '@/api/generated'
import { openStream } from '@/api/http'

export interface ToolCall { name: string; query?: string | null; url?: string | null }
export interface Citation { kind: 'kb' | 'web'; entry_id?: string | null; url?: string | null; title?: string | null }
export interface DoneData {
  message_id: string | null
  source: Source | null
  usage: { input: number; output: number }
  path: PathEntry[]
  warnings: string[]
}
export interface ErrorData { code: string; message: string }

export type StreamEvent =
  | { id: string; event: 'path'; data: PathEntry }
  | { id: string; event: 'token'; data: { text: string } }
  | { id: string; event: 'tool'; data: ToolCall }
  | { id: string; event: 'citation'; data: Citation }
  | { id: string; event: 'outline'; data: { nodes: NodeOut[] } }
  | { id: string; event: 'done'; data: DoneData }
  | { id: string; event: 'error'; data: ErrorData }

const EVENTS = new Set(['path', 'token', 'tool', 'citation', 'outline', 'done', 'error'])

/** Incremental parser: push decoded text, get complete events; repeated ids are dropped. */
export class SseParser {
  private buffer = ''
  private readonly seen = new Set<string>()

  push(chunk: string): StreamEvent[] {
    this.buffer += chunk.replace(/\r\n?/g, '\n')
    const out: StreamEvent[] = []
    let end: number
    while ((end = this.buffer.indexOf('\n\n')) !== -1) {
      const block = this.buffer.slice(0, end)
      this.buffer = this.buffer.slice(end + 2)
      const event = parseBlock(block)
      if (!event) continue
      if (event.id) {
        if (this.seen.has(event.id)) continue
        this.seen.add(event.id)
      }
      out.push(event)
    }
    return out
  }
}

function parseBlock(block: string): StreamEvent | null {
  let id = ''
  let name = ''
  const data: string[] = []
  for (const line of block.split('\n')) {
    if (line.startsWith(':')) continue
    const colon = line.indexOf(':')
    const field = colon === -1 ? line : line.slice(0, colon)
    const value = colon === -1 ? '' : line.slice(colon + 1).replace(/^ /, '')
    if (field === 'id') id = value
    else if (field === 'event') name = value
    else if (field === 'data') data.push(value)
  }
  if (!EVENTS.has(name) || data.length === 0) return null
  return { id, event: name, data: JSON.parse(data.join('\n')) as unknown } as StreamEvent
}

export interface StreamState {
  phase: 'idle' | 'streaming' | 'done' | 'error' | 'aborted'
  step: string | null
  steps: PathEntry[]
  text: string
  tools: ToolCall[]
  citations: Citation[]
  outline: NodeOut[] | null
  messageId: string | null
  placement: { branchId: string; nodeId: string } | null
  done: DoneData | null
  error: ErrorData | null
}

export const initialStream: StreamState = {
  phase: 'idle',
  step: null,
  steps: [],
  text: '',
  tools: [],
  citations: [],
  outline: null,
  messageId: null,
  placement: null,
  done: null,
  error: null,
}

export function reduceStream(state: StreamState, e: StreamEvent): StreamState {
  switch (e.event) {
    case 'path': {
      const detail = e.data.detail ?? {}
      const next = { ...state, phase: 'streaming' as const, step: e.data.step, steps: [...state.steps, e.data] }
      if (typeof detail.message_id === 'string') next.messageId = detail.message_id
      if (typeof detail.branch_id === 'string' && typeof detail.node_id === 'string') {
        next.placement = { branchId: detail.branch_id, nodeId: detail.node_id }
      }
      return next
    }
    case 'token':
      return { ...state, phase: 'streaming', text: state.text + e.data.text }
    case 'tool':
      // The tokens before a tool call were the model talking to itself, not the answer.
      return { ...state, text: '', tools: [...state.tools, e.data] }
    case 'citation':
      return { ...state, citations: [...state.citations, e.data] }
    case 'outline':
      return { ...state, outline: e.data.nodes }
    case 'done':
      return { ...state, phase: 'done', done: e.data, messageId: e.data.message_id ?? state.messageId }
    case 'error':
      return { ...state, phase: 'error', error: e.data }
  }
}

/** POST to a streaming endpoint and feed each event to `onEvent` until the stream ends. HTTP errors
 * before the stream opens (404, 409, 422) reject with `ApiError`. */
export async function postStream(
  path: string,
  body: unknown,
  onEvent: (e: StreamEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  const response = await openStream(path, body, signal)
  if (!response.body) return
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader()
  const parser = new SseParser()
  for (;;) {
    const { value, done } = await reader.read()
    if (done) return
    for (const e of parser.push(value)) onEvent(e)
  }
}
