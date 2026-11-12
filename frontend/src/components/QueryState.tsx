import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'

export function QueryLoading() {
  const { t } = useTranslation()
  return <p className="text-muted-foreground">{t('app.loading')}</p>
}

// Failures are shown with a retry, never masked (PRODUCT.md principle 5). Without `onRetry` it is
// the inline error line under a form.
export function QueryError({ error, onRetry }: { error: Error; onRetry?: () => void }) {
  const { t } = useTranslation()
  return (
    <div role="alert" className="flex flex-wrap items-center gap-3 text-sm text-destructive">
      <span>{t('app.error', { message: error.message })}</span>
      {onRetry && (
        <Button variant="outline" size="sm" onClick={onRetry}>
          {t('app.retry')}
        </Button>
      )}
    </div>
  )
}
