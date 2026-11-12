import { Plus } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { Button } from '@/components/ui/button'
import { QueryError, QueryLoading } from '@/components/QueryState'
import { NewTopicDialog } from '@/features/topics/NewTopicDialog'
import { useTopics } from '@/lib/queries'

export function TopicsPage() {
  const { t } = useTranslation()
  const topics = useTopics()
  const [creating, setCreating] = useState(false)
  return (
    <div className="mx-auto max-w-3xl p-4 md:p-8">
      <div className="mb-6 flex items-center justify-between gap-3">
        <h1 className="font-serif text-2xl font-semibold">{t('topics.title')}</h1>
        <Button onClick={() => { setCreating(true); }}>
          <Plus aria-hidden />
          {t('topics.new')}
        </Button>
      </div>
      {topics.isPending && <QueryLoading />}
      {topics.error && <QueryError error={topics.error} onRetry={() => void topics.refetch()} />}
      {topics.data?.length === 0 && <p className="text-muted-foreground">{t('topics.empty')}</p>}
      {topics.data && topics.data.length > 0 && (
      <ul className="divide-y border-y">
        {topics.data.map((topic) => (
          <li key={topic.id}>
            <Link to={`/topics/${topic.id}`} className="flex flex-col gap-1 px-1 py-4 hover:bg-accent/50 sm:flex-row sm:items-baseline sm:justify-between">
              <span className="font-serif text-lg">{topic.title}</span>
              <span className="text-sm text-muted-foreground">
                {topic.status === 'archived'
                  ? t('topics.archived')
                  : topic.outline_status === 'draft'
                    ? t('topics.draft')
                    : t('topics.progress', { done: topic.done, total: topic.nodes })}
              </span>
            </Link>
          </li>
        ))}
      </ul>
      )}
      <NewTopicDialog open={creating} onOpenChange={setCreating} />
    </div>
  )
}
