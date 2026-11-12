import { describe, expect, it } from 'vitest'
import { SseParser, initialStream, reduceStream, type StreamEvent } from '@/api/sse'

// Frames as sse-starlette writes them (backend api/sse.py): `id`, `event`, `data`, CRLF line ends,
// and `: ping` comments every 15 s.
const frame = (id: number, event: string, data: object) =>
  `id: ${id}\r\nevent: ${event}\r\ndata: ${JSON.stringify(data)}\r\n\r\n`

describe('SseParser', () => {
  it('yields events across chunk boundaries and ignores comments', () => {
    const wire = frame(1, 'token', { text: 'Hel' }) + ': ping - 2026-11-13\r\n\r\n' + frame(2, 'token', { text: 'lo' })
    const parser = new SseParser()
    const got: StreamEvent[] = []
    for (let i = 0; i < wire.length; i += 7) got.push(...parser.push(wire.slice(i, i + 7)))
    expect(got).toEqual([
      { id: '1', event: 'token', data: { text: 'Hel' } },
      { id: '2', event: 'token', data: { text: 'lo' } },
    ])
  })

  it('drops a repeated id', () => {
    const parser = new SseParser()
    const got = parser.push(frame(1, 'token', { text: 'a' }) + frame(1, 'token', { text: 'a' }) + frame(2, 'token', { text: 'b' }))
    expect(got.map((e) => e.id)).toEqual(['1', '2'])
  })

  it('joins multi-line data and handles LF-only frames', () => {
    const parser = new SseParser()
    expect(parser.push('id: 1\nevent: token\ndata: {"text":\ndata: "x"}\n\n')).toEqual([
      { id: '1', event: 'token', data: { text: 'x' } },
    ])
  })
})

describe('reduceStream', () => {
  const run = (events: StreamEvent[]) => events.reduce(reduceStream, initialStream)
  const ev = (event: string, data: object, id = '0'): StreamEvent => ({ id, event, data }) as StreamEvent

  it('keeps only the tokens after the last tool call as the answer', () => {
    const state = run([
      ev('path', { step: 'scope', status: 'ok', detail: { message_id: 'm1', branch_id: 'b1', node_id: 'n1' } }),
      ev('token', { text: 'Let me search. ' }),
      ev('tool', { name: 'web_search', query: 'k8s' }),
      ev('token', { text: 'Answer ' }),
      ev('token', { text: 'text.' }),
      ev('citation', { kind: 'web', url: 'https://k8s.io', title: 'k8s' }),
      ev('done', { message_id: 'm1', source: 'web', usage: { input: 10, output: 5 }, path: [], warnings: [] }),
    ])
    expect(state.text).toBe('Answer text.')
    expect(state.messageId).toBe('m1')
    expect(state.placement).toEqual({ branchId: 'b1', nodeId: 'n1' })
    expect(state.tools).toEqual([{ name: 'web_search', query: 'k8s' }])
    expect(state.citations).toEqual([{ kind: 'web', url: 'https://k8s.io', title: 'k8s' }])
    expect(state.phase).toBe('done')
    expect(state.done?.source).toBe('web')
    expect(state.step).toBe('scope')
  })

  it('tracks the current step and ends on error', () => {
    const state = run([
      ev('path', { step: 'retrieve', status: 'ok' }),
      ev('path', { step: 'answer', status: 'running' }),
      ev('error', { code: 'role_provider', message: 'provider down' }),
    ])
    expect(state.step).toBe('answer')
    expect(state.phase).toBe('error')
    expect(state.error).toEqual({ code: 'role_provider', message: 'provider down' })
  })

  it('collects the outline', () => {
    const nodes = [{ id: 'n1', title: 'Pods' }]
    expect(run([ev('outline', { nodes })]).outline).toEqual(nodes)
  })
})
