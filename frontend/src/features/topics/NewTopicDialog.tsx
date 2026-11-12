// New topic as a short, code-driven conversation (design §5.1 steps 1–2): the topic, then fixed
// clarifying questions with defaults and skip; no LLM call until the outline is generated.
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, type SyntheticEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router'
import type { Language, Level } from '@/api/generated'
import { api } from '@/api/endpoints'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { currentLanguage } from '@/i18n'
import { LEVELS, TOPIC_LANGUAGES } from '@/lib/enums'
import { keys } from '@/lib/queries'

const STEPS = ['topic', 'goal', 'level', 'language', 'ready'] as const
type Step = (typeof STEPS)[number]

function Bubble({ who, children }: { who: 'app' | 'you'; children: React.ReactNode }) {
  return (
    <div className={who === 'you' ? 'flex justify-end' : 'flex'}>
      <p className={who === 'you' ? 'max-w-[85%] rounded-lg bg-secondary px-3 py-2 text-sm' : 'max-w-[85%] text-sm font-medium'}>{children}</p>
    </div>
  )
}

export function NewTopicDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [step, setStep] = useState<Step>('topic')
  const [title, setTitle] = useState('')
  const [goal, setGoal] = useState('')
  const [level, setLevel] = useState<Level>('intermediate')
  const [language, setLanguage] = useState<Language>(currentLanguage())

  const reset = () => {
    setStep('topic')
    setTitle('')
    setGoal('')
    setLevel('intermediate')
  }
  const create = useMutation({
    mutationFn: () => api.createTopic({ title: title.trim(), goal: goal.trim() || null, level, language }),
    onSuccess: async (topic) => {
      await queryClient.invalidateQueries({ queryKey: keys.topics })
      onOpenChange(false)
      reset()
      void navigate(`/topics/${topic.id}?generate=1`)
    },
  })

  const at = (s: Step) => STEPS.indexOf(step) > STEPS.indexOf(s)
  const submitTitle = (e: SyntheticEvent) => {
    e.preventDefault()
    if (title.trim()) setStep('goal')
  }
  const submitGoal = (e: SyntheticEvent) => {
    e.preventDefault()
    setStep('level')
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-auto">
        <DialogHeader>
          <DialogTitle className="font-serif">{t('newTopic.title')}</DialogTitle>
          <DialogDescription className="sr-only">{t('newTopic.askTopic')}</DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <Bubble who="app">{t('newTopic.askTopic')}</Bubble>
          {at('topic') ? (
            <Bubble who="you">{title}</Bubble>
          ) : (
            <form onSubmit={submitTitle} className="flex gap-2">
              <Input autoFocus aria-label={t('newTopic.askTopic')} placeholder={t('newTopic.topicPlaceholder')} value={title} onChange={(e) => { setTitle(e.target.value); }} maxLength={200} />
              <Button type="submit" disabled={!title.trim()}>
                {t('newTopic.next')}
              </Button>
            </form>
          )}

          {at('topic') && <Bubble who="app">{t('newTopic.askGoal')}</Bubble>}
          {step === 'goal' && (
            <form onSubmit={submitGoal} className="flex flex-wrap gap-2">
              <Input autoFocus className="min-w-0 flex-1" aria-label={t('newTopic.askGoal')} placeholder={t('newTopic.goalPlaceholder')} value={goal} onChange={(e) => { setGoal(e.target.value); }} />
              <Button type="button" variant="ghost" onClick={() => { setGoal(''); setStep('level') }}>
                {t('newTopic.skip')}
              </Button>
              <Button type="submit">{t('newTopic.next')}</Button>
            </form>
          )}
          {at('goal') && <Bubble who="you">{goal.trim() || '—'}</Bubble>}

          {at('goal') && <Bubble who="app">{t('newTopic.askLevel')}</Bubble>}
          {step === 'level' && (
            <div className="flex flex-wrap gap-2" role="group" aria-label={t('newTopic.askLevel')}>
              {LEVELS.map((l) => (
                <Button key={l} variant={l === level ? 'default' : 'outline'} onClick={() => { setLevel(l); setStep('language') }}>
                  {t(`level.${l}`)}
                </Button>
              ))}
            </div>
          )}
          {at('level') && <Bubble who="you">{t(`level.${level}`)}</Bubble>}

          {at('level') && <Bubble who="app">{t('newTopic.askLanguage')}</Bubble>}
          {step === 'language' && (
            <div className="flex flex-wrap gap-2" role="group" aria-label={t('newTopic.askLanguage')}>
              {TOPIC_LANGUAGES.map((l) => (
                <Button key={l} variant={l === language ? 'default' : 'outline'} onClick={() => { setLanguage(l); setStep('ready') }}>
                  {t(`topicLanguage.${l}`)}
                </Button>
              ))}
            </div>
          )}
          {at('language') && <Bubble who="you">{t(`topicLanguage.${language}`)}</Bubble>}

          {step === 'ready' && (
            <Button className="mt-2" onClick={() => { create.mutate(); }} disabled={create.isPending}>
              {create.isPending ? t('newTopic.creating') : t('newTopic.create')}
            </Button>
          )}
          {create.error && <p role="alert" className="text-sm text-destructive">{t('app.error', { message: create.error.message })}</p>}
        </div>
      </DialogContent>
    </Dialog>
  )
}
