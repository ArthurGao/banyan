// The HTML study guide (design §10.2): built by the backend from stored notes, previewed in a
// sandboxed iframe, downloaded, or shared through the system share sheet where there is one.
import { useMutation } from '@tanstack/react-query'
import { Download, Share2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useLocation } from 'react-router'
import { api } from '@/api/endpoints'
import { Button } from '@/components/ui/button'

export function ExportPanel({ topicId, title }: { topicId: string; title: string }) {
  const { t } = useTranslation()
  const [doc, setDoc] = useState<{ html: string; url: string } | null>(null)
  // The object URL behind "download" is released when it is replaced or the panel goes away.
  const urlRef = useRef<string | null>(null)
  // The notes pane's "export HTML" link lands here.
  const sectionRef = useRef<HTMLElement>(null)
  const { hash } = useLocation()
  useEffect(() => {
    if (hash === '#export') sectionRef.current?.scrollIntoView({ block: 'start' })
  }, [hash])
  useEffect(
    () => () => {
      if (urlRef.current) URL.revokeObjectURL(urlRef.current)
    },
    [],
  )
  const build = useMutation({
    mutationFn: async () => {
      const out = await api.exportTopic(topicId)
      if (out.status === 'failed') throw new Error(t('export.failed', { error: out.error ?? '' }))
      return api.exportHtml(out.id)
    },
    onSuccess: (html) => {
      if (urlRef.current) URL.revokeObjectURL(urlRef.current)
      const url = URL.createObjectURL(new Blob([html], { type: 'text/html' }))
      urlRef.current = url
      setDoc({ html, url })
    },
  })

  const filename = `${title.replace(/[\\/:*?"<>|]+/g, '-').slice(0, 80) || 'banyan'}.html`
  const file = doc ? new File([doc.html], filename, { type: 'text/html' }) : null
  const canShare = file !== null && typeof navigator.canShare === 'function' && navigator.canShare({ files: [file] })

  return (
    <section ref={sectionRef} id="export" aria-label={t('export.title')} className="mt-8 scroll-mt-4">
      <h2 className="mb-3 font-serif text-lg font-medium">{t('export.title')}</h2>
      <div className="flex flex-wrap gap-2">
        <Button
          variant="outline"
          onClick={() => {
            build.mutate()
          }}
          disabled={build.isPending}
        >
          {build.isPending ? t('export.creating') : t('export.create')}
        </Button>
        {doc && (
          <Button variant="outline" asChild>
            <a href={doc.url} download={filename}>
              <Download aria-hidden />
              {t('export.download')}
            </a>
          </Button>
        )}
        {canShare && (
          <Button variant="outline" onClick={() => void navigator.share({ files: [file], title }).catch(() => undefined)}>
            <Share2 aria-hidden />
            {t('export.share')}
          </Button>
        )}
      </div>
      {build.error && (
        <p role="alert" className="mt-2 text-sm text-destructive">
          {build.error.message}
        </p>
      )}
      {doc && (
        // Scripts in the export (KaTeX) run only inside the sandbox, without the app's origin.
        <iframe title={t('export.preview')} sandbox="allow-scripts" srcDoc={doc.html} className="mt-4 h-[70vh] w-full rounded-md border bg-white" />
      )}
    </section>
  )
}
