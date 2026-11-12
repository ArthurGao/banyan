// The question form shared by "ask here" (a new branch) and the branch composer: the question,
// "allow web", Cmd/Ctrl+Enter to send.
import { useState, type SyntheticEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'

export interface PendingAsk {
  question: string
  allowWeb: boolean
}

export function AskComposer({
  label,
  placeholder,
  submitLabel,
  disabled = false,
  onAsk,
  className,
}: {
  label: string
  placeholder: string
  submitLabel: string
  disabled?: boolean
  onAsk: (ask: PendingAsk) => void
  className?: string
}) {
  const { t } = useTranslation()
  const [question, setQuestion] = useState('')
  const [allowWeb, setAllowWeb] = useState(true)
  const submit = (e: SyntheticEvent) => {
    e.preventDefault()
    if (!question.trim() || disabled) return
    onAsk({ question: question.trim(), allowWeb })
    setQuestion('')
  }
  return (
    <form onSubmit={submit} className={cn('flex flex-col gap-2', className)}>
      <Textarea
        aria-label={label}
        placeholder={placeholder}
        value={question}
        onChange={(e) => {
          setQuestion(e.target.value)
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) submit(e)
        }}
      />
      <div className="flex items-center justify-between gap-3">
        <label className="flex items-center gap-2 text-sm text-muted-foreground">
          <Checkbox
            checked={allowWeb}
            onCheckedChange={(v) => {
              setAllowWeb(v === true)
            }}
          />
          {t('branch.allowWeb')}
        </label>
        <Button type="submit" disabled={disabled || !question.trim()}>
          {submitLabel}
        </Button>
      </div>
    </form>
  )
}
