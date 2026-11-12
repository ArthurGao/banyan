// An answer while it streams: the status line, the tokens so far (uncommitted until `done`, design
// §9), then citations and provenance once done. Stop aborts; an error offers a retry.
import { useDeferredValue } from 'react'
import { useTranslation } from 'react-i18next'
import type { StreamState } from '@/api/sse'
import { Markdown } from '@/components/Markdown'
import { StatusLine } from '@/components/StatusLine'
import { Button } from '@/components/ui/button'
import { ProvenanceCard, SourceRow } from '@/features/message/AssistantMessage'

export function StreamingMessage({ state, onStop, onRetry }: { state: StreamState; onStop: () => void; onRetry?: (() => void) | undefined }) {
  const { t } = useTranslation()
  // Re-parsing the whole answer on every token is quadratic: let React skip renders under load.
  const text = useDeferredValue(state.text)
  if (state.phase === 'idle') return null
  return (
    <article aria-busy={state.phase === 'streaming'} data-streaming={state.phase}>
      <StatusLine state={state} />
      {state.phase === 'done' && state.done && (
        <SourceRow source={state.done.source} citations={state.citations.length} usage={state.done.usage} />
      )}
      {text && <Markdown text={text} />}
      {state.phase === 'streaming' && (
        <Button variant="outline" size="sm" className="mt-3" onClick={onStop}>
          {t('branch.stop')}
        </Button>
      )}
      {state.phase === 'done' && state.done && (
        <ProvenanceCard citations={state.citations} path={state.done.path} warnings={state.done.warnings} />
      )}
      {state.phase === 'aborted' && <p className="mt-2 text-sm text-muted-foreground">{t('branch.aborted')}</p>}
      {state.phase === 'error' && (
        <div className="mt-2 flex flex-wrap items-center gap-3 text-sm text-destructive">
          <span>{state.error ? `${t('branch.failed')} ${state.error.message}` : t('branch.failed')}</span>
          {onRetry && (
            <Button variant="outline" size="sm" onClick={onRetry}>
              {t('app.retry')}
            </Button>
          )}
        </div>
      )}
    </article>
  )
}
