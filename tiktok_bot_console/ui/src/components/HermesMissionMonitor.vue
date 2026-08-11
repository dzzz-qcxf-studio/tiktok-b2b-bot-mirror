<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import {
  getActivePipelineCheckpoint,
  getPipelineLive,
  resolvePipelineCheckpoint,
  subscribePipelineLiveEvents,
} from '../api'
import type {
  PipelineDecisionCheckpoint,
  PipelineDecisionResolution,
  PipelineJobStatus,
  PipelineLiveEvent,
  PipelineLiveEventType,
  PipelineLiveResponse,
  PipelineLiveSubscription,
  PipelineLiveTransport,
  PipelineStageName,
} from '../types/pipeline'

const props = defineProps<{
  jobId: string
}>()

const emit = defineEmits<{
  (event: 'open-review-workbench', checkpoint: PipelineDecisionCheckpoint): void
}>()

const { t } = useI18n()

const EVENT_LIMIT = 60
const TERMINAL_STATUSES = new Set<PipelineJobStatus>([
  'succeeded',
  'partial_failed',
  'failed',
  'cancelled',
  'interrupted',
])
const ALL_STAGES: PipelineStageName[] = [
  'collect',
  'filter',
  'strategy',
  'outreach',
  'report',
  'iterate',
]
const EVENT_LABEL_KEYS: Record<PipelineLiveEventType, string> = {
  'job.lifecycle': 'jobLifecycle',
  'stage.lifecycle': 'stageLifecycle',
  'decision.lifecycle': 'decisionLifecycle',
  'candidate.lifecycle': 'candidateLifecycle',
  'browse.navigate': 'browseNavigate',
  'browse.click': 'browseClick',
  'browse.scroll': 'browseScroll',
  'browse.wait': 'browseWait',
  'browse.extract': 'browseExtract',
  'browse.done': 'browseDone',
  'browse.error': 'browseError',
}

const snapshot = shallowRef<PipelineLiveResponse | null>(null)
const events = shallowRef<PipelineLiveEvent[]>([])
const loading = ref(false)
const loadError = ref(false)
const transport = ref<PipelineLiveTransport>('closed')
const connectionIssue = ref(false)
const collapsed = ref(false)
const pendingOption = ref<string | null>(null)
const decisionError = ref(false)
const resolution = ref<PipelineDecisionResolution | null>(null)
const now = ref(Date.now())
const pendingCheckpointRefreshId = ref<string | null>(null)
const monitorTitle = ref<HTMLElement | null>(null)
const decisionPanel = ref<HTMLElement | null>(null)

let subscription: PipelineLiveSubscription | null = null
let generation = 0
let countdownTimer: ReturnType<typeof setInterval> | null = null
let checkpointRetryTimer: ReturnType<typeof setTimeout> | null = null
let metricsRefreshTimer: ReturnType<typeof setTimeout> | null = null
let authorityRefreshEpoch = 0

const activeCheckpoint = computed(() => snapshot.value?.activeCheckpoint ?? null)
const isTerminal = computed(() => {
  const status = snapshot.value?.job.status
  return status ? TERMINAL_STATUSES.has(status) : false
})
const isManualSession = computed(() => Boolean(
  activeCheckpoint.value?.context.manualSession
  || activeCheckpoint.value?.kind === 'manual_review_session',
))
const stageName = computed(() => {
  const stage = snapshot.value?.stage?.stage || snapshot.value?.job.currentStage
  return stage ? t(`pipeline.${stage}`) : t('pipeline.mission.noActiveStage')
})
const latestEvent = computed(() => events.value.at(-1) ?? null)
const countdownSeconds = computed(() => {
  const deadline = activeCheckpoint.value?.deadlineAt
  if (!deadline) return null
  const deadlineMs = Date.parse(deadline)
  if (!Number.isFinite(deadlineMs)) return null
  return Math.max(0, Math.ceil((deadlineMs - now.value) / 1_000))
})
const countdownText = computed(() => {
  if (countdownSeconds.value === null) return t('pipeline.mission.manualDeadline')
  if (countdownSeconds.value <= 0) return t('pipeline.mission.awaitingServer')
  return t('pipeline.mission.countdown', { seconds: countdownSeconds.value })
})
const modeText = computed(() => isTerminal.value
  ? t('pipeline.mission.replay')
  : t('pipeline.mission.live'))
const connectionText = computed(() => {
  if (isTerminal.value) return t('pipeline.mission.connection.replay')
  if (connectionIssue.value && transport.value !== 'streaming') {
    return t('pipeline.mission.connection.disconnected')
  }
  return t(`pipeline.mission.connection.${transport.value}`)
})
const decisionLabelId = computed(() => (
  activeCheckpoint.value || pendingCheckpointRefreshId.value
    ? 'mission-decision-title'
    : 'mission-resolution-title'
))
const ariaAnnouncement = computed(() => {
  if (loadError.value) return t('pipeline.mission.loadError')
  if (resolution.value) {
    return t('pipeline.mission.resolutionAnnouncement', {
      source: resolutionSourceLabel(resolution.value.source),
    })
  }
  if (activeCheckpoint.value) {
    return String(activeCheckpoint.value.context.question
      || activeCheckpoint.value.context.title
      || t('pipeline.mission.decisionRequired'))
  }
  if (pendingCheckpointRefreshId.value) return t('pipeline.mission.decisionRequired')
  if (snapshot.value) {
    return t('pipeline.mission.stageAnnouncement', { stage: stageName.value })
  }
  return ''
})

function stageLabel(stage: PipelineStageName) {
  return t(`pipeline.${stage}`)
}

function statusLabel(status: PipelineJobStatus) {
  return t(`pipeline.jobStatus.${status}`)
}

function resolutionSourceLabel(source: PipelineDecisionResolution['source']) {
  return t(`pipeline.mission.resolutionSource.${source}`)
}

function optionLabel(optionKey: string) {
  const key = `pipeline.mission.options.${optionKey}`
  const translated = t(key)
  return translated === key ? optionKey.replaceAll('_', ' ') : translated
}

function eventTitle(event: PipelineLiveEvent) {
  return t(`pipeline.mission.events.${EVENT_LABEL_KEYS[event.eventType]}`)
}

function eventDetail(event: PipelineLiveEvent) {
  const payload = event.payload
  const details: string[] = []
  for (const key of [
    'action',
    'keyword',
    'pageType',
    'status',
    'resolutionKey',
    'resolutionSource',
    'message',
  ]) {
    const value = payload[key]
    if (typeof value === 'string' && value.trim()) details.push(value.trim())
  }
  for (const key of ['videoCount', 'commentCount', 'candidateCount', 'evidenceCount']) {
    const value = payload[key]
    if (typeof value === 'number') details.push(`${key}: ${value}`)
  }
  return details.join(' · ') || t('pipeline.mission.eventRecorded')
}

function formatEventTime(value: string | null) {
  if (!value) return '--:--:--'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return '--:--:--'
  return new Intl.DateTimeFormat(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(parsed)
}

function appendEvent(nextEvent: PipelineLiveEvent) {
  if (nextEvent.jobId !== props.jobId) return
  const bySequence = new Map(events.value.map(item => [item.sequence, item]))
  bySequence.set(nextEvent.sequence, nextEvent)
  events.value = [...bySequence.values()]
    .sort((left, right) => left.sequence - right.sequence)
    .slice(-EVENT_LIMIT)

  applyDecisionResolutionEvent(nextEvent)

  if (
    nextEvent.eventType === 'decision.lifecycle'
    && nextEvent.payload.status === 'pending'
    && typeof nextEvent.payload.checkpointId === 'string'
  ) {
    const checkpointId = nextEvent.payload.checkpointId
    resolution.value = null
    decisionError.value = false
    if (
      snapshot.value?.activeCheckpoint
      && snapshot.value.activeCheckpoint.id !== checkpointId
    ) {
      snapshot.value = { ...snapshot.value, activeCheckpoint: null }
    }
    if (
      snapshot.value?.activeCheckpoint?.id !== checkpointId
      && pendingCheckpointRefreshId.value !== checkpointId
    ) {
      startPendingCheckpointRefresh(checkpointId)
    }
  }

  if (
    nextEvent.eventType === 'candidate.lifecycle'
    || nextEvent.eventType === 'browse.done'
    || nextEvent.eventType === 'stage.lifecycle'
  ) scheduleMetricsRefresh()

  if (nextEvent.eventType === 'job.lifecycle') {
    const nextStatus = nextEvent.payload.status
    if (
      snapshot.value
      && typeof nextStatus === 'string'
      && TERMINAL_STATUSES.has(nextStatus as PipelineJobStatus)
    ) {
      snapshot.value = {
        ...snapshot.value,
        job: { ...snapshot.value.job, status: nextStatus as PipelineJobStatus },
      }
      subscription?.abort()
      subscription = null
      transport.value = 'closed'
    }
  }
}

function applyDecisionResolutionEvent(nextEvent: PipelineLiveEvent) {
  if (nextEvent.eventType !== 'decision.lifecycle') return
  const payload = nextEvent.payload
  if (!['resolved', 'expired', 'cancelled'].includes(String(payload.status))) return
  if (
    typeof payload.checkpointId !== 'string'
    || typeof payload.kind !== 'string'
    || !['human', 'timeout', 'system'].includes(String(payload.resolutionSource))
  ) return
  const currentCheckpoint = snapshot.value?.activeCheckpoint
  if (currentCheckpoint && currentCheckpoint.id !== payload.checkpointId) return
  resolution.value = {
    checkpointId: payload.checkpointId,
    jobId: nextEvent.jobId,
    stage: nextEvent.stage,
    kind: payload.kind,
    optionKey: typeof payload.resolutionKey === 'string' ? payload.resolutionKey : null,
    source: payload.resolutionSource as PipelineDecisionResolution['source'],
    status: payload.status as PipelineDecisionResolution['status'],
    resolvedAt: nextEvent.createdAt,
    deadlineAt: typeof payload.deadlineAt === 'string' ? payload.deadlineAt : null,
  }
  pendingOption.value = null
  if (snapshot.value) snapshot.value = { ...snapshot.value, activeCheckpoint: null }
  pendingCheckpointRefreshId.value = null
  focusMonitorTitle()
}

function mergeEvents(nextEvents: PipelineLiveEvent[]) {
  const bySequence = new Map<number, PipelineLiveEvent>()
  for (const item of [...events.value, ...nextEvents]) {
    bySequence.set(item.sequence, item)
  }
  events.value = [...bySequence.values()]
    .sort((left, right) => left.sequence - right.sequence)
    .slice(-EVENT_LIMIT)
}

function applyLatestMatchingResolution() {
  const activeCheckpointId = snapshot.value?.activeCheckpoint?.id ?? null
  for (let index = events.value.length - 1; index >= 0; index -= 1) {
    const item = events.value[index]
    if (!item || item.eventType !== 'decision.lifecycle') continue
    if (!['resolved', 'expired', 'cancelled'].includes(String(item.payload.status))) continue
    if (
      activeCheckpointId
      && item.payload.checkpointId !== activeCheckpointId
    ) continue
    applyDecisionResolutionEvent(item)
    return
  }
}

function refreshIsCurrent(
  expectedGeneration: number,
  expectedJobId: string,
  refreshEpoch: number,
  checkpointId: string,
) {
  return expectedGeneration === generation
    && expectedJobId === props.jobId
    && refreshEpoch === authorityRefreshEpoch
    && pendingCheckpointRefreshId.value === checkpointId
    && !collapsed.value
}

function clearCheckpointRetry() {
  if (checkpointRetryTimer !== null) {
    clearTimeout(checkpointRetryTimer)
    checkpointRetryTimer = null
  }
}

function clearMetricsRefresh() {
  if (metricsRefreshTimer !== null) {
    clearTimeout(metricsRefreshTimer)
    metricsRefreshTimer = null
  }
}

function invalidateAuthorityRefreshes() {
  authorityRefreshEpoch += 1
  clearCheckpointRetry()
  clearMetricsRefresh()
}

function targetResolvedIn(eventsToCheck: PipelineLiveEvent[], checkpointId: string) {
  return eventsToCheck.some(item => (
    item.eventType === 'decision.lifecycle'
    && item.payload.checkpointId === checkpointId
    && ['resolved', 'expired', 'cancelled'].includes(String(item.payload.status))
  ))
}

function startPendingCheckpointRefresh(checkpointId: string) {
  clearCheckpointRetry()
  clearMetricsRefresh()
  pendingCheckpointRefreshId.value = checkpointId
  resolution.value = null
  const refreshEpoch = ++authorityRefreshEpoch
  void refreshPendingCheckpoint(
    generation,
    props.jobId,
    checkpointId,
    refreshEpoch,
    0,
  )
}

async function refreshPendingCheckpoint(
  expectedGeneration: number,
  expectedJobId: string,
  checkpointId: string,
  refreshEpoch: number,
  attempt: number,
) {
  let refreshed = false
  try {
    const response = await getPipelineLive(expectedJobId)
    if (!refreshIsCurrent(expectedGeneration, expectedJobId, refreshEpoch, checkpointId)) return
    if (
      response.data.activeCheckpoint?.id !== checkpointId
      && !targetResolvedIn(response.data.recentEvents, checkpointId)
    ) throw new Error('checkpoint snapshot not ready')
    snapshot.value = response.data
    mergeEvents(response.data.recentEvents)
    applyLatestMatchingResolution()
    if (isTerminal.value) stopSubscription()
    refreshed = true
  } catch {
    if (!refreshIsCurrent(expectedGeneration, expectedJobId, refreshEpoch, checkpointId)) return
    try {
      const response = await getActivePipelineCheckpoint(expectedJobId)
      if (!refreshIsCurrent(expectedGeneration, expectedJobId, refreshEpoch, checkpointId)) return
      if (response.data.checkpoint?.id === checkpointId && snapshot.value) {
        snapshot.value = {
          ...snapshot.value,
          activeCheckpoint: response.data.checkpoint,
        }
        resolution.value = null
        refreshed = true
      }
    } catch {
      // A bounded retry below keeps the pending card visible without exposing
      // either endpoint's private failure body.
    }

    if (
      !refreshed
      && attempt < 1
      && refreshIsCurrent(expectedGeneration, expectedJobId, refreshEpoch, checkpointId)
    ) {
      checkpointRetryTimer = setTimeout(() => {
        checkpointRetryTimer = null
        if (!refreshIsCurrent(expectedGeneration, expectedJobId, refreshEpoch, checkpointId)) return
        const retryEpoch = ++authorityRefreshEpoch
        void refreshPendingCheckpoint(
          expectedGeneration,
          expectedJobId,
          checkpointId,
          retryEpoch,
          attempt + 1,
        )
      }, 500)
    }
  }

  if (
    refreshed
    && refreshIsCurrent(expectedGeneration, expectedJobId, refreshEpoch, checkpointId)
  ) pendingCheckpointRefreshId.value = null
  if (!refreshed) connectionIssue.value = true
}

function scheduleMetricsRefresh() {
  if (
    metricsRefreshTimer !== null
    || pendingCheckpointRefreshId.value
    || collapsed.value
    || isTerminal.value
  ) return
  const expectedGeneration = generation
  const expectedJobId = props.jobId
  metricsRefreshTimer = setTimeout(() => {
    metricsRefreshTimer = null
    if (
      expectedGeneration !== generation
      || expectedJobId !== props.jobId
      || pendingCheckpointRefreshId.value
      || collapsed.value
    ) return
    const refreshEpoch = ++authorityRefreshEpoch
    void refreshMetricsSnapshot(expectedGeneration, expectedJobId, refreshEpoch)
  }, 250)
}

async function refreshMetricsSnapshot(
  expectedGeneration: number,
  expectedJobId: string,
  refreshEpoch: number,
) {
  try {
    const response = await getPipelineLive(expectedJobId)
    if (
      expectedGeneration !== generation
      || expectedJobId !== props.jobId
      || refreshEpoch !== authorityRefreshEpoch
      || pendingCheckpointRefreshId.value
      || collapsed.value
    ) return
    snapshot.value = response.data
    mergeEvents(response.data.recentEvents)
    if (response.data.activeCheckpoint) resolution.value = null
    applyLatestMatchingResolution()
    if (isTerminal.value) stopSubscription()
  } catch {
    if (expectedGeneration === generation && expectedJobId === props.jobId) {
      connectionIssue.value = true
    }
  }
}

function stopSubscription() {
  subscription?.abort()
  subscription = null
  transport.value = 'closed'
}

function startSubscription(expectedGeneration: number) {
  if (
    !snapshot.value
    || isTerminal.value
    || collapsed.value
    || expectedGeneration !== generation
  ) return
  transport.value = 'connecting'
  subscription = subscribePipelineLiveEvents(props.jobId, {
    afterSequence: snapshot.value.lastSequence,
    onEvent(nextEvent) {
      if (expectedGeneration !== generation) return
      appendEvent(nextEvent)
    },
    onTransportChange(nextTransport) {
      if (expectedGeneration !== generation) return
      transport.value = nextTransport
      if (nextTransport === 'streaming') connectionIssue.value = false
    },
    onError() {
      if (expectedGeneration !== generation) return
      connectionIssue.value = true
    },
  })
}

async function loadMission() {
  const expectedGeneration = ++generation
  invalidateAuthorityRefreshes()
  stopSubscription()
  loading.value = true
  loadError.value = false
  connectionIssue.value = false
  decisionError.value = false
  pendingCheckpointRefreshId.value = null
  pendingOption.value = null
  resolution.value = null
  snapshot.value = null
  events.value = []

  if (!props.jobId.trim()) {
    loading.value = false
    return
  }

  try {
    const response = await getPipelineLive(props.jobId)
    if (expectedGeneration !== generation) return
    snapshot.value = response.data
    events.value = []
    mergeEvents(response.data.recentEvents)
    applyLatestMatchingResolution()
    loading.value = false
    startSubscription(expectedGeneration)
  } catch {
    if (expectedGeneration !== generation) return
    loading.value = false
    loadError.value = true
  }
}

function resolutionMatches(
  candidate: Partial<PipelineDecisionResolution>,
  expectedJobId: string,
  expectedCheckpointId: string,
) {
  return candidate.jobId === expectedJobId
    && candidate.checkpointId === expectedCheckpointId
    && ['human', 'timeout', 'system'].includes(String(candidate.source))
    && ['resolved', 'expired', 'cancelled'].includes(String(candidate.status))
}

function authoritativeResolution(
  error: unknown,
  expectedJobId: string,
  expectedCheckpointId: string,
): PipelineDecisionResolution | null {
  if (!error || typeof error !== 'object') return null
  const response = (error as { response?: unknown }).response
  if (!response || typeof response !== 'object') return null
  if ((response as { status?: unknown }).status !== 409) return null
  const data = (response as { data?: unknown }).data
  if (!data || typeof data !== 'object') return null
  const detail = (data as { detail?: unknown }).detail
  if (!detail || typeof detail !== 'object') return null
  const value = (detail as { resolution?: unknown }).resolution
  if (!value || typeof value !== 'object') return null
  const candidate = value as Partial<PipelineDecisionResolution>
  if (!resolutionMatches(candidate, expectedJobId, expectedCheckpointId)) return null
  return candidate as PipelineDecisionResolution
}

function acceptSubmittedResolution(
  nextResolution: PipelineDecisionResolution,
  expectedJobId: string,
  expectedCheckpointId: string,
) {
  if (!resolutionMatches(nextResolution, expectedJobId, expectedCheckpointId)) return false
  if (snapshot.value?.activeCheckpoint?.id !== expectedCheckpointId) return false
  resolution.value = nextResolution
  snapshot.value = { ...snapshot.value, activeCheckpoint: null }
  pendingCheckpointRefreshId.value = null
  focusMonitorTitle()
  return true
}

function focusMonitorTitle() {
  void nextTick(() => monitorTitle.value?.focus())
}

async function chooseOption(optionKey: string) {
  const checkpoint = activeCheckpoint.value
  if (!checkpoint || pendingOption.value || resolution.value) return
  const expectedGeneration = generation
  const expectedJobId = props.jobId
  pendingOption.value = optionKey
  decisionError.value = false
  try {
    const response = await resolvePipelineCheckpoint(props.jobId, checkpoint.id, {
      optionKey,
      version: checkpoint.version,
    })
    if (expectedGeneration !== generation || expectedJobId !== props.jobId) return
    if (!acceptSubmittedResolution(
      response.data.resolution,
      expectedJobId,
      checkpoint.id,
    )) decisionError.value = true
  } catch (error) {
    if (expectedGeneration !== generation || expectedJobId !== props.jobId) return
    const authority = authoritativeResolution(error, expectedJobId, checkpoint.id)
    if (!authority || !acceptSubmittedResolution(
      authority,
      expectedJobId,
      checkpoint.id,
    )) {
      decisionError.value = true
    }
  } finally {
    if (expectedGeneration === generation && expectedJobId === props.jobId) {
      pendingOption.value = null
    }
  }
}

function openReviewWorkbench() {
  const checkpoint = activeCheckpoint.value
  if (!checkpoint || !isManualSession.value) return
  emit('open-review-workbench', checkpoint)
}

function toggleCollapsed() {
  if (!collapsed.value) {
    collapsed.value = true
    generation += 1
    invalidateAuthorityRefreshes()
    pendingCheckpointRefreshId.value = null
    stopSubscription()
    return
  }
  collapsed.value = false
  void loadMission()
}

watch(() => props.jobId, () => {
  if (collapsed.value) {
    generation += 1
    invalidateAuthorityRefreshes()
    pendingCheckpointRefreshId.value = null
    stopSubscription()
    loading.value = false
    loadError.value = false
    decisionError.value = false
    pendingOption.value = null
    resolution.value = null
    snapshot.value = null
    events.value = []
    return
  }
  void loadMission()
}, { immediate: true })

watch(
  () => activeCheckpoint.value?.id ?? pendingCheckpointRefreshId.value,
  async (checkpointId, previousCheckpointId) => {
    if (!checkpointId || checkpointId === previousCheckpointId || collapsed.value) return
    await nextTick()
    decisionPanel.value?.focus()
  },
)

countdownTimer = setInterval(() => {
  now.value = Date.now()
}, 250)

onBeforeUnmount(() => {
  generation += 1
  invalidateAuthorityRefreshes()
  stopSubscription()
  if (countdownTimer !== null) {
    clearInterval(countdownTimer)
    countdownTimer = null
  }
})
</script>

<template>
  <section class="mission-monitor" :class="{ 'is-collapsed': collapsed }" aria-labelledby="mission-title">
    <p class="sr-only" aria-live="polite">{{ ariaAnnouncement }}</p>

    <header class="mission-header">
      <div class="mission-identity">
        <span class="mission-mark" aria-hidden="true"></span>
        <div>
          <p class="mission-eyebrow">HERMES LIVE</p>
          <h2 id="mission-title" ref="monitorTitle" tabindex="-1">{{ t('pipeline.mission.title') }}</h2>
        </div>
      </div>
      <div class="mission-header-meta">
        <span v-if="snapshot" class="mission-job mono">{{ snapshot.job.id }}</span>
        <span v-if="snapshot" class="state-badge" :data-state="snapshot.job.status">
          {{ statusLabel(snapshot.job.status) }}
        </span>
        <span v-if="snapshot" data-testid="mission-mode" class="mode-badge">{{ modeText }}</span>
        <button
          data-testid="mission-collapse"
          type="button"
          class="collapse-button"
          :aria-expanded="!collapsed"
          :aria-label="collapsed ? t('pipeline.mission.expand') : t('pipeline.mission.collapse')"
          @click="toggleCollapsed"
        >
          <span aria-hidden="true">{{ collapsed ? '+' : '−' }}</span>
        </button>
      </div>
    </header>

    <div v-if="!collapsed" class="mission-body">
      <div v-if="loading" class="mission-state" role="status">
        <span class="loading-line" aria-hidden="true"></span>
        <span>{{ t('pipeline.mission.loading') }}</span>
      </div>

      <div v-else-if="loadError" class="mission-state is-error" role="alert">
        <div>
          <strong>{{ t('pipeline.mission.loadError') }}</strong>
          <p>{{ t('pipeline.mission.loadErrorHint') }}</p>
        </div>
        <button data-testid="mission-retry" type="button" class="mission-button" @click="loadMission">
          {{ t('common.retry') }}
        </button>
      </div>

      <div v-else-if="!snapshot" class="mission-state is-empty">
        {{ t('pipeline.mission.empty') }}
      </div>

      <template v-else>
        <div class="mission-connection" :class="{ 'has-issue': connectionIssue && !isTerminal }">
          <span class="connection-dot" aria-hidden="true"></span>
          <span>{{ connectionText }}</span>
          <span v-if="snapshot.stage" data-testid="mission-stage" class="current-stage">
            {{ stageName }}
          </span>
        </div>

        <ol class="stage-track" :aria-label="t('pipeline.mission.stageTrack')">
          <li
            v-for="(stage, index) in ALL_STAGES"
            :key="stage"
            :class="{
              'is-requested': snapshot.job.requestedStages.includes(stage),
              'is-current': snapshot.job.currentStage === stage,
              'is-past': snapshot.stage && index < snapshot.stage.order,
            }"
          >
            <span class="stage-index mono">{{ String(index + 1).padStart(2, '0') }}</span>
            <span>{{ stageLabel(stage) }}</span>
          </li>
        </ol>

        <div class="mission-grid">
          <section class="action-panel" :aria-label="t('pipeline.mission.currentAction')">
            <div class="section-heading">
              <span>{{ t('pipeline.mission.currentAction') }}</span>
              <span v-if="latestEvent" class="mono">#{{ latestEvent.sequence }}</span>
            </div>
            <template v-if="latestEvent">
              <strong>{{ eventTitle(latestEvent) }}</strong>
              <p>{{ eventDetail(latestEvent) }}</p>
              <small>{{ formatEventTime(latestEvent.createdAt) }}</small>
            </template>
            <p v-else class="muted-copy">{{ t('pipeline.mission.noEvents') }}</p>
          </section>

          <section class="metrics-panel" :aria-label="t('pipeline.mission.metrics')">
            <div class="section-heading">{{ t('pipeline.mission.metrics') }}</div>
            <dl class="metric-grid">
              <div><dt>{{ t('pipeline.mission.metric.videos') }}</dt><dd>{{ snapshot.metrics.videos }}</dd></div>
              <div><dt>{{ t('pipeline.mission.metric.comments') }}</dt><dd>{{ snapshot.metrics.comments }}</dd></div>
              <div data-testid="mission-candidates"><dt>{{ t('pipeline.mission.metric.candidates') }}</dt><dd>{{ snapshot.metrics.candidates }}</dd></div>
              <div><dt>{{ t('pipeline.mission.metric.evidence') }}</dt><dd>{{ snapshot.metrics.evidence }}</dd></div>
              <div><dt>{{ t('pipeline.mission.metric.actions') }}</dt><dd>{{ snapshot.metrics.browserActions }}</dd></div>
              <div><dt>{{ t('pipeline.mission.metric.llmCalls') }}</dt><dd>{{ snapshot.metrics.llmCalls }}</dd></div>
            </dl>
            <div data-testid="mission-budget" class="budget-line mono">
              <span>{{ t('pipeline.mission.remainingBudget') }}</span>
              <span v-if="!Object.keys(snapshot.metrics.remainingBudget).length">--</span>
              <span v-for="(value, key) in snapshot.metrics.remainingBudget" :key="key">
                {{ key }} {{ value }}
              </span>
            </div>
          </section>
        </div>

        <section
          v-if="activeCheckpoint || pendingCheckpointRefreshId || resolution"
          ref="decisionPanel"
          data-testid="mission-decision"
          class="decision-panel"
          :class="{ 'is-manual': isManualSession }"
          :data-checkpoint-id="activeCheckpoint?.id || pendingCheckpointRefreshId || resolution?.checkpointId"
          :aria-labelledby="decisionLabelId"
          tabindex="-1"
        >
          <div v-if="activeCheckpoint || pendingCheckpointRefreshId" class="decision-copy">
            <p class="decision-kicker">{{ t('pipeline.mission.decisionRequired') }}</p>
            <h3 id="mission-decision-title">
              {{ activeCheckpoint?.context.title || t('pipeline.mission.decisionFallbackTitle') }}
            </h3>
            <p v-if="activeCheckpoint">
              {{ activeCheckpoint.context.question || activeCheckpoint.context.summary }}
            </p>
            <span v-if="activeCheckpoint && !isManualSession" data-testid="mission-countdown" class="countdown mono">
              {{ countdownText }}
            </span>
          </div>

          <div v-if="activeCheckpoint && isManualSession" class="decision-actions">
            <button
              data-testid="mission-open-review"
              type="button"
              class="mission-button is-primary"
              @click="openReviewWorkbench"
            >
              {{ t('pipeline.mission.openReview') }}
            </button>
          </div>

          <div v-else-if="activeCheckpoint" class="decision-actions">
            <button
              v-for="optionKey in activeCheckpoint.optionKeys"
              :key="optionKey"
              type="button"
              class="mission-button"
              :class="{ 'is-primary': optionKey === activeCheckpoint.defaultOptionKey }"
              :data-option="optionKey"
              :disabled="Boolean(pendingOption)"
              @click="chooseOption(optionKey)"
            >
              <span>{{ optionLabel(optionKey) }}</span>
              <small v-if="optionKey === activeCheckpoint.defaultOptionKey">
                {{ t('pipeline.mission.defaultOption') }}
              </small>
            </button>
          </div>

          <p v-if="decisionError" class="decision-error" role="alert">
            {{ t('pipeline.mission.decisionError') }}
          </p>
          <p
            v-if="resolution"
            id="mission-resolution-title"
            data-testid="mission-resolution"
            class="resolution-line"
          >
            {{ t('pipeline.mission.resolvedBy', { source: resolutionSourceLabel(resolution.source) }) }}
            <span v-if="resolution.optionKey" class="mono">{{ optionLabel(resolution.optionKey) }}</span>
          </p>
        </section>

        <section class="event-panel" :aria-label="t('pipeline.mission.eventTimeline')">
          <div class="section-heading">
            <span>{{ isTerminal ? t('pipeline.mission.replayTimeline') : t('pipeline.mission.eventTimeline') }}</span>
            <span class="mono">{{ events.length }}/{{ snapshot.metrics.totalEvents }}</span>
          </div>
          <ol class="event-list">
            <li v-for="item in events" :key="item.sequence" :data-level="item.level">
              <time class="mono">{{ formatEventTime(item.createdAt) }}</time>
              <span class="event-marker" aria-hidden="true"></span>
              <div>
                <strong>{{ eventTitle(item) }}</strong>
                <p>{{ eventDetail(item) }}</p>
              </div>
            </li>
            <li v-if="!events.length" class="event-empty">{{ t('pipeline.mission.noEvents') }}</li>
          </ol>
        </section>
      </template>
    </div>
  </section>
</template>

<style scoped>
.mission-monitor {
  color: var(--sb-fg);
  background: var(--sb-bg);
  border: 1px solid var(--sb-border);
  border-radius: var(--card-radius);
  box-shadow: var(--shadow-2);
  overflow: hidden;
  container-type: inline-size;
}

.mission-header {
  min-height: 72px;
  padding: 12px 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  border-bottom: 1px solid var(--sb-border);
}

.mission-identity,
.mission-header-meta,
.mission-connection,
.section-heading,
.resolution-line {
  display: flex;
  align-items: center;
}

.mission-identity { gap: 11px; min-width: 0; }
.mission-header-meta { gap: 8px; justify-content: flex-end; flex-wrap: wrap; }
.mission-mark { width: 9px; height: 28px; border-radius: 3px; background: var(--brand); }
.mission-eyebrow { margin: 0 0 2px; color: var(--brand); font: 700 10px/1 var(--font-mono); letter-spacing: 0.14em; }
.mission-header h2 { margin: 0; font-size: 15px; line-height: 1.25; color: #fff; }
.mission-header h2:focus-visible, .decision-panel:focus-visible { outline: 2px solid var(--brand); outline-offset: 3px; }
.mission-job { max-width: 180px; overflow: hidden; text-overflow: ellipsis; color: var(--sb-muted); font-size: 10.5px; white-space: nowrap; }

.state-badge,
.mode-badge {
  min-height: 24px;
  padding: 3px 8px;
  display: inline-flex;
  align-items: center;
  border: 1px solid var(--sb-border);
  border-radius: var(--chip-radius);
  font: 650 10.5px/1 var(--font-mono);
}
.state-badge[data-state="running"], .state-badge[data-state="succeeded"] { color: var(--ok); }
.state-badge[data-state="waiting_decision"], .state-badge[data-state="partial_failed"] { color: var(--warn); }
.state-badge[data-state="failed"], .state-badge[data-state="cancelled"] { color: var(--err); }
.mode-badge { color: var(--cyan); }

.collapse-button,
.mission-button {
  min-width: 44px;
  min-height: 44px;
  border: 1px solid var(--sb-border);
  border-radius: 8px;
  color: var(--sb-fg);
  background: var(--sb-bg-2);
}
.collapse-button { display: grid; place-items: center; font: 600 20px/1 var(--font-mono); }
.collapse-button:hover, .mission-button:hover:not(:disabled) { border-color: var(--brand); }
.collapse-button:focus-visible, .mission-button:focus-visible { outline: 2px solid var(--brand); outline-offset: 2px; }

.mission-body { min-height: 420px; padding: 14px; display: grid; gap: 12px; }
.mission-state { min-height: 240px; display: flex; align-items: center; justify-content: center; gap: 14px; color: var(--sb-muted); text-align: center; }
.mission-state.is-error { flex-direction: column; }
.mission-state strong { color: var(--sb-fg); }
.mission-state p { margin: 4px 0 0; }
.loading-line { width: 42px; height: 2px; background: var(--brand); animation: mission-pulse 1s ease-in-out infinite alternate; }

.mission-connection {
  min-height: 30px;
  gap: 8px;
  color: var(--sb-muted);
  font: 500 11px/1.3 var(--font-mono);
}
.connection-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--ok); }
.mission-connection.has-issue { color: var(--warn); }
.mission-connection.has-issue .connection-dot { background: var(--warn); }
.current-stage { margin-left: auto; color: var(--sb-fg); font-family: var(--font-sans); font-weight: 650; }

.stage-track {
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  list-style: none;
  border: 1px solid var(--sb-border);
  border-radius: 8px;
  overflow: hidden;
}
.stage-track li { min-height: 48px; padding: 8px; display: flex; align-items: center; gap: 7px; color: var(--sb-muted); background: var(--sb-bg-2); border-right: 1px solid var(--sb-border); font-size: 11px; }
.stage-track li:last-child { border-right: 0; }
.stage-track li.is-requested { color: var(--sb-fg); }
.stage-track li.is-past { color: var(--ok); }
.stage-track li.is-current { color: #fff; box-shadow: inset 0 -2px 0 var(--brand); }
.stage-index { color: inherit; font-size: 9.5px; }

.mission-grid { display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(280px, 0.9fr); gap: 12px; }
.action-panel, .metrics-panel, .event-panel, .decision-panel { border: 1px solid var(--sb-border); border-radius: 8px; background: var(--sb-bg-2); }
.action-panel, .metrics-panel { min-height: 132px; padding: 12px; }
.section-heading { justify-content: space-between; gap: 12px; margin-bottom: 10px; color: var(--sb-muted); font: 650 10px/1.2 var(--font-mono); letter-spacing: 0.08em; text-transform: uppercase; }
.action-panel strong { color: #fff; font-size: 13px; }
.action-panel p { margin: 7px 0 3px; color: var(--sb-fg); text-wrap: pretty; }
.action-panel small { color: var(--sb-muted); font-family: var(--font-mono); }
.muted-copy { color: var(--sb-muted) !important; }

.metric-grid { margin: 0; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
.metric-grid div { min-width: 0; }
.metric-grid dt { color: var(--sb-muted); font-size: 10px; white-space: nowrap; }
.metric-grid dd { margin: 1px 0 0; color: #fff; font: 650 17px/1.2 var(--font-mono); }
.budget-line { margin-top: 10px; display: flex; flex-wrap: wrap; gap: 5px 12px; color: var(--cyan); font-size: 10px; }

.decision-panel { padding: 14px; display: grid; grid-template-columns: minmax(220px, 0.8fr) minmax(320px, 1.2fr); gap: 14px; border-color: color-mix(in oklch, var(--warn), var(--sb-border) 55%); }
.decision-panel.is-manual { border-color: color-mix(in oklch, var(--brand), var(--sb-border) 55%); }
.decision-kicker { margin: 0 0 5px; color: var(--warn) !important; font: 700 10px/1 var(--font-mono); letter-spacing: 0.1em; }
.decision-copy h3 { margin: 0; color: #fff; font-size: 14px; }
.decision-copy p { margin: 6px 0; color: var(--sb-fg); text-wrap: pretty; }
.countdown { display: inline-flex; min-height: 24px; align-items: center; color: var(--warn); font-size: 11px; }
.decision-actions { display: grid; grid-template-columns: repeat(auto-fit, minmax(145px, 1fr)); gap: 8px; align-content: center; }
.mission-button { padding: 8px 12px; font-weight: 600; }
.mission-button small { display: block; margin-top: 2px; color: var(--warn); font-size: 9.5px; }
.mission-button.is-primary { color: #fff; background: var(--brand); border-color: var(--brand); }
.mission-button.is-primary small { color: #fff; opacity: 0.82; }
.mission-button:disabled { cursor: wait; opacity: 0.55; }
.decision-error { grid-column: 1 / -1; margin: 0; color: var(--err); }
.resolution-line { grid-column: 1 / -1; min-height: 44px; gap: 8px; color: var(--ok); }

.event-panel { min-height: 154px; padding: 12px; }
.event-list { max-height: 168px; margin: 0; padding: 0; overflow: auto; list-style: none; scrollbar-color: var(--sb-border) transparent; }
.event-list li { min-height: 40px; display: grid; grid-template-columns: 64px 8px minmax(0, 1fr); align-items: start; gap: 8px; padding: 6px 2px; border-top: 1px solid var(--sb-border); }
.event-list li:first-child { border-top: 0; }
.event-list time { color: var(--sb-muted); font-size: 9.5px; }
.event-marker { width: 6px; height: 6px; margin-top: 5px; border-radius: 50%; background: var(--cyan); }
.event-list li[data-level="warning"] .event-marker { background: var(--warn); }
.event-list li[data-level="error"] .event-marker { background: var(--err); }
.event-list strong { display: block; color: var(--sb-fg); font-size: 11px; }
.event-list p { margin: 2px 0 0; color: var(--sb-muted); font-size: 10.5px; overflow-wrap: anywhere; }
.event-empty { display: block !important; color: var(--sb-muted); }

.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }

@keyframes mission-pulse { from { opacity: 0.35; transform: scaleX(0.45); } to { opacity: 1; transform: scaleX(1); } }

@container (max-width: 720px) {
  .mission-header { align-items: flex-start; }
  .mission-header-meta { max-width: 58%; }
  .mission-job { display: none; }
  .stage-track { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .stage-track li:nth-child(3) { border-right: 0; }
  .stage-track li:nth-child(-n+3) { border-bottom: 1px solid var(--sb-border); }
  .mission-grid, .decision-panel { grid-template-columns: 1fr; }
}

@container (max-width: 430px) {
  .mission-header { flex-direction: column; }
  .mission-header-meta { width: 100%; max-width: none; justify-content: flex-start; }
  .collapse-button { margin-left: auto; }
  .stage-track { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .stage-track li { border-bottom: 1px solid var(--sb-border); }
  .stage-track li:nth-child(2n) { border-right: 0; }
  .stage-track li:nth-last-child(-n+2) { border-bottom: 0; }
  .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .decision-actions { grid-template-columns: 1fr; }
}

@media (prefers-reduced-motion: reduce) {
  .loading-line { animation: none; }
  .mission-monitor *, .mission-monitor *::before, .mission-monitor *::after { scroll-behavior: auto !important; transition: none !important; }
}
</style>
