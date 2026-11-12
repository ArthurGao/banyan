// Topic settings (design §9 PATCH /topics/{id}): title, goal, level, explanation language, the
// topic instructions sent with every explanation and answer, archive.
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, type SyntheticEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import type { Language, Level, TopicPatch } from '@/api/generated'
import { api } from '@/api/endpoints'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { useTopicContext } from '@/features/topic/context'
import { LEVELS, TOPIC_LANGUAGES } from '@/lib/enums'
import { toastError } from '@/lib/errors'
import { refreshTopic } from '@/lib/queries'


export function TopicSettings() {
  const { t } = useTranslation()
  const { detail } = useTopicContext()
  const topic = detail.topic
  const queryClient = useQueryClient()
  const [title, setTitle] = useState(topic.title)
  const [goal, setGoal] = useState(topic.goal ?? '')
  const [level, setLevel] = useState<Level>(topic.level)
  const [language, setLanguage] = useState<Language>(topic.language)
  const [instructions, setInstructions] = useState(topic.instructions ?? '')
  const save = useMutation({
    mutationFn: (body: TopicPatch) => api.updateTopic(topic.id, body),
    onSuccess: async () => {
      toast.success(t('topicSettings.saved'))
      await refreshTopic(queryClient, topic.id)
    },
    onError: toastError,
  })
  const submit = (e: SyntheticEvent) => {
    e.preventDefault()
    save.mutate({ title: title.trim(), goal: goal.trim() || null, level, language, instructions: instructions.trim() || null })
  }
  return (
    <div className="mx-auto max-w-2xl">
      <h1 className="mb-6 font-serif text-2xl font-medium">{t('topicSettings.title')}</h1>
      <form onSubmit={submit} className="flex flex-col gap-4">
        <div className="flex flex-col gap-2">
          <Label htmlFor="t-title">{t('topicSettings.name')}</Label>
          <Input id="t-title" required maxLength={200} value={title} onChange={(e) => { setTitle(e.target.value) }} />
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="t-goal">{t('topicSettings.goal')}</Label>
          <Input id="t-goal" maxLength={2000} value={goal} onChange={(e) => { setGoal(e.target.value) }} />
        </div>
        <div className="flex flex-wrap gap-4">
          <div className="flex flex-col gap-2">
            <Label>{t('topicSettings.level')}</Label>
            <Select value={level} onValueChange={(v) => { setLevel(v as Level) }}>
              <SelectTrigger className="w-44" aria-label={t('topicSettings.level')}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {LEVELS.map((l) => (
                  <SelectItem key={l} value={l}>
                    {t(`level.${l}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-2">
            <Label>{t('topicSettings.language')}</Label>
            <Select value={language} onValueChange={(v) => { setLanguage(v as Language) }}>
              <SelectTrigger className="w-44" aria-label={t('topicSettings.language')}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {TOPIC_LANGUAGES.map((l) => (
                  <SelectItem key={l} value={l}>
                    {t(`topicLanguage.${l}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="t-instr">{t('topicSettings.instructions')}</Label>
          <p className="text-xs text-muted-foreground">{t('topicSettings.instructionsHint')}</p>
          <Textarea id="t-instr" rows={6} maxLength={10_000} value={instructions} onChange={(e) => { setInstructions(e.target.value) }} />
        </div>
        <div className="flex flex-wrap justify-between gap-2">
          <Button
            type="button"
            variant="ghost"
            onClick={() => {
              save.mutate({ status: topic.status === 'archived' ? 'active' : 'archived' })
            }}
          >
            {topic.status === 'archived' ? t('topicSettings.unarchive') : t('topicSettings.archive')}
          </Button>
          <Button type="submit" disabled={save.isPending || !title.trim()}>
            {t('app.save')}
          </Button>
        </div>
      </form>
    </div>
  )
}
