import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, test, vi } from 'vitest'
import { getToken, setToken } from '@/api/http'
import { TokenGate } from '@/features/auth/TokenGate'

afterEach(() => vi.restoreAllMocks())

const renderGate = () =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <TokenGate>APP</TokenGate>
    </QueryClientProvider>,
  )

test('asks for the token, checks it against the API and keeps it', async () => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('[]', { status: 200 }))
  renderGate()
  expect(screen.queryByText('APP')).toBeNull()
  await userEvent.type(screen.getByLabelText('API token'), 'secret')
  await userEvent.click(screen.getByRole('button', { name: 'Continue' }))
  expect(await screen.findByText('APP')).toBeInTheDocument()
  expect(getToken()).toBe('secret')
  expect(new Headers(fetch.mock.calls[0]![1]!.headers).get('Authorization')).toBe('Bearer secret')
})

test('a refused token stays out and says so', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{"title":"unauthorized"}', { status: 401 }))
  renderGate()
  await userEvent.type(screen.getByLabelText('API token'), 'wrong')
  await userEvent.click(screen.getByRole('button', { name: 'Continue' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('That token was refused')
  expect(screen.queryByText('APP')).toBeNull()
  expect(getToken()).toBeNull()
})

test('a 401 anywhere later brings the form back', async () => {
  setToken('old')
  renderGate()
  expect(screen.getByText('APP')).toBeInTheDocument()
  setToken(null)
  expect(await screen.findByLabelText('API token')).toBeInTheDocument()
})
