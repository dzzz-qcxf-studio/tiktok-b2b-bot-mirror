import axios from 'axios'
import mockApi from './mock'
import { isMockNow, apiMode } from '../composables/useApiMode'

/** Runtime-aware: re-evaluates on every call so the Settings toggle takes effect
 * without a page reload. */
const isMock = isMockNow
const ENV_MOCK = import.meta.env.VITE_USE_MOCK === 'true'

/** Raw axios instance — auth.ts / legacy callers use this directly. */
const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || 'http://localhost:8000',
  timeout: 30000,
})

const t = localStorage.getItem('token')
if (t && !ENV_MOCK && !isMock()) api.defaults.headers.common['Authorization'] = `Bearer ${t}`

import { reactive } from 'vue'

/** True when the app is currently serving data via the withMockFallback path
 * (i.e. real backend was requested but failed). Distinct from USE_MOCK which
 * means the app was *intentionally* started in mock mode. */
export const fallbackState = reactive({ active: false, hits: 0 })

/** Wrap a real call so a backend failure (dev only) falls back to mock data.
 *  - auto/mock mode: try real, fall back to mock on failure (graceful)
 *  - real mode (user explicitly chose real): NO fallback — surface hard error
 *    so the user knows the backend is down.
 */
function withMockFallback<T>(real: () => Promise<{ data: T }>, fallback: () => Promise<{ data: T }>) {
  if (isMock()) return fallback
  if (apiMode.value === 'real') return real   // explicit real → fail loud
  return async () => {
    try { return await real() }
    catch (e) {
      fallbackState.active = true
      fallbackState.hits += 1
      if (import.meta.env.DEV) console.warn('[api] backend unreachable, falling back to mock:', (e as any)?.message)
      return await fallback()
    }
  }
}

/** Pick mock or real at call time — supports runtime mode toggle. */
function pickMode(mock: (...a: any[]) => any, real: (...a: any[]) => any) {
  return (...args: any[]) => (isMock() ? mock(...args) : real(...args))
}

// ---------- Real API endpoints (call backend) ----------
const realApi = {
  login: (username: string, password: string) => api.post('/api/auth/login', { username, password }),
  register: (username: string, password: string, invite_code = '') => api.post('/api/auth/register', { username, password, invite_code }),
  me: () => api.get('/api/auth/me'),

  getUsers: (params: any) => api.get('/api/users', { params }),
  getUserStats: () => api.get('/api/users/stats'),

  getDashboard: () => api.get('/api/stats/dashboard'),
  getPipelineEvents: (limit = 60) => api.get('/api/pipeline/events', { params: { limit } }),
  getPipelineOverview: () => api.get('/api/pipeline/overview'),
  getUserDetail: (username: string) => api.get(`/api/users/${encodeURIComponent(username)}/detail`),
  runPipeline: (stages: string[], opts: any = {}) => api.post('/api/pipeline/run', { stages, ...opts }),
  streamPipelineEvents: () => api.get('/api/pipeline/events/stream'),

  getDailyReport: (d?: string) => api.get('/api/reports/daily', { params: d ? { d } : {} }),
  getTrendReport: (days = 30) => api.get('/api/reports/trend', { params: { days } }),
  getWordcloud: (lang: 'en' | 'cn' = 'en', limit?: number) => api.get('/api/stats/wordcloud', { params: { lang, ...(typeof limit === 'number' ? { limit } : {}) } }),

  getConfig: () => api.get('/api/config'),
  getReportsOverview: () => api.get('/api/reports/overview'),
  getLlmProviders: () => api.get('/api/llm/providers'),
  setConfigKey: (key: string, value: string) => api.put(`/api/config/${key}`, { value }),
  saveApiKey: (api_key: string) => api.post('/api/config/apikey', { api_key }),

  addUser: (data: { username: string; platform?: string; bio?: string; follower_count?: number; country?: string; category?: string }) => api.post('/api/users', data),
  getAccounts: (platform?: string) => api.get('/api/accounts', { params: platform ? { platform } : {} }),
  addAccount: (platform: string, username: string) => api.post('/api/accounts', { platform, username }),
  deleteAccount: (id: number) => api.delete(`/api/accounts/${id}`),
  updateAccountCookies: (id: number, cookies_json: string) => api.put(`/api/accounts/${id}/cookies`, { cookies_json }),
  startQrcodeLogin: (platform: string, username: string) => api.post('/api/accounts/login-qrcode', { platform, username }),
  getLoginStatus: (token: string) => api.get('/api/accounts/login-status', { params: { token } }),
  getQrcodeUrl: (token: string) => `/api/accounts/qrcode/${token}`,
  checkAccountSession: (id: number) => api.post(`/api/accounts/${id}/check-session`),

  // Lead discovery — public TikTok web search (no login required, rate-limited)
  searchLeads: (keyword: string, limit = 20) => api.get('/api/leads/search', { params: { keyword, limit } }),
}

// ---------- Exported wrapped API (used by views) ----------
const wrapped = {
  // Runtime flag (read-only ref) — see useApiMode.ts
  USE_MOCK: ENV_MOCK,

  // Auth — pickMode re-evaluates on every call so Settings toggle takes effect live
  login: pickMode(mockApi.login, realApi.login),
  register: pickMode(mockApi.register, realApi.register),
  me: pickMode(mockApi.me, realApi.me),

  // Reads: real with mock fallback (dev) / direct mock (mock mode)
  getUsers: pickMode(mockApi.getUsers, withMockFallback(() => realApi.getUsers({}), () => mockApi.getUsers({}))),
  getUserStats: pickMode(mockApi.getUserStats, withMockFallback(realApi.getUserStats, mockApi.getUserStats)),
  getUserDetail: (username: string) => pickMode(
    () => mockApi.getUserDetail(username),
    withMockFallback(() => realApi.getUserDetail(username), () => mockApi.getUserDetail(username))
  ),
  getDashboard: pickMode(mockApi.getDashboard, withMockFallback(realApi.getDashboard, mockApi.getDashboard)),
  getPipelineEvents: pickMode(mockApi.getPipelineEvents, withMockFallback(() => realApi.getPipelineEvents(60), () => mockApi.getPipelineEvents(60))),
  getPipelineOverview: pickMode(mockApi.getPipelineOverview, withMockFallback(realApi.getPipelineOverview, mockApi.getPipelineOverview)),
  getDailyReport: pickMode(mockApi.getDailyReport, withMockFallback(realApi.getDailyReport, mockApi.getDailyReport)),
  getTrendReport: pickMode(mockApi.getTrendReport, withMockFallback(() => realApi.getTrendReport(30), () => mockApi.getTrendReport(30))),
  getWordcloud: pickMode(mockApi.getWordcloud, withMockFallback(realApi.getWordcloud, mockApi.getWordcloud)),
  getConfig: pickMode(mockApi.getConfig, withMockFallback(realApi.getConfig, mockApi.getConfig)),
  getReportsOverview: pickMode(mockApi.getReportsOverview, withMockFallback(realApi.getReportsOverview, mockApi.getReportsOverview)),
  getLlmProviders: pickMode(mockApi.getLlmProviders, withMockFallback(realApi.getLlmProviders, mockApi.getLlmProviders)),
  getAccounts: pickMode(mockApi.getAccounts, withMockFallback(realApi.getAccounts, mockApi.getAccounts)),

  // Writes: always real (mock would echo — dev only) — but still go through pickMode
  // so a "real" mode toggle in Settings actually hits the backend.
  runPipeline: pickMode(mockApi.runPipeline, withMockFallback(realApi.runPipeline as any, mockApi.runPipeline as any)),
  setConfigKey: pickMode(mockApi.setConfigKey, realApi.setConfigKey),
  saveApiKey: pickMode(mockApi.saveApiKey, realApi.saveApiKey),
  addAccount: pickMode(mockApi.addAccount, realApi.addAccount),
  addUser: pickMode(mockApi.addUser, realApi.addUser),
  deleteAccount: pickMode(mockApi.deleteAccount, realApi.deleteAccount),
  updateAccountCookies: pickMode(mockApi.updateAccountCookies, realApi.updateAccountCookies),
  startQrcodeLogin: pickMode(mockApi.startQrcodeLogin, realApi.startQrcodeLogin),
  getLoginStatus: pickMode(mockApi.getLoginStatus, realApi.getLoginStatus),
  getQrcodeUrl: realApi.getQrcodeUrl,
  checkAccountSession: pickMode(mockApi.checkAccountSession, realApi.checkAccountSession),
  streamPipelineEvents: realApi.streamPipelineEvents,
  searchLeads: pickMode(mockApi.searchLeads, withMockFallback(() => realApi.searchLeads(''), () => mockApi.searchLeads(''))),
}

// Default = raw axios (legacy compat for auth.ts + any deep usage)
// Named = wrapped API (used by views)
export default api
export const USE_MOCK_FLAG = ENV_MOCK

// Re-export each function as named export, so views can `import { getUsers } from '../api'`
export const login = wrapped.login
export const register = wrapped.register
export const me = wrapped.me
export const getUsers = wrapped.getUsers
export const getUserStats = wrapped.getUserStats
export const getUserDetail = wrapped.getUserDetail
export const getDashboard = wrapped.getDashboard
export const getPipelineEvents = wrapped.getPipelineEvents
export const getPipelineOverview = wrapped.getPipelineOverview
export const runPipeline = wrapped.runPipeline
export const streamPipelineEvents = wrapped.streamPipelineEvents
export const getDailyReport = wrapped.getDailyReport
export const getTrendReport = wrapped.getTrendReport
export const getWordcloud = wrapped.getWordcloud
export const getConfig = wrapped.getConfig
export const getReportsOverview = wrapped.getReportsOverview
export const getLlmProviders = wrapped.getLlmProviders
export const setConfigKey = wrapped.setConfigKey
export const saveApiKey = wrapped.saveApiKey
export const getAccounts = wrapped.getAccounts
export const addAccount = wrapped.addAccount
export const addUser = wrapped.addUser
export const deleteAccount = wrapped.deleteAccount
export const updateAccountCookies = wrapped.updateAccountCookies
export const startQrcodeLogin = wrapped.startQrcodeLogin
export const getLoginStatus = wrapped.getLoginStatus
export const getQrcodeUrl = wrapped.getQrcodeUrl
export const checkAccountSession = wrapped.checkAccountSession
export const searchLeads = wrapped.searchLeads