// The open node's structured notes (design §10.1), the only source of the HTML export.
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { Markdown } from '@/components/Markdown'
import { QueryError, QueryLoading } from '@/components/QueryState'
import { useNotes } from '@/lib/queries'

// comp A: a small-caps header with the export link, the node's title in serif, then the sections.
export function NotesPane({ topicId, nodeId, nodeTitle }: { topicId: string; nodeId: string | undefined; nodeTitle: string | undefined }) {
  const { t } = useTranslation()
  return (
    <div className="px-2.5 text-sm">
      <div className="mb-3.5 flex items-center justify-between">
        <h2 className="eyebrow">{t('notes.heading')}</h2>
        <Link to={`/topics/${topicId}#export`} className="text-[13px] text-kb hover:underline">
          {t('notes.export')}
        </Link>
      </div>
      {nodeId ? <Notes nodeId={nodeId} title={nodeTitle} /> : <p className="text-muted-foreground">{t('notes.noNode')}</p>}
    </div>
  )
}

function Notes({ nodeId, title }: { nodeId: string; title: string | undefined }) {
  const { t } = useTranslation()
  const notes = useNotes(nodeId)
  if (notes.isPending) return <QueryLoading />
  if (notes.error) return <QueryError error={notes.error} onRetry={() => void notes.refetch()} />
  const c = notes.data.content
  return (
    <div data-notes-version={notes.data.version}>
      <div className="mb-2.5 flex items-baseline justify-between gap-2">
        {title && <h3 className="font-serif text-lg leading-snug font-medium">{title}</h3>}
        {notes.data.version > 0 && <span className="shrink-0 text-xs text-muted-foreground">{t('notes.version', { version: notes.data.version })}</span>}
      </div>
      {!c ? (
        <p className="text-muted-foreground">{t('notes.empty')}</p>
      ) : (
        <div className="space-y-5">
          {c.summary && (
            <Section title={t('notes.summary')}>
              <Markdown text={c.summary} className="prose-compact" />
            </Section>
          )}
          <List title={t('notes.keyPoints')} items={c.key_points} />
          {c.examples && c.examples.length > 0 && (
            <Section title={t('notes.examples')}>
              {c.examples.map((e, i) => (
                <details key={`${e.title}-${i}`} className="border-t py-2">
                  <summary className="cursor-pointer">{e.title}</summary>
                  <Markdown text={e.body_md} className="prose-compact mt-2" />
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
                  <Markdown text={h.answer_md} className="prose-compact mt-2" />
                </details>
              ))}
            </Section>
          )}
        </div>
      )}
    </div>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section>
      <h4 className="eyebrow mb-1.5">{title}</h4>
      {children}
    </section>
  )
}

function List({ title, items }: { title: string; items: string[] | undefined }) {
  if (!items?.length) return null
  return (
    <Section title={title}>
      <ul className="list-disc space-y-1 pl-4.5 leading-[1.6]">
        {items.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ul>
    </Section>
  )
}
