// The open node's structured notes (design §10.1), the only source of the HTML export.
import { useTranslation } from 'react-i18next'
import { Markdown } from '@/components/Markdown'
import { QueryError, QueryLoading } from '@/components/QueryState'
import { useNotes } from '@/lib/queries'

export function NotesPane({ nodeId }: { nodeId: string | undefined }) {
  const { t } = useTranslation()
  if (!nodeId) return <p className="text-sm text-muted-foreground">{t('notes.noNode')}</p>
  return <Notes nodeId={nodeId} />
}

function Notes({ nodeId }: { nodeId: string }) {
  const { t } = useTranslation()
  const notes = useNotes(nodeId)
  if (notes.isPending) return <QueryLoading />
  if (notes.error) return <QueryError error={notes.error} onRetry={() => void notes.refetch()} />
  const c = notes.data.content
  return (
    <div className="text-sm" data-notes-version={notes.data.version}>
      <div className="mb-3 flex items-baseline justify-between">
        <h2 className="font-serif text-base font-semibold">{t('notes.title')}</h2>
        {notes.data.version > 0 && <span className="text-xs text-muted-foreground">{t('notes.version', { version: notes.data.version })}</span>}
      </div>
      {!c ? (
        <p className="text-muted-foreground">{t('notes.empty')}</p>
      ) : (
        <div className="space-y-5">
          {c.summary && (
            <Section title={t('notes.summary')}>
              <Markdown text={c.summary} />
            </Section>
          )}
          <List title={t('notes.keyPoints')} items={c.key_points} />
          {c.examples && c.examples.length > 0 && (
            <Section title={t('notes.examples')}>
              {c.examples.map((e, i) => (
                <details key={`${e.title}-${i}`} className="border-t py-2">
                  <summary className="cursor-pointer">{e.title}</summary>
                  <Markdown text={e.body_md} className="mt-2 text-sm" />
                </details>
              ))}
            </Section>
          )}
          <List title={t('notes.pitfalls')} items={c.pitfalls} />
          {c.branch_highlights && c.branch_highlights.length > 0 && (
            <Section title={t('notes.highlights')}>
              {c.branch_highlights.map((h) => (
                <details key={h.branch_id + (h.message_id ?? '')} className="border-t py-2">
                  <summary className="cursor-pointer">{h.question}</summary>
                  <Markdown text={h.answer_md} className="mt-2 text-sm" />
                </details>
              ))}
            </Section>
          )}
        </div>
      )}
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h3 className="mb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">{title}</h3>
      {children}
    </section>
  )
}

function List({ title, items }: { title: string; items: string[] | undefined }) {
  if (!items?.length) return null
  return (
    <Section title={title}>
      <ul className="prose-banyan list-disc pl-5">
        {items.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ul>
    </Section>
  )
}
