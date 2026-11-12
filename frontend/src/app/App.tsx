import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider } from 'react-router'
import { router } from '@/app/router'
import { Toaster } from '@/components/ui/sonner'
import { TokenGate } from '@/features/auth/TokenGate'
import { ApiError } from '@/api/http'
import { useTheme } from '@/lib/theme'

// The toaster follows the manual theme choice (lib/theme.ts).
function ThemedToaster() {
  const { choice } = useTheme()
  return <Toaster position="bottom-center" theme={choice} />
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // 4xx are answers, not outages: never retried (401/403 in particular, web-authz §5).
      retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
      staleTime: 5_000,
    },
  },
})

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <TokenGate>
        <RouterProvider router={router} />
      </TokenGate>
      <ThemedToaster />
    </QueryClientProvider>
  )
}
