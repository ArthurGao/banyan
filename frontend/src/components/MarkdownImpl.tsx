// The Markdown renderer behind `Markdown` (loaded lazily): GFM, KaTeX math, Shiki-highlighted code
// (its grammars load on demand). Raw HTML in the Markdown is not rendered.
import 'katex/dist/katex.min.css'
import { memo, useEffect, useState, type ComponentProps } from 'react'
import ReactMarkdown from 'react-markdown'
import type * as Shiki from 'shiki'
import rehypeKatex from 'rehype-katex'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import { cn } from '@/lib/utils'

// One import of Shiki for every code block.
let shiki: Promise<typeof Shiki> | null = null
const loadShiki = () => (shiki ??= import('shiki'))

function CodeBlock({ code, lang }: { code: string; lang: string }) {
  const [html, setHtml] = useState<string | null>(null)
  useEffect(() => {
    let live = true
    loadShiki()
      .then(({ codeToHtml, bundledLanguages }) =>
        codeToHtml(code, {
          lang: lang in bundledLanguages ? lang : 'text',
          theme: 'github-dark', // code blocks are dark in both app themes (comp A)
        }),
      )
      .then((out) => {
        if (live) setHtml(out)
      })
      .catch(() => undefined) // plain text stays
    return () => {
      live = false
    }
  }, [code, lang])
  // Shiki escapes the code; its output is markup we trust.
  if (html) return <div dangerouslySetInnerHTML={{ __html: html }} />
  return (
    <pre>
      <code>{code}</code>
    </pre>
  )
}

const components: ComponentProps<typeof ReactMarkdown>['components'] = {
  pre: ({ children }) => <>{children}</>,
  code: ({ className, children }) => {
    const lang = /language-([\w-]+)/.exec(className ?? '')?.[1]
    const text = Array.isArray(children) ? children.join('') : typeof children === 'string' ? children : ''
    if (!lang && !text.includes('\n')) return <code>{children}</code>
    return <CodeBlock code={text.replace(/\n$/, '')} lang={lang ?? 'text'} />
  },
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  ),
}

// Memoised: a streaming answer re-renders its page on every token; finished messages stay parsed.
export default memo(function MarkdownImpl({ text, className }: { text: string; className?: string | undefined }) {
  return (
    <div className={cn('prose-banyan', className)}>
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]} components={components}>
        {text}
      </ReactMarkdown>
    </div>
  )
})
