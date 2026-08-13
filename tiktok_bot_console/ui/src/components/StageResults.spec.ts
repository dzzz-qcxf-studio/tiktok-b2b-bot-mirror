// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import StageDiscoveryResult from './StageDiscoveryResult.vue'
import StageQualificationResult from './StageQualificationResult.vue'
import {
  getAcquisitionStage01,
  getAcquisitionStage02,
  listAcquisitionKeywords,
} from '../api'
import enUS from '../i18n/en-US'
import zhCN from '../i18n/zh-CN'

vi.mock('../api', () => ({
  getAcquisitionStage01: vi.fn(),
  getAcquisitionStage02: vi.fn(),
  listAcquisitionKeywords: vi.fn(),
}))

const stage01Mock = vi.mocked(getAcquisitionStage01)
const stage02Mock = vi.mocked(getAcquisitionStage02)
const keywordsMock = vi.mocked(listAcquisitionKeywords)

const i18n = () => createI18n({
  legacy: false,
  locale: 'zh-CN',
  messages: { 'zh-CN': zhCN, 'en-US': enUS },
})

function mountDiscovery(props: Record<string, unknown> = {}) {
  return mount(StageDiscoveryResult, {
    props: {
      jobId: 'job-1',
      stageStatus: 'succeeded',
      stageResult: {},
      legacy: false,
      refreshToken: 0,
      ...props,
    },
    global: { plugins: [i18n()] },
  })
}

function mountQualification(props: Record<string, unknown> = {}) {
  return mount(StageQualificationResult, {
    props: {
      jobId: 'job-1',
      stageStatus: 'succeeded',
      stageResult: {},
      legacy: false,
      refreshToken: 0,
      ...props,
    },
    global: { plugins: [i18n()] },
  })
}

describe('StageDiscoveryResult', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    stage01Mock.mockResolvedValue({
      data: {
        jobId: 'job-1',
        summary: {
          totalCandidates: 7,
          evidenceCount: 22,
          keywordCount: 2,
          byDiscoveryStatus: { candidate: 5, needs_more_evidence: 2 },
          bySourceType: { video_comment: 12, direct_user_search: 10 },
        },
      },
    } as never)
    keywordsMock.mockResolvedValue({
      data: {
        items: [
          {
            id: 9,
            jobId: 'job-1',
            text: '越南 电力 EPC',
            normalizedText: '越南 电力 epc',
            language: 'zh-CN',
            keywordType: 'effective',
            source: 'manual',
            status: 'effective',
            usageCount: 2,
            videoCount: 8,
            relevantVideoCount: 5,
            candidateCount: 4,
            qualifiedCount: 0,
            replyCount: 0,
            businessLeadCount: 0,
            lastUsedAt: null,
            createdAt: null,
            updatedAt: null,
          },
        ],
        total: 1,
        limit: 100,
        offset: 0,
      },
    } as never)
  })

  it('renders real keyword, evidence, source and discovery status data without raw JSON', async () => {
    const wrapper = mountDiscovery()
    await flushPromises()

    expect(wrapper.text()).toContain('越南 电力 EPC')
    expect(wrapper.text()).toContain('22')
    expect(wrapper.text()).toContain('video_comment')
    expect(wrapper.text()).toContain('需要更多证据')
    expect(wrapper.find('pre').exists()).toBe(false)
  })

  it('emits exact candidate filters from status, keyword and source metrics', async () => {
    const wrapper = mountDiscovery()
    await flushPromises()

    await wrapper.get('[data-discovery-status="needs_more_evidence"]').trigger('click')
    await wrapper.get('[data-keyword-id="9"]').trigger('click')
    await wrapper.get('[data-source-type="video_comment"]').trigger('click')

    expect(wrapper.emitted('filter-candidates')).toEqual([
      [{ discoveryStatus: 'needs_more_evidence' }],
      [{ keywordId: 9 }],
      [{ sourceType: 'video_comment' }],
    ])
  })

  it('renders explicit legacy, failed and budget-truncated states', async () => {
    const legacy = mountDiscovery({ legacy: true })
    await flushPromises()
    expect(legacy.text()).toContain('旧版任务')
    expect(stage01Mock).not.toHaveBeenCalled()

    const failed = mountDiscovery({ stageStatus: 'failed' })
    await flushPromises()
    expect(failed.get('[role="alert"]').text()).toContain('阶段执行失败')

    const truncated = mountDiscovery({
      stageResult: { truncation_reasons: ['max_pages', 'max_duration'] },
    })
    await flushPromises()
    expect(truncated.get('[data-testid="stage-01-truncated"]').text()).toContain('max_pages')
  })

  it('keeps a stable recoverable error instead of exposing upstream text', async () => {
    stage01Mock.mockRejectedValueOnce(new Error('private database path'))
    const wrapper = mountDiscovery()
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('阶段结果暂时不可用')
    expect(wrapper.text()).not.toContain('private database path')
  })
})

describe('StageQualificationResult', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    stage02Mock.mockResolvedValue({
      data: {
        jobId: 'job-1',
        summary: {
          totalCandidates: 7,
          byQualificationStatus: {
            qualified: 2,
            manual_review: 3,
            need_enrichment: 1,
            rejected: 1,
          },
          pendingHumanReview: 4,
          averageMatchScore: 78.5,
          averageConfidenceScore: 82.25,
        },
      },
    } as never)
  })

  it('renders discoverable review actions plus all four statuses and both scores', async () => {
    const wrapper = mountQualification()
    await flushPromises()

    expect(wrapper.text()).toContain('合格')
    expect(wrapper.text()).toContain('人工复核')
    expect(wrapper.text()).toContain('补充资料')
    expect(wrapper.text()).toContain('淘汰')
    expect(wrapper.text()).toContain('78.5')
    expect(wrapper.text()).toContain('82.3')

    const manualReviewAction = wrapper.get('[data-testid="stage-02-open-manual-review"]')
    expect(manualReviewAction.text()).toContain('3')
    await manualReviewAction.trigger('click')

    const enrichmentAction = wrapper.get('[data-testid="stage-02-open-enrichment"]')
    expect(enrichmentAction.text()).toContain('1')
    await enrichmentAction.trigger('click')

    await wrapper.get('[data-qualification-status="manual_review"]').trigger('click')
    expect(wrapper.emitted('filter-candidates')).toEqual([
      [{ qualificationStatus: 'manual_review' }],
      [{ qualificationStatus: 'need_enrichment' }],
      [{ qualificationStatus: 'manual_review' }],
    ])
  })

  it('renders an honest empty state and refreshes when the event token changes', async () => {
    stage02Mock.mockResolvedValue({
      data: {
        jobId: 'job-1',
        summary: {
          totalCandidates: 0,
          byQualificationStatus: {},
          pendingHumanReview: 0,
          averageMatchScore: null,
          averageConfidenceScore: null,
        },
      },
    } as never)
    const wrapper = mountQualification()
    await flushPromises()
    expect(wrapper.get('[data-testid="stage-02-empty"]').text()).toContain('还没有候选')

    await wrapper.setProps({ refreshToken: 1 })
    await flushPromises()
    expect(stage02Mock).toHaveBeenCalledTimes(2)
  })
})
