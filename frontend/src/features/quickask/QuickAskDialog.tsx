// Quick ask (design §5.3): a question without a node. Step 1 classifies it into the outline (JSON)
// and shows where it goes — changeable; when no node fits, the learner decides whether to add the
// suggested one. Step 2 opens a branch there and streams the answer.
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router'
import type { ClassifyOut, QuickAskIn } from '@/api/generated'
import { api } from '@/api/endpoints'
import { QueryError } from '@/components/QueryState'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { StreamingMessage } from '@/features/message/StreamingMessage'
import { refreshTopic, useTopic, useTopics } from '@/lib/queries'
import { runStream, useStream } from '@/lib/useStream'

export function QuickAskDialog({ open, onOpenChange, topicId }: { open: boolean; onOpenChange: (o: boolean) => void; topicId: string | undefined }) {
  const { t } = useTranslation()
  const topics = useTopics()
  const ready = (topics.data ?? []).filter((x) => x.outline_status === 'confirmed' && x.status === 'active')
  const [chosenTopic, setChosenTopic] = useState<string | undefined>(undefined)
  const current = chosenTopic ?? (ready.some((x) => x.id === topicId) ? topicId : ready[0]?.id)
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t('quickAsk.label')}</DialogTitle>
          <DialogDescription className="sr-only">{t('quickAsk.placeholder')}</DialogDescription>
        </DialogHeader>
        {ready.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t('quickAsk.noTopics')}</p>
        ) : (
          <>
            {ready.length > 1 && (
              <Select value={current ?? ''} onValueChange={setChosenTopic}>
                <SelectTrigger aria-label={t('quickAsk.pickTopic')}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {ready.map((x) => (
                    <SelectItem key={x.id} value={x.id}>
                      {x.title}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
            {current && (
              <QuickAsk
                key={current}
                topicId={current}
                onClose={() => {
                  onOpenChange(false)
                }}
              />
            )}
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}

function QuickAsk({ topicId, onClose }: { topicId: string; onClose: () => void }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const topic = useTopic(topicId)
  const { state, start, stop } = useStream()
  const [question, setQuestion] = useState('')
  const [allowWeb, setAllowWeb] = useState(true)
  const [verdict, setVerdict] = useState<ClassifyOut | null>(null)
  const [nodeId, setNodeId] = useState<string | null>(null)
  const [picking, setPicking] = useState(false)
  const order = topic.data?.order ?? []
  const titleOf = (id: string | null | undefined) => order.find((n) => n.node.id === id)?.node.title ?? ''

  const classify = useMutation({
    mutationFn: () => api.classify(topicId, question.trim()),
    onSuccess: (v) => {
      setVerdict(v)
      setNodeId(v.node_id)
      setPicking(false)
    },
  })

  const ask = async (body: QuickAskIn) => {
    await runStream(start, (on, signal) => api.quickAsk(topicId, body, on, signal))
    await refreshTopic(queryClient, topicId)
  }
  const base = { question: question.trim(), allow_web: allowWeb }

  if (state.phase !== 'idle') {
    const place = state.placement
    return (
      <div className="flex flex-col gap-4">
        <p className="ml-auto w-fit rounded-lg bg-secondary px-3 py-2 text-sm whitespace-pre-wrap">{question}</p>
        {place && <p className="text-xs text-muted-foreground">{t('quickAsk.filedUnder', { title: titleOf(place.nodeId) || verdict?.suggested_node?.title })}</p>}
        <StreamingMessage state={state} onStop={stop} />
        {place && state.phase !== 'streaming' && (
          <Button
            className="self-start"
            onClick={() => {
              onClose()
              void navigate(`/topics/${topicId}/nodes/${place.nodeId}/branches/${place.branchId}`)
            }}
          >
            {t('quickAsk.openBranch')}
          </Button>
        )}
      </div>
    )
  }

  const suggested = verdict?.suggested_node
  return (
    <div className="flex flex-col gap-3">
      <Textarea
        autoFocus
        aria-label={t('quickAsk.label')}
        placeholder={t('quickAsk.placeholder')}
        value={question}
        onChange={(e) => {
          setQuestion(e.target.value)
          setVerdict(null)
        }}
      />
      <label className="flex items-center gap-2 text-sm text-muted-foreground">
        <Checkbox checked={allowWeb} onCheckedChange={(v) => { setAllowWeb(v === true) }} />
        {t('branch.allowWeb')}
      </label>
      {!verdict && (
        <Button className="self-end" disabled={!question.trim() || classify.isPending} onClick={() => { classify.mutate() }}>
          {classify.isPending ? t('quickAsk.classifying') : t('quickAsk.ask')}
        </Button>
      )}
      {classify.error && <QueryError error={classify.error} />}

      {verdict && (
        <div className="flex flex-col gap-3 border-t pt-3" data-confident={verdict.confident}>
          {!verdict.confident && suggested && (
            <div className="flex flex-col gap-2">
              <p className="text-sm">
                {suggested.parent_id
                  ? t('quickAsk.suggest', { title: suggested.title, parent: titleOf(suggested.parent_id) })
                  : t('quickAsk.suggestTopLevel', { title: suggested.title })}
              </p>
              <Button className="self-start" onClick={() => void ask({ ...base, new_node: suggested })}>
                {t('quickAsk.addNode')}
              </Button>
            </div>
          )}
          {nodeId && !picking && <p className="text-sm">{t('quickAsk.filedUnder', { title: titleOf(nodeId) })}</p>}
          {(picking || !nodeId) && (
            <Select value={nodeId ?? ''} onValueChange={(v) => { setNodeId(v); setPicking(false) }}>
              <SelectTrigger aria-label={t('quickAsk.pickNode')}>
                <SelectValue placeholder={t('quickAsk.pickNode')} />
              </SelectTrigger>
              <SelectContent>
                {order.map((n) => (
                  <SelectItem key={n.node.id} value={n.node.id}>
                    {n.number} {n.node.title}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          <div className="flex flex-wrap gap-2">
            {nodeId && !picking && (
              <Button variant="outline" onClick={() => { setPicking(true) }}>
                {t('quickAsk.change')}
              </Button>
            )}
            <Button disabled={!nodeId} variant={!verdict.confident && suggested ? 'outline' : 'default'} onClick={() => nodeId && void ask({ ...base, node_id: nodeId })}>
              {!verdict.confident && suggested ? t('quickAsk.useChosen') : t('quickAsk.ask')}
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
