import { ListTree, NotebookText } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { Sheet, SheetContent, SheetDescription, SheetTitle } from '@/components/ui/sheet'
import { useLayout } from '@/lib/layout'

// One shell for every size (frontend/AGENTS.md rule 2): three panes from 1200px, two from 768px
// (outline in a left drawer), one below (outline and notes in bottom sheets). Panes are separated by
// 1px hairlines, no shadows.
interface Props {
  outline: ReactNode
  main: ReactNode
  notes: ReactNode
  /** Changes on navigation; the sheets close when it does. */
  closeKey?: string
}

export function ResponsiveSplit({ outline, main, notes, closeKey }: Props) {
  const { t } = useTranslation()
  const layout = useLayout()
  const [open, setOpen] = useState<'outline' | 'notes' | null>(null)
  const [lastKey, setLastKey] = useState(closeKey)
  if (closeKey !== lastKey) {
    setLastKey(closeKey)
    setOpen(null)
  }

  if (layout === 'three') {
    return (
      <div data-layout="three" className="grid h-full grid-cols-[18rem_minmax(0,1fr)_20rem]">
        <Pane name="outline">{outline}</Pane>
        <Pane name="main">{main}</Pane>
        <Pane name="notes">{notes}</Pane>
      </div>
    )
  }

  return (
    <div data-layout={layout} className={layout === 'two' ? 'grid h-full grid-cols-[minmax(0,1fr)_18rem]' : 'h-full'}>
      <Pane name="main">
        <div className="mb-3 flex gap-2">
          <Button variant="outline" size="sm" onClick={() => { setOpen('outline'); }} aria-expanded={open === 'outline'}>
            <ListTree aria-hidden />
            {t('outline.openOutline')}
          </Button>
          {layout === 'one' && (
            <Button variant="outline" size="sm" onClick={() => { setOpen('notes'); }} aria-expanded={open === 'notes'}>
              <NotebookText aria-hidden />
              {t('notes.title')}
            </Button>
          )}
        </div>
        {main}
      </Pane>
      {layout === 'two' && <Pane name="notes">{notes}</Pane>}
      <Sheet open={open !== null} onOpenChange={(o) => {
          if (!o) setOpen(null)
        }}>
        <SheetContent
          side={layout === 'two' ? 'left' : 'bottom'}
          data-pane={open ?? undefined}
          aria-label={open === 'notes' ? t('notes.title') : t('outline.openOutline')}
          className={layout === 'two' ? 'w-80 overflow-auto p-4' : 'max-h-[80vh] overflow-auto rounded-t-xl p-4'}
        >
          <SheetTitle className="sr-only">{open === 'notes' ? t('notes.title') : t('outline.openOutline')}</SheetTitle>
          <SheetDescription className="sr-only">{open === 'notes' ? t('notes.title') : t('outline.openOutline')}</SheetDescription>
          {open === 'notes' ? notes : outline}
        </SheetContent>
      </Sheet>
    </div>
  )
}

function Pane({ name, children }: { name: string; children: ReactNode }) {
  return (
    <section data-pane={name} aria-label={name} className="min-w-0 overflow-auto border-border p-4 not-last:border-r md:p-6">
      {children}
    </section>
  )
}
