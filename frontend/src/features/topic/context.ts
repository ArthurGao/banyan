import { useOutletContext } from 'react-router'
import type { RunOut, TopicDetail } from '@/api/generated'
import type { TreeNode } from '@/lib/outline'

export interface TopicContext {
  detail: TopicDetail & { order: TreeNode[] }
  run: RunOut | undefined
}
export const useTopicContext = () => useOutletContext<TopicContext>()
