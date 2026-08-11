// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import CandidateReviewDrawer from './CandidateReviewDrawer.vue'
import {
  approveAcquisitionCandidate,
  completeAcquisitionCandidateEnrichment,
  completePipelineReviewCheckpoint,
  getAcquisitionCandidate,
  listAcquisitionCandidateAudits,
  listAcquisitionCandidates,
  rejectAcquisitionCandidate,
  requestAcquisitionCandidateEnrichment,
  updateAcquisitionCandidateLabels,
} from '../api'
import enUS from '../i18n/en-US'
import zhCN from '../i18n/zh-CN'
import type {
  AcquisitionCandidate,
  AcquisitionCandidateDetailResponse,
  CandidateReviewAuditListResponse,
  PipelineDecisionCheckpoint,
} from '../types/pipeline'

vi.mock('../api', () => ({
  approveAcquisitionCandidate: vi.fn(),
  completeAcquisitionCandidateEnrichment: vi.fn(),
  completePipelineReviewCheckpoint: vi.fn(),
  getAcquisitionCandidate: vi.fn(),
  listAcquisitionCandidateAudits: vi.fn(),
  listAcquisitionCandidates: vi.fn(),
  rejectAcquisitionCandidate: vi.fn(),
  requestAcquisitionCandidateEnrichment: vi.fn(),
  updateAcquisitionCandidateLabels: vi.fn(),
}))

const listMock = vi.mocked(listAcquisitionCandidates)
const detailMock = vi.mocked(getAcquisitionCandidate)
const auditsMock = vi.mocked(listAcquisitionCandidateAudits)
const approveMock = vi.mocked(approveAcquisitionCandidate)
const rejectMock = vi.mocked(rejectAcquisitionCandidate)
const enrichMock = vi.mocked(requestAcquisitionCandidateEnrichment)
const completeEnrichmentMock = vi.mocked(completeAcquisitionCandidateEnrichment)
const labelsMock = vi.mocked(updateAcquisitionCandidateLabels)
const reviewCompleteMock = vi.mocked(completePipelineReviewCheckpoint)

function candidate(overrides: Partial<AcquisitionCandidate> = {}): AcquisitionCandidate {
  return {
    jobId: 'job-1',
    userId: 7,
    platform: 'douyin',
    username: 'grid-builder',
    nickname: '越南电力工程',
    bio: '变电站 EPC 和输配电工程',
    country: 'VN',
    followerCount: 1800,
    profileUrl: 'https://www.douyin.com/user/public-7',
    sourceStage: 'collect',
    discoveryStatus: 'candidate',
    qualificationStatus: 'manual_review',
    matchScore: 86,
    confidenceScore: 74,
    labels: ['EPC', '采购线索'],
    priority: 3,
    reviewVersion: 2,
    manuallyConfirmedAt: null,
    evidenceCount: 2,
    createdAt: '2026-08-11T08:00:00Z',
    updatedAt: '2026-08-11T08:01:00Z',
    ...overrides,
  }
}

function detail(value = candidate(), evidenceOffset = 0): AcquisitionCandidateDetailResponse {
  return {
    candidate: value,
    evidence: {
      items: [
        {
          id: 91,
          sourceType: 'video_comment',
          keywordId: 4,
          keywordText: '越南 电力 EPC',
          videoId: 'video-1',
          videoUrl: 'https://www.douyin.com/video/public-1',
          commentId: 'comment-1',
          commentUrl: 'https://www.douyin.com/comment/public-1',
          authorId: 'author-1',
          authorUrl: 'https://www.douyin.com/user/public-author',
          rawText: '需要询价中压柜',
          translatedText: '',
          relevanceScore: 91,
          completenessScore: 72,
          collectedAt: '2026-08-11T08:00:30Z',
        },
      ],
      total: 9,
      limit: 5,
      offset: evidenceOffset,
    },
    latestAssessment: {
      id: 3,
      labels: ['EPC', '买家'],
      matchScore: 86,
      confidenceScore: 74,
      positiveEvidence: ['主页明确提到电力工程'],
      negativeEvidence: [],
      missingFields: ['registered_capital', 'employee_count'],
      reasoning: '公开证据显示存在项目需求',
      suggestedStatus: 'manual_review',
      modelProvider: 'deepseek',
      modelName: 'deepseek-chat',
      schemaVersion: '1',
      createdAt: '2026-08-11T08:01:00Z',
    },
  }
}

function audits(offset = 0): CandidateReviewAuditListResponse {
  return {
    items: [{
      id: 5,
      jobId: 'job-1',
      userId: 7,
      action: 'request_enrichment',
      beforeStatus: 'manual_review',
      afterStatus: 'need_enrichment',
      labelsBefore: ['EPC'],
      labelsAfter: ['EPC'],
      priorityBefore: 3,
      priorityAfter: 3,
      reason: '需核验企业体量',
      operator: 'reviewer-a',
      createdAt: '2026-08-11T08:02:00Z',
    }],
    total: 8,
    limit: 5,
    offset,
  }
}

function manualCheckpoint(): PipelineDecisionCheckpoint {
  return {
    id: 'checkpoint-review',
    jobId: 'job-1',
    stage: 'filter',
    kind: 'manual_review_session',
    version: 4,
    optionKeys: ['review_complete'],
    defaultOptionKey: 'review_complete',
    context: { schemaVersion: 1, manualSession: true },
    status: 'pending',
    deadlineAt: null,
    resolvedAt: null,
    resolutionKey: null,
    resolutionSource: null,
    createdAt: null,
    updatedAt: null,
  }
}

function mountDrawer(
  props: Record<string, unknown> = {},
  options: { stubTeleport?: boolean } = {},
) {
  const i18n = createI18n({
    legacy: false,
    locale: 'zh-CN',
    messages: { 'zh-CN': zhCN, 'en-US': enUS },
  })
  return mount(CandidateReviewDrawer, {
    attachTo: document.body,
    props: {
      open: true,
      jobId: 'job-1',
      filter: { qualificationStatus: 'manual_review' },
      initialUserId: 7,
      manualCheckpoint: null,
      ...props,
    },
    global: {
      plugins: [i18n],
      stubs: { teleport: options.stubTeleport !== false },
    },
  })
}

describe('CandidateReviewDrawer', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    listMock.mockResolvedValue({
      data: { items: [candidate(), candidate({ userId: 8, username: 'buyer-8' })], total: 12, limit: 10, offset: 0 },
    } as never)
    detailMock.mockResolvedValue({ data: detail() } as never)
    auditsMock.mockResolvedValue({ data: audits() } as never)
    for (const mutation of [approveMock, rejectMock, enrichMock, completeEnrichmentMock, labelsMock]) {
      mutation.mockResolvedValue({ data: { candidate: candidate() } } as never)
    }
    reviewCompleteMock.mockResolvedValue({
      data: {
        resolution: {
          checkpointId: 'checkpoint-review',
          jobId: 'job-1',
          stage: 'filter',
          kind: 'manual_review_session',
          optionKey: 'review_complete',
          source: 'human',
          status: 'resolved',
          resolvedAt: '2026-08-11T08:10:00Z',
          deadlineAt: null,
        },
      },
    } as never)
  })

  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('loads one Job queue, paginated evidence and audits with abortable reads', async () => {
    const wrapper = mountDrawer()
    await flushPromises()

    expect(listMock).toHaveBeenCalledWith(
      'job-1',
      expect.objectContaining({ qualificationStatus: 'manual_review', limit: 10, offset: 0 }),
      expect.any(AbortSignal),
    )
    expect(detailMock).toHaveBeenCalledWith('job-1', 7, { limit: 5, offset: 0 }, expect.any(AbortSignal))
    expect(auditsMock).toHaveBeenCalledWith('job-1', 7, { limit: 5, offset: 0 }, expect.any(AbortSignal))
    expect(wrapper.text()).toContain('越南电力工程')
    expect(wrapper.text()).toContain('https://www.douyin.com/user/public-7')
    expect(wrapper.text()).toContain('video_comment')
    expect(wrapper.text()).toContain('需要询价中压柜')
    expect(wrapper.text()).toContain('86')
    expect(wrapper.text()).toContain('74')
    expect(wrapper.text()).toContain('registered_capital')
    expect(wrapper.text()).toContain('reviewer-a')
  })

  it('paginates queue, evidence and audit independently', async () => {
    const wrapper = mountDrawer()
    await flushPromises()

    await wrapper.get('[data-testid="queue-next"]').trigger('click')
    await wrapper.get('[data-testid="evidence-next"]').trigger('click')
    await wrapper.get('[data-testid="audit-next"]').trigger('click')
    await flushPromises()

    expect(listMock).toHaveBeenLastCalledWith('job-1', expect.objectContaining({ offset: 10 }), expect.any(AbortSignal))
    expect(detailMock).toHaveBeenLastCalledWith('job-1', 7, { limit: 5, offset: 5 }, expect.any(AbortSignal))
    expect(auditsMock).toHaveBeenLastCalledWith('job-1', 7, { limit: 5, offset: 5 }, expect.any(AbortSignal))
  })

  it('clears the previous candidate and actions while a newly selected candidate loads', async () => {
    const wrapper = mountDrawer()
    await flushPromises()

    let resolveNextDetail!: (value: unknown) => void
    detailMock.mockReturnValueOnce(new Promise(resolve => { resolveNextDetail = resolve }) as never)
    auditsMock.mockReturnValueOnce(new Promise(() => {}) as never)
    await wrapper.findAll('.queue-item')[1]!.trigger('click')

    expect(wrapper.get('.candidate-detail').text()).not.toContain('@grid-builder')
    expect(wrapper.find('[data-action="approve"]').exists()).toBe(false)
    expect(wrapper.find('.audit-list').exists()).toBe(false)

    resolveNextDetail({
      data: detail(candidate({ userId: 8, username: 'buyer-8' })),
    })
    await flushPromises()
    expect(wrapper.text()).toContain('@buyer-8')
  })

  it('offers exact manual-review actions and globally locks duplicate mutations', async () => {
    let resolveApprove!: (value: unknown) => void
    approveMock.mockReturnValueOnce(new Promise(resolve => { resolveApprove = resolve }) as never)
    const wrapper = mountDrawer()
    await flushPromises()

    expect(wrapper.find('[data-action="approve"]').exists()).toBe(true)
    expect(wrapper.find('[data-action="reject"]').exists()).toBe(true)
    expect(wrapper.find('[data-action="request-enrichment"]').exists()).toBe(true)
    expect(wrapper.find('[data-action="complete-enrichment"]').exists()).toBe(false)

    const approve = wrapper.get('[data-action="approve"]')
    await approve.trigger('click')
    await approve.trigger('click')
    expect(approveMock).toHaveBeenCalledTimes(1)
    expect(wrapper.get('[data-action="reject"]').attributes('disabled')).toBeDefined()

    resolveApprove({ data: { candidate: candidate({ qualificationStatus: 'qualified', reviewVersion: 3 }) } })
    await flushPromises()
    expect(listMock).toHaveBeenCalledTimes(2)
    expect(detailMock).toHaveBeenCalledTimes(2)
    expect(auditsMock).toHaveBeenCalledTimes(2)
  })

  it('offers enrichment completion and disables review actions for terminal candidates', async () => {
    detailMock.mockResolvedValueOnce({
      data: detail(candidate({ qualificationStatus: 'need_enrichment' })),
    } as never)
    const enrichment = mountDrawer()
    await flushPromises()
    expect(enrichment.find('[data-action="complete-enrichment"]').exists()).toBe(true)
    expect(enrichment.find('[data-action="approve"]').exists()).toBe(true)
    expect(enrichment.find('[data-action="reject"]').exists()).toBe(true)

    detailMock.mockResolvedValueOnce({
      data: detail(candidate({ userId: 9, qualificationStatus: 'qualified' })),
    } as never)
    const terminal = mountDrawer({ initialUserId: 9 })
    await flushPromises()
    expect(terminal.find('[data-testid="review-terminal"]').exists()).toBe(true)
    expect(terminal.find('[data-action]').exists()).toBe(false)
  })

  it('updates labels with the current review version and rereads all authority data', async () => {
    const wrapper = mountDrawer()
    await flushPromises()
    await wrapper.get('[data-testid="labels-input"]').setValue('EPC, contractor')
    await wrapper.get('[data-action="save-labels"]').trigger('click')
    await flushPromises()

    expect(labelsMock).toHaveBeenCalledWith('job-1', 7, {
      reviewVersion: 2,
      labels: ['EPC', 'contractor'],
    })
    expect(listMock).toHaveBeenCalledTimes(2)
    expect(detailMock).toHaveBeenCalledTimes(2)
    expect(auditsMock).toHaveBeenCalledTimes(2)
  })

  it('keeps the selected candidate and hides private mutation failures', async () => {
    approveMock.mockRejectedValueOnce(new Error('private database path and token'))
    const wrapper = mountDrawer()
    await flushPromises()
    await wrapper.get('[data-action="approve"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('grid-builder')
    expect(wrapper.get('[role="alert"]').text()).toContain('操作未完成')
    expect(wrapper.text()).not.toContain('private database path')
  })

  it('does not report mutation success when an authority reread fails', async () => {
    const wrapper = mountDrawer()
    await flushPromises()
    listMock.mockRejectedValueOnce(new Error('private refresh path'))

    await wrapper.get('[data-action="approve"]').trigger('click')
    await flushPromises()

    expect(wrapper.emitted('candidate-updated')).toBeUndefined()
    expect(wrapper.find('[data-testid="refresh-error"]').exists()).toBe(true)
    expect(wrapper.find('[data-action]').exists()).toBe(false)
    expect(wrapper.get('.candidate-detail').text()).not.toContain('@grid-builder')
    expect(wrapper.text()).not.toContain('private refresh path')
  })

  it('aborts stale reads on Job change and never paints the old candidate', async () => {
    let resolveOld!: (value: unknown) => void
    let oldSignal: AbortSignal | undefined
    listMock.mockImplementationOnce((_jobId, _params, signal) => {
      oldSignal = signal
      return new Promise(resolve => { resolveOld = resolve }) as never
    })
    listMock.mockResolvedValueOnce({
      data: { items: [candidate({ jobId: 'job-2', userId: 22, username: 'job-two-user' })], total: 1, limit: 10, offset: 0 },
    } as never)
    detailMock.mockResolvedValueOnce({
      data: detail(candidate({ jobId: 'job-2', userId: 22, username: 'job-two-user' })),
    } as never)
    const wrapper = mountDrawer({ initialUserId: null })
    await flushPromises()
    await wrapper.setProps({ jobId: 'job-2', filter: {}, initialUserId: 22 })
    await flushPromises()

    expect(oldSignal?.aborted).toBe(true)
    resolveOld({ data: { items: [candidate({ username: 'stale-user' })], total: 1, limit: 10, offset: 0 } })
    await flushPromises()
    expect(wrapper.text()).toContain('job-two-user')
    expect(wrapper.text()).not.toContain('stale-user')
  })

  it('supports Escape, focus restoration, close abort and unmount abort', async () => {
    const opener = document.createElement('button')
    document.body.appendChild(opener)
    opener.focus()
    const wrapper = mountDrawer({}, { stubTeleport: false })
    await flushPromises()
    const dialog = document.body.querySelector<HTMLElement>('[role="dialog"]')
    expect(dialog).not.toBeNull()
    expect(document.activeElement).toBe(dialog)

    const signal = detailMock.mock.calls[0]?.[3]
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await flushPromises()
    expect(wrapper.emitted('close')).toHaveLength(1)
    expect(signal?.aborted).toBe(true)
    expect(document.activeElement).toBe(opener)

    await wrapper.setProps({ open: false })
    wrapper.unmount()
    expect(signal?.aborted).toBe(true)
  })

  it('completes the optional same-Job manual checkpoint through the authority API', async () => {
    const wrapper = mountDrawer({ manualCheckpoint: manualCheckpoint() })
    await flushPromises()
    const complete = wrapper.get('[data-action="review-complete"]')
    await complete.trigger('click')
    await complete.trigger('click')
    await flushPromises()

    expect(reviewCompleteMock).toHaveBeenCalledTimes(1)
    expect(reviewCompleteMock).toHaveBeenCalledWith('job-1', 'checkpoint-review', { version: 4 })
    expect(wrapper.emitted('review-complete')).toHaveLength(1)
  })

  it('unlocks a replacement manual checkpoint and ignores the old checkpoint response', async () => {
    let resolveOld!: (value: unknown) => void
    reviewCompleteMock.mockReturnValueOnce(new Promise(resolve => { resolveOld = resolve }) as never)
    const wrapper = mountDrawer({ manualCheckpoint: manualCheckpoint() })
    await flushPromises()
    await wrapper.get('[data-action="review-complete"]').trigger('click')

    const replacement = manualCheckpoint()
    replacement.id = 'checkpoint-review-2'
    replacement.version = 5
    await wrapper.setProps({ manualCheckpoint: replacement })
    expect(wrapper.get('[data-action="review-complete"]').attributes('disabled')).toBeUndefined()

    resolveOld({
      data: {
        resolution: {
          checkpointId: 'checkpoint-review',
          jobId: 'job-1',
          stage: 'filter',
          kind: 'manual_review_session',
          optionKey: 'review_complete',
          source: 'human',
          status: 'resolved',
          resolvedAt: '2026-08-11T08:10:00Z',
          deadlineAt: null,
        },
      },
    })
    await flushPromises()
    expect(wrapper.emitted('review-complete')).toBeUndefined()

    reviewCompleteMock.mockResolvedValueOnce({
      data: {
        resolution: {
          checkpointId: 'checkpoint-review-2',
          jobId: 'job-1',
          stage: 'filter',
          kind: 'manual_review_session',
          optionKey: 'review_complete',
          source: 'human',
          status: 'resolved',
          resolvedAt: '2026-08-11T08:11:00Z',
          deadlineAt: null,
        },
      },
    } as never)
    await wrapper.get('[data-action="review-complete"]').trigger('click')
    await flushPromises()

    expect(reviewCompleteMock).toHaveBeenLastCalledWith('job-1', 'checkpoint-review-2', { version: 5 })
    expect(wrapper.emitted('review-complete')).toHaveLength(1)
    expect(wrapper.emitted('review-complete')?.[0]?.[0]).toMatchObject({ checkpointId: 'checkpoint-review-2' })
  })
})
