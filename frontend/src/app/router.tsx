import { createBrowserRouter } from 'react-router'
import { NotFound } from '@/app/NotFound'
import { Shell } from '@/app/Shell'
import { TopicsPage } from '@/features/topics/TopicsPage'

// The topic list is in the entry chunk; every other page loads on first visit.
const page = <T,>(load: () => Promise<T>, pick: (m: T) => React.ComponentType) => async () => ({ Component: pick(await load()) })

export const routes = [
  {
    path: '/',
    element: <Shell />,
    children: [
      { index: true, element: <TopicsPage /> },
      {
        path: 'topics/:topicId',
        lazy: page(() => import('@/features/topic/TopicLayout'), (m) => m.TopicLayout),
        children: [
          { index: true, lazy: page(() => import('@/features/topic/TopicHome'), (m) => m.TopicHome) },
          { path: 'nodes/:nodeId', lazy: page(() => import('@/features/node/NodePage'), (m) => m.NodePage) },
          { path: 'nodes/:nodeId/branches/:branchId', lazy: page(() => import('@/features/branch/BranchPage'), (m) => m.BranchPage) },
          { path: 'settings', lazy: page(() => import('@/features/topics/TopicSettings'), (m) => m.TopicSettings) },
        ],
      },
      { path: 'memory', lazy: page(() => import('@/features/memory/MemoryPage'), (m) => m.MemoryPage) },
      { path: 'usage', lazy: page(() => import('@/features/usage/UsagePage'), (m) => m.UsagePage) },
      { path: '*', element: <NotFound /> },
    ],
  },
]

export const router = createBrowserRouter(routes)
