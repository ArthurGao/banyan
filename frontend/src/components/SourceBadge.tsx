import { useTranslation } from 'react-i18next'
import type { Source } from '@/api/generated'
import { cn } from '@/lib/utils'

// The accent means one thing: "came from the knowledge base". `kb` is filled; the others outlined.
export function SourceBadge({ source }: { source: Source }) {
  const { t } = useTranslation()
  const name = t(`message.source.${source}`)
  return (
    <span
      data-source={source}
      aria-label={t('message.sourceLabel', { source: name })}
      className={cn(
        'inline-flex h-6 items-center rounded-sm border px-2 text-xs font-medium',
        source === 'kb' ? 'border-kb bg-kb text-kb-foreground' : 'border-border text-muted-foreground',
        source === 'mixed' && 'border-kb text-kb',
      )}
    >
      {name}
    </span>
  )
}
