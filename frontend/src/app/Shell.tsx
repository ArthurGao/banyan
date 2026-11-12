// Top bar (comp A): mark and topic breadcrumb on the left; generate-all meter, quick ask, memory and
// the menu on the right. Pages, language and theme live in the menu at every width.
import { Lightbulb, MoreHorizontal, Search } from 'lucide-react'
import { Suspense, lazy, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, Outlet, useMatch, useNavigate } from 'react-router'
import { Button } from '@/components/ui/button'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { HealthBadge } from '@/features/health/HealthBadge'
import { RunMeter } from '@/features/run/RunMeter'
import { currentLanguage, setLanguage } from '@/i18n'
import { useCandidates, useTopicTitle } from '@/lib/queries'
import { nextTheme, useTheme } from '@/lib/theme'

// Loaded when first opened: it pulls in the Markdown stack.
const QuickAskDialog = lazy(() => import('@/features/quickask/QuickAskDialog').then((m) => ({ default: m.QuickAskDialog })))

/** A banyan: a trunk, two leaves, two aerial roots that became supports. */
function BanyanMark() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" className="text-kb" aria-hidden>
      <path d="M12 21V9" />
      <path d="M12 9c-4 0-7-3-7-6 3 0 7 2 7 6Zm0 0c4 0 7-3 7-6-3 0-7 2-7 6Z" />
      <path d="M8 21v-5M16 21v-5" />
    </svg>
  )
}

function Menu() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { choice, cycle } = useTheme()
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" aria-label={t('app.settings')}>
          <MoreHorizontal aria-hidden />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem onSelect={() => void navigate('/')}>{t('app.topics')}</DropdownMenuItem>
        <DropdownMenuItem onSelect={() => void navigate('/memory')}>{t('app.memory')}</DropdownMenuItem>
        <DropdownMenuItem onSelect={() => void navigate('/usage')}>{t('app.usage')}</DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => void setLanguage(currentLanguage() === 'en' ? 'zh' : 'en')}>{t('app.language')}</DropdownMenuItem>
        <DropdownMenuItem onSelect={cycle}>{t('app.theme.label', { name: t(`app.theme.${nextTheme(choice)}`) })}</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

function Breadcrumb({ topicId }: { topicId: string }) {
  const title = useTopicTitle(topicId).data
  if (!title) return null
  return (
    <>
      <span className="text-muted-foreground" aria-hidden>
        /
      </span>
      <Link to={`/topics/${topicId}`} className="min-w-0 truncate font-serif text-[17px]">
        {title}
      </Link>
    </>
  )
}

export function Shell() {
  const { t, i18n } = useTranslation()
  const [asking, setAsking] = useState(false)
  const topicId = useMatch('/topics/:topicId/*')?.params.topicId
  // Candidates waiting for a decision; a down steward just shows no dot (the page says why).
  const waiting = useCandidates().data?.items.length ?? 0
  return (
    <div className="flex h-full flex-col" lang={i18n.resolvedLanguage}>
      <header className="flex h-14 shrink-0 items-center gap-2.5 border-b bg-panel px-3 md:px-5">
        <Link to="/" className="flex shrink-0 items-center gap-2" aria-label={t('app.topics')}>
          <BanyanMark />
          <span className="font-serif text-xl font-medium">{t('app.name')}</span>
        </Link>
        {topicId && <Breadcrumb topicId={topicId} />}
        <div className="ml-auto flex shrink-0 items-center gap-2 md:gap-3">
          {topicId && <RunMeter topicId={topicId} />}
          <HealthBadge />
          <button
            type="button"
            aria-label={t('quickAsk.label')}
            onClick={() => {
              setAsking(true)
            }}
            className="flex h-9 items-center gap-2 rounded-md border border-input bg-card px-2.5 text-sm text-muted-foreground hover:text-foreground md:w-65 pointer-coarse:min-h-11"
          >
            <Search className="size-4" aria-hidden />
            <span className="hidden md:inline">{t('quickAsk.trigger')}</span>
          </button>
          <Button variant="outline" size="icon" asChild className="relative">
            <Link to="/memory" aria-label={waiting > 0 ? t('memory.candidatesBadge', { count: waiting }) : t('app.memory')}>
              <Lightbulb aria-hidden />
              {waiting > 0 && <span aria-hidden className="absolute top-1.5 right-1.5 size-2 rounded-full bg-warn" />}
            </Link>
          </Button>
          <Menu />
        </div>
      </header>
      <main className="min-h-0 flex-1">
        <Outlet />
      </main>
      {asking && (
        <Suspense>
          <QuickAskDialog open onOpenChange={setAsking} topicId={topicId} />
        </Suspense>
      )}
    </div>
  )
}
