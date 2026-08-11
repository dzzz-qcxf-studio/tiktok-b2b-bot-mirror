<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'

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
import type {
  AcquisitionCandidate,
  AcquisitionCandidateListParams,
  AcquisitionCandidateDetailResponse,
  CandidateQualificationStatus,
  CandidateReviewAuditListResponse,
  PipelineDecisionCheckpoint,
  PipelineDecisionResolution,
} from '../types/pipeline'

const props = withDefaults(defineProps<{
  open: boolean
  jobId: string
  filter: AcquisitionCandidateListParams
  initialUserId?: number | null
  manualCheckpoint?: PipelineDecisionCheckpoint | null
}>(), {
  initialUserId: null,
  manualCheckpoint: null,
})

const emit = defineEmits<{
  (event: 'close'): void
  (event: 'candidate-updated', userId: number): void
  (event: 'review-complete', resolution: PipelineDecisionResolution): void
}>()

const { t } = useI18n()
const QUEUE_LIMIT = 10
const DETAIL_LIMIT = 5

const drawer = ref<HTMLElement | null>(null)
const queue = shallowRef<AcquisitionCandidate[]>([])
const detail = shallowRef<AcquisitionCandidateDetailResponse | null>(null)
const audits = shallowRef<CandidateReviewAuditListResponse | null>(null)
const selectedUserId = ref<number | null>(null)
const queueOffset = ref(0)
const evidenceOffset = ref(0)
const auditOffset = ref(0)
const queueTotal = ref(0)
const queueLoading = ref(false)
const detailLoading = ref(false)
const auditLoading = ref(false)
const queueError = ref(false)
const detailError = ref(false)
const auditError = ref(false)
const mutationError = ref(false)
const refreshError = ref(false)
const mutationPending = ref<string | null>(null)
const reason = ref('')
const labelsInput = ref('')
const reviewCompleteSubmitted = ref(false)

let sessionGeneration = 0
let queueRequestGeneration = 0
let detailRequestGeneration = 0
let auditRequestGeneration = 0
let checkpointRequestGeneration = 0
let queueController: AbortController | null = null
let detailController: AbortController | null = null
let auditController: AbortController | null = null
let returnFocus: HTMLElement | null = null

const selectedCandidate = computed(() => detail.value?.candidate ?? null)
const assessment = computed(() => detail.value?.latestAssessment ?? null)
const candidateEvidence = computed(() => detail.value?.evidence ?? null)
const manualCheckpointKey = computed(() => {
  const checkpoint = props.manualCheckpoint
  return checkpoint ? `${checkpoint.id}:${checkpoint.version}` : null
})
const filterKey = computed(() => JSON.stringify({
  discoveryStatus: props.filter.discoveryStatus ?? null,
  qualificationStatus: props.filter.qualificationStatus ?? null,
  keywordId: props.filter.keywordId ?? null,
  sourceType: props.filter.sourceType ?? null,
}))
const validManualCheckpoint = computed(() => {
  const checkpoint = props.manualCheckpoint
  if (
    !checkpoint
    || checkpoint.jobId !== props.jobId
    || checkpoint.status !== 'pending'
    || !(checkpoint.context.manualSession || checkpoint.kind === 'manual_review_session')
  ) return null
  return checkpoint
})
const isTerminalCandidate = computed(() => {
  const status = selectedCandidate.value?.qualificationStatus
  return status === 'qualified' || status === 'rejected'
})
const canApproveOrReject = computed(() => {
  const status = selectedCandidate.value?.qualificationStatus
  return status === 'manual_review' || status === 'need_enrichment'
})
const canRequestEnrichment = computed(() => (
  selectedCandidate.value?.qualificationStatus === 'manual_review'
))
const canCompleteEnrichment = computed(() => (
  selectedCandidate.value?.qualificationStatus === 'need_enrichment'
))

function abortReads() {
  queueController?.abort()
  detailController?.abort()
  auditController?.abort()
  queueController = null
  detailController = null
  auditController = null
}

function isSessionCurrent(generation: number, jobId: string) {
  return props.open && generation === sessionGeneration && jobId === props.jobId
}

function resetState() {
  queue.value = []
  detail.value = null
  audits.value = null
  selectedUserId.value = null
  queueOffset.value = 0
  evidenceOffset.value = 0
  auditOffset.value = 0
  queueTotal.value = 0
  queueLoading.value = false
  detailLoading.value = false
  auditLoading.value = false
  queueError.value = false
  detailError.value = false
  auditError.value = false
  mutationError.value = false
  refreshError.value = false
  mutationPending.value = null
  reason.value = ''
  labelsInput.value = ''
  reviewCompleteSubmitted.value = false
}

function beginSession() {
  abortReads()
  sessionGeneration += 1
  resetState()
  const active = document.activeElement
  if (active instanceof HTMLElement && active !== document.body) returnFocus = active
  const generation = sessionGeneration
  const jobId = props.jobId
  void loadQueue({
    generation,
    jobId,
    selectAfter: true,
    preferredUserId: props.initialUserId,
  })
  void nextTick(() => drawer.value?.focus())
}

function restorePreviousFocus() {
  const target = returnFocus
  returnFocus = null
  if (target?.isConnected) void nextTick(() => target.focus())
}

function deactivate(restoreFocus: boolean) {
  sessionGeneration += 1
  checkpointRequestGeneration += 1
  abortReads()
  mutationPending.value = null
  if (restoreFocus) restorePreviousFocus()
}

function requestClose() {
  deactivate(true)
  emit('close')
}

async function loadQueue(options: {
  generation?: number
  jobId?: string
  selectAfter?: boolean
  preferredUserId?: number | null
} = {}) {
  const generation = options.generation ?? sessionGeneration
  const jobId = options.jobId ?? props.jobId
  const requestGeneration = ++queueRequestGeneration
  queueController?.abort()
  const controller = new AbortController()
  queueController = controller
  queueLoading.value = true
  queueError.value = false
  try {
    const response = await listAcquisitionCandidates(
      jobId,
      {
        ...props.filter,
        limit: QUEUE_LIMIT,
        offset: queueOffset.value,
      },
      controller.signal,
    )
    if (
      !isSessionCurrent(generation, jobId)
      || requestGeneration !== queueRequestGeneration
      || controller.signal.aborted
    ) return false
    queue.value = response.data.items
    queueTotal.value = response.data.total
    if (options.selectAfter) {
      const preferred = options.preferredUserId
      const target = preferred ?? response.data.items[0]?.userId ?? null
      if (target !== null) selectCandidate(target)
      else {
        selectedUserId.value = null
        detail.value = null
        audits.value = null
      }
    }
    return true
  } catch {
    if (
      isSessionCurrent(generation, jobId)
      && requestGeneration === queueRequestGeneration
      && !controller.signal.aborted
    ) queueError.value = true
    return false
  } finally {
    if (requestGeneration === queueRequestGeneration) queueLoading.value = false
  }
}

function selectCandidate(userId: number) {
  if (!Number.isInteger(userId) || userId <= 0) return
  selectedUserId.value = userId
  detail.value = null
  audits.value = null
  evidenceOffset.value = 0
  auditOffset.value = 0
  mutationError.value = false
  refreshError.value = false
  reason.value = ''
  void Promise.all([loadDetail(userId), loadAudits(userId)])
}

async function loadDetail(userId: number) {
  const generation = sessionGeneration
  const jobId = props.jobId
  const requestGeneration = ++detailRequestGeneration
  detailController?.abort()
  const controller = new AbortController()
  detailController = controller
  detailLoading.value = true
  detailError.value = false
  try {
    const response = await getAcquisitionCandidate(
      jobId,
      userId,
      { limit: DETAIL_LIMIT, offset: evidenceOffset.value },
      controller.signal,
    )
    if (
      !isSessionCurrent(generation, jobId)
      || requestGeneration !== detailRequestGeneration
      || controller.signal.aborted
      || selectedUserId.value !== userId
      || response.data.candidate.jobId !== jobId
      || response.data.candidate.userId !== userId
    ) return false
    detail.value = response.data
    labelsInput.value = response.data.candidate.labels.join(', ')
    return true
  } catch {
    if (
      isSessionCurrent(generation, jobId)
      && requestGeneration === detailRequestGeneration
      && !controller.signal.aborted
    ) detailError.value = true
    return false
  } finally {
    if (requestGeneration === detailRequestGeneration) detailLoading.value = false
  }
}

async function loadAudits(userId: number) {
  const generation = sessionGeneration
  const jobId = props.jobId
  const requestGeneration = ++auditRequestGeneration
  auditController?.abort()
  const controller = new AbortController()
  auditController = controller
  auditLoading.value = true
  auditError.value = false
  try {
    const response = await listAcquisitionCandidateAudits(
      jobId,
      userId,
      { limit: DETAIL_LIMIT, offset: auditOffset.value },
      controller.signal,
    )
    if (
      !isSessionCurrent(generation, jobId)
      || requestGeneration !== auditRequestGeneration
      || controller.signal.aborted
      || selectedUserId.value !== userId
      || response.data.items.some(item => item.jobId !== jobId || item.userId !== userId)
    ) return false
    audits.value = response.data
    return true
  } catch {
    if (
      isSessionCurrent(generation, jobId)
      && requestGeneration === auditRequestGeneration
      && !controller.signal.aborted
    ) auditError.value = true
    return false
  } finally {
    if (requestGeneration === auditRequestGeneration) auditLoading.value = false
  }
}

async function refreshAfterMutation(userId: number, generation: number, jobId: string) {
  if (!isSessionCurrent(generation, jobId) || selectedUserId.value !== userId) return false
  detail.value = null
  audits.value = null
  refreshError.value = false
  const results = await Promise.all([
    loadQueue({ generation, jobId, selectAfter: false }),
    loadDetail(userId),
    loadAudits(userId),
  ])
  if (!isSessionCurrent(generation, jobId) || selectedUserId.value !== userId) return false
  if (results.some(success => !success)) {
    detail.value = null
    audits.value = null
    refreshError.value = true
    return false
  }
  return true
}

function reviewPayload() {
  const candidate = selectedCandidate.value
  if (!candidate) return null
  const normalizedReason = reason.value.trim()
  return {
    reviewVersion: candidate.reviewVersion,
    ...(normalizedReason ? { reason: normalizedReason } : {}),
  }
}

async function performCandidateAction(
  action: 'approve' | 'reject' | 'request-enrichment' | 'complete-enrichment',
) {
  const candidate = selectedCandidate.value
  const payload = reviewPayload()
  if (!candidate || !payload || mutationPending.value || isTerminalCandidate.value) return
  if (action === 'request-enrichment' && !canRequestEnrichment.value) return
  if (action === 'complete-enrichment' && !canCompleteEnrichment.value) return
  if ((action === 'approve' || action === 'reject') && !canApproveOrReject.value) return

  const generation = sessionGeneration
  const jobId = props.jobId
  const userId = candidate.userId
  mutationPending.value = action
  mutationError.value = false
  refreshError.value = false
  try {
    if (action === 'approve') await approveAcquisitionCandidate(jobId, userId, payload)
    else if (action === 'reject') await rejectAcquisitionCandidate(jobId, userId, payload)
    else if (action === 'request-enrichment') {
      await requestAcquisitionCandidateEnrichment(jobId, userId, payload)
    } else {
      await completeAcquisitionCandidateEnrichment(jobId, userId, payload)
    }
    if (!isSessionCurrent(generation, jobId) || selectedUserId.value !== userId) return
    const refreshed = await refreshAfterMutation(userId, generation, jobId)
    if (!refreshed) return
    if (!isSessionCurrent(generation, jobId)) return
    emit('candidate-updated', userId)
  } catch {
    if (isSessionCurrent(generation, jobId) && selectedUserId.value === userId) {
      mutationError.value = true
    }
  } finally {
    if (isSessionCurrent(generation, jobId)) mutationPending.value = null
  }
}

function normalizedLabels() {
  return [...new Set(
    labelsInput.value
      .split(',')
      .map(value => value.trim())
      .filter(Boolean),
  )].slice(0, 50)
}

async function saveLabels() {
  const candidate = selectedCandidate.value
  if (!candidate || mutationPending.value || isTerminalCandidate.value) return
  const generation = sessionGeneration
  const jobId = props.jobId
  const userId = candidate.userId
  mutationPending.value = 'save-labels'
  mutationError.value = false
  refreshError.value = false
  try {
    await updateAcquisitionCandidateLabels(jobId, userId, {
      reviewVersion: candidate.reviewVersion,
      labels: normalizedLabels(),
    })
    if (!isSessionCurrent(generation, jobId) || selectedUserId.value !== userId) return
    const refreshed = await refreshAfterMutation(userId, generation, jobId)
    if (refreshed && isSessionCurrent(generation, jobId)) emit('candidate-updated', userId)
  } catch {
    if (isSessionCurrent(generation, jobId) && selectedUserId.value === userId) {
      mutationError.value = true
    }
  } finally {
    if (isSessionCurrent(generation, jobId)) mutationPending.value = null
  }
}

async function completeManualReview() {
  const checkpoint = validManualCheckpoint.value
  if (!checkpoint || mutationPending.value || reviewCompleteSubmitted.value) return
  const generation = sessionGeneration
  const jobId = props.jobId
  const checkpointId = checkpoint.id
  const checkpointVersion = checkpoint.version
  const requestGeneration = ++checkpointRequestGeneration
  mutationPending.value = 'review-complete'
  reviewCompleteSubmitted.value = true
  mutationError.value = false
  try {
    const response = await completePipelineReviewCheckpoint(jobId, checkpointId, {
      version: checkpointVersion,
    })
    const current = validManualCheckpoint.value
    if (
      !isSessionCurrent(generation, jobId)
      || requestGeneration !== checkpointRequestGeneration
      || current?.id !== checkpointId
      || current.version !== checkpointVersion
      || response.data.resolution.checkpointId !== checkpointId
      || response.data.resolution.jobId !== jobId
    ) return
    emit('review-complete', response.data.resolution)
  } catch {
    const current = validManualCheckpoint.value
    if (
      isSessionCurrent(generation, jobId)
      && requestGeneration === checkpointRequestGeneration
      && current?.id === checkpointId
      && current.version === checkpointVersion
    ) {
      reviewCompleteSubmitted.value = false
      mutationError.value = true
    }
  } finally {
    if (
      isSessionCurrent(generation, jobId)
      && requestGeneration === checkpointRequestGeneration
    ) mutationPending.value = null
  }
}

function changeQueuePage(direction: -1 | 1) {
  const nextOffset = Math.max(0, queueOffset.value + direction * QUEUE_LIMIT)
  if (nextOffset === queueOffset.value || nextOffset >= queueTotal.value) return
  queueOffset.value = nextOffset
  void loadQueue({ selectAfter: true, preferredUserId: null })
}

function changeEvidencePage(direction: -1 | 1) {
  const userId = selectedUserId.value
  const total = detail.value?.evidence.total ?? 0
  const nextOffset = Math.max(0, evidenceOffset.value + direction * DETAIL_LIMIT)
  if (!userId || nextOffset === evidenceOffset.value || nextOffset >= total) return
  evidenceOffset.value = nextOffset
  void loadDetail(userId)
}

function changeAuditPage(direction: -1 | 1) {
  const userId = selectedUserId.value
  const total = audits.value?.total ?? 0
  const nextOffset = Math.max(0, auditOffset.value + direction * DETAIL_LIMIT)
  if (!userId || nextOffset === auditOffset.value || nextOffset >= total) return
  auditOffset.value = nextOffset
  void loadAudits(userId)
}

function statusLabel(status: CandidateQualificationStatus) {
  return t(`pipeline.reviewWorkbench.status.${status}`)
}

function handleKeydown(event: KeyboardEvent) {
  if (props.open && event.key === 'Escape') requestClose()
}

watch(
  () => [props.open, props.jobId, filterKey.value, props.initialUserId] as const,
  ([open]) => {
    if (open) beginSession()
    else deactivate(true)
  },
  { immediate: true },
)

watch(manualCheckpointKey, () => {
  checkpointRequestGeneration += 1
  reviewCompleteSubmitted.value = false
  if (mutationPending.value === 'review-complete') mutationPending.value = null
  mutationError.value = false
})

onMounted(() => window.addEventListener('keydown', handleKeydown))
onBeforeUnmount(() => {
  window.removeEventListener('keydown', handleKeydown)
  deactivate(true)
})
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="review-backdrop" @click.self="requestClose">
      <section
        ref="drawer"
        class="review-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="review-workbench-title"
        tabindex="-1"
      >
        <header class="review-header">
          <div>
            <p class="review-eyebrow">HERMES REVIEW</p>
            <h2 id="review-workbench-title">{{ t('pipeline.reviewWorkbench.title') }}</h2>
            <p>{{ t('pipeline.reviewWorkbench.jobContext', { jobId }) }}</p>
          </div>
          <button type="button" class="review-button is-quiet" data-testid="review-close" @click="requestClose">
            {{ t('common.close') }}
          </button>
        </header>

        <div class="review-layout">
          <aside class="candidate-queue" :aria-label="t('pipeline.reviewWorkbench.queue')">
            <div class="section-title">
              <span>{{ t('pipeline.reviewWorkbench.queue') }}</span>
              <span class="mono">{{ queueTotal }}</span>
            </div>
            <p v-if="queueLoading" class="review-state">{{ t('common.loading') }}</p>
            <p v-else-if="queueError" class="review-state is-error" role="alert">
              {{ t('pipeline.reviewWorkbench.queueError') }}
            </p>
            <p v-else-if="!queue.length" class="review-state">{{ t('pipeline.reviewWorkbench.queueEmpty') }}</p>
            <div v-else class="queue-list">
              <button
                v-for="item in queue"
                :key="item.userId"
                type="button"
                class="queue-item"
                :class="{ 'is-active': item.userId === selectedUserId }"
                :aria-current="item.userId === selectedUserId ? 'true' : undefined"
                @click="selectCandidate(item.userId)"
              >
                <strong>{{ item.nickname || item.username }}</strong>
                <span>@{{ item.username }}</span>
                <small>{{ statusLabel(item.qualificationStatus) }} · {{ item.matchScore ?? '—' }}</small>
              </button>
            </div>
            <div class="pager">
              <button type="button" class="review-button is-quiet" :disabled="queueOffset === 0" @click="changeQueuePage(-1)">
                {{ t('common.prev') }}
              </button>
              <span class="mono">{{ queueOffset + 1 }}–{{ Math.min(queueOffset + QUEUE_LIMIT, queueTotal) }}</span>
              <button
                data-testid="queue-next"
                type="button"
                class="review-button is-quiet"
                :disabled="queueOffset + QUEUE_LIMIT >= queueTotal"
                @click="changeQueuePage(1)"
              >
                {{ t('common.next') }}
              </button>
            </div>
          </aside>

          <main class="candidate-detail">
            <p v-if="refreshError" data-testid="refresh-error" class="review-state is-error" role="alert">
              {{ t('pipeline.reviewWorkbench.refreshError') }}
            </p>
            <p v-else-if="detailLoading && !detail" class="review-state">{{ t('common.loading') }}</p>
            <p v-else-if="detailError && !detail" class="review-state is-error" role="alert">
              {{ t('pipeline.reviewWorkbench.detailError') }}
            </p>
            <p v-else-if="!selectedCandidate" class="review-state">{{ t('pipeline.reviewWorkbench.selectCandidate') }}</p>

            <template v-else>
              <section class="candidate-identity">
                <div>
                  <span class="status-chip" :data-status="selectedCandidate.qualificationStatus">
                    {{ statusLabel(selectedCandidate.qualificationStatus) }}
                  </span>
                  <h3>{{ selectedCandidate.nickname || selectedCandidate.username }}</h3>
                  <p>@{{ selectedCandidate.username }} · {{ selectedCandidate.country || '—' }}</p>
                </div>
                <a
                  v-if="selectedCandidate.profileUrl"
                  class="profile-link"
                  :href="selectedCandidate.profileUrl"
                  target="_blank"
                  rel="noopener noreferrer"
                >{{ selectedCandidate.profileUrl }}</a>
              </section>

              <p class="candidate-bio">{{ selectedCandidate.bio || t('pipeline.reviewWorkbench.noBio') }}</p>

              <section class="score-grid" :aria-label="t('pipeline.reviewWorkbench.scores')">
                <article><span>{{ t('pipeline.reviewWorkbench.matchScore') }}</span><strong>{{ selectedCandidate.matchScore ?? '—' }}</strong></article>
                <article><span>{{ t('pipeline.reviewWorkbench.confidenceScore') }}</span><strong>{{ selectedCandidate.confidenceScore ?? '—' }}</strong></article>
                <article><span>{{ t('pipeline.reviewWorkbench.evidenceCount') }}</span><strong>{{ selectedCandidate.evidenceCount }}</strong></article>
              </section>

              <section class="detail-section">
                <div class="section-title">{{ t('pipeline.reviewWorkbench.labels') }}</div>
                <div class="tag-list">
                  <span v-for="label in selectedCandidate.labels" :key="label" class="review-tag">{{ label }}</span>
                  <span v-if="!selectedCandidate.labels.length" class="muted">{{ t('pipeline.reviewWorkbench.none') }}</span>
                </div>
                <div v-if="!isTerminalCandidate" class="label-editor">
                  <input data-testid="labels-input" v-model="labelsInput" :placeholder="t('pipeline.reviewWorkbench.labelsPlaceholder')">
                  <button
                    type="button"
                    class="review-button"
                    data-action="save-labels"
                    :disabled="Boolean(mutationPending)"
                    @click="saveLabels"
                  >{{ t('pipeline.reviewWorkbench.saveLabels') }}</button>
                </div>
              </section>

              <section class="detail-section">
                <div class="section-title">{{ t('pipeline.reviewWorkbench.missingFields') }}</div>
                <div class="tag-list">
                  <span v-for="field in assessment?.missingFields || []" :key="field" class="review-tag is-warning">{{ field }}</span>
                  <span v-if="!assessment?.missingFields.length" class="muted">{{ t('pipeline.reviewWorkbench.none') }}</span>
                </div>
              </section>

              <section class="detail-section">
                <div class="section-title">
                  <span>{{ t('pipeline.reviewWorkbench.evidence') }}</span>
                  <span class="mono">{{ candidateEvidence?.total ?? 0 }}</span>
                </div>
                <div class="evidence-list">
                  <article v-for="item in candidateEvidence?.items || []" :key="item.id" class="evidence-card">
                    <header><strong>{{ item.sourceType }}</strong><span>{{ item.keywordText || '—' }}</span></header>
                    <p>{{ item.translatedText || item.rawText }}</p>
                    <nav :aria-label="t('pipeline.reviewWorkbench.sourceChain')">
                      <a v-if="item.authorUrl" :href="item.authorUrl" target="_blank" rel="noopener noreferrer">{{ t('pipeline.reviewWorkbench.authorSource') }}</a>
                      <a v-if="item.videoUrl" :href="item.videoUrl" target="_blank" rel="noopener noreferrer">{{ t('pipeline.reviewWorkbench.videoSource') }}</a>
                      <a v-if="item.commentUrl" :href="item.commentUrl" target="_blank" rel="noopener noreferrer">{{ t('pipeline.reviewWorkbench.commentSource') }}</a>
                    </nav>
                  </article>
                </div>
                <div class="pager">
                  <button type="button" class="review-button is-quiet" :disabled="evidenceOffset === 0" @click="changeEvidencePage(-1)">{{ t('common.prev') }}</button>
                  <span class="mono">{{ evidenceOffset + 1 }}–{{ Math.min(evidenceOffset + DETAIL_LIMIT, candidateEvidence?.total ?? 0) }}</span>
                  <button
                    data-testid="evidence-next"
                    type="button"
                    class="review-button is-quiet"
                    :disabled="evidenceOffset + DETAIL_LIMIT >= (candidateEvidence?.total ?? 0)"
                    @click="changeEvidencePage(1)"
                  >{{ t('common.next') }}</button>
                </div>
              </section>

              <section class="detail-section">
                <div class="section-title">
                  <span>{{ t('pipeline.reviewWorkbench.audit') }}</span>
                  <span class="mono">{{ audits?.total ?? 0 }}</span>
                </div>
                <p v-if="auditLoading && !audits" class="muted">{{ t('common.loading') }}</p>
                <p v-else-if="auditError" class="review-state is-error" role="alert">{{ t('pipeline.reviewWorkbench.auditError') }}</p>
                <ol v-else class="audit-list">
                  <li v-for="item in audits?.items || []" :key="item.id">
                    <strong>{{ item.action }}</strong>
                    <span>{{ item.beforeStatus }} → {{ item.afterStatus }}</span>
                    <p>{{ item.reason || t('pipeline.reviewWorkbench.noReason') }} · {{ item.operator }}</p>
                  </li>
                </ol>
                <div class="pager">
                  <button type="button" class="review-button is-quiet" :disabled="auditOffset === 0" @click="changeAuditPage(-1)">{{ t('common.prev') }}</button>
                  <span class="mono">{{ auditOffset + 1 }}–{{ Math.min(auditOffset + DETAIL_LIMIT, audits?.total ?? 0) }}</span>
                  <button
                    data-testid="audit-next"
                    type="button"
                    class="review-button is-quiet"
                    :disabled="auditOffset + DETAIL_LIMIT >= (audits?.total ?? 0)"
                    @click="changeAuditPage(1)"
                  >{{ t('common.next') }}</button>
                </div>
              </section>

              <section class="review-actions" aria-labelledby="candidate-review-actions-title">
                <h4 id="candidate-review-actions-title">{{ t('pipeline.reviewWorkbench.actions') }}</h4>
                <textarea v-model="reason" :placeholder="t('pipeline.reviewWorkbench.reasonPlaceholder')"></textarea>
                <p v-if="mutationError" class="review-state is-error" role="alert">
                  {{ t('pipeline.reviewWorkbench.mutationError') }}
                </p>
                <p v-if="isTerminalCandidate" data-testid="review-terminal" class="review-state">
                  {{ t('pipeline.reviewWorkbench.terminal') }}
                </p>
                <div v-else class="action-grid">
                  <button
                    v-if="canApproveOrReject"
                    type="button"
                    class="review-button is-success"
                    data-action="approve"
                    :disabled="Boolean(mutationPending)"
                    @click="performCandidateAction('approve')"
                  >{{ t('pipeline.reviewWorkbench.approve') }}</button>
                  <button
                    v-if="canApproveOrReject"
                    type="button"
                    class="review-button is-danger"
                    data-action="reject"
                    :disabled="Boolean(mutationPending)"
                    @click="performCandidateAction('reject')"
                  >{{ t('pipeline.reviewWorkbench.reject') }}</button>
                  <button
                    v-if="canRequestEnrichment"
                    type="button"
                    class="review-button"
                    data-action="request-enrichment"
                    :disabled="Boolean(mutationPending)"
                    @click="performCandidateAction('request-enrichment')"
                  >{{ t('pipeline.reviewWorkbench.requestEnrichment') }}</button>
                  <button
                    v-if="canCompleteEnrichment"
                    type="button"
                    class="review-button"
                    data-action="complete-enrichment"
                    :disabled="Boolean(mutationPending)"
                    @click="performCandidateAction('complete-enrichment')"
                  >{{ t('pipeline.reviewWorkbench.completeEnrichment') }}</button>
                </div>
              </section>
            </template>
          </main>
        </div>

        <footer v-if="validManualCheckpoint" class="review-footer">
          <p>{{ t('pipeline.reviewWorkbench.manualSessionHint') }}</p>
          <button
            type="button"
            class="review-button is-primary"
            data-action="review-complete"
            :disabled="Boolean(mutationPending) || reviewCompleteSubmitted"
            @click="completeManualReview"
          >{{ t('pipeline.reviewWorkbench.reviewComplete') }}</button>
        </footer>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.review-backdrop { position: fixed; inset: 0; z-index: 80; display: flex; justify-content: flex-end; background: oklch(8% 0.01 280 / .62); }
.review-drawer { width: min(1120px, 96vw); height: 100%; display: flex; flex-direction: column; background: var(--surface); box-shadow: var(--shadow-pop); outline: none; }
.review-drawer:focus-visible { box-shadow: inset 0 0 0 2px var(--brand), var(--shadow-pop); }
.review-header { min-height: 78px; padding: 14px 18px; display: flex; align-items: center; justify-content: space-between; gap: 16px; border-bottom: 1px solid var(--border); }
.review-eyebrow { margin: 0 0 3px; color: var(--brand); font: 700 10px/1 var(--font-mono); letter-spacing: .13em; }
.review-header h2 { margin: 0; color: var(--fg); font-size: 18px; }
.review-header p:last-child { margin: 3px 0 0; color: var(--muted); font: 11px/1.3 var(--font-mono); }
.review-layout { min-height: 0; flex: 1; display: grid; grid-template-columns: 280px minmax(0, 1fr); }
.candidate-queue { min-height: 0; padding: 14px; display: flex; flex-direction: column; border-right: 1px solid var(--border); background: var(--bg-sub); }
.candidate-detail { min-width: 0; padding: 18px; overflow: auto; }
.section-title { min-height: 28px; display: flex; align-items: center; justify-content: space-between; gap: 12px; color: var(--fg-2); font: 700 10.5px/1.2 var(--font-mono); letter-spacing: .07em; text-transform: uppercase; }
.queue-list { min-height: 0; flex: 1; overflow: auto; }
.queue-item { width: 100%; min-height: 68px; padding: 10px; display: block; border: 1px solid transparent; border-radius: 8px; background: transparent; text-align: left; }
.queue-item + .queue-item { margin-top: 5px; }
.queue-item:hover, .queue-item:focus-visible { border-color: var(--border-strong); outline: none; background: var(--surface); }
.queue-item.is-active { border-color: var(--brand); background: var(--surface); box-shadow: var(--shadow-1); }
.queue-item strong, .queue-item span, .queue-item small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.queue-item strong { color: var(--fg); font-size: 13px; }
.queue-item span { color: var(--fg-2); font-size: 11.5px; }
.queue-item small { margin-top: 4px; color: var(--muted); font: 10px/1.2 var(--font-mono); }
.candidate-identity { display: flex; justify-content: space-between; gap: 20px; }
.candidate-identity h3 { margin: 8px 0 2px; color: var(--fg); font-size: 20px; }
.candidate-identity p { margin: 0; color: var(--muted); }
.profile-link { max-width: 48%; color: var(--brand-deep); font: 11px/1.5 var(--font-mono); overflow-wrap: anywhere; }
.status-chip, .review-tag { min-height: 24px; padding: 3px 8px; display: inline-flex; align-items: center; border: 1px solid var(--border); border-radius: var(--chip-radius); background: var(--bg-sub); font-size: 11px; }
.status-chip[data-status="qualified"] { color: oklch(42% .16 150); background: var(--ok-soft); }
.status-chip[data-status="rejected"] { color: var(--err); background: var(--err-soft); }
.status-chip[data-status="manual_review"], .status-chip[data-status="need_enrichment"] { color: oklch(45% .16 75); background: var(--warn-soft); }
.candidate-bio { margin: 14px 0; padding: 12px; border-radius: 8px; background: var(--bg-sub); color: var(--fg-2); text-wrap: pretty; }
.score-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
.score-grid article { padding: 10px 12px; border: 1px solid var(--border); border-radius: 8px; }
.score-grid span { display: block; color: var(--muted); font-size: 11px; }
.score-grid strong { display: block; margin-top: 3px; color: var(--fg); font: 700 20px/1.2 var(--font-mono); }
.detail-section, .review-actions { margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--border); }
.tag-list { display: flex; flex-wrap: wrap; gap: 6px; }
.review-tag.is-warning { color: oklch(45% .16 75); background: var(--warn-soft); }
.label-editor { margin-top: 9px; display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px; }
.label-editor input, .review-actions textarea { width: 100%; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); color: var(--fg); }
.label-editor input { min-height: 44px; padding: 8px 10px; }
.review-actions textarea { min-height: 70px; padding: 10px; resize: vertical; }
.label-editor input:focus, .review-actions textarea:focus { outline: 2px solid var(--brand); outline-offset: 1px; }
.evidence-list { display: grid; gap: 8px; }
.evidence-card { padding: 11px; border: 1px solid var(--border); border-radius: 8px; background: var(--bg-sub); }
.evidence-card header { display: flex; justify-content: space-between; gap: 12px; color: var(--fg); font-size: 11.5px; }
.evidence-card header span { color: var(--muted); }
.evidence-card p { margin: 7px 0; color: var(--fg-2); }
.evidence-card nav { display: flex; flex-wrap: wrap; gap: 12px; }
.evidence-card a { color: var(--brand-deep); font-size: 11px; text-decoration: underline; text-underline-offset: 2px; }
.audit-list { margin: 0; padding: 0; list-style: none; }
.audit-list li { padding: 9px 0; border-top: 1px solid var(--border); }
.audit-list strong { color: var(--fg); }
.audit-list span { margin-left: 10px; color: var(--muted); font-family: var(--font-mono); font-size: 10.5px; }
.audit-list p { margin: 3px 0 0; color: var(--fg-2); }
.pager { min-height: 52px; margin-top: 8px; display: flex; align-items: center; justify-content: space-between; gap: 8px; color: var(--muted); font-size: 10.5px; }
.review-actions h4 { margin: 0 0 8px; color: var(--fg); }
.action-grid { margin-top: 9px; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
.review-button { min-height: 44px; padding: 8px 13px; border: 1px solid var(--border-strong); border-radius: 8px; background: var(--surface); color: var(--fg); font-weight: 650; }
.review-button:hover:not(:disabled), .review-button:focus-visible { border-color: var(--brand); outline: none; }
.review-button:focus-visible { box-shadow: 0 0 0 2px var(--brand-soft); }
.review-button:disabled { cursor: not-allowed; opacity: .48; }
.review-button.is-primary { color: #fff; border-color: var(--brand); background: var(--brand); }
.review-button.is-success { color: oklch(38% .15 150); border-color: oklch(82% .08 150); background: var(--ok-soft); }
.review-button.is-danger { color: var(--err); border-color: oklch(84% .08 25); background: var(--err-soft); }
.review-button.is-quiet { background: var(--bg-sub); }
.review-state { margin: 8px 0; padding: 10px; border-radius: 8px; background: var(--bg-sub); color: var(--muted); }
.review-state.is-error { color: var(--err); background: var(--err-soft); }
.review-footer { min-height: 70px; padding: 12px 18px; display: flex; align-items: center; justify-content: flex-end; gap: 18px; border-top: 1px solid var(--border); }
.review-footer p { margin: 0; color: var(--muted); }
.muted { color: var(--muted); }
.mono { font-family: var(--font-mono); font-variant-numeric: tabular-nums; }

@media (max-width: 760px) {
  .review-drawer { width: 100%; }
  .review-layout { grid-template-columns: 1fr; overflow: auto; }
  .candidate-queue { min-height: 240px; max-height: 42vh; border-right: 0; border-bottom: 1px solid var(--border); }
  .candidate-detail { overflow: visible; }
  .candidate-identity { flex-direction: column; }
  .profile-link { max-width: none; }
  .action-grid { grid-template-columns: 1fr; }
  .review-footer { align-items: stretch; flex-direction: column; }
}

@media (max-width: 430px) {
  .review-header, .candidate-detail { padding: 14px; }
  .score-grid { grid-template-columns: 1fr 1fr; }
  .label-editor { grid-template-columns: 1fr; }
}

@media (prefers-reduced-motion: reduce) {
  .review-drawer, .review-button, .queue-item { transition: none !important; animation: none !important; }
}
</style>
