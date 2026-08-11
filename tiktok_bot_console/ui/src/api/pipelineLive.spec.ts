import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  PipelineDecisionResolution,
  PipelineLiveEvent,
} from '../types/pipeline'

const axiosGet = vi.hoisted(() => vi.fn())
const axiosPost = vi.hoisted(() => vi.fn())

vi.mock('axios', () => ({
  default: {
    create: vi.fn(() => ({
      delete: vi.fn(),
      get: axiosGet,
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

import {
  completePipelineReviewCheckpoint,
  getActivePipelineCheckpoint,
  getPipelineLive,
  getPipelineLiveEvents,
  resolvePipelineCheckpoint,
  subscribePipelineLiveEvents,
} from './index'
import * as pipelineApi from './index'

const browseEvent = (sequence: number): PipelineLiveEvent => ({
  sequence,
  jobId: 'job /?',
  stage: 'collect',
  eventType: 'browse.extract',
  level: 'info',
  payload: {
    schemaVersion: 1,
    action: 'extract',
    pageType: 'search_results',
    rationale: '读取公开搜索结果',
  },
  createdAt: '2026-08-11T08:00:00Z',
})

function streamResponse(chunks: string[], status = 200): Response {
  const encoder = new TextEncoder()
  return new Response(
    new ReadableStream({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
        controller.close()
      },
    }),
    {
      status,
      headers: { 'Content-Type': 'text/event-stream' },
    },
  )
}

describe('pipeline live HTTP contracts', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
  })

  it('encodes the Job id and sends history cursors as camelCase query parameters', async () => {
    axiosGet.mockResolvedValue({ data: {} })

    await getPipelineLive('job /?')
    await getPipelineLiveEvents('job /?', { afterSequence: 17, limit: 40 })

    expect(axiosGet).toHaveBeenNthCalledWith(
      1,
      '/api/pipeline/jobs/job%20%2F%3F/live',
    )
    expect(axiosGet).toHaveBeenNthCalledWith(
      2,
      '/api/pipeline/jobs/job%20%2F%3F/events',
      { params: { afterSequence: 17, limit: 40 } },
    )
  })

  it('encodes checkpoint paths and preserves the strict resolve request body', async () => {
    axiosPost.mockResolvedValue({ data: {} })
    const payload = {
      optionKey: 'continue_with_qualified_only',
      version: 3,
      reason: '人工确认',
    }

    await resolvePipelineCheckpoint('job /?', 'checkpoint /?', payload)

    expect(axiosPost).toHaveBeenCalledWith(
      '/api/pipeline/jobs/job%20%2F%3F/checkpoints/checkpoint%20%2F%3F/resolve',
      payload,
    )
  })

  it('loads the active checkpoint from the encoded Job-scoped endpoint', async () => {
    axiosGet.mockResolvedValue({ data: { checkpoint: null } })

    await getActivePipelineCheckpoint('job /?')

    expect(axiosGet).toHaveBeenCalledWith(
      '/api/pipeline/jobs/job%20%2F%3F/checkpoints/active',
    )
  })

  it('completes a manual review checkpoint with only version and optional reason', async () => {
    axiosPost.mockResolvedValue({ data: {} })
    const payload = { version: 4, reason: '本轮复核完成' }

    await completePipelineReviewCheckpoint('job /?', 'checkpoint /?', payload)

    expect(axiosPost).toHaveBeenCalledWith(
      '/api/pipeline/jobs/job%20%2F%3F/checkpoints/checkpoint%20%2F%3F/review-complete',
      payload,
    )
  })

  it('accepts an authoritative cancelled resolution without an option key', () => {
    const resolution: PipelineDecisionResolution = {
      checkpointId: 'checkpoint-1',
      jobId: 'job-1',
      stage: 'filter',
      kind: 'manual_review_session',
      optionKey: null,
      source: 'system',
      status: 'cancelled',
      resolvedAt: '2026-08-11T08:00:00Z',
      deadlineAt: null,
    }

    expect(resolution).toMatchObject({ status: 'cancelled', optionKey: null })
  })

  it('does not expose deprecated cross-Job event clients in real mode', () => {
    expect(pipelineApi).not.toHaveProperty('getPipelineEvents')
    expect(pipelineApi).not.toHaveProperty('streamPipelineEvents')
  })
})

describe('pipeline live stream subscription', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.clearAllMocks()
    localStorage.clear()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('uses Bearer fetch without placing the token in the URL and parses split SSE frames', async () => {
    const token = 'credential-that-must-not-appear-in-the-url'
    localStorage.setItem('token', token)
    const event = browseEvent(8)
    const serialized = JSON.stringify(event)
    const fetchMock = vi.fn().mockResolvedValue(streamResponse([
      `id: 8\ndata: ${serialized.slice(0, 35)}`,
      `${serialized.slice(35)}\n\n`,
    ]))
    vi.stubGlobal('fetch', fetchMock)
    const onEvent = vi.fn()

    const subscription = subscribePipelineLiveEvents('job /?', {
      afterSequence: 7,
      onEvent,
    })
    await vi.waitFor(() => expect(onEvent).toHaveBeenCalledWith(event))

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toContain('/api/pipeline/jobs/job%20%2F%3F/events/stream')
    expect(url).toContain('afterSequence=7')
    expect(url).not.toContain(token)
    expect(init.headers).toMatchObject({
      Authorization: `Bearer ${token}`,
      Accept: 'text/event-stream',
      'Last-Event-ID': '7',
    })
    expect(subscription.lastSequence).toBe(8)

    subscription.abort()
  })

  it('continues from the last streamed sequence using one-second history polling after disconnect', async () => {
    const first = browseEvent(8)
    const second = browseEvent(9)
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(streamResponse([
        `id: 8\ndata: ${JSON.stringify(first)}\n\n`,
      ])),
    )
    axiosGet.mockResolvedValue({
      data: {
        items: [second],
        lastSequence: 9,
        hasMore: false,
      },
    })
    const onEvent = vi.fn()
    const onTransportChange = vi.fn()

    const subscription = subscribePipelineLiveEvents('job /?', {
      afterSequence: 7,
      onEvent,
      onTransportChange,
    })
    await vi.waitFor(() => expect(onEvent).toHaveBeenCalledWith(first))
    await vi.waitFor(() => expect(onTransportChange).toHaveBeenCalledWith('polling'))

    await vi.advanceTimersByTimeAsync(1_000)

    expect(axiosGet).toHaveBeenCalledWith(
      '/api/pipeline/jobs/job%20%2F%3F/events',
      expect.objectContaining({
        params: { afterSequence: 8, limit: 100 },
        signal: expect.any(AbortSignal),
      }),
    )
    expect(onEvent).toHaveBeenLastCalledWith(second)
    expect(subscription.lastSequence).toBe(9)

    subscription.abort()
  })

  it('aborts the fetch reader and polling timer without producing later requests', async () => {
    let capturedSignal: AbortSignal | undefined
    const cancel = vi.fn()
    vi.stubGlobal('fetch', vi.fn((_url: string, init: RequestInit) => {
      capturedSignal = init.signal as AbortSignal
      return Promise.resolve(new Response(new ReadableStream({
        pull() {
          return new Promise(() => undefined)
        },
        cancel,
      }), { status: 200 }))
    }))
    const subscription = subscribePipelineLiveEvents('job-1', {
      onEvent: vi.fn(),
    })
    await vi.waitFor(() => expect(capturedSignal).toBeDefined())

    subscription.abort()
    await Promise.resolve()
    await vi.advanceTimersByTimeAsync(2_000)

    expect(capturedSignal?.aborted).toBe(true)
    expect(cancel).toHaveBeenCalled()
    expect(axiosGet).not.toHaveBeenCalled()
  })

  it('automatically releases transport resources after a terminal lifecycle event', async () => {
    const terminal: PipelineLiveEvent = {
      sequence: 12,
      jobId: 'job-1',
      stage: 'report',
      eventType: 'job.lifecycle',
      level: 'info',
      payload: { schemaVersion: 1, status: 'succeeded' },
      createdAt: '2026-08-11T08:00:00Z',
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(streamResponse([
      `id: 12\ndata: ${JSON.stringify(terminal)}\n\n`,
    ])))
    const onEvent = vi.fn()
    const onTransportChange = vi.fn()

    subscribePipelineLiveEvents('job-1', { onEvent, onTransportChange })
    await vi.waitFor(() => expect(onEvent).toHaveBeenCalledWith(terminal))
    await vi.advanceTimersByTimeAsync(2_000)

    expect(onTransportChange).toHaveBeenLastCalledWith('closed')
    expect(axiosGet).not.toHaveBeenCalled()
  })

  it('still cancels a terminal stream when the external event callback throws', async () => {
    const privateText = 'credential-private-callback-detail'
    const terminal: PipelineLiveEvent = {
      sequence: 12,
      jobId: 'job-1',
      stage: 'report',
      eventType: 'job.lifecycle',
      level: 'info',
      payload: { schemaVersion: 1, status: 'succeeded' },
      createdAt: '2026-08-11T08:00:00Z',
    }
    const encoder = new TextEncoder()
    const cancel = vi.fn()
    let requestSignal: AbortSignal | undefined
    let sent = false
    vi.stubGlobal('fetch', vi.fn((_url: string, init: RequestInit) => {
      requestSignal = init.signal as AbortSignal
      return Promise.resolve(new Response(new ReadableStream({
        pull(controller) {
          if (!sent) {
            sent = true
            controller.enqueue(encoder.encode(
              `id: 12\ndata: ${JSON.stringify(terminal)}\n\n`,
            ))
          }
          return new Promise(() => undefined)
        },
        cancel,
      }), { status: 200 }))
    }))
    const onError = vi.fn()
    const onTransportChange = vi.fn()

    subscribePipelineLiveEvents('job-1', {
      onEvent: () => { throw new Error(privateText) },
      onError,
      onTransportChange,
    })
    await vi.waitFor(() => expect(onTransportChange).toHaveBeenLastCalledWith('closed'))
    await vi.advanceTimersByTimeAsync(2_000)

    expect(requestSignal?.aborted).toBe(true)
    expect(cancel).toHaveBeenCalled()
    expect(axiosGet).not.toHaveBeenCalled()
    const reported = onError.mock.calls[0]?.[0] as Error & { code?: string }
    expect(reported.code).toBe('pipeline_live_subscriber_error')
    expect(reported.message).not.toContain(privateText)
  })

  it('continues consuming non-terminal events after an external callback throws', async () => {
    const first = browseEvent(8)
    const terminal: PipelineLiveEvent = {
      sequence: 9,
      jobId: 'job /?',
      stage: 'collect',
      eventType: 'job.lifecycle',
      level: 'info',
      payload: { schemaVersion: 1, status: 'succeeded' },
      createdAt: '2026-08-11T08:00:01Z',
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(streamResponse([
      `id: 8\ndata: ${JSON.stringify(first)}\n\n`,
      `id: 9\ndata: ${JSON.stringify(terminal)}\n\n`,
    ])))
    const onEvent = vi.fn((event: PipelineLiveEvent) => {
      if (event.sequence === 8) throw new Error('private callback failure')
    })
    const onError = vi.fn()

    subscribePipelineLiveEvents('job /?', { onEvent, onError })
    await vi.waitFor(() => expect(onEvent).toHaveBeenCalledTimes(2))
    await vi.advanceTimersByTimeAsync(2_000)

    expect(onEvent).toHaveBeenNthCalledWith(2, terminal)
    expect(onError).toHaveBeenCalledWith(expect.objectContaining({
      code: 'pipeline_live_subscriber_error',
    }))
    expect(axiosGet).not.toHaveBeenCalled()
  })

  it('reports a stable public transport error without echoing the Bearer token', async () => {
    const token = 'credential-private-value'
    localStorage.setItem('token', token)
    vi.stubGlobal(
      'fetch',
      vi.fn().mockRejectedValue(new Error(`socket failed near ${token}`)),
    )
    const onError = vi.fn()
    const subscription = subscribePipelineLiveEvents('job-1', {
      onEvent: vi.fn(),
      onError,
    })

    await vi.waitFor(() => expect(onError).toHaveBeenCalled())

    const reported = onError.mock.calls[0]?.[0] as Error & { code?: string }
    expect(reported.message).not.toContain(token)
    expect(reported.code).toBe('pipeline_live_stream_unavailable')

    subscription.abort()
  })
})
