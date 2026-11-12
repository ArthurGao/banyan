import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, TOKEN_CLEARED, getToken, request, setToken } from '@/api/http'

const reply = (status: number, body: unknown) =>
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }))

afterEach(() => {
  vi.restoreAllMocks()
  localStorage.clear()
})

describe('request', () => {
  it('sends the stored token and returns the JSON body', async () => {
    setToken('t0k')
    const fetch = reply(200, { id: 'x' })
    await expect(request('GET', '/v1/topics/x')).resolves.toEqual({ id: 'x' })
    const init = fetch.mock.calls[0]![1]!
    expect(new Headers(init.headers).get('Authorization')).toBe('Bearer t0k')
  })

  it('maps an RFC 7807 problem to ApiError', async () => {
    reply(409, { title: 'answer_streaming', detail: 'an answer is already streaming' })
    const err = await request('POST', '/v1/branches/b/ask', {}).catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err).toMatchObject({ status: 409, title: 'answer_streaming', message: 'an answer is already streaming' })
  })

  it('drops the token on 401 and announces it', async () => {
    setToken('stale')
    const cleared = vi.fn()
    window.addEventListener(TOKEN_CLEARED, cleared)
    reply(401, { title: 'unauthorized' })
    await expect(request('GET', '/v1/topics')).rejects.toMatchObject({ status: 401 })
    expect(getToken()).toBeNull()
    expect(cleared).toHaveBeenCalledOnce()
    window.removeEventListener(TOKEN_CLEARED, cleared)
  })

  it('keeps the token on 403', async () => {
    setToken('keep')
    reply(403, { title: 'forbidden' })
    await expect(request('GET', '/v1/topics')).rejects.toMatchObject({ status: 403 })
    expect(getToken()).toBe('keep')
  })
})
