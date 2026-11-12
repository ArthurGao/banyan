import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'

export function NotFound() {
  const { t } = useTranslation()
  return (
    <div className="p-6">
      <p>{t('app.notFound')}</p>
      <Link to="/" className="underline">
        {t('app.topics')}
      </Link>
    </div>
  )
}
