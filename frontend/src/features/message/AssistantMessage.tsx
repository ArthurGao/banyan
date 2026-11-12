// An assistant message with its provenance (AGENTS.md rule 5, frontend rule 3): source badge,
// citations, model and path; feedback buttons sit at the bottom where a thumb reaches them.
import { useMutation } from '@tanstack/react-query'
import { ExternalLink, ThumbsDown, ThumbsUp, CircleDashed } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import type { MessageOut, PathEntry, Source } from '@/api/generated'
import { api } from '@/api/endpoints'
import type { Citation } from '@/api/sse'
import { Markdown } from '@/components/Markdown'
import { SourceBadge } from '@/components/SourceBadge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Textarea } from '@/components/ui/textarea'
import { toastError } from '@/lib/errors'
import { cn } from '@/lib/utils'

export type Reanswer = (feedback: 'wrong' | 'incomplete', note: string | null) => void

function asCitation(c: Record<string, unknown>): Citation {
  const str = (v: unknown) => (typeof v === 'string' ? v : null)
  return { kind: c.kind === 'kb' ? 'kb' : 'web', entry_id: str(c.entry_id), url: str(c.url), title: str(c.title) }
}

export function Citations({ items }: { items: Citation[] }) {
  const { t } = useTranslation()
  if (items.length === 0) return null
  return (
    <div className="mt-3 border-t pt-2 text-sm">
      <p className="mb-1 text-xs font-medium text-muted-foreground">{t('message.citations')}</p>
      <ol className="space-y-1">
        {items.map((c, i) => (
          <li key={`${c.entry_id ?? c.url ?? ''}-${i}`} className="flex items-start gap-2">
            <span className={cn('font-mono text-xs leading-5', c.kind === 'kb' ? 'text-kb' : 'text-muted-foreground')}>
              {c.kind === 'kb' ? 'S' : 'W'}
              {i + 1}
            </span>
            {c.url ? (
              <a href={c.url} target="_blank" rel="noopener noreferrer" className="inline-flex min-w-0 items-center gap-1 underline underline-offset-2">
                <span className="truncate">{c.title ?? c.url}</span>
                <ExternalLink className="size-3 shrink-0" aria-hidden />
              </a>
            ) : (
              <span className="text-kb">{c.title ?? t('message.kbEntry', { id: (c.entry_id ?? '').slice(0, 8) })}</span>
            )}
          </li>
        ))}
      </ol>
    </div>
  )
}

export function Meta({
  source,
  provenance,
  path,
  usage,
  warnings,
}: {
  source: Source | null
  provenance?: string | null
  path: PathEntry[]
  usage?: { input: number; output: number } | null
  warnings?: string[]
}) {
  const { t } = useTranslation()
  return (
    <div className="mt-3 space-y-1">
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        {source && <SourceBadge source={source} />}
        {provenance && <span className="min-w-0 font-mono break-all">{provenance}</span>}
        {usage && <span>{t('message.tokens', { input: usage.input, output: usage.output })}</span>}
        {path.length > 0 && (
          <details className="w-full">
            <summary className="cursor-pointer select-none">{t('message.path')}</summary>
            <ol className="mt-1 font-mono">
              {path.map((p, i) => (
                <li key={`${p.step}-${i}`}>
                  {p.step}: {p.status}
                </li>
              ))}
            </ol>
          </details>
        )}
      </div>
      {warnings?.map((w) => (
        <p key={w} className="text-xs text-warn">
          {t(`message.warnings.${w}`, { defaultValue: w })}
        </p>
      ))}
    </div>
  )
}

function FeedbackBar({ message, onReanswer, onChanged }: { message: MessageOut; onReanswer: Reanswer; onChanged: () => void }) {
  const { t } = useTranslation()
  const [asking, setAsking] = useState<'wrong' | 'incomplete' | null>(null)
  const [note, setNote] = useState('')
  const good = useMutation({
    mutationFn: () => api.feedback(message.id, { feedback: 'good' }),
    onSuccess: () => {
      toast.success(t('message.feedbackSaved'))
      onChanged()
    },
    onError: toastError,
  })
  if (message.feedback !== 'none') {
    return <p className="mt-2 text-xs text-muted-foreground">{t('message.given', { feedback: t(`message.feedback.${message.feedback}`) })}</p>
  }
  return (
    <div className="mt-3 flex flex-wrap gap-2">
      <Button variant="outline" size="sm" onClick={() => { good.mutate(); }} disabled={good.isPending}>
        <ThumbsUp aria-hidden />
        {t('message.feedback.good')}
      </Button>
      <Button variant="outline" size="sm" onClick={() => { setAsking('wrong'); }}>
        <ThumbsDown aria-hidden />
        {t('message.feedback.wrong')}
      </Button>
      <Button variant="outline" size="sm" onClick={() => { setAsking('incomplete'); }}>
        <CircleDashed aria-hidden />
        {t('message.feedback.incomplete')}
      </Button>
      <Dialog open={asking !== null} onOpenChange={(open) => {
          if (!open) setAsking(null)
        }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{asking && t(`message.feedback.${asking}`)}</DialogTitle>
          </DialogHeader>
          <Textarea aria-label={t('message.feedbackNote')} placeholder={t('message.feedbackNote')} value={note} onChange={(e) => { setNote(e.target.value); }} />
          <DialogFooter>
            <Button variant="outline" onClick={() => { setAsking(null); }}>
              {t('app.cancel')}
            </Button>
            <Button
              onClick={() => {
                if (asking) onReanswer(asking, note.trim() || null)
                setAsking(null)
                setNote('')
              }}
            >
              {t('message.reanswer')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

/** A stored assistant message (status `done`). */
export function AssistantMessage({
  message,
  onReanswer,
  onChanged,
}: {
  message: MessageOut
  onReanswer: Reanswer
  /** Called after feedback was saved: the caller refreshes what it shows. */
  onChanged: () => void
}) {
  return (
    <article data-message={message.id} data-source={message.source ?? undefined}>
      <Markdown text={message.content_md} />
      <Citations items={message.citations.map(asCitation)} />
      <Meta source={message.source} provenance={message.provenance} path={message.path} />
      {/* A replaced answer takes no more feedback. */}
      {!message.superseded_by && (
        <FeedbackBar message={message} onReanswer={onReanswer} onChanged={onChanged} />
      )}
    </article>
  )
}
