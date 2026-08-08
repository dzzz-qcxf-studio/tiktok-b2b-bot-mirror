interface UnauthorizedResponse {
  status?: number
  requestUrl?: string
  requestToken?: string
}

type Navigate = (url: string) => void

const CREDENTIAL_ENDPOINTS = new Set([
  '/api/auth/login',
  '/api/auth/register',
])

export function handleUnauthorizedResponse(
  response: UnauthorizedResponse,
  currentPath: string,
  navigate: Navigate,
): boolean {
  const requestPath = (response.requestUrl || '').split('?', 1)[0] ?? ''
  if (response.status !== 401 || CREDENTIAL_ENDPOINTS.has(requestPath)) {
    return false
  }

  const currentToken = localStorage.getItem('token') || ''
  if ((response.requestToken || '') !== currentToken) return false

  localStorage.removeItem('token')
  localStorage.removeItem('username')
  if (!currentPath.startsWith('/login')) {
    const returnTo = currentPath.startsWith('/') ? currentPath : '/dashboard'
    navigate(`/login?redirect=${encodeURIComponent(returnTo)}`)
  }
  return true
}

export function resolvePostLoginTarget(value: unknown): string {
  if (
    typeof value === 'string'
    && value.startsWith('/')
    && !value.startsWith('//')
    && !value.startsWith('/login')
  ) {
    return value
  }
  return '/dashboard'
}
