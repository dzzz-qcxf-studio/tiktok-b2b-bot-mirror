import axios from 'axios'
import mockApi from './mock'
import { isMockNow, apiMode } from '../composables/useApiMode'
import router from '../router'
import { handleUnauthorizedResponse } from './authSession'
import type {
  AcquisitionCampaignPayload,
  AcquisitionCampaignResponse,
  AcquisitionCandidateDetailResponse,
  AcquisitionCandidateListParams,
  AcquisitionCandidateListResponse,
  AcquisitionKeywordCreatePayload,
  AcquisitionKeywordListResponse,
  AcquisitionKeywordResponse,
  AcquisitionKeywordStatsPayload,
  AcquisitionPageParams,
  AcquisitionStage01Response,
  AcquisitionStage02Response,
  CandidateLabelsPayload,
  CandidateResponse,
  CandidateReviewAuditListResponse,
  CandidateReviewPayload,
  CompletePipelineReviewCheckpointPayload,
  CreateAcquisitionJobPayload,
  CreateAcquisitionJobResponse,
  CreatePipelineJobPayload,
  PipelineCapabilities,
  PipelineJobListParams,
  PipelineJobListResponse,
  PipelineJobResponse,
  PipelineLiveEvent,
  PipelineLiveEventListParams,
  PipelineLiveEventListResponse,
  PipelineLiveResponse,
  PipelineLiveSubscription,
  PipelineLiveSubscriptionOptions,
  PipelineLiveTransport,
  PipelineActiveCheckpointResponse,
  ResolvePipelineCheckpointPayload,
  ResolvePipelineCheckpointResponse,
  PipelineScheduleListResponse,
  PipelineSchedulePayload,
  PipelineScheduleResponse,
  PipelineStageName,
  PipelineRuntimeConfigPayload,
  PipelineRuntimeConfigResponse,
} from '../types/pipeline'

export interface LlmProvider {
  id: string
  name: string
  displayName: string
  protocol: 'openai_chat'
  baseUrl: string
  defaultModel: string
  apiKeyEnv: string
  enabled: boolean
  timeoutSeconds: number
  configured: boolean
  createdAt: string
  updatedAt: string
}

export interface LlmProviderCreatePayload {
  name: string
  displayName: string
  protocol?: 'openai_chat'
  baseUrl: string
  defaultModel: string
  apiKeyEnv: string
  enabled?: boolean
  timeoutSeconds?: number
}

export type LlmProviderUpdatePayload = Partial<LlmProviderCreatePayload>

export interface LlmRouteEntry {
  providerId: string
  priority: number
  modelOverride?: string | null
  enabled?: boolean
}

export interface LlmRoute {
  routeKey: string
  providers: LlmRouteEntry[]
}

export interface LlmUsage {
  requestCount: number
  successCount: number
  failureCount: number
  inputTokens: number
  outputTokens: number
  totalTokens: number
  fallbackCount: number
  averageLatencyMs: number
}

export interface LlmConnectionTest {
  reachable: boolean
  latencyMs: number
  errorCategory?: string
}

export type LoginPlatform = 'tiktok' | 'douyin'

export interface CreateLoginSessionPayload {
  platform: LoginPlatform
  accountAlias: string
  accountId?: number | null
}

export interface LoginSessionResponse {
  token: string
  platform: LoginPlatform
  accountAlias: string
  accountId: number | null
  status:
    | 'launching'
    | 'waiting_user'
    | 'verifying'
    | 'persisted'
    | 'confirmed'
    | 'failed'
    | 'expired'
    | 'cancelled'
  browserOpened: boolean
  browserProvider: string
  authenticated: boolean
  persisted: boolean
  startedAt: string
  expiresAt: string
  errorCode: string
  errorMessage: string
}

/** Runtime-aware: re-evaluates on every call so the Settings toggle takes effect
 * without a page reload. */
const isMock = isMockNow
const ENV_MOCK = import.meta.env.VITE_USE_MOCK === 'true'
const API_BASE_URL = import.meta.env.VITE_API_BASE || 'http://localhost:8000'

/** Raw axios instance — auth.ts / legacy callers use this directly. */
const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,
})

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  else delete config.headers.Authorization
  return config
})

api.interceptors.response.use(
  response => response,
  (error) => {
    const requestHeaders = error?.config?.headers
    const authorization = typeof requestHeaders?.get === 'function'
      ? requestHeaders.get('Authorization')
      : requestHeaders?.Authorization || requestHeaders?.authorization
    const requestToken = typeof authorization === 'string'
      ? authorization.replace(/^Bearer\s+/i, '')
      : ''
    handleUnauthorizedResponse(
      {
        status: error?.response?.status,
        requestUrl: error?.config?.url,
        requestToken,
      },
      router.currentRoute.value.fullPath,
      (url) => { void router.replace(url) },
    )
    return Promise.reject(error)
  },
)

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
  if (apiMode.value === 'real' || !import.meta.env.DEV) return real
  return async () => {
    try {
      const response = await real()
      fallbackState.active = false
      return response
    } catch (e) {
      fallbackState.active = true
      fallbackState.hits += 1
      console.warn('[api] backend unreachable, falling back to mock:', (e as Error)?.message)
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
  getPipelineOverview: () => api.get('/api/pipeline/overview'),
  getUserDetail: (username: string) => api.get(`/api/users/${encodeURIComponent(username)}/detail`),
  createPipelineJob: (payload: CreatePipelineJobPayload) =>
    api.post<PipelineJobResponse>('/api/pipeline/jobs', payload),
  createAcquisitionJob: (payload: CreateAcquisitionJobPayload) =>
    api.post<CreateAcquisitionJobResponse>('/api/acquisition/jobs', payload),
  listPipelineJobs: (params: PipelineJobListParams = {}) =>
    api.get<PipelineJobListResponse>('/api/pipeline/jobs', { params }),
  getPipelineJob: (jobId: string) =>
    api.get<PipelineJobResponse>(`/api/pipeline/jobs/${encodeURIComponent(jobId)}`),
  getPipelineLive: (jobId: string) =>
    api.get<PipelineLiveResponse>(
      `/api/pipeline/jobs/${encodeURIComponent(jobId)}/live`,
    ),
  getPipelineLiveEvents: (
    jobId: string,
    params: PipelineLiveEventListParams = {},
  ) => api.get<PipelineLiveEventListResponse>(
    `/api/pipeline/jobs/${encodeURIComponent(jobId)}/events`,
    { params },
  ),
  getActivePipelineCheckpoint: (jobId: string) =>
    api.get<PipelineActiveCheckpointResponse>(
      `/api/pipeline/jobs/${encodeURIComponent(jobId)}/checkpoints/active`,
    ),
  resolvePipelineCheckpoint: (
    jobId: string,
    checkpointId: string,
    payload: ResolvePipelineCheckpointPayload,
  ) => api.post<ResolvePipelineCheckpointResponse>(
    `/api/pipeline/jobs/${encodeURIComponent(jobId)}/checkpoints/${encodeURIComponent(checkpointId)}/resolve`,
    payload,
  ),
  completePipelineReviewCheckpoint: (
    jobId: string,
    checkpointId: string,
    payload: CompletePipelineReviewCheckpointPayload,
  ) => api.post<ResolvePipelineCheckpointResponse>(
    `/api/pipeline/jobs/${encodeURIComponent(jobId)}/checkpoints/${encodeURIComponent(checkpointId)}/review-complete`,
    payload,
  ),
  cancelPipelineJob: (jobId: string) =>
    api.post<PipelineJobResponse>(`/api/pipeline/jobs/${encodeURIComponent(jobId)}/cancel`),
  retryPipelineJob: (jobId: string) =>
    api.post<PipelineJobResponse>(`/api/pipeline/jobs/${encodeURIComponent(jobId)}/retry`),
  getPipelineCapabilities: () =>
    api.get<PipelineCapabilities>('/api/pipeline/capabilities'),
  createAcquisitionCampaign: (jobId: string, payload: AcquisitionCampaignPayload) =>
    api.post<AcquisitionCampaignResponse>(
      `/api/acquisition/jobs/${encodeURIComponent(jobId)}/campaign`,
      payload,
    ),
  getAcquisitionCampaign: (jobId: string) =>
    api.get<AcquisitionCampaignResponse>(
      `/api/acquisition/jobs/${encodeURIComponent(jobId)}/campaign`,
    ),
  createAcquisitionKeyword: (jobId: string, payload: AcquisitionKeywordCreatePayload) =>
    api.post<AcquisitionKeywordResponse>(
      `/api/acquisition/jobs/${encodeURIComponent(jobId)}/keywords`,
      payload,
    ),
  listAcquisitionKeywords: (
    jobId: string,
    params: AcquisitionPageParams = {},
  ) =>
    api.get<AcquisitionKeywordListResponse>(
      `/api/acquisition/jobs/${encodeURIComponent(jobId)}/keywords`,
      { params },
    ),
  updateAcquisitionKeyword: (
    jobId: string,
    keywordId: number,
    payload: AcquisitionKeywordStatsPayload,
  ) => api.patch<AcquisitionKeywordResponse>(
    `/api/acquisition/jobs/${encodeURIComponent(jobId)}/keywords/${keywordId}`,
    payload,
  ),
  deleteAcquisitionKeyword: (jobId: string, keywordId: number) =>
    api.delete<void>(
      `/api/acquisition/jobs/${encodeURIComponent(jobId)}/keywords/${keywordId}`,
    ),
  getAcquisitionStage01: (jobId: string) =>
    api.get<AcquisitionStage01Response>(
      `/api/acquisition/jobs/${encodeURIComponent(jobId)}/stage-01`,
    ),
  getAcquisitionStage02: (jobId: string) =>
    api.get<AcquisitionStage02Response>(
      `/api/acquisition/jobs/${encodeURIComponent(jobId)}/stage-02`,
    ),
  listAcquisitionCandidates: (
    jobId: string,
    params: AcquisitionCandidateListParams = {},
  ) => api.get<AcquisitionCandidateListResponse>(
    `/api/acquisition/jobs/${encodeURIComponent(jobId)}/candidates`,
    { params },
  ),
  getAcquisitionCandidate: (
    jobId: string,
    userId: number,
    params: AcquisitionPageParams = {},
  ) =>
    api.get<AcquisitionCandidateDetailResponse>(
      `/api/acquisition/jobs/${encodeURIComponent(jobId)}/candidates/${userId}`,
      { params },
    ),
  approveAcquisitionCandidate: (
    jobId: string,
    userId: number,
    payload: CandidateReviewPayload,
  ) => api.post<CandidateResponse>(
    `/api/acquisition/jobs/${encodeURIComponent(jobId)}/candidates/${userId}/approve`,
    payload,
  ),
  rejectAcquisitionCandidate: (
    jobId: string,
    userId: number,
    payload: CandidateReviewPayload,
  ) => api.post<CandidateResponse>(
    `/api/acquisition/jobs/${encodeURIComponent(jobId)}/candidates/${userId}/reject`,
    payload,
  ),
  requestAcquisitionCandidateEnrichment: (
    jobId: string,
    userId: number,
    payload: CandidateReviewPayload,
  ) => api.post<CandidateResponse>(
    `/api/acquisition/jobs/${encodeURIComponent(jobId)}/candidates/${userId}/request-enrichment`,
    payload,
  ),
  completeAcquisitionCandidateEnrichment: (
    jobId: string,
    userId: number,
    payload: CandidateReviewPayload,
  ) => api.post<CandidateResponse>(
    `/api/acquisition/jobs/${encodeURIComponent(jobId)}/candidates/${userId}/complete-enrichment`,
    payload,
  ),
  updateAcquisitionCandidateLabels: (
    jobId: string,
    userId: number,
    payload: CandidateLabelsPayload,
  ) => api.put<CandidateResponse>(
    `/api/acquisition/jobs/${encodeURIComponent(jobId)}/candidates/${userId}/labels`,
    payload,
  ),
  listAcquisitionCandidateAudits: (
    jobId: string,
    userId: number,
    params: AcquisitionPageParams = {},
  ) =>
    api.get<CandidateReviewAuditListResponse>(
      `/api/acquisition/jobs/${encodeURIComponent(jobId)}/candidates/${userId}/audits`,
      { params },
    ),
  createPipelineSchedule: (payload: PipelineSchedulePayload) =>
    api.post<PipelineScheduleResponse>('/api/pipeline/schedules', payload),
  listPipelineSchedules: (platform?: CreatePipelineJobPayload['platform']) =>
    api.get<PipelineScheduleListResponse>('/api/pipeline/schedules', {
      params: platform ? { platform } : {},
    }),
  updatePipelineSchedule: (scheduleId: number, payload: PipelineSchedulePayload) =>
    api.put<PipelineScheduleResponse>(
      `/api/pipeline/schedules/${scheduleId}`,
      payload,
    ),
  deletePipelineSchedule: (scheduleId: number) =>
    api.delete<void>(`/api/pipeline/schedules/${scheduleId}`),
  getDailyReport: (d?: string) => api.get('/api/reports/daily', { params: d ? { d } : {} }),
  getTrendReport: (days = 30) => api.get('/api/reports/trend', { params: { days } }),
  getWordcloud: (lang: 'en' | 'cn' = 'en', limit?: number) => api.get('/api/stats/wordcloud', { params: { lang, ...(typeof limit === 'number' ? { limit } : {}) } }),

  getConfig: () => api.get('/api/config'),
  updatePipelineConfig: (payload: PipelineRuntimeConfigPayload) =>
    api.put<PipelineRuntimeConfigResponse>('/api/config/pipeline', payload),
  getReportsOverview: () => api.get('/api/reports/overview'),
  getLlmProviders: () => api.get<LlmProvider[]>('/api/llm/providers'),
  createLlmProvider: (payload: LlmProviderCreatePayload) =>
    api.post<LlmProvider>('/api/llm/providers', payload),
  updateLlmProvider: (id: string, payload: LlmProviderUpdatePayload) =>
    api.put<LlmProvider>(`/api/llm/providers/${encodeURIComponent(id)}`, payload),
  deleteLlmProvider: (id: string) =>
    api.delete<void>(`/api/llm/providers/${encodeURIComponent(id)}`),
  testLlmProvider: (id: string) =>
    api.post<LlmConnectionTest>(
      `/api/llm/providers/${encodeURIComponent(id)}/test`,
    ),
  updateLlmProviderSecret: (id: string, apiKey: string) =>
    api.put<{ status: 'ok'; configured: boolean; envVar: string }>(
      `/api/llm/providers/${encodeURIComponent(id)}/secret`,
      { apiKey },
    ),
  getLlmRoutes: () => api.get<LlmRoute[]>('/api/llm/routes'),
  updateLlmRoute: (routeKey: string, providers: LlmRouteEntry[]) =>
    api.put<LlmRoute>(
      `/api/llm/routes/${encodeURIComponent(routeKey)}`,
      { providers },
    ),
  getLlmUsage: () => api.get<LlmUsage>('/api/llm/usage'),
  setConfigKey: (key: string, value: string) => api.put(`/api/config/${key}`, { value }),
  saveApiKey: (api_key: string) => api.post('/api/config/apikey', { api_key }),

  addUser: (data: { username: string; platform?: string; bio?: string; follower_count?: number; country?: string; category?: string; profile_url?: string }) => api.post('/api/users', data),
  getAccounts: (platform?: string) => api.get('/api/accounts', { params: platform ? { platform } : {} }),
  addAccount: (platform: string, username: string) => api.post('/api/accounts', { platform, username }),
  deleteAccount: (id: number) => api.delete(`/api/accounts/${id}`),
  updateAccountMetadata: (id: number, displayName: string) =>
    api.put(`/api/accounts/${id}`, { displayName }),
  updateAccountCookies: (id: number, cookies_json: string) => api.put(`/api/accounts/${id}/cookies`, { cookies_json }),
  createLoginSession: (payload: CreateLoginSessionPayload) =>
    api.post<LoginSessionResponse>('/api/accounts/login-sessions', payload),
  getLoginSession: (token: string) =>
    api.get<LoginSessionResponse>(
      `/api/accounts/login-sessions/${encodeURIComponent(token)}`,
    ),
  verifyLoginSession: (token: string) =>
    api.post<LoginSessionResponse>(
      `/api/accounts/login-sessions/${encodeURIComponent(token)}/verify`,
    ),
  cancelLoginSession: (token: string) =>
    api.post<LoginSessionResponse>(
      `/api/accounts/login-sessions/${encodeURIComponent(token)}/cancel`,
    ),
  /** @deprecated Use createLoginSession. */
  startQrcodeLogin: (platform: string, username: string) => api.post('/api/accounts/login-qrcode', { platform, username }),
  /** @deprecated Use getLoginSession. */
  getLoginStatus: (token: string) => api.get('/api/accounts/login-status', { params: { token } }),
  /** @deprecated QR images are no longer generated; the backend returns 410. */
  getQrcodeUrl: (token: string) => `/api/accounts/qrcode/${token}`,
  checkAccountSession: (id: number) => api.post(`/api/accounts/${id}/check-session`),

  // Lead discovery — public TikTok web search (no login required, rate-limited)
  searchLeads: (keyword: string, limit = 20) => api.get('/api/leads/search', { params: { keyword, limit } }),
}

const TERMINAL_PIPELINE_STATUSES = new Set([
  'cancelled',
  'succeeded',
  'partial_failed',
  'failed',
  'interrupted',
])

function pipelineLiveTransportError(
  code:
    | 'pipeline_live_stream_unavailable'
    | 'pipeline_live_history_unavailable'
    | 'pipeline_live_subscriber_error',
  message: string,
) {
  const error = new Error(message) as Error & { code: string }
  error.code = code
  return error
}

function isPipelineLiveEvent(
  value: unknown,
  expectedJobId: string,
): value is PipelineLiveEvent {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Partial<PipelineLiveEvent>
  return Number.isSafeInteger(candidate.sequence)
    && (candidate.sequence ?? 0) > 0
    && candidate.jobId === expectedJobId
    && typeof candidate.eventType === 'string'
    && typeof candidate.level === 'string'
    && !!candidate.payload
    && typeof candidate.payload === 'object'
}

function isTerminalPipelineEvent(event: PipelineLiveEvent) {
  return event.eventType === 'job.lifecycle'
    && typeof event.payload.status === 'string'
    && TERMINAL_PIPELINE_STATUSES.has(event.payload.status)
}

function buildPipelineLiveStreamUrl(jobId: string, afterSequence: number) {
  const base = API_BASE_URL.replace(/\/+$/, '')
  const path = `${base}/api/pipeline/jobs/${encodeURIComponent(jobId)}/events/stream`
  const origin = typeof window === 'undefined'
    ? 'http://localhost'
    : window.location.origin
  const url = new URL(path, origin)
  if (afterSequence > 0) {
    url.searchParams.set('afterSequence', String(afterSequence))
  }
  return url.toString()
}

/**
 * Subscribe to one Job's authoritative event stream.
 *
 * A returned subscription owns exactly one fetch reader and, after a stream
 * disconnect, one history-poll timer. Consumers must call abort when replacing
 * the selected Job or unmounting. Terminal lifecycle events close it
 * automatically.
 */
export function subscribePipelineLiveEvents(
  jobId: string,
  options: PipelineLiveSubscriptionOptions,
): PipelineLiveSubscription {
  const normalizedJobId = String(jobId)
  let lastSequence = Math.max(0, Math.trunc(options.afterSequence ?? 0))
  let stopped = false
  let reader: ReadableStreamDefaultReader<Uint8Array> | null = null
  let pollTimer: ReturnType<typeof setTimeout> | null = null
  let transport: PipelineLiveTransport | null = null
  const controller = new AbortController()

  const setTransport = (next: PipelineLiveTransport) => {
    if (transport === next) return
    transport = next
    try {
      options.onTransportChange?.(next)
    } catch {
      // Consumer callbacks must never control the transport state machine.
    }
  }

  const reportError = (
    code:
      | 'pipeline_live_stream_unavailable'
      | 'pipeline_live_history_unavailable'
      | 'pipeline_live_subscriber_error',
    message: string,
  ) => {
    try {
      options.onError?.(pipelineLiveTransportError(code, message))
    } catch {
      // Error observers are isolated for the same reason as event observers.
    }
  }

  const abort = () => {
    if (stopped) return
    stopped = true
    if (pollTimer !== null) {
      clearTimeout(pollTimer)
      pollTimer = null
    }
    controller.abort()
    if (reader) {
      void reader.cancel().catch(() => undefined)
    }
    setTransport('closed')
  }

  const acceptEvent = (value: unknown) => {
    if (stopped || !isPipelineLiveEvent(value, normalizedJobId)) return
    if (value.sequence <= lastSequence) return
    lastSequence = value.sequence
    const terminal = isTerminalPipelineEvent(value)
    try {
      options.onEvent(value)
    } catch {
      reportError(
        'pipeline_live_subscriber_error',
        '实时事件处理暂时不可用',
      )
    } finally {
      if (terminal) abort()
    }
  }

  const pollOnce = async () => {
    pollTimer = null
    if (stopped) return
    try {
      const response = await api.get<PipelineLiveEventListResponse>(
        `/api/pipeline/jobs/${encodeURIComponent(normalizedJobId)}/events`,
        {
          params: { afterSequence: lastSequence, limit: 100 },
          signal: controller.signal,
        },
      )
      for (const event of response.data.items) acceptEvent(event)
    } catch {
      if (!stopped) {
        reportError(
          'pipeline_live_history_unavailable',
          '实时历史暂时不可用，正在继续重试',
        )
      }
    }
    if (!stopped) pollTimer = setTimeout(pollOnce, 1_000)
  }

  const startPolling = () => {
    if (stopped || pollTimer !== null) return
    setTransport('polling')
    pollTimer = setTimeout(pollOnce, 1_000)
  }

  const handleSseFrame = (frame: string) => {
    const dataLines = frame
      .split(/\r?\n/)
      .filter(line => line.startsWith('data:'))
      .map(line => line.slice(5).replace(/^ /, ''))
    if (!dataLines.length) return
    try {
      acceptEvent(JSON.parse(dataLines.join('\n')))
    } catch {
      // The durable history endpoint remains authoritative for malformed or
      // partially delivered frames, so never surface raw server data here.
    }
  }

  const consumeStream = async (response: Response) => {
    if (!response.body) throw new Error('stream body unavailable')
    reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    try {
      while (!stopped) {
        const chunk = await reader.read()
        if (chunk.done) break
        buffer += decoder.decode(chunk.value, { stream: true })
        let boundary = buffer.match(/\r?\n\r?\n/)
        while (boundary?.index !== undefined) {
          const frame = buffer.slice(0, boundary.index)
          buffer = buffer.slice(boundary.index + boundary[0].length)
          handleSseFrame(frame)
          if (stopped) return
          boundary = buffer.match(/\r?\n\r?\n/)
        }
      }
    } finally {
      try {
        reader.releaseLock()
      } catch {
        // A concurrent abort may already have released the stream.
      }
      reader = null
    }
  }

  const openStream = async () => {
    setTransport('connecting')
    const token = localStorage.getItem('token') || ''
    const headers: Record<string, string> = { Accept: 'text/event-stream' }
    if (token) headers.Authorization = `Bearer ${token}`
    if (lastSequence > 0) headers['Last-Event-ID'] = String(lastSequence)
    const requestUrl = buildPipelineLiveStreamUrl(normalizedJobId, lastSequence)
    try {
      const response = await fetch(requestUrl, {
        method: 'GET',
        headers,
        signal: controller.signal,
      })
      if (!response.ok) {
        handleUnauthorizedResponse(
          { status: response.status, requestUrl, requestToken: token },
          router.currentRoute.value.fullPath,
          (url) => { void router.replace(url) },
        )
        throw new Error('stream request rejected')
      }
      if (stopped) {
        await response.body?.cancel().catch(() => undefined)
        return
      }
      setTransport('streaming')
      await consumeStream(response)
    } catch {
      if (!stopped) {
        reportError(
          'pipeline_live_stream_unavailable',
          '实时连接暂时不可用，已切换到历史同步',
        )
      }
    }
    startPolling()
  }

  void openStream()

  return {
    get lastSequence() {
      return lastSequence
    },
    abort,
  }
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
  getPipelineOverview: pickMode(mockApi.getPipelineOverview, withMockFallback(realApi.getPipelineOverview, mockApi.getPipelineOverview)),
  getDailyReport: pickMode(mockApi.getDailyReport, withMockFallback(realApi.getDailyReport, mockApi.getDailyReport)),
  getTrendReport: pickMode(mockApi.getTrendReport, withMockFallback(() => realApi.getTrendReport(30), () => mockApi.getTrendReport(30))),
  getWordcloud: pickMode(mockApi.getWordcloud, withMockFallback(realApi.getWordcloud, mockApi.getWordcloud)),
  getConfig: pickMode(mockApi.getConfig, withMockFallback(realApi.getConfig, mockApi.getConfig)),
  updatePipelineConfig: (payload: PipelineRuntimeConfigPayload) => pickMode(
    () => mockApi.updatePipelineConfig(payload),
    withMockFallback(
      () => realApi.updatePipelineConfig(payload),
      () => mockApi.updatePipelineConfig(payload),
    ),
  )(),
  getReportsOverview: pickMode(mockApi.getReportsOverview, withMockFallback(realApi.getReportsOverview, mockApi.getReportsOverview)),
  getLlmProviders: realApi.getLlmProviders,
  createLlmProvider: realApi.createLlmProvider,
  updateLlmProvider: realApi.updateLlmProvider,
  deleteLlmProvider: realApi.deleteLlmProvider,
  testLlmProvider: realApi.testLlmProvider,
  updateLlmProviderSecret: realApi.updateLlmProviderSecret,
  getLlmRoutes: realApi.getLlmRoutes,
  updateLlmRoute: realApi.updateLlmRoute,
  getLlmUsage: realApi.getLlmUsage,
  getAccounts: pickMode(mockApi.getAccounts, withMockFallback(realApi.getAccounts, mockApi.getAccounts)),

  // Writes: always real (mock would echo — dev only) — but still go through pickMode
  // so a "real" mode toggle in Settings actually hits the backend.
  createAcquisitionJob: (payload: CreateAcquisitionJobPayload) => pickMode(
    () => mockApi.createAcquisitionJob(payload),
    () => realApi.createAcquisitionJob(payload),
  )(),

  createPipelineJob: (payload: CreatePipelineJobPayload) => pickMode(
    () => mockApi.createPipelineJob(payload),
    withMockFallback(
      () => realApi.createPipelineJob(payload),
      () => mockApi.createPipelineJob(payload),
    ),
  )(),
  listPipelineJobs: (params: PipelineJobListParams = {}) => pickMode(
    () => mockApi.listPipelineJobs(params),
    withMockFallback(
      () => realApi.listPipelineJobs(params),
      () => mockApi.listPipelineJobs(params),
    ),
  )(),
  getPipelineJob: (jobId: string) => pickMode(
    () => mockApi.getPipelineJob(jobId),
    withMockFallback(
      () => realApi.getPipelineJob(jobId),
      () => mockApi.getPipelineJob(jobId),
    ),
  )(),
  getPipelineLive: realApi.getPipelineLive,
  getPipelineLiveEvents: realApi.getPipelineLiveEvents,
  getActivePipelineCheckpoint: realApi.getActivePipelineCheckpoint,
  resolvePipelineCheckpoint: realApi.resolvePipelineCheckpoint,
  completePipelineReviewCheckpoint: realApi.completePipelineReviewCheckpoint,
  cancelPipelineJob: (jobId: string) => pickMode(
    () => mockApi.cancelPipelineJob(jobId),
    withMockFallback(
      () => realApi.cancelPipelineJob(jobId),
      () => mockApi.cancelPipelineJob(jobId),
    ),
  )(),
  retryPipelineJob: (jobId: string) => pickMode(
    () => mockApi.retryPipelineJob(jobId),
    withMockFallback(
      () => realApi.retryPipelineJob(jobId),
      () => mockApi.retryPipelineJob(jobId),
    ),
  )(),
  getPipelineCapabilities: () => pickMode(
    mockApi.getPipelineCapabilities,
    withMockFallback(
      realApi.getPipelineCapabilities,
      mockApi.getPipelineCapabilities,
    ),
  )(),
  createAcquisitionCampaign: realApi.createAcquisitionCampaign,
  getAcquisitionCampaign: realApi.getAcquisitionCampaign,
  createAcquisitionKeyword: realApi.createAcquisitionKeyword,
  listAcquisitionKeywords: realApi.listAcquisitionKeywords,
  updateAcquisitionKeyword: realApi.updateAcquisitionKeyword,
  deleteAcquisitionKeyword: realApi.deleteAcquisitionKeyword,
  getAcquisitionStage01: realApi.getAcquisitionStage01,
  getAcquisitionStage02: realApi.getAcquisitionStage02,
  listAcquisitionCandidates: realApi.listAcquisitionCandidates,
  getAcquisitionCandidate: realApi.getAcquisitionCandidate,
  approveAcquisitionCandidate: realApi.approveAcquisitionCandidate,
  rejectAcquisitionCandidate: realApi.rejectAcquisitionCandidate,
  requestAcquisitionCandidateEnrichment: realApi.requestAcquisitionCandidateEnrichment,
  completeAcquisitionCandidateEnrichment: realApi.completeAcquisitionCandidateEnrichment,
  updateAcquisitionCandidateLabels: realApi.updateAcquisitionCandidateLabels,
  listAcquisitionCandidateAudits: realApi.listAcquisitionCandidateAudits,
  createPipelineSchedule: (payload: PipelineSchedulePayload) => pickMode(
    () => mockApi.createPipelineSchedule(payload),
    withMockFallback(
      () => realApi.createPipelineSchedule(payload),
      () => mockApi.createPipelineSchedule(payload),
    ),
  )(),
  listPipelineSchedules: (platform?: CreatePipelineJobPayload['platform']) => pickMode(
    () => mockApi.listPipelineSchedules(platform),
    withMockFallback(
      () => realApi.listPipelineSchedules(platform),
      () => mockApi.listPipelineSchedules(platform),
    ),
  )(),
  updatePipelineSchedule: (scheduleId: number, payload: PipelineSchedulePayload) => pickMode(
    () => mockApi.updatePipelineSchedule(scheduleId, payload),
    withMockFallback(
      () => realApi.updatePipelineSchedule(scheduleId, payload),
      () => mockApi.updatePipelineSchedule(scheduleId, payload),
    ),
  )(),
  deletePipelineSchedule: (scheduleId: number) => pickMode(
    () => mockApi.deletePipelineSchedule(scheduleId),
    withMockFallback(
      () => realApi.deletePipelineSchedule(scheduleId),
      () => mockApi.deletePipelineSchedule(scheduleId),
    ),
  )(),
  setConfigKey: pickMode(mockApi.setConfigKey, realApi.setConfigKey),
  saveApiKey: pickMode(mockApi.saveApiKey, realApi.saveApiKey),
  addAccount: pickMode(mockApi.addAccount, realApi.addAccount),
  addUser: pickMode(mockApi.addUser, realApi.addUser),
  deleteAccount: pickMode(mockApi.deleteAccount, realApi.deleteAccount),
  updateAccountMetadata: pickMode(mockApi.updateAccountMetadata, realApi.updateAccountMetadata),
  updateAccountCookies: pickMode(mockApi.updateAccountCookies, realApi.updateAccountCookies),
  createLoginSession: realApi.createLoginSession,
  getLoginSession: realApi.getLoginSession,
  verifyLoginSession: realApi.verifyLoginSession,
  cancelLoginSession: realApi.cancelLoginSession,
  startQrcodeLogin: pickMode(mockApi.startQrcodeLogin, realApi.startQrcodeLogin),
  getLoginStatus: pickMode(mockApi.getLoginStatus, realApi.getLoginStatus),
  getQrcodeUrl: realApi.getQrcodeUrl,
  checkAccountSession: pickMode(mockApi.checkAccountSession, realApi.checkAccountSession),
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
export const getPipelineOverview = wrapped.getPipelineOverview
export const createAcquisitionJob = wrapped.createAcquisitionJob
export const createPipelineJob = wrapped.createPipelineJob
export const listPipelineJobs = wrapped.listPipelineJobs
export const getPipelineJob = wrapped.getPipelineJob
export const getPipelineLive = wrapped.getPipelineLive
export const getPipelineLiveEvents = wrapped.getPipelineLiveEvents
export const getActivePipelineCheckpoint = wrapped.getActivePipelineCheckpoint
export const resolvePipelineCheckpoint = wrapped.resolvePipelineCheckpoint
export const completePipelineReviewCheckpoint = wrapped.completePipelineReviewCheckpoint
export const cancelPipelineJob = wrapped.cancelPipelineJob
export const retryPipelineJob = wrapped.retryPipelineJob
export const getPipelineCapabilities = wrapped.getPipelineCapabilities
export const createAcquisitionCampaign = wrapped.createAcquisitionCampaign
export const getAcquisitionCampaign = wrapped.getAcquisitionCampaign
export const createAcquisitionKeyword = wrapped.createAcquisitionKeyword
export const listAcquisitionKeywords = wrapped.listAcquisitionKeywords
export const updateAcquisitionKeyword = wrapped.updateAcquisitionKeyword
export const deleteAcquisitionKeyword = wrapped.deleteAcquisitionKeyword
export const getAcquisitionStage01 = wrapped.getAcquisitionStage01
export const getAcquisitionStage02 = wrapped.getAcquisitionStage02
export const listAcquisitionCandidates = wrapped.listAcquisitionCandidates
export const getAcquisitionCandidate = wrapped.getAcquisitionCandidate
export const approveAcquisitionCandidate = wrapped.approveAcquisitionCandidate
export const rejectAcquisitionCandidate = wrapped.rejectAcquisitionCandidate
export const requestAcquisitionCandidateEnrichment = wrapped.requestAcquisitionCandidateEnrichment
export const completeAcquisitionCandidateEnrichment = wrapped.completeAcquisitionCandidateEnrichment
export const updateAcquisitionCandidateLabels = wrapped.updateAcquisitionCandidateLabels
export const listAcquisitionCandidateAudits = wrapped.listAcquisitionCandidateAudits
export const createPipelineSchedule = wrapped.createPipelineSchedule
export const listPipelineSchedules = wrapped.listPipelineSchedules
export const updatePipelineSchedule = wrapped.updatePipelineSchedule
export const deletePipelineSchedule = wrapped.deletePipelineSchedule
export const runPipeline = (
  stages: string[],
  opts: Partial<Omit<CreatePipelineJobPayload, 'stages'>> = {},
) => {
  if (!opts.platform || !opts.accountMode) {
    const error = new Error('请选择执行平台和账号模式') as Error & {
      code: string
      response: {
        status: number
        data: { detail: { code: string; message: string } }
      }
    }
    error.code = 'pipeline_selection_required'
    error.response = {
      status: 422,
      data: {
        detail: {
          code: error.code,
          message: error.message,
        },
      },
    }
    return Promise.reject(error)
  }
  return createPipelineJob({
    platform: opts.platform,
    accountMode: opts.accountMode,
    accountId: opts.accountId,
    configSnapshot: opts.configSnapshot,
    stages: stages as PipelineStageName[],
  })
}
export const getDailyReport = wrapped.getDailyReport
export const getTrendReport = wrapped.getTrendReport
export const getWordcloud = wrapped.getWordcloud
export const getConfig = wrapped.getConfig
export const updatePipelineConfig = wrapped.updatePipelineConfig
export const getReportsOverview = wrapped.getReportsOverview
export const getLlmProviders = wrapped.getLlmProviders
export const createLlmProvider = wrapped.createLlmProvider
export const updateLlmProvider = wrapped.updateLlmProvider
export const deleteLlmProvider = wrapped.deleteLlmProvider
export const testLlmProvider = wrapped.testLlmProvider
export const updateLlmProviderSecret = wrapped.updateLlmProviderSecret
export const getLlmRoutes = wrapped.getLlmRoutes
export const updateLlmRoute = wrapped.updateLlmRoute
export const getLlmUsage = wrapped.getLlmUsage
export const setConfigKey = wrapped.setConfigKey
export const saveApiKey = wrapped.saveApiKey
export const getAccounts = wrapped.getAccounts
export const addAccount = wrapped.addAccount
export const addUser = wrapped.addUser
export const deleteAccount = wrapped.deleteAccount
export const updateAccountMetadata = wrapped.updateAccountMetadata
export const updateAccountCookies = wrapped.updateAccountCookies
export const createLoginSession = wrapped.createLoginSession
export const getLoginSession = wrapped.getLoginSession
export const verifyLoginSession = wrapped.verifyLoginSession
export const cancelLoginSession = wrapped.cancelLoginSession
export const startQrcodeLogin = wrapped.startQrcodeLogin
export const getLoginStatus = wrapped.getLoginStatus
export const getQrcodeUrl = wrapped.getQrcodeUrl
export const checkAccountSession = wrapped.checkAccountSession
export const searchLeads = wrapped.searchLeads
