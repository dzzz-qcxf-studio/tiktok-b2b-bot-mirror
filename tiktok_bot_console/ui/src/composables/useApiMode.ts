import { ref, watch, readonly } from 'vue'

/**
 * Runtime override for the build-time VITE_USE_MOCK env var.
 * Persisted in localStorage so the choice survives page reloads.
 *
 * Modes:
 *  - 'auto'  → follow VITE_USE_MOCK (default; whatever the .env says)
 *  - 'mock'  → force mock, even if .env says real
 *  - 'real'  → force real backend, falling back to mock only on network failure
 */
export type ApiMode = 'auto' | 'mock' | 'real'
const STORAGE_KEY = 'tiktok-bot:api-mode'
const ENV_MOCK = import.meta.env.VITE_USE_MOCK === 'true'

const initial = (() => {
  if (typeof localStorage === 'undefined') return 'auto' as ApiMode
  const stored = localStorage.getItem(STORAGE_KEY)
  if (stored === 'auto' || stored === 'mock' || stored === 'real') return stored
  return 'auto' as ApiMode
})()

const _mode = ref<ApiMode>(initial)
watch(_mode, (m) => {
  if (typeof localStorage !== 'undefined') localStorage.setItem(STORAGE_KEY, m)
})

/** Effective boolean — what the API layer should treat as mock right now. */
export function isMockNow(): boolean {
  if (_mode.value === 'mock') return true
  if (_mode.value === 'real') return false
  return ENV_MOCK
}

export const apiMode = readonly(_mode)
export function setApiMode(m: ApiMode) { _mode.value = m }
