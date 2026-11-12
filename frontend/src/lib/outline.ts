// The outline as a tree (design §4: at most three levels), numbered 1 / 1.2 / 1.2.3 in position
// order (the swiss-grid numbering of the visual direction).
import type { NodeOut } from '@/api/generated'

/** A node's explanation while a generation run makes it in the background. */
export type ExplanationState = 'generating' | 'failed'

export interface TreeNode {
  node: NodeOut
  number: string
  children: TreeNode[]
}

export const MAX_DEPTH = 3

const byPosition = (a: NodeOut, b: NodeOut) => a.position - b.position

export function buildTree(nodes: NodeOut[]): TreeNode[] {
  const children = new Map<string | null, NodeOut[]>()
  for (const n of nodes) {
    const list = children.get(n.parent_id) ?? []
    list.push(n)
    children.set(n.parent_id, list)
  }
  const build = (parent: string | null, prefix: string): TreeNode[] =>
    (children.get(parent) ?? []).sort(byPosition).map((node, i) => {
      const number = prefix ? `${prefix}.${i + 1}` : String(i + 1)
      return { node, number, children: build(node.id, number) }
    })
  return build(null, '')
}

export function flatten(tree: TreeNode[]): TreeNode[] {
  return tree.flatMap((t) => [t, ...flatten(t.children)])
}

export function siblingsOf(nodes: NodeOut[], id: string): NodeOut[] {
  const parent = nodes.find((n) => n.id === id)?.parent_id ?? null
  return nodes.filter((n) => n.parent_id === parent).sort(byPosition)
}

/** The outline in reading order, each node with its number. */
export const readingOrder = (nodes: NodeOut[]): TreeNode[] => flatten(buildTree(nodes))
