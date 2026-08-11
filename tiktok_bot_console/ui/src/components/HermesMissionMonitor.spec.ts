// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import HermesMissionMonitor from './HermesMissionMonitor.vue'
import {
  getActivePipelineCheckpoint,
  getPipelineLive,
  resolvePipelineCheckpoint,
  subscribePipelineLiveEvents,
} from '../api'
import enUS from '../i18n/en-US'
import zhCN from '../i18n/zh-CN'
import type {
  PipelineDecisionCheckpoint,
  PipelineLiveEvent,
  PipelineLiveResponse,
} from '../types/pipeline'

vi.mock('../api', () => ({
  getActivePipelineCheckpoint: vi.fn(),
  getPipelineLive: vi.fn(),
  resolvePipelineCheckpoint: vi.fn(),
  subscribePipelineLiveEvents: vi.fn(),
}))

const liveMock = vi.mocked(getPipelineLive)
const activeCheckpointMock = vi.mocked(getActivePipelineCheckpoint)
const resolveMock = vi.mocked(resolvePipelineCheckpoint)
const subscribeMock = vi.mocked(subscribePipelineLiveEvents)

function checkpoint(overrides: Partial<PipelineDecisionCheckpoint> = {}): PipelineDecisionCheckpoint {
  return {
    id: 'checkpoint-1',
    jobId: 'job-1',
    stage: 'collect',
    kind: 'insufficient_evidence',
    version: 1,
    optionKeys: ['continue_with_current_evidence', 'skip_remaining', 'cancel_job'],
    defaultOptionKey: 'continue_with_current_evidence',
    context: {
      schemaVersion: 1,
      title: '证据不足',
      question: '是否继续当前任务？',
      summary: '当前发现 3 位候选客户',
      remainingBudget: { pages: 8, llmCalls: 4 },
    },
    status: 'pending',
    deadlineAt: new Date(Date.now() + 10_000).toISOString(),
    resolvedAt: null,
    resolutionKey: null,
    resolutionSource: null,
    createdAt: '2026-08-11T08:00:00Z',
    updatedAt: '2026-08-11T08:00:00Z',
    ...overrides,
  }
}

function event(sequence: number, overrides: Partial<PipelineLiveEvent> = {}): PipelineLiveEvent {
  return {
    sequence,
    jobId: 'job-1',
    stage: 'collect',
    eventType: 'browse.extract',
    level: 'info',
    payload: {
      schemaVersion: 1,
      action: 'extract',
      keyword: '越南 电力 基建',
      evidenceCount: 3,
      pageType: 'search_results',
    },
    createdAt: '2026-08-11T08:00:00Z',
    ...overrides,
  }
}

function live(overrides: Partial<PipelineLiveResponse> = {}): PipelineLiveResponse {
  return {
    job: {
      id: 'job-1',
      platform: 'douyin',
      status: 'running',
      currentStage: 'collect',
      requestedStages: ['collect', 'filter'],
      startedAt: '2026-08-11T08:00:00Z',
      finishedAt: null,
      updatedAt: '2026-08-11T08:00:01Z',
    },
    stage: {
      stage: 'collect',
      order: 0,
      status: 'running',
      attempt: 1,
      startedAt: '2026-08-11T08:00:00Z',
      finishedAt: null,
    },
    metrics: {
      totalEvents: 3,
      browserActions: 2,
      videos: 4,
      comments: 11,
      candidates: 3,
      evidence: 15,
      llmCalls: 2,
      remainingBudget: { pages: 8, llmCalls: 4 },
    },
    recentEvents: [event(3)],
    activeCheckpoint: null,
    lastSequence: 3,
    ...overrides,
  }
}

function mountMonitor(jobId = 'job-1', attachTo?: Element) {
  const i18n = createI18n({
    legacy: false,
    locale: 'zh-CN',
    messages: { 'zh-CN': zhCN, 'en-US': enUS },
  })
  const options = {
    props: { jobId },
    global: { plugins: [i18n] },
  }
  return attachTo
    ? mount(HermesMissionMonitor, { ...options, attachTo })
    : mount(HermesMissionMonitor, options)
}

describe('HermesMissionMonitor', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-08-11T08:00:00Z'))
    vi.resetAllMocks()
    liveMock.mockResolvedValue({ data: live() } as never)
    activeCheckpointMock.mockResolvedValue({ data: { checkpoint: null } } as never)
    resolveMock.mockResolvedValue({
      data: {
        resolution: {
          checkpointId: 'checkpoint-1',
          jobId: 'job-1',
          stage: 'collect',
          kind: 'insufficient_evidence',
          optionKey: 'skip_remaining',
          source: 'human',
          status: 'resolved',
          resolvedAt: '2026-08-11T08:00:01Z',
          deadlineAt: '2026-08-11T08:00:10Z',
        },
      },
    } as never)
    subscribeMock.mockReturnValue({ lastSequence: 3, abort: vi.fn() })
  })

  afterEach(() => {
    document.body.innerHTML = ''
    vi.useRealTimers()
  })

  it('renders only real live DTO metrics and appends the selected Job event stream', async () => {
    let onEvent: ((value: PipelineLiveEvent) => void) | undefined
    subscribeMock.mockImplementation((_jobId, options) => {
      onEvent = options.onEvent
      return { lastSequence: 3, abort: vi.fn() }
    })
    const wrapper = mountMonitor()
    await flushPromises()

    expect(wrapper.get('[data-testid="mission-stage"]').text()).toContain('用户搜集')
    expect(wrapper.get('[data-testid="mission-candidates"]').text()).toContain('3')
    expect(wrapper.get('[data-testid="mission-budget"]').text()).toContain('pages')
    expect(wrapper.text()).toContain('越南 电力 基建')
    expect(wrapper.get('[aria-live="polite"]')).toBeTruthy()
    expect(subscribeMock).toHaveBeenCalledTimes(1)
    expect(subscribeMock).toHaveBeenCalledWith('job-1', expect.objectContaining({ afterSequence: 3 }))

    onEvent?.(event(4, { eventType: 'browse.navigate', payload: { schemaVersion: 1, action: 'navigate', keyword: '变电站 EPC' } }))
    await flushPromises()
    expect(wrapper.text()).toContain('变电站 EPC')
  })

  it('does not open a stream for a terminal Job and presents durable replay mode', async () => {
    liveMock.mockResolvedValue({
      data: live({ job: { ...live().job, status: 'succeeded', finishedAt: '2026-08-11T08:02:00Z' } }),
    } as never)
    const wrapper = mountMonitor()
    await flushPromises()

    expect(subscribeMock).not.toHaveBeenCalled()
    expect(wrapper.get('[data-testid="mission-mode"]').text()).toContain('回放')
  })

  it('shows the server deadline, never resolves locally at zero, and locks a submitted option', async () => {
    liveMock.mockResolvedValue({ data: live({ activeCheckpoint: checkpoint() }) } as never)
    const wrapper = mountMonitor()
    await flushPromises()

    expect(wrapper.get('[data-testid="mission-countdown"]').text()).toContain('10')
    expect(wrapper.text()).toContain('默认')
    await vi.advanceTimersByTimeAsync(10_000)
    expect(wrapper.get('[data-testid="mission-countdown"]').text()).toContain('等待服务端')
    expect(resolveMock).not.toHaveBeenCalled()

    const option = wrapper.get('[data-option="skip_remaining"]')
    await option.trigger('click')
    await option.trigger('click')
    expect(resolveMock).toHaveBeenCalledTimes(1)
    expect(resolveMock).toHaveBeenCalledWith('job-1', 'checkpoint-1', {
      optionKey: 'skip_remaining',
      version: 1,
    })
  })

  it('uses an authoritative 409 resolution without exposing private error text', async () => {
    liveMock.mockResolvedValue({ data: live({ activeCheckpoint: checkpoint() }) } as never)
    resolveMock.mockRejectedValue({
      response: {
        status: 409,
        data: {
          detail: {
            code: 'checkpoint_conflict',
            message: '关卡状态已变化，请使用当前权威结果',
            resolution: {
              checkpointId: 'checkpoint-1',
              jobId: 'job-1',
              stage: 'collect',
              kind: 'insufficient_evidence',
              optionKey: 'continue_with_current_evidence',
              source: 'timeout',
              status: 'expired',
              resolvedAt: '2026-08-11T08:00:10Z',
              deadlineAt: '2026-08-11T08:00:10Z',
            },
          },
        },
      },
      message: 'private token and upstream body',
    })
    const wrapper = mountMonitor()
    await flushPromises()
    await wrapper.get('[data-option="skip_remaining"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('[data-testid="mission-resolution"]').text()).toContain('自动')
    expect(wrapper.text()).not.toContain('private token')
  })

  it('rejects a mismatched 409 authority and keeps the submitted checkpoint open', async () => {
    liveMock.mockResolvedValue({ data: live({ activeCheckpoint: checkpoint() }) } as never)
    resolveMock.mockRejectedValue({
      response: {
        status: 409,
        data: {
          detail: {
            resolution: {
              checkpointId: 'checkpoint-other',
              jobId: 'job-1',
              stage: 'collect',
              kind: 'insufficient_evidence',
              optionKey: 'continue_with_current_evidence',
              source: 'timeout',
              status: 'expired',
              resolvedAt: '2026-08-11T08:00:10Z',
              deadlineAt: '2026-08-11T08:00:10Z',
            },
          },
        },
      },
      message: 'private upstream body',
    })
    const wrapper = mountMonitor()
    await flushPromises()
    await wrapper.get('[data-option="skip_remaining"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('[data-option="continue_with_current_evidence"]')).toBeTruthy()
    expect(wrapper.find('[data-testid="mission-resolution"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('private upstream body')
  })

  it('stops the old stream on Job switch and on unmount', async () => {
    const firstAbort = vi.fn()
    const secondAbort = vi.fn()
    subscribeMock
      .mockReturnValueOnce({ lastSequence: 3, abort: firstAbort })
      .mockReturnValueOnce({ lastSequence: 3, abort: secondAbort })
    liveMock
      .mockResolvedValueOnce({ data: live() } as never)
      .mockResolvedValueOnce({ data: live({ job: { ...live().job, id: 'job-2' }, recentEvents: [] }) } as never)
    const wrapper = mountMonitor()
    await flushPromises()

    await wrapper.setProps({ jobId: 'job-2' })
    await flushPromises()
    expect(firstAbort).toHaveBeenCalledTimes(1)
    expect(subscribeMock).toHaveBeenLastCalledWith('job-2', expect.any(Object))

    wrapper.unmount()
    expect(secondAbort).toHaveBeenCalledTimes(1)
  })

  it('stops the stream while collapsed and reloads the authority when expanded', async () => {
    const firstAbort = vi.fn()
    const secondAbort = vi.fn()
    subscribeMock
      .mockReturnValueOnce({ lastSequence: 3, abort: firstAbort })
      .mockReturnValueOnce({ lastSequence: 3, abort: secondAbort })
    liveMock
      .mockResolvedValueOnce({ data: live() } as never)
      .mockResolvedValueOnce({
        data: live({
          job: { ...live().job, id: 'job-2' },
          recentEvents: [event(7, { jobId: 'job-2' })],
          lastSequence: 7,
        }),
      } as never)
    const wrapper = mountMonitor()
    await flushPromises()

    await wrapper.get('[data-testid="mission-collapse"]').trigger('click')
    expect(firstAbort).toHaveBeenCalledTimes(1)
    await wrapper.setProps({ jobId: 'job-2' })
    expect(wrapper.text()).not.toContain('job-1')

    await wrapper.get('[data-testid="mission-collapse"]').trigger('click')
    await flushPromises()
    expect(liveMock).toHaveBeenCalledTimes(2)
    expect(subscribeMock).toHaveBeenCalledTimes(2)
  })

  it('does not let an old checkpoint terminal event clear a newer active checkpoint', async () => {
    const currentCheckpoint = checkpoint({ id: 'checkpoint-new' })
    const oldTerminal = event(2, {
      eventType: 'decision.lifecycle',
      payload: {
        schemaVersion: 1,
        checkpointId: 'checkpoint-old',
        kind: 'insufficient_evidence',
        status: 'expired',
        resolutionKey: 'continue_with_current_evidence',
        resolutionSource: 'timeout',
      },
    })
    liveMock.mockResolvedValue({
      data: live({ activeCheckpoint: currentCheckpoint, recentEvents: [oldTerminal] }),
    } as never)
    const wrapper = mountMonitor()
    await flushPromises()

    expect(wrapper.get('[data-option="continue_with_current_evidence"]')).toBeTruthy()
    expect(wrapper.find('[data-testid="mission-resolution"]').exists()).toBe(false)
  })

  it('reloads the live snapshot when the stream announces a pending checkpoint', async () => {
    let onEvent: ((value: PipelineLiveEvent) => void) | undefined
    subscribeMock.mockImplementation((_jobId, options) => {
      onEvent = options.onEvent
      return { lastSequence: 3, abort: vi.fn() }
    })
    liveMock
      .mockResolvedValueOnce({ data: live() } as never)
      .mockResolvedValueOnce({ data: live({ activeCheckpoint: checkpoint(), lastSequence: 4 }) } as never)
    const wrapper = mountMonitor()
    await flushPromises()

    onEvent?.(event(4, {
      eventType: 'decision.lifecycle',
      payload: {
        schemaVersion: 1,
        checkpointId: 'checkpoint-1',
        kind: 'insufficient_evidence',
        status: 'pending',
        defaultOptionKey: 'continue_with_current_evidence',
      },
    }))
    await flushPromises()

    expect(liveMock).toHaveBeenCalledTimes(2)
    expect(wrapper.get('[data-option="continue_with_current_evidence"]')).toBeTruthy()
  })

  it('keeps the newest pending checkpoint when live refreshes complete out of order', async () => {
    let onEvent: ((value: PipelineLiveEvent) => void) | undefined
    let resolveFirst!: (value: unknown) => void
    let resolveSecond!: (value: unknown) => void
    const firstRefresh = new Promise(resolve => { resolveFirst = resolve })
    const secondRefresh = new Promise(resolve => { resolveSecond = resolve })
    subscribeMock.mockImplementation((_jobId, options) => {
      onEvent = options.onEvent
      return { lastSequence: 3, abort: vi.fn() }
    })
    liveMock
      .mockResolvedValueOnce({ data: live() } as never)
      .mockReturnValueOnce(firstRefresh as never)
      .mockReturnValueOnce(secondRefresh as never)
    const wrapper = mountMonitor()
    await flushPromises()

    const checkpointA = checkpoint({ id: 'checkpoint-a' })
    const checkpointB = checkpoint({ id: 'checkpoint-b', kind: 'qualification_review' })
    onEvent?.(event(4, {
      eventType: 'decision.lifecycle',
      payload: { schemaVersion: 1, checkpointId: checkpointA.id, kind: checkpointA.kind, status: 'pending' },
    }))
    onEvent?.(event(5, {
      eventType: 'decision.lifecycle',
      payload: { schemaVersion: 1, checkpointId: checkpointB.id, kind: checkpointB.kind, status: 'pending' },
    }))

    resolveSecond({ data: live({ activeCheckpoint: checkpointB, lastSequence: 5 }) })
    await flushPromises()
    resolveFirst({ data: live({ activeCheckpoint: checkpointA, lastSequence: 4 }) })
    await flushPromises()

    expect(wrapper.get('[data-testid="mission-decision"]').attributes('data-checkpoint-id')).toBe('checkpoint-b')
  })

  it('falls back to the active-checkpoint endpoint when a pending live refresh fails', async () => {
    let onEvent: ((value: PipelineLiveEvent) => void) | undefined
    subscribeMock.mockImplementation((_jobId, options) => {
      onEvent = options.onEvent
      return { lastSequence: 3, abort: vi.fn() }
    })
    liveMock
      .mockResolvedValueOnce({ data: live() } as never)
      .mockRejectedValueOnce(new Error('private live failure'))
    activeCheckpointMock.mockResolvedValueOnce({ data: { checkpoint: checkpoint() } } as never)
    const wrapper = mountMonitor()
    await flushPromises()

    onEvent?.(event(4, {
      eventType: 'decision.lifecycle',
      payload: {
        schemaVersion: 1,
        checkpointId: 'checkpoint-1',
        kind: 'insufficient_evidence',
        status: 'pending',
      },
    }))
    await flushPromises()

    expect(activeCheckpointMock).toHaveBeenCalledWith('job-1')
    expect(wrapper.get('[data-option="continue_with_current_evidence"]')).toBeTruthy()
    expect(wrapper.text()).not.toContain('private live failure')
  })

  it('moves focus into a new decision and back to the monitor title after resolution', async () => {
    liveMock.mockResolvedValue({ data: live({ activeCheckpoint: checkpoint() }) } as never)
    const wrapper = mountMonitor('job-1', document.body)
    await flushPromises()

    const decision = wrapper.get('[data-testid="mission-decision"]')
    expect(document.activeElement).toBe(decision.element)

    await wrapper.get('[data-option="skip_remaining"]').trigger('click')
    await flushPromises()
    expect(document.activeElement).toBe(wrapper.get('#mission-title').element)
    const resolutionPanel = wrapper.get('[data-testid="mission-decision"]')
    const labelledBy = resolutionPanel.attributes('aria-labelledby')
    expect(labelledBy).toBeTruthy()
    expect(wrapper.find(`#${labelledBy}`).exists()).toBe(true)
  })

  it('refreshes authoritative metrics once for a low-frequency candidate event', async () => {
    let onEvent: ((value: PipelineLiveEvent) => void) | undefined
    subscribeMock.mockImplementation((_jobId, options) => {
      onEvent = options.onEvent
      return { lastSequence: 3, abort: vi.fn() }
    })
    liveMock
      .mockResolvedValueOnce({ data: live() } as never)
      .mockResolvedValueOnce({
        data: live({ metrics: { ...live().metrics, candidates: 9 }, lastSequence: 4 }),
      } as never)
    const wrapper = mountMonitor()
    await flushPromises()

    onEvent?.(event(4, {
      eventType: 'candidate.lifecycle',
      payload: { schemaVersion: 1, userId: 'user-1', status: 'manual_review' },
    }))
    await vi.advanceTimersByTimeAsync(300)
    await flushPromises()

    expect(liveMock).toHaveBeenCalledTimes(2)
    expect(wrapper.get('[data-testid="mission-candidates"]').text()).toContain('9')
  })

  it('renders loading, recoverable error, manual review, and accessible controls', async () => {
    liveMock.mockRejectedValueOnce(new Error('private upstream response'))
    const wrapper = mountMonitor()
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('实时监控暂时不可用')
    expect(wrapper.text()).not.toContain('private upstream response')

    liveMock.mockResolvedValue({
      data: live({
        activeCheckpoint: checkpoint({
          kind: 'manual_review_session',
          optionKeys: ['review_complete'],
          defaultOptionKey: 'review_complete',
          deadlineAt: null,
          context: { schemaVersion: 1, manualSession: true, title: '人工复核' },
        }),
      }),
    } as never)
    await wrapper.get('[data-testid="mission-retry"]').trigger('click')
    await flushPromises()
    const openButton = wrapper.get('[data-testid="mission-open-review"]')
    expect(openButton.attributes('type')).toBe('button')
    await openButton.trigger('click')
    expect(wrapper.emitted('open-review-workbench')).toHaveLength(1)
  })
})
