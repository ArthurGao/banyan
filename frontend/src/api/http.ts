// The one place requests are made. Same origin in production (the API serves the build, design §11);
// the dev server proxies /v1 and /health. Errors are RFC 7807 problems (design §9).

const TOKEN_KEY = 'banyan.token'
export const TOKEN_CLEARED = 'banyan:token-cleared'

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    // blocked storage: the token lasts for this page only
  }
  if (!token) window.dispatchEvent(new Event(TOKEN_CLEARED))
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly title: string,
    readonly detail: string | null,
  ) {
    super(detail ?? title)
    this.name = 'ApiError'
  }
}

async function problem(response: Response): Promise<ApiError> {
  let title = response.statusText || `HTTP ${response.status}`
  let detail: string | null = null
  try {
    const body = (await response.json()) as { title?: unknown; detail?: unknown }
    if (typeof body.title === 'string') title = body.title
    if (typeof body.detail === 'string') detail = body.detail
    else if (Array.isArray(body.detail)) detail = JSON.stringify(body.detail)
  } catch {
    // not JSON: keep the status text
  }
  // 401/403 are never retried: the token is wrong, so it is dropped and asked for again.
  if (response.status === 401) setToken(null)
  return new ApiError(response.status, title, detail)
}

function headers(json: boolean, accept = 'application/json'): Headers {
  const h = new Headers({ Accept: accept })
  if (json) h.set('Content-Type', 'application/json')
  const token = getToken()
  if (token) h.set('Authorization', `Bearer ${token}`)
  return h
}

export async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, headers: headers(body !== undefined) }
  if (body !== undefined) init.body = JSON.stringify(body)
  const response = await fetch(path, init)
  if (!response.ok) throw await problem(response)
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

/** Fetch a text body (the export HTML). */
export async function requestText(path: string): Promise<string> {
  const response = await fetch(path, { headers: headers(false, 'text/html') })
  if (!response.ok) throw await problem(response)
  return response.text()
}

export async function openStream(path: string, body: unknown, signal: AbortSignal): Promise<Response> {
  const init: RequestInit = { method: 'POST', headers: headers(body !== undefined, 'text/event-stream'), signal }
  if (body !== undefined) init.body = JSON.stringify(body)
  const response = await fetch(path, init)
  if (!response.ok) throw await problem(response)
  return response
}
