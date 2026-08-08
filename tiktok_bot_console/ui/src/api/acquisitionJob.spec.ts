import { beforeEach, describe, expect, it, vi } from 'vitest'

import { setApiMode, type ApiMode } from '../composables/useApiMode'
import type {
  CreateAcquisitionJobPayload,
  CreateAcquisitionJobResponse,
} from '../types/pipeline'

const axiosPost = vi.hoisted(() => vi.fn())

vi.mock('axios', () => ({
  default: {
    create: vi.fn(() => ({
      delete: vi.fn(),
      get: vi.fn(),
      patch: vi.fn(),
      post: axiosPost,
      put: vi.fn(),
      interceptors: {
        request: { use: vi.fn() },
        response: { use: vi.fn() },
      },
    })),
  },
}))

import { createAcquisitionJob, fallbackState } from './index'

const payload: CreateAcquisitionJobPayload = {
  platform: 'tiktok',
  accountMode: 'specified',
  accountId: 7,
  stages: ['collect', 'filter', 'strategy', 'outreach', 'report', 'iterate'],
  configSnapshot: {
    acquisitionMode: 'hermes',
    explorationDepth: 'balanced',
  },
  campaign: {
    countries: ['VN'],
    languages: ['vi'],
    industries: ['power infrastructure'],
    products: ['transformer'],
    customerRoles: ['procurement'],
    hardConditions: {
      excludedSubjects: ['consumer-only'],
      requiredKeywords: ['substation'],
      mustBeBusinessAccount: true,
      notListed: null,
    },
    preferenceConditions: {
      employeeCount: '10-20',
      registeredCapital: '100w-1000w',
      listingStatus: 'unlisted',
      companyScale: 'small',
      minimumYearsEstablished: 2,
      maximumYearsEstablished: 20,
    },
    excludedTargets: ['supplier'],
    searchBudget: {
      maxKeywords: 20,
      maxVideosPerKeyword: 20,
      maxCommentsPerVideo: 30,
      maxAuthorVideos: 5,
      maxPages: 10,
      maxDurationMinutes: 60,
      maxLlmCalls: 100,
    },
    keywordMix: {
      effectivePercent: 70,
      newPercent: 30,
    },
  },
  keywords: [
    {
      text: 'Vietnam substation contractor',
      language: 'en',
      keywordType: 'industry',
      source: 'manual',
      status: 'new',
    },
  ],
}

const serverResponse: CreateAcquisitionJobResponse = {
  job: {
    id: 'job-h2-001',
    triggerType: 'manual',
    scheduleId: null,
    platform: 'tiktok',
    accountMode: 'specified',
    accountId: 7,
    requestedStages: payload.stages,
    stages: payload.stages.map((stage, order) => ({
      id: order + 1,
      stage,
      order,
      status: 'pending',
      attempt: 0,
      result: {},
      errorMessage: '',
      startedAt: null,
      finishedAt: null,
    })),
    configSnapshot: payload.configSnapshot ?? {},
    status: 'queued',
    currentStage: '',
    priority: 0,
    retryOfJobId: null,
    errorSummary: '',
    queuedAt: '2026-08-08T08:00:00Z',
    startedAt: null,
    finishedAt: null,
    createdAt: '2026-08-08T08:00:00Z',
    updatedAt: '2026-08-08T08:00:00Z',
  },
  campaign: {
    id: 31,
    jobId: 'job-h2-001',
    platform: 'tiktok',
    countries: ['VN'],
    languages: ['vi'],
    industries: ['power infrastructure'],
    products: ['transformer'],
    customerRoles: ['procurement'],
    hardConditions: {
      excludedSubjects: ['consumer-only'],
      requiredKeywords: ['substation'],
      mustBeBusinessAccount: true,
      notListed: null,
    },
    preferenceConditions: {
      employeeCount: '10-20',
      registeredCapital: '100w-1000w',
      listingStatus: 'unlisted',
      companyScale: 'small',
      minimumYearsEstablished: 2,
      maximumYearsEstablished: 20,
    },
    excludedTargets: ['supplier'],
    searchBudget: {
      maxKeywords: 20,
      maxVideosPerKeyword: 20,
      maxCommentsPerVideo: 30,
      maxAuthorVideos: 5,
      maxPages: 10,
      maxDurationMinutes: 60,
      maxLlmCalls: 100,
    },
    keywordMix: {
      effectivePercent: 70,
      newPercent: 30,
    },
    createdAt: '2026-08-08T08:00:00Z',
  },
  keywords: [
    {
      id: 41,
      jobId: 'job-h2-001',
      platform: 'tiktok',
      text: 'Vietnam substation contractor',
      language: 'en',
      keywordType: 'industry',
      source: 'manual',
      status: 'new',
      usageCount: 0,
      videoCount: 0,
      relevantVideoCount: 0,
      candidateCount: 0,
      qualifiedCount: 0,
      replyCount: 0,
      businessLeadCount: 0,
      lastUsedAt: null,
      createdAt: '2026-08-08T08:00:00Z',
      updatedAt: '2026-08-08T08:00:00Z',
    },
  ],
}

describe('createAcquisitionJob', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setApiMode('real')
    fallbackState.active = false
    fallbackState.hits = 0
  })

  it('POSTs the complete atomic definition to the real acquisition endpoint', async () => {
    axiosPost.mockResolvedValue({ data: serverResponse })

    const response = await createAcquisitionJob(payload)

    expect(axiosPost).toHaveBeenCalledOnce()
    expect(axiosPost).toHaveBeenCalledWith('/api/acquisition/jobs', payload)
    expect(response.data).toEqual(serverResponse)
  })

  it('returns a mock response with the same complete contract in explicit mock mode', async () => {
    setApiMode('mock')

    const response = await createAcquisitionJob(payload)

    expect(axiosPost).not.toHaveBeenCalled()
    expect(response.data.job).toMatchObject({
      platform: payload.platform,
      accountMode: payload.accountMode,
      accountId: payload.accountId,
      requestedStages: payload.stages,
      configSnapshot: payload.configSnapshot,
    })
    expect(response.data.campaign).toMatchObject({
      jobId: response.data.job.id,
      platform: payload.platform,
      ...payload.campaign,
    })
    expect(response.data.keywords).toHaveLength(1)
    expect(response.data.keywords[0]).toMatchObject({
      jobId: response.data.job.id,
      platform: payload.platform,
      ...payload.keywords[0],
    })
  })

  it.each<ApiMode>(['real', 'auto'])(
    'surfaces %s mode write failures instead of returning a fake success',
    async (mode) => {
      const backendError = new Error(`backend unavailable in ${mode}`)
      setApiMode(mode)
      axiosPost.mockRejectedValue(backendError)

      await expect(createAcquisitionJob(payload)).rejects.toBe(backendError)

      expect(axiosPost).toHaveBeenCalledOnce()
      expect(fallbackState.active).toBe(false)
      expect(fallbackState.hits).toBe(0)
    },
  )
})
