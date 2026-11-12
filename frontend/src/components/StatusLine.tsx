import { Loader2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { StreamState } from '@/api/sse'

// The pipeline's actual step while a stream runs (frontend/AGENTS.md rule 4); a tool call names what
// is being searched or read.
export function StatusLine({ state, label }: { state: StreamState; label?: string }) {
  const { t } = useTranslation()
  if (state.phase !== 'streaming') return null
  const tool = state.tools.at(-1)
  const answering = state.step === 'answer' || state.step === null
  let text: string
  if (label) text = label
  else if (tool && answering && !state.text) text = t(`step.tool.${tool.name}`, { q: tool.query ?? tool.url ?? '', defaultValue: tool.name })
  else text = t(`step.${state.step ?? 'scope'}`, { defaultValue: state.step ?? '' })
  return (
    <p role="status" aria-live="polite" className="flex items-center gap-2 text-sm text-muted-foreground">
      <Loader2 className="size-4 animate-spin" aria-hidden />
      {text}
    </p>
  )
}
