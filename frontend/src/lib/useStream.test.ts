import { act, renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ApiError } from '@/api/http'
import type { StreamEvent } from '@/api/sse'
import { useStream, type StreamRunner } from '@/lib/useStream'

const token = (id: string, text: string): StreamEvent => ({ id, event: 'token', data: { text } })
const done: StreamEvent = {
  id: '9', event: 'done', data: { message_id: 'm', source: 'llm', usage: { input: 1, output: 2 }, path: [], warnings: [] },
}

async function runWith(runner: StreamRunner) {
  const hook = renderHook(() => useStream())
  let outcome: unknown
  await act(async () => {
    outcome = await hook.result.current.start(runner).catch((e: unknown) => e)
  })
  return { outcome, state: hook.result.current.state, hook }
}

describe('useStream', () => {
  it('ends done with the streamed text', async () => {
    const { outcome, state } = await runWith((on) => {
      on(token('1', 'a'))
      on(token('2', 'b'))
      on(done)
      return Promise.resolve()
    })
    expect(outcome).toBe('done')
    expect(state).toMatchObject({ phase: 'done', text: 'ab' })
  })

  it('reports a stream that closed without done as lost', async () => {
    const { outcome, state } = await runWith((on) => {
      on(token('1', 'partial'))
      return Promise.resolve()
    })
    expect(outcome).toBe('lost')
    expect(state.phase).toBe('error')
  })

  it('reports a network failure mid-stream as lost', async () => {
    const { outcome } = await runWith((on) => {
      on(token('1', 'partial'))
      return Promise.reject(new TypeError('network error'))
    })
    expect(outcome).toBe('lost')
  })

  it('rethrows an HTTP error raised before the stream opened', async () => {
    const { outcome } = await runWith(() => Promise.reject(new ApiError(409, 'answer_streaming', null)))
    expect(outcome).toBeInstanceOf(ApiError)
  })

  it('stop() aborts the request', async () => {
    const hook = renderHook(() => useStream())
    let outcome: unknown
    await act(async () => {
      const running = hook.result.current.start(
        (on, signal) =>
          new Promise<void>((_, reject) => {
            on(token('1', 'x'))
            signal.addEventListener('abort', () => { reject(new DOMException('aborted', 'AbortError')); })
          }),
      )
      hook.result.current.stop()
      outcome = await running
    })
    expect(outcome).toBe('aborted')
    expect(hook.result.current.state.phase).toBe('aborted')
  })
})

describe('useStream lifetime', () => {
  const pending: StreamRunner = (on, signal) =>
    new Promise<void>((_, reject) => {
      on(token('1', 'x'))
      signal.addEventListener('abort', () => { reject(new DOMException('aborted', 'AbortError')); })
    })

  it('aborts the stream when the component unmounts', async () => {
    const hook = renderHook(() => useStream())
    let running!: Promise<unknown>
    act(() => {
      running = hook.result.current.start(pending)
    })
    hook.unmount()
    await expect(running).resolves.toBe('aborted')
  })

  it('a stream started on mount survives StrictMode remounting', async () => {
    const { StrictMode, createElement, useEffect, useRef } = await import('react')
    const { render } = await import('@testing-library/react')
    let phase = ''
    let aborted = false
    // Like NodePage: start once from a mount effect, guarded by a ref.
    function Auto() {
      const { state, start } = useStream()
      const started = useRef(false)
      useEffect(() => {
        if (started.current) return
        started.current = true
        void start((on, signal) => {
          signal.addEventListener('abort', () => (aborted = true))
          return pending(on, signal)
        })
      })
      phase = state.phase
      return null
    }
    const view = render(createElement(StrictMode, null, createElement(Auto)))
    await act(() => new Promise((r) => setTimeout(r, 20)))
    expect(aborted).toBe(false)
    expect(phase).toBe('streaming')
    view.unmount()
    await act(() => new Promise((r) => setTimeout(r, 0)))
    expect(aborted).toBe(true)
  })
})
