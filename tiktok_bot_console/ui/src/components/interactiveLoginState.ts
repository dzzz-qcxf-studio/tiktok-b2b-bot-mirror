import type { LoginSessionResponse } from '../api'

type SessionSnapshot = Pick<LoginSessionResponse, 'token' | 'status'>

const TERMINAL_LOGIN_STATUSES = new Set<LoginSessionResponse['status']>([
  'confirmed',
  'failed',
  'expired',
  'cancelled',
])

export function isTerminalLoginStatus(status: LoginSessionResponse['status']) {
  return TERMINAL_LOGIN_STATUSES.has(status)
}

export function canApplyLoginSnapshot(
  current: SessionSnapshot | null,
  incoming: SessionSnapshot,
) {
  if (!current) return true
  if (current.token !== incoming.token) return false
  return !isTerminalLoginStatus(current.status)
}
