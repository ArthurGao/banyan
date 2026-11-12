// An assistant message with its provenance (AGENTS.md rule 5, frontend rule 3): source badge,
// citations, model and path (comp A: source row above, citations and feedback in one card below).
import { useMutation } from '@tanstack/react-query'
import { ExternalLink, ThumbsDown, ThumbsUp, CircleDashed } from 'lucide-react'
import { useState, type ReactNode } from 'react'
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
import { answeredBy } from '@/lib/provenance'
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
    <div className="min-w-0 flex-1">
      <p className="eyebrow mb-1.5">{t('message.citations')}</p>
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
              <span>{c.title ?? t('message.kbEntry', { id: (c.entry_id ?? '').slice(0, 8) })}</span>
            )}
          </li>
        ))}
      </ol>
    </div>
  )
}

/** Above the answer (comp A): where it came from, how many citations, what it cost, which model. */
export function SourceRow({
  source,
  provenance,
  citations,
  usage,
}: {
  source: Source | null
  provenance?: string | null
  citations: number
  usage?: { input: number; output: number } | null
}) {
  const { t } = useTranslation()
  const facts = [citations > 0 && t('message.citationCount', { count: citations }), usage && t('message.tokens', { input: usage.input, output: usage.output })].filter(Boolean)
  if (!source && !provenance && facts.length === 0) return null
  return (
    <div className="mb-4 flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
      {source && <SourceBadge source={source} />}
      {facts.length > 0 && <span className="text-[13px] text-muted-foreground">{facts.join(' · ')}</span>}
      {provenance && (
        <span className="ml-auto min-w-0 truncate font-mono text-xs text-muted-foreground" title={provenance}>
          {answeredBy(provenance)}
        </span>
      )}
    </div>
  )
}

/** Below the answer: citations and the pipeline path on the left, feedback on the right (comp A).
 * On a phone the feedback wraps under the citations, where a thumb reaches it (frontend rule 3). */
export function ProvenanceCard({ citations, path, warnings, children }: { citations: Citation[]; path: PathEntry[]; warnings?: string[]; children?: ReactNode }) {
  const { t } = useTranslation()
  if (citations.length === 0 && path.length === 0 && !warnings?.length && !children) return null
  return (
    <div className="mt-5 flex flex-col gap-3 rounded-lg border bg-panel px-4 py-3.5 text-[13px] md:flex-row md:gap-4">
      <div className="min-w-0 flex-1 space-y-2">
        <Citations items={citations} />
        {warnings?.map((w) => (
          <p key={w} className="text-warn">
            {t(`message.warnings.${w}`, { defaultValue: w })}
          </p>
        ))}
        {path.length > 0 && (
          <details className="text-xs text-muted-foreground">
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
      {children}
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
    return <p className="self-start text-xs text-muted-foreground">{t('message.given', { feedback: t(`message.feedback.${message.feedback}`) })}</p>
  }
  return (
    <div className="flex flex-wrap gap-2 self-start">
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
  const citations = message.citations.map(asCitation)
  return (
    <article data-message={message.id} data-source={message.source ?? undefined}>
      <SourceRow source={message.source} provenance={message.provenance} citations={citations.length} />
      <Markdown text={message.content_md} />
      <ProvenanceCard citations={citations} path={message.path}>
        {/* A replaced answer takes no more feedback. */}
        {!message.superseded_by && <FeedbackBar message={message} onReanswer={onReanswer} onChanged={onChanged} />}
      </ProvenanceCard>
    </article>
  )
}
