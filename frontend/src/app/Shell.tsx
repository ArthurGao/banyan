import { Languages, MessageCircleQuestion, MoreHorizontal } from 'lucide-react'
import { Suspense, lazy, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, NavLink, Outlet, useMatch, useNavigate } from 'react-router'
import { ThemeToggle } from '@/components/ThemeToggle'
import { Button } from '@/components/ui/button'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { HealthBadge } from '@/features/health/HealthBadge'
import { useCandidates } from '@/lib/queries'
import { nextTheme, useTheme } from '@/lib/theme'
import { cn } from '@/lib/utils'

// Loaded when first opened: it pulls in the Markdown stack.
const QuickAskDialog = lazy(() => import('@/features/quickask/QuickAskDialog').then((m) => ({ default: m.QuickAskDialog })))

const navClass = ({ isActive }: { isActive: boolean }) =>
  cn('inline-flex h-9 items-center rounded-md px-1.5 text-sm whitespace-nowrap sm:px-2 pointer-coarse:min-h-11', isActive ? 'text-foreground' : 'text-muted-foreground hover:text-foreground')

function toggleLanguage(i18n: { resolvedLanguage?: string | undefined; changeLanguage: (l: string) => Promise<unknown> }) {
  void i18n.changeLanguage(i18n.resolvedLanguage === 'en' ? 'zh' : 'en')
}

// On a phone the top bar keeps topics, memory and quick ask; usage, language and theme move into
// a menu so nothing scrolls sideways at 390px with 44px touch targets.
function PhoneMenu() {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const { choice, cycle } = useTheme()
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" className="sm:hidden" aria-label={t('app.settings')}>
          <MoreHorizontal aria-hidden />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem onSelect={() => void navigate('/usage')}>{t('app.usage')}</DropdownMenuItem>
        <DropdownMenuItem
          onSelect={() => {
            toggleLanguage(i18n)
          }}
        >
          {t('app.language')}
        </DropdownMenuItem>
        <DropdownMenuItem
          onSelect={cycle}
        >
          {t('app.theme.label', { name: t(`app.theme.${nextTheme(choice)}`) })}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

export function Shell() {
  const { t, i18n } = useTranslation()
  const [asking, setAsking] = useState(false)
  const topicId = useMatch('/topics/:topicId/*')?.params.topicId
  // Candidates waiting for a decision; a down steward just shows no badge (the page says why).
  const waiting = useCandidates().data?.items.length ?? 0
  return (
    <div className="flex h-full flex-col" lang={i18n.resolvedLanguage}>
      <header className="flex items-center gap-1 border-b px-2 py-1.5 sm:gap-2 md:px-4">
        <Link to="/" className="px-1 font-serif text-lg font-semibold tracking-tight">
          {t('app.name')}
        </Link>
        <nav className="flex items-center gap-0.5 sm:ml-2 sm:gap-1" aria-label="main">
          <NavLink to="/" end className={navClass}>
            {t('app.topics')}
          </NavLink>
          <NavLink to="/memory" className={navClass}>
            {t('app.memory')}
            {waiting > 0 && (
              <span className="ml-1 rounded-sm bg-kb px-1.5 text-xs text-kb-foreground tabular-nums" aria-label={t('memory.candidatesBadge', { count: waiting })}>
                {waiting}
              </span>
            )}
          </NavLink>
          <NavLink to="/usage" className={(s) => cn(navClass(s), 'hidden sm:inline-flex')}>
            {t('app.usage')}
          </NavLink>
        </nav>
        <div className="ml-auto flex items-center gap-1">
          <Button
            variant="outline"
            size="sm"
            aria-label={t('quickAsk.label')}
            onClick={() => {
              setAsking(true)
            }}
          >
            <MessageCircleQuestion aria-hidden />
            <span className="hidden md:inline">{t('quickAsk.label')}</span>
          </Button>
          <HealthBadge />
          <Button
            variant="ghost"
            size="sm"
            className="hidden sm:inline-flex"
            title={t('app.languageLabel')}
            onClick={() => {
              toggleLanguage(i18n)
            }}
          >
            <Languages aria-hidden />
            {t('app.language')}
          </Button>
          <ThemeToggle className="hidden sm:inline-flex" />
          <PhoneMenu />
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
