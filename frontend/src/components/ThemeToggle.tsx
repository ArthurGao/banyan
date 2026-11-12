import { Monitor, Moon, Sun } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { useTheme, type ThemeChoice } from '@/lib/theme'

const ICON = { system: Monitor, light: Sun, dark: Moon } satisfies Record<ThemeChoice, unknown>

export function ThemeToggle({ className }: { className?: string }) {
  const { t } = useTranslation()
  const { choice, cycle } = useTheme()
  const Icon = ICON[choice]
  const label = t('app.theme.label', { name: t(`app.theme.${choice}`) })
  return (
    <Button variant="ghost" size="icon" title={label} className={className} onClick={cycle}>
      <Icon aria-hidden />
      <span className="sr-only">{label}</span>
    </Button>
  )
}
