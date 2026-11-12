// Usage per topic (design §9 GET /usage): KB hit rate, tokens per role, estimated savings — the
// evidence that the knowledge base pays off (PRODUCT.md, Positioning).
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { api } from '@/api/endpoints'
import { QueryError, QueryLoading } from '@/components/QueryState'
import { useFormatNumber } from '@/lib/format'
import { keys } from '@/lib/queries'

export function UsagePage() {
  const { t } = useTranslation()
  const usage = useQuery({ queryKey: keys.usage, queryFn: api.usage })
  const fmt = useFormatNumber()
  return (
    <div className="mx-auto max-w-3xl p-4 md:p-8">
      <h1 className="mb-2 font-serif text-2xl font-semibold">{t('usage.title')}</h1>
      <p className="mb-6 text-sm text-muted-foreground">{t('usage.intro')}</p>
      {usage.isPending && <QueryLoading />}
      {usage.error && <QueryError error={usage.error} onRetry={() => void usage.refetch()} />}
      {usage.data?.length === 0 && <p className="text-sm text-muted-foreground">{t('usage.empty')}</p>}
      <ul className="flex flex-col gap-8">
        {usage.data?.map((u) => {
          const roles = Object.entries(u.tokens_by_role).sort((a, b) => b[1] - a[1])
          const max = roles[0]?.[1] ?? 0
          return (
            <li key={u.topic_id} data-usage={u.topic_id} className="border-t pt-4">
              <Link to={`/topics/${u.topic_id}`} className="font-serif text-lg font-semibold">
                {u.title}
              </Link>
              <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-4">
                <Stat label={t('usage.answers')} value={fmt(u.answers)} />
                <Stat label={t('usage.kbAnswers')} value={fmt(u.kb_answers)} kb />
                <Stat label={t('usage.hitRate')} value={`${Math.round(u.hit_rate * 100)}%`} kb />
                <Stat label={t('usage.saved')} value={`${fmt(u.estimated_tokens_saved)} token`} />
              </dl>
              {roles.length > 0 && (
                <div className="mt-4">
                  <h2 className="mb-2 text-xs font-medium text-muted-foreground">{t('usage.byRole')}</h2>
                  <table className="w-full text-sm">
                    <tbody>
                      {roles.map(([role, tokens]) => (
                        <tr key={role}>
                          <th scope="row" className="w-40 py-1 pr-3 text-left font-mono text-xs font-normal">
                            {role}
                          </th>
                          <td className="py-1">
                            <div className="flex items-center gap-2">
                              <div className="h-1.5 rounded-sm bg-foreground/70" style={{ width: `${max ? Math.max(2, (tokens / max) * 60) : 0}%` }} aria-hidden />
                              <span className="tabular-nums text-muted-foreground">{fmt(tokens)}</span>
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </li>
          )
        })}
      </ul>
    </div>
  )
}

function Stat({ label, value, kb = false }: { label: string; value: string; kb?: boolean }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className={kb ? 'text-lg text-kb tabular-nums' : 'text-lg tabular-nums'}>{value}</dd>
    </div>
  )
}
