// The outline tree (design §11.2): numbered 1 / 1.2 / 1.2.3, progress marks, explanation state;
// arrow keys move between nodes (frontend rule 6). In edit mode each node has a menu (rename, add
// child, insert prerequisite, move up / down, delete) and a drag handle to reorder among siblings.
import { DndContext, KeyboardSensor, PointerSensor, closestCenter, useSensor, useSensors, type DragEndEvent } from '@dnd-kit/core'
import { SortableContext, sortableKeyboardCoordinates, useSortable, verticalListSortingStrategy } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { GripVertical, Loader2, MoreHorizontal } from 'lucide-react'
import { useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import type { NodeOut, NodeStatus } from '@/api/generated'
import { api } from '@/api/endpoints'
import { QueryError } from '@/components/QueryState'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { MAX_DEPTH, buildTree, siblingsOf, type ExplanationState, type TreeNode } from '@/lib/outline'
import { toastError } from '@/lib/errors'
import { refreshTopic } from '@/lib/queries'
import { cn } from '@/lib/utils'


// Progress as a 10px dot (comp A): empty, half green while learning, solid green when done.
const DOT: Record<NodeStatus, string> = {
  not_started: 'border-muted-foreground/50',
  learning: 'border-kb bg-[linear-gradient(90deg,var(--kb)_50%,transparent_50%)]',
  done: 'border-kb bg-kb',
}

interface Props {
  topicId: string
  nodes: NodeOut[]
  activeNodeId?: string | undefined
  editable?: boolean
  /** Background generation state per node (generation runs, design §11.2). */
  explanation?: Record<string, ExplanationState>
  onRetry?: ((nodeId: string) => void) | undefined
}

type Editing =
  | { kind: 'rename'; node: NodeOut }
  | { kind: 'add'; parentId: string | null; position: number | null; origin: 'user' | 'prerequisite' }
  | { kind: 'delete'; node: NodeOut }

export function OutlineTree({ topicId, nodes, activeNodeId, editable = false, explanation = {}, onRetry }: Props) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState<Editing | null>(null)
  const listRef = useRef<HTMLUListElement>(null)
  const tree = buildTree(nodes)
  const active = nodes.find((n) => n.id === activeNodeId)
  const refresh = () => refreshTopic(queryClient, topicId).then(() => undefined)

  const move = useMutation({
    mutationFn: ({ id, parentId, position }: { id: string; parentId: string | null; position: number }) =>
      api.moveNode(id, { parent_id: parentId, position }),
    onSettled: refresh,
    onError: toastError,
  })

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  )
  const onDragEnd = ({ active, over }: DragEndEvent) => {
    if (!over || active.id === over.id) return
    const a = nodes.find((n) => n.id === active.id)
    const o = nodes.find((n) => n.id === over.id)
    if (!a || a.parent_id !== o?.parent_id) return // reorder among siblings only
    const order = siblingsOf(nodes, a.id)
    move.mutate({ id: a.id, parentId: a.parent_id, position: order.findIndex((n) => n.id === o.id) })
  }

  // Arrow keys move focus between node links (a flat list in reading order).
  const onKeyDown = (e: KeyboardEvent<HTMLUListElement>) => {
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) return
    const links = [...(listRef.current?.querySelectorAll<HTMLAnchorElement>('a[data-node-link]') ?? [])]
    const i = links.indexOf(document.activeElement as HTMLAnchorElement)
    const next = e.key === 'Home' ? 0 : e.key === 'End' ? links.length - 1 : e.key === 'ArrowDown' ? Math.min(i + 1, links.length - 1) : Math.max(i - 1, 0)
    links[next]?.focus()
    e.preventDefault()
  }

  const renderLevel = (level: TreeNode[]): ReactNode => (
    <SortableContext items={level.map((t) => t.node.id)} strategy={verticalListSortingStrategy}>
      {level.map((item) => (
        <Row
          key={item.node.id}
          item={item}
          topicId={topicId}
          active={item.node.id === activeNodeId}
          editable={editable}
          state={explanation[item.node.id]}
          onRetry={onRetry}
          onEdit={setEditing}
          onMove={(delta) => {
            const order = siblingsOf(nodes, item.node.id)
            const at = order.findIndex((n) => n.id === item.node.id)
            const position = at + delta
            if (position >= 0 && position < order.length) move.mutate({ id: item.node.id, parentId: item.node.parent_id, position })
          }}
        >
          {item.children.length > 0 && <ul role="group">{renderLevel(item.children)}</ul>}
        </Row>
      ))}
    </SortableContext>
  )

  return (
    <nav aria-label={t('outline.title')}>
      {nodes.length === 0 && <p className="text-sm text-muted-foreground">{t('outline.empty')}</p>}
      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
        <ul ref={listRef} role="tree" aria-label={t('outline.title')} onKeyDown={onKeyDown} className="text-sm">
          {renderLevel(tree)}
        </ul>
      </DndContext>
      {editable && (
        <Button variant="ghost" size="sm" className="mt-2" onClick={() => { setEditing({ kind: 'add', parentId: null, position: null, origin: 'user' }); }}>
          + {t('outline.addNode')}
        </Button>
      )}
      {!editable && active && (
        <Button
          variant="outline"
          className="mt-4 w-full"
          onClick={() => {
            setEditing({ kind: 'add', parentId: active.parent_id, position: active.position, origin: 'prerequisite' })
          }}
        >
          + {t('outline.insertPrerequisite')}
        </Button>
      )}
      <EditDialog topicId={topicId} editing={editing} onClose={() => { setEditing(null); }} onDone={refresh} />
    </nav>
  )
}

function Row({
  item,
  topicId,
  active,
  editable,
  state,
  onRetry,
  onEdit,
  onMove,
  children,
}: {
  item: TreeNode
  topicId: string
  active: boolean
  editable: boolean
  state: ExplanationState | undefined
  onRetry: ((nodeId: string) => void) | undefined
  onEdit: (e: Editing) => void
  onMove: (delta: number) => void
  children: ReactNode
}) {
  const { t } = useTranslation()
  const { node } = item
  const { setNodeRef, transform, transition, isDragging, attributes, listeners } = useSortable({ id: node.id, disabled: !editable })
  const style = { transform: CSS.Transform.toString(transform), transition }
  return (
    <li ref={setNodeRef} style={style} role="treeitem" aria-level={node.depth + 1} aria-selected={active} aria-current={active ? 'page' : undefined}>
      <div
        className={cn('group flex items-center gap-1 rounded-md pr-1', active && 'bg-kb-soft font-semibold text-foreground', isDragging && 'opacity-60')}
        style={{ paddingLeft: `${0.375 + node.depth * 1.125}rem` }}
      >
        {editable && (
          <button type="button" aria-label={t('outline.dragHandle')} className="cursor-grab touch-none p-1 text-muted-foreground pointer-coarse:hidden" {...attributes} {...listeners}>
            <GripVertical className="size-3.5" aria-hidden />
          </button>
        )}
        <Link
          data-node-link
          to={`/topics/${topicId}/nodes/${node.id}`}
          className="flex min-h-9 min-w-0 flex-1 items-center gap-2.5 px-1 py-1 pointer-coarse:min-h-11"
          title={node.summary ?? undefined}
        >
          <span className="w-7 shrink-0 text-xs text-muted-foreground tabular-nums">{item.number}</span>
          <span aria-hidden data-status={node.status} className={cn('size-2.5 shrink-0 rounded-full border-[1.5px]', DOT[node.status])} />
          <span className="min-w-0 flex-1 truncate">{node.title}</span>
          <span className="sr-only">{t(`outline.status.${node.status}`)}</span>
          {state === 'generating' && (
            <Badge variant="muted" className="h-5 text-[11px]">
              <Loader2 className="animate-spin" aria-hidden />
              {t('outline.explanation.generating')}
            </Badge>
          )}
        </Link>
        {state === 'failed' && (
          <button
            type="button"
            onClick={() => onRetry?.(node.id)}
            className="inline-flex min-h-9 shrink-0 items-center pointer-coarse:min-h-11"
            title={t('outline.explanation.failed')}
          >
            <Badge variant="warn" className="h-5 text-[11px]">
              {t('outline.explanation.failed')}
            </Badge>
          </button>
        )}
        {editable && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon-sm" aria-label={`${t('app.edit')}: ${node.title}`}>
                <MoreHorizontal aria-hidden />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onSelect={() => { onEdit({ kind: 'rename', node }); }}>{t('outline.rename')}</DropdownMenuItem>
              {node.depth < MAX_DEPTH - 1 && (
                <DropdownMenuItem onSelect={() => { onEdit({ kind: 'add', parentId: node.id, position: null, origin: 'user' }); }}>{t('outline.addChild')}</DropdownMenuItem>
              )}
              <DropdownMenuItem onSelect={() => { onEdit({ kind: 'add', parentId: node.parent_id, position: node.position, origin: 'prerequisite' }); }}>
                {t('outline.addPrerequisite')}
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => { onMove(-1); }}>{t('outline.moveUp')}</DropdownMenuItem>
              <DropdownMenuItem onSelect={() => { onMove(1); }}>{t('outline.moveDown')}</DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem variant="destructive" onSelect={() => { onEdit({ kind: 'delete', node }); }}>
                {t('outline.deleteNode')}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </div>
      {children}
    </li>
  )
}

function EditDialog({ topicId, editing, onClose, onDone }: { topicId: string; editing: Editing | null; onClose: () => void; onDone: () => Promise<void> }) {
  const { t } = useTranslation()
  const [title, setTitle] = useState('')
  const [summary, setSummary] = useState('')
  const [deprecate, setDeprecate] = useState(false)
  const [shown, setShown] = useState<Editing | null>(null)
  if (editing !== shown) {
    setShown(editing)
    setTitle(editing?.kind === 'rename' ? editing.node.title : '')
    setSummary(editing?.kind === 'rename' ? (editing.node.summary ?? '') : '')
    setDeprecate(false)
  }
  const save = useMutation({
    mutationFn: async () => {
      if (!editing) return
      if (editing.kind === 'rename') await api.updateNode(editing.node.id, { title: title.trim(), summary: summary.trim() || null })
      else if (editing.kind === 'add')
        await api.addNode(topicId, { title: title.trim(), summary: summary.trim() || null, parent_id: editing.parentId, position: editing.position, origin: editing.origin })
      else await api.deleteNode(editing.node.id, deprecate)
    },
    onSuccess: async () => {
      await onDone()
      onClose()
    },
  })
  const heading =
    editing?.kind === 'rename' ? t('outline.rename') : editing?.kind === 'delete' ? t('outline.deleteNode') : editing?.origin === 'prerequisite' ? t('outline.addPrerequisite') : t('outline.addNode')
  return (
    <Dialog open={editing !== null} onOpenChange={(o) => {
        if (!o) onClose()
      }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{heading}</DialogTitle>
          <DialogDescription className={editing?.kind === 'delete' ? '' : 'sr-only'}>
            {editing?.kind === 'delete' ? t('outline.deleteConfirm', { title: editing.node.title }) : heading}
          </DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault()
            save.mutate()
          }}
        >
          {editing?.kind === 'delete' ? (
            <label className="flex items-center gap-2 text-sm">
              <Checkbox checked={deprecate} onCheckedChange={(v) => { setDeprecate(v === true); }} />
              {t('outline.deprecateEntries')}
            </label>
          ) : (
            <>
              <Label htmlFor="node-title">{t('outline.newTitle')}</Label>
              <Input id="node-title" autoFocus required value={title} onChange={(e) => { setTitle(e.target.value); }} maxLength={200} />
              <Label htmlFor="node-summary">{t('outline.summary')}</Label>
              <Input id="node-summary" value={summary} onChange={(e) => { setSummary(e.target.value); }} />
            </>
          )}
          {save.error && <QueryError error={save.error} />}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              {t('app.cancel')}
            </Button>
            <Button type="submit" variant={editing?.kind === 'delete' ? 'destructive' : 'default'} disabled={save.isPending || (editing?.kind !== 'delete' && !title.trim())}>
              {editing?.kind === 'delete' ? t('app.delete') : t('app.save')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
