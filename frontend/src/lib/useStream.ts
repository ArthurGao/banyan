// One SSE stream at a time per component: progressive state from the events, `stop()` aborts the
// request (the server then marks the answer `aborted`, design §9), unmount aborts too.
import { useCallback, useEffect, useReducer, useRef } from 'react'
import { initialStream, reduceStream, type StreamEvent, type StreamState } from '@/api/sse'
import i18n from '@/i18n'
import { isStatus, toastError } from '@/lib/errors'

type Action = { type: 'start' } | { type: 'event'; e: StreamEvent } | { type: 'end'; phase: 'aborted' | 'lost' }

function reduce(state: StreamState, a: Action): StreamState {
  switch (a.type) {
    case 'start':
      return { ...initialStream, phase: 'streaming' }
    case 'event':
      return reduceStream(state, a.e)
    case 'end':
      // A stream that ended without `done` or `error` was lost: the caller fetches the latest message.
      return state.phase === 'streaming' ? { ...state, phase: a.phase === 'aborted' ? 'aborted' : 'error' } : state
  }
}

export type Outcome = 'done' | 'error' | 'aborted' | 'lost'
export type StreamRunner = (onEvent: (e: StreamEvent) => void, signal: AbortSignal) => Promise<void>

export function useStream() {
  const [state, dispatch] = useReducer(reduce, initialStream)
  const controller = useRef<AbortController | null>(null)

  // Leaving the page aborts the stream. StrictMode's simulated unmount remounts synchronously, so
  // the abort waits a microtask and is skipped when the component is back.
  const mounted = useRef(false)
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      queueMicrotask(() => {
        if (!mounted.current) controller.current?.abort()
      })
    }
  }, [])

  /** Runs a stream to its end. HTTP errors before the stream opens (404, 409, 422) are thrown. */
  const start = useCallback(async (run: StreamRunner): Promise<Outcome> => {
    controller.current?.abort()
    const ctrl = new AbortController()
    controller.current = ctrl
    dispatch({ type: 'start' })
    let last: StreamEvent['event'] | null = null
    const onEvent = (e: StreamEvent) => {
      last = e.event
      dispatch({ type: 'event', e })
    }
    try {
      await run(onEvent, ctrl.signal)
    } catch (err) {
      if (ctrl.signal.aborted) {
        dispatch({ type: 'end', phase: 'aborted' })
        return 'aborted'
      }
      if (last === null) {
        dispatch({ type: 'end', phase: 'lost' })
        throw err
      }
    } finally {
      if (controller.current === ctrl) controller.current = null
    }
    if (last === 'done') return 'done'
    if (last === 'error') return 'error'
    dispatch({ type: 'end', phase: 'lost' })
    return 'lost'
  }, [])

  const stop = useCallback(() => controller.current?.abort(), [])

  return { state, start, stop }
}

/** Runs a stream and reports what the page does not show: a lost stream (it may still have
 * committed: refresh after this) and an HTTP error before the stream opened. A 409 goes to
 * `onConflict` when given (another answer is already streaming there). */
export async function runStream(
  start: (run: StreamRunner) => Promise<Outcome>,
  runner: StreamRunner,
  onConflict?: () => void,
): Promise<Outcome | null> {
  try {
    const outcome = await start(runner)
    if (outcome === 'lost') toastError(i18n.t('branch.failed'))
    return outcome
  } catch (e) {
    if (onConflict && isStatus(e, 409)) onConflict()
    else toastError(e)
    return null
  }
}
