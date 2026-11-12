import { BookMarked } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { Source } from '@/api/generated'
import { Badge } from '@/components/ui/badge'

// The accent marks what came from the knowledge base: `kb` is filled green with a book, `mixed`
// outlined green, `llm` / `web` outlined neutral (comp A).
const VARIANT = { kb: 'kb', mixed: 'kb-outline', llm: 'quiet', web: 'quiet' } as const

export function SourceBadge({ source }: { source: Source }) {
  const { t } = useTranslation()
  return (
    <Badge
      variant={VARIANT[source]}
      data-source={source}
      aria-label={t('message.sourceLabel', { source: t(`message.source.${source}`) })}
      className="h-6 gap-1.5 px-2.5 tracking-[0.02em]"
    >
      {source === 'kb' && <BookMarked aria-hidden />}
      {t(`message.sourceFrom.${source}`)}
    </Badge>
  )
}
