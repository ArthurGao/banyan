import { describe, expect, it } from 'vitest'
import type { NodeOut } from '@/api/generated'
import { readingOrder, siblingsOf } from '@/lib/outline'

const node = (id: string, parent: string | null, position: number, depth: number): NodeOut => ({
  id, parent_id: parent, position, depth, title: id, summary: null, status: 'not_started', origin: 'outline',
})

// Stored out of order on purpose: the tree is ordered by `position`, not by array order.
const NODES = [
  node('b', null, 1, 0),
  node('b2', 'b', 1, 1),
  node('a', null, 0, 0),
  node('b1', 'b', 0, 1),
  node('b1x', 'b1', 0, 2),
]

describe('outline', () => {
  it('numbers nodes 1 / 1.2 / 1.2.3 in position order', () => {
    expect(readingOrder(NODES).map((n) => [n.node.id, n.number])).toEqual([
      ['a', '1'],
      ['b', '2'],
      ['b1', '2.1'],
      ['b1x', '2.1.1'],
      ['b2', '2.2'],
    ])
  })

  it('finds siblings ordered by position', () => {
    expect(siblingsOf(NODES, 'b2').map((n) => n.id)).toEqual(['b1', 'b2'])
    expect(siblingsOf(NODES, 'a').map((n) => n.id)).toEqual(['a', 'b'])
  })
})
