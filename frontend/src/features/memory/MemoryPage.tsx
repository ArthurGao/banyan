// The memory page (design §8.5): what steward learned about the learner (pin, delete) and the KB
// cards waiting for a decision. These calls go to steward synchronously; 503 means it is down.
import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Pin, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import type { CandidateOut, MemoryOut } from '@/api/generated'
import { api } from '@/api/endpoints'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import { Markdown } from '@/components/Markdown'
import { QueryError } from '@/components/QueryState'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import i18n from '@/i18n'
import { isStatus, toastError } from '@/lib/errors'
import { keys, useCandidates } from '@/lib/queries'
import { cn } from '@/lib/utils'

const unavailable = (e: Error | null) => isStatus(e, 503)
// 503: steward is down or not configured (design §8.5).
const fail = (e: Error) => {
  if (unavailable(e)) toast.error(i18n.t('memory.unavailable'))
  else toastError(e)
}

export function MemoryPage() {
  const { t } = useTranslation()
  const memories = useInfiniteQuery({
    queryKey: keys.memories,
    queryFn: ({ pageParam }) => api.memories(pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
  })
  const candidates = useCandidates()
  const down = unavailable(memories.error) || unavailable(candidates.error)
  return (
    <div className="mx-auto max-w-3xl p-4 md:p-8">
      <h1 className="mb-2 font-serif text-2xl font-medium">{t('memory.title')}</h1>
      <p className="mb-6 text-sm text-muted-foreground">{t('memory.intro')}</p>
      {down && <p role="alert" className="mb-6 text-sm text-warn">{t('memory.unavailable')}</p>}

      <section aria-label={t('memory.candidates')} className="mb-10">
        <h2 className="mb-3 font-serif text-lg font-medium">{t('memory.candidates')}</h2>
        {candidates.error && !down && <QueryError error={candidates.error} onRetry={() => void candidates.refetch()} />}
        {candidates.data?.items.length === 0 && <p className="text-sm text-muted-foreground">{t('memory.candidatesEmpty')}</p>}
        <ul className="flex flex-col gap-4">
          {candidates.data?.items.map((c) => <CandidateCard key={c.id} candidate={c} />)}
        </ul>
      </section>

      <section aria-label={t('memory.title')}>
        <h2 className="mb-3 font-serif text-lg font-medium">{t('memory.title')}</h2>
        {memories.error && !down && <QueryError error={memories.error} onRetry={() => void memories.refetch()} />}
        {memories.data?.pages[0]?.items.length === 0 && <p className="text-sm text-muted-foreground">{t('memory.empty')}</p>}
        <ul className="divide-y border-y empty:hidden">
          {memories.data?.pages.flatMap((p) => p.items).map((m) => <MemoryRow key={m.id} memory={m} />)}
        </ul>
        {memories.hasNextPage && (
          <Button variant="outline" className="mt-4" onClick={() => void memories.fetchNextPage()} disabled={memories.isFetchingNextPage}>
            {t('memory.more')}
          </Button>
        )}
      </section>
    </div>
  )
}

function MemoryRow({ memory }: { memory: MemoryOut }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [confirming, setConfirming] = useState(false)
  const refresh = () => queryClient.invalidateQueries({ queryKey: keys.memories })
  const pin = useMutation({ mutationFn: () => api.pinMemory(memory.id, !memory.pinned), onSuccess: refresh, onError: fail })
  const remove = useMutation({ mutationFn: () => api.deleteMemory(memory.id), onSuccess: refresh, onError: fail })
  return (
    <li className="flex items-start gap-2 py-3" data-memory={memory.id}>
      <div className="min-w-0 flex-1">
        <p className="text-sm">{memory.content}</p>
        <p className="mt-1 text-xs text-muted-foreground">
          {memory.memory_type} · {new Date(memory.updated_at).toLocaleDateString()}
        </p>
      </div>
      <Button
        variant="ghost"
        size="icon"
        aria-pressed={memory.pinned}
        aria-label={memory.pinned ? t('memory.unpin') : t('memory.pin')}
        title={memory.pinned ? t('memory.unpin') : t('memory.pin')}
        onClick={() => {
          pin.mutate()
        }}
        className={cn(memory.pinned && 'text-foreground')}
      >
        <Pin aria-hidden className={cn(memory.pinned && 'fill-current')} />
      </Button>
      <Button
        variant="ghost"
        size="icon"
        aria-label={t('app.delete')}
        onClick={() => {
          setConfirming(true)
        }}
      >
        <Trash2 aria-hidden />
      </Button>
      <ConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        title={t('app.delete')}
        body={t('memory.deleteConfirm')}
        confirmLabel={t('app.delete')}
        destructive
        onConfirm={() => {
          remove.mutate()
        }}
      />
    </li>
  )
}

function CandidateCard({ candidate }: { candidate: CandidateOut }) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [note, setNote] = useState('')
  const decide = useMutation({
    mutationFn: (decision: 'accept' | 'reject') => api.decideCandidate(candidate.id, decision, note.trim() || null),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.candidates }),
    onError: fail,
  })
  return (
    <li className="rounded-md border p-4" data-candidate={candidate.id}>
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="font-medium">{candidate.title}</h3>
        <span className={cn('text-xs', candidate.risk_level === 'high' ? 'text-destructive' : 'text-muted-foreground')}>
          {t(`memory.risk.${candidate.risk_level}`)}
        </span>
      </div>
      <Markdown text={candidate.content} className="text-sm" />
      {candidate.reason && <p className="mt-2 text-xs text-muted-foreground">{candidate.reason}</p>}
      <Input className="mt-3" aria-label={t('memory.note')} placeholder={t('memory.note')} value={note} onChange={(e) => { setNote(e.target.value) }} />
      <div className="mt-3 flex gap-2">
        <Button onClick={() => { decide.mutate('accept') }} disabled={decide.isPending}>
          {t('memory.accept')}
        </Button>
        <Button variant="outline" onClick={() => { decide.mutate('reject') }} disabled={decide.isPending}>
          {t('memory.reject')}
        </Button>
      </div>
    </li>
  )
}
