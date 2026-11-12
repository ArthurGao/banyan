// Explanations, answers and notes. The renderer (react-markdown, remark/rehype, KaTeX, Shiki) loads on
// first use, so screens without Markdown do not download it; until then the text shows as is.
import { Suspense, lazy } from 'react'
import { cn } from '@/lib/utils'

const MarkdownImpl = lazy(() => import('@/components/MarkdownImpl'))

export function Markdown({ text, className }: { text: string; className?: string }) {
  return (
    <Suspense fallback={<div className={cn('prose-banyan whitespace-pre-wrap', className)}>{text}</div>}>
      <MarkdownImpl text={text} className={className} />
    </Suspense>
  )
}
