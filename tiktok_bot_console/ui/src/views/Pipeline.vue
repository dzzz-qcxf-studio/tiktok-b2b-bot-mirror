<template>
  <div class="page pipeline-page">
    <div class="page-head">
      <div>
        <div class="eyebrow">{{ $t('pipeline.controlCenter') }}</div>
        <h1>{{ $t('pipeline.title') }}</h1>
        <p>{{ $t('pipeline.unifiedSubtitle') }}</p>
      </div>
      <button class="btn" @click="$router.push('/config-pipeline')">
        {{ $t('pipeline.scheduleSettings') }}
      </button>
    </div>

    <AcquisitionJobCreator
      class="acquisition-creator-shell"
      @accounts-loaded="handleAccountsLoaded"
      @created="handleAcquisitionCreated"
    />
    <div class="console-grid">
      <section class="card history-card">
        <div class="card-hd">
          <div>
            <h3>{{ $t('pipeline.historyTitle') }}</h3>
            <span class="hint">{{ $t('pipeline.historyHint') }}</span>
          </div>
          <button
            class="icon-action"
            type="button"
            :aria-label="$t('pipeline.refresh')"
            :disabled="historyLoading"
            @click="() => refreshJobs()"
          >
            <span :class="{ spinning: historyLoading }">↻</span>
          </button>
        </div>

        <div v-if="historyLoading && jobs.length === 0" class="state-panel">
          <span class="spinner dark"></span>
          <p>{{ $t('pipeline.loadingHistory') }}</p>
        </div>
        <div v-else-if="historyError && jobs.length === 0" class="state-panel error">
          <strong>{{ $t('pipeline.historyErrorTitle') }}</strong>
          <p>{{ historyError }}</p>
          <button class="btn sm" type="button" @click="() => refreshJobs()">{{ $t('common.retry') }}</button>
        </div>
        <div v-else-if="jobs.length === 0" class="state-panel">
          <span class="empty-mark">00</span>
          <strong>{{ $t('pipeline.historyEmpty') }}</strong>
          <p>{{ $t('pipeline.historyEmptyHint') }}</p>
        </div>
        <div v-else class="job-list">
          <button
            v-for="job in jobs"
            :key="job.id"
            type="button"
            :class="['job-item', { selected: selectedJobId === job.id }]"
            @click="selectJob(job)"
          >
            <span class="job-item-top">
              <span :class="['platform-badge', job.platform]">
                {{ job.platform === 'tiktok' ? 'TT' : 'DY' }}
              </span>
              <span :class="['status-badge', statusClass(job.status)]">
                <span class="status-dot"></span>
                {{ $t(`pipeline.jobStatus.${job.status}`) }}
              </span>
              <time>{{ formatDate(job.createdAt || job.queuedAt) }}</time>
            </span>
            <span class="job-id mono">{{ shortJobId(job.id) }}</span>
            <span class="job-item-meta">
              {{ $t(`pipeline.trigger.${job.triggerType}`) }}
              · {{ job.requestedStages.length }} {{ $t('pipeline.stageUnit') }}
            </span>
          </button>
        </div>

        <div class="history-footer">
          <span>{{ $t('pipeline.historyCount', { from: historyFrom, to: historyTo, total: historyTotal }) }}</span>
          <div>
            <button class="btn sm" type="button" :disabled="historyOffset === 0 || historyLoading" @click="previousPage">
              {{ $t('common.prev') }}
            </button>
            <button class="btn sm" type="button" :disabled="historyOffset + PAGE_SIZE >= historyTotal || historyLoading" @click="nextPage">
              {{ $t('common.next') }}
            </button>
          </div>
        </div>
      </section>

      <section class="card detail-card">
        <div v-if="detailLoading && !selectedJob" class="state-panel detail-state">
          <span class="spinner dark"></span>
          <p>{{ $t('pipeline.loadingDetail') }}</p>
        </div>
        <div v-else-if="detailError && !selectedJob" class="state-panel detail-state error">
          <strong>{{ $t('pipeline.detailErrorTitle') }}</strong>
          <p>{{ detailError }}</p>
        </div>
        <div v-else-if="!selectedJob" class="state-panel detail-state">
          <span class="empty-mark">ID</span>
          <strong>{{ $t('pipeline.selectJobTitle') }}</strong>
          <p>{{ $t('pipeline.selectJobHint') }}</p>
        </div>
        <template v-else>
          <div class="detail-head">
            <div>
              <div class="detail-title-row">
                <span :class="['platform-badge large', selectedJob.platform]">
                  {{ selectedJob.platform === 'tiktok' ? 'TT' : 'DY' }}
                </span>
                <div>
                  <span class="detail-kicker">{{ $t('pipeline.jobDetail') }}</span>
                  <h2 class="mono">{{ selectedJob.id }}</h2>
                </div>
              </div>
              <div class="detail-status-row">
                <span :class="['status-badge', 'large', statusClass(selectedJob.status)]">
                  <span class="status-dot"></span>
                  {{ $t(`pipeline.jobStatus.${selectedJob.status}`) }}
                </span>
                <span v-if="selectedJob.currentStage">
                  {{ $t('pipeline.currentStage') }} · {{ $t(`pipeline.${selectedJob.currentStage}`) }}
                </span>
              </div>
            </div>
            <div class="detail-actions">
              <button
                v-if="isSelectedAcquisitionJob"
                class="btn primary"
                type="button"
                data-testid="open-candidate-review"
                @click="openCandidateReview(selectedJob.id, { qualificationStatus: 'manual_review' })"
              >
                {{ $t('pipeline.reviewWorkbench.openQueue') }}
              </button>
              <button
                v-if="isSelectedAcquisitionJob && selectedJob.requestedStages.includes('strategy')"
                class="btn primary"
                type="button"
                data-testid="open-strategy-review-header"
                @click="openStrategyReview(selectedJob.id)"
              >
                {{ $t('pipeline.strategyWorkbench.open') }}
              </button>
              <button
                v-if="canCancel"
                class="btn danger"
                type="button"
                :disabled="actionLoading"
                @click="cancelJob"
              >
                {{ $t('pipeline.cancelJob') }}
              </button>
              <button
                v-if="canRetry"
                class="btn primary"
                type="button"
                :disabled="actionLoading"
                @click="retryJob"
              >
                {{ $t('pipeline.retryJob') }}
              </button>
            </div>
          </div>

          <HermesMissionMonitor
            :key="selectedJob.id"
            :job-id="selectedJob.id"
            @open-review-workbench="checkpoint => openReviewWorkbench(selectedJob!.id, checkpoint)"
          />

          <div class="detail-meta">
            <div>
              <span>{{ $t('pipeline.platform') }}</span>
              <b>{{ selectedJob.platform === 'tiktok' ? 'TikTok' : $t('pipeline.douyin') }}</b>
            </div>
            <div>
              <span>{{ $t('pipeline.account') }}</span>
              <b>{{ accountLabel(selectedJob) }}</b>
            </div>
            <div>
              <span>{{ $t('pipeline.triggerLabel') }}</span>
              <b>{{ $t(`pipeline.trigger.${selectedJob.triggerType}`) }}</b>
            </div>
            <div>
              <span>{{ $t('pipeline.queuedAt') }}</span>
              <b class="mono">{{ formatDateTime(selectedJob.queuedAt || selectedJob.createdAt) }}</b>
            </div>
          </div>

          <div v-if="detailError" class="detail-notice error" role="alert">{{ detailError }}</div>
          <div v-if="selectedJob.errorSummary" class="detail-notice error" role="alert">
            <strong>{{ $t('pipeline.jobError') }}</strong>
            <span>{{ selectedJob.errorSummary }}</span>
          </div>

          <div class="stage-detail-head">
            <div>
              <h3>{{ $t('pipeline.stageProgress') }}</h3>
              <span>{{ stageProgressText }}</span>
            </div>
            <span v-if="detailLoading" class="spinner dark"></span>
          </div>

          <div class="stage-timeline">
            <article
              v-for="(stage, index) in detailStages"
              :key="stage.stage"
              :class="['stage-node', stage.status]"
            >
              <div class="stage-rail">
                <span class="stage-index">{{ String(index + 1).padStart(2, '0') }}</span>
                <span v-if="index < detailStages.length - 1" class="rail-line"></span>
              </div>
              <div class="stage-content">
                <div class="stage-content-head">
                  <div>
                    <h4>{{ $t(`pipeline.${stage.stage}`) }}</h4>
                    <p>{{ $t(`pipeline.${stage.stage}Short`) }}</p>
                  </div>
                  <span :class="['stage-status', stage.status]">
                    {{ $t(`pipeline.stageStatus.${stage.status}`) }}
                  </span>
                </div>
                <div class="stage-times">
                  <span>{{ $t('pipeline.startedAt') }} {{ formatDateTime(stage.startedAt) }}</span>
                  <span>{{ $t('pipeline.finishedAt') }} {{ formatDateTime(stage.finishedAt) }}</span>
                  <span v-if="stage.attempt > 1">{{ $t('pipeline.attempt', { n: stage.attempt }) }}</span>
                </div>
                <div v-if="stage.errorMessage" class="stage-error">
                  <code>{{ stage.errorMessage }}</code>
                </div>
                <StageDiscoveryResult
                  v-if="isSelectedAcquisitionJob && stage.stage === 'collect'"
                  :job-id="selectedJob.id"
                  :stage-status="stage.status"
                  :stage-result="stage.result"
                  :legacy="false"
                  :refresh-token="stageRefreshToken"
                  @filter-candidates="filter => openCandidateReview(selectedJob!.id, filter)"
                />
                <StageQualificationResult
                  v-else-if="isSelectedAcquisitionJob && stage.stage === 'filter'"
                  :job-id="selectedJob.id"
                  :stage-status="stage.status"
                  :stage-result="stage.result"
                  :legacy="false"
                  :refresh-token="stageRefreshToken"
                  @filter-candidates="filter => openCandidateReview(selectedJob!.id, filter)"
                />
                <StageStrategyResult
                  v-else-if="isSelectedAcquisitionJob && stage.stage === 'strategy'"
                  :job-id="selectedJob.id"
                  :stage-status="stage.status"
                  :legacy="false"
                  :refresh-token="stageRefreshToken"
                  @open-workbench="openStrategyReview"
                />
                <p v-else-if="!isSelectedAcquisitionJob && hasResult(stage.result)" class="legacy-stage-summary">
                  {{ formatLegacySummary(stage.result) }}
                </p>
              </div>
            </article>
          </div>

          <details v-if="diagnosticStages.length" class="technical-diagnostics">
            <summary>{{ $t('pipeline.technicalDiagnostics') }}</summary>
            <section v-for="stage in diagnosticStages" :key="stage.stage">
              <h4>{{ $t(`pipeline.${stage.stage}`) }}</h4>
              <pre>{{ formatResult(stage.result) }}</pre>
            </section>
          </details>
        </template>
      </section>
    </div>

    <CandidateReviewDrawer
      v-if="reviewDrawerJobId"
      :key="reviewDrawerJobId"
      :open="reviewDrawerOpen"
      :job-id="reviewDrawerJobId"
      :filter="reviewFilter"
      :manual-checkpoint="reviewManualCheckpoint"
      @close="closeReviewDrawer"
      @candidate-updated="handleReviewDataChanged(reviewDrawerJobId)"
      @review-complete="handleReviewComplete(reviewDrawerJobId)"
    />
    <StrategyReviewDrawer
      v-if="strategyDrawerJobId"
      :key="strategyDrawerJobId"
      :open="strategyDrawerOpen"
      :job-id="strategyDrawerJobId"
      :manual-checkpoint="strategyManualCheckpoint"
      @close="closeStrategyDrawer"
      @strategy-updated="handleStrategyDataChanged(strategyDrawerJobId)"
      @review-complete="handleStrategyReviewComplete(strategyDrawerJobId)"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useI18n } from 'vue-i18n'
import {
  cancelPipelineJob,
  getPipelineJob,
  listPipelineJobs,
  retryPipelineJob,
} from '../api'
import type {
  AcquisitionCandidateListParams,
  CreateAcquisitionJobResponse,
  PipelineDecisionCheckpoint,
  PipelineJob,
  PipelineJobStatus,
  PipelinePlatform,
  PipelineStage,
  PipelineStageName,
} from '../types/pipeline'
import AcquisitionJobCreator from '../components/AcquisitionJobCreator.vue'
import CandidateReviewDrawer from '../components/CandidateReviewDrawer.vue'
import HermesMissionMonitor from '../components/HermesMissionMonitor.vue'
import StageDiscoveryResult from '../components/StageDiscoveryResult.vue'
import StageQualificationResult from '../components/StageQualificationResult.vue'
import StageStrategyResult from '../components/StageStrategyResult.vue'
import StrategyReviewDrawer from '../components/StrategyReviewDrawer.vue'

interface SocialAccount {
  id: number
  platform: PipelinePlatform
  username: string
  nickname?: string
  status: string
}

const { t, locale } = useI18n()
const ALL_STAGES: PipelineStageName[] = ['collect', 'filter', 'strategy', 'outreach', 'report', 'iterate']
const JOB_STATUSES: PipelineJobStatus[] = [
  'queued',
  'running',
  'waiting_decision',
  'cancelling',
  'succeeded',
  'partial_failed',
  'failed',
  'interrupted',
  'cancelled',
]
const PAGE_SIZE = 10
const accounts = ref<SocialAccount[]>([])
const jobs = ref<PipelineJob[]>([])
const historyLoading = ref(false)
const historyError = ref('')
const historyTotal = ref(0)
const historyOffset = ref(0)
const selectedJobId = ref('')
const selectedJob = ref<PipelineJob | null>(null)
const detailLoading = ref(false)
const detailError = ref('')
const actionLoading = ref(false)
const stageRefreshToken = ref(0)
const reviewDrawerOpen = ref(false)
const reviewDrawerJobId = ref('')
const reviewFilter = ref<AcquisitionCandidateListParams>({})
const reviewManualCheckpoint = ref<PipelineDecisionCheckpoint | null>(null)
const strategyDrawerOpen = ref(false)
const strategyDrawerJobId = ref('')
const strategyManualCheckpoint = ref<PipelineDecisionCheckpoint | null>(null)
let pollTimer: number | null = null
let historyRequestToken = 0
let detailRequestToken = 0
let actionRequestToken = 0
let pollInFlight = false

const canCancel = computed(() =>
  Boolean(selectedJob.value && ['queued', 'running', 'waiting_decision'].includes(selectedJob.value.status)),
)
const canRetry = computed(() =>
  Boolean(selectedJob.value && ['failed', 'partial_failed', 'interrupted'].includes(selectedJob.value.status)),
)
const historyFrom = computed(() => historyTotal.value === 0 ? 0 : historyOffset.value + 1)
const historyTo = computed(() => Math.min(historyOffset.value + jobs.value.length, historyTotal.value))
const isSelectedAcquisitionJob = computed(() => {
  const snapshot = selectedJob.value?.configSnapshot ?? {}
  return snapshot.businessMode === 'ai_acquisition'
    || snapshot.creatorSource === 'pipeline_ui'
})
const detailStages = computed<PipelineStage[]>(() => {
  const byName = new Map((selectedJob.value?.stages || []).map(stage => [stage.stage, stage]))
  const requested = new Set(selectedJob.value?.requestedStages || [])
  return ALL_STAGES.map((stage, index) => byName.get(stage) || {
    id: -(index + 1),
    stage,
    order: index,
    status: requested.has(stage) ? 'pending' : 'skipped',
    attempt: 0,
    result: {},
    errorMessage: '',
    startedAt: null,
    finishedAt: null,
  })
})
const diagnosticStages = computed(() => detailStages.value.filter(stage => hasResult(stage.result)))
const stageProgressText = computed(() => {
  const completed = detailStages.value.filter(stage =>
    ['succeeded', 'failed', 'skipped', 'cancelled'].includes(stage.status),
  ).length
  return t('pipeline.stageProgressCount', { done: completed, total: detailStages.value.length })
})

function extractError(error: unknown, fallback: string) {
  const candidate = error as {
    message?: string
    code?: string
    response?: { data?: { detail?: string | { code?: string; message?: string } } }
  }
  const detail = candidate?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (detail && typeof detail === 'object') {
    return [detail.code, detail.message].filter(Boolean).join(' · ')
  }
  return candidate?.message || fallback
}

function handleAccountsLoaded(loadedAccounts: SocialAccount[]) {
  const byId = new Map(accounts.value.map(account => [account.id, account]))
  loadedAccounts.forEach(account => byId.set(account.id, account))
  accounts.value = [...byId.values()]
}

async function handleAcquisitionCreated(response: CreateAcquisitionJobResponse) {
  ElMessage.success(t('pipeline.createdMessage'))
  historyOffset.value = 0
  await refreshJobs(false)
  await selectJob(response.job)
}

function closeReviewDrawer() {
  reviewDrawerOpen.value = false
  reviewDrawerJobId.value = ''
  reviewFilter.value = {}
  reviewManualCheckpoint.value = null
}

function closeStrategyDrawer() {
  strategyDrawerOpen.value = false
  strategyDrawerJobId.value = ''
  strategyManualCheckpoint.value = null
}

function openStrategyReview(jobId: string, checkpoint: PipelineDecisionCheckpoint | null = null) {
  if (!isSelectedAcquisitionJob.value || selectedJobId.value !== jobId) return
  closeReviewDrawer()
  strategyDrawerJobId.value = jobId
  strategyManualCheckpoint.value = checkpoint
  strategyDrawerOpen.value = true
}

function openCandidateReview(jobId: string, filter: AcquisitionCandidateListParams) {
  if (!isSelectedAcquisitionJob.value || selectedJobId.value !== jobId) return
  closeStrategyDrawer()
  reviewDrawerJobId.value = jobId
  reviewFilter.value = { ...filter }
  reviewManualCheckpoint.value = null
  reviewDrawerOpen.value = true
}

function openReviewWorkbench(jobId: string, checkpoint: PipelineDecisionCheckpoint) {
  if (
    !isSelectedAcquisitionJob.value
    || selectedJobId.value !== jobId
    || checkpoint.jobId !== jobId
  ) return
  if (checkpoint.stage === 'outreach' || selectedJob.value?.currentStage === 'outreach') {
    openStrategyReview(jobId, checkpoint)
    return
  }
  reviewDrawerJobId.value = jobId
  reviewFilter.value = {}
  reviewManualCheckpoint.value = checkpoint
  reviewDrawerOpen.value = true
}

async function handleReviewDataChanged(jobId: string) {
  if (!jobId || selectedJobId.value !== jobId) return
  stageRefreshToken.value += 1
  await refreshSelectedJobDetail()
}

async function handleReviewComplete(jobId: string) {
  if (!jobId || selectedJobId.value !== jobId) return
  closeReviewDrawer()
  stageRefreshToken.value += 1
  await refreshSelectedJobDetail()
}

async function handleStrategyDataChanged(jobId: string) {
  if (!jobId || selectedJobId.value !== jobId) return
  stageRefreshToken.value += 1
  await refreshSelectedJobDetail()
}

async function handleStrategyReviewComplete(jobId: string) {
  if (!jobId || selectedJobId.value !== jobId) return
  closeStrategyDrawer()
  stageRefreshToken.value += 1
  await refreshSelectedJobDetail()
}

async function refreshJobs(showLoading = true) {
  const requestToken = ++historyRequestToken
  const offsetSnapshot = historyOffset.value
  if (showLoading) historyLoading.value = true
  historyError.value = ''
  try {
    const { data } = await listPipelineJobs({
      limit: PAGE_SIZE,
      offset: offsetSnapshot,
    })
    if (requestToken !== historyRequestToken || offsetSnapshot !== historyOffset.value) return
    jobs.value = data.items || []
    historyTotal.value = data.total || 0
    const firstJob = jobs.value[0]
    if (!selectedJobId.value && firstJob) {
      await selectJob(firstJob)
    }
  } catch (error) {
    if (requestToken !== historyRequestToken || offsetSnapshot !== historyOffset.value) return
    historyError.value = extractError(error, t('pipeline.historyErrorTitle'))
  } finally {
    if (requestToken === historyRequestToken && offsetSnapshot === historyOffset.value) {
      historyLoading.value = false
    }
  }
}

async function selectJob(job: PipelineJob) {
  closeReviewDrawer()
  closeStrategyDrawer()
  stageRefreshToken.value = 0
  selectedJobId.value = job.id
  selectedJob.value = job
  await loadJobDetail(job.id)
}

async function loadJobDetail(jobIdSnapshot: string, showLoading = true) {
  const requestToken = ++detailRequestToken
  if (showLoading) detailLoading.value = true
  detailError.value = ''
  try {
    const { data } = await getPipelineJob(jobIdSnapshot)
    if (requestToken !== detailRequestToken || selectedJobId.value !== jobIdSnapshot) return
    selectedJob.value = data.job
    detailError.value = ''
  } catch (error) {
    if (requestToken !== detailRequestToken || selectedJobId.value !== jobIdSnapshot) return
    detailError.value = extractError(error, t('pipeline.detailErrorTitle'))
  } finally {
    if (requestToken === detailRequestToken && selectedJobId.value === jobIdSnapshot) {
      detailLoading.value = false
    }
  }
}

async function refreshSelectedJobDetail() {
  const jobIdSnapshot = selectedJobId.value
  if (!jobIdSnapshot) return
  await loadJobDetail(jobIdSnapshot, false)
}

async function cancelJob() {
  if (!selectedJob.value || !canCancel.value) return
  const targetId = selectedJob.value.id
  const requestToken = ++actionRequestToken
  ++detailRequestToken
  detailLoading.value = false
  actionLoading.value = true
  if (selectedJobId.value === targetId) detailError.value = ''
  try {
    const { data } = await cancelPipelineJob(targetId)
    if (requestToken !== actionRequestToken) return
    if (selectedJobId.value === targetId) {
      selectedJob.value = data.job
      detailError.value = ''
      ElMessage.success(t('pipeline.cancelledMessage'))
    }
    await refreshJobs(false)
  } catch (error) {
    if (requestToken === actionRequestToken && selectedJobId.value === targetId) {
      detailError.value = extractError(error, t('pipeline.cancelError'))
    }
  } finally {
    if (requestToken === actionRequestToken) actionLoading.value = false
  }
}

async function retryJob() {
  if (!selectedJob.value || !canRetry.value) return
  const targetId = selectedJob.value.id
  const requestToken = ++actionRequestToken
  ++detailRequestToken
  detailLoading.value = false
  actionLoading.value = true
  if (selectedJobId.value === targetId) detailError.value = ''
  try {
    const { data } = await retryPipelineJob(targetId)
    if (requestToken !== actionRequestToken) return
    historyOffset.value = 0
    await refreshJobs(false)
    if (requestToken === actionRequestToken && selectedJobId.value === targetId) {
      detailError.value = ''
      await selectJob(data.job)
      ElMessage.success(t('pipeline.retriedMessage'))
    }
  } catch (error) {
    if (requestToken === actionRequestToken && selectedJobId.value === targetId) {
      detailError.value = extractError(error, t('pipeline.retryError'))
    }
  } finally {
    if (requestToken === actionRequestToken) actionLoading.value = false
  }
}

async function previousPage() {
  historyOffset.value = Math.max(0, historyOffset.value - PAGE_SIZE)
  await refreshJobs()
}

async function nextPage() {
  historyOffset.value += PAGE_SIZE
  await refreshJobs()
}

function statusClass(status: PipelineJobStatus) {
  if (!JOB_STATUSES.includes(status)) return 'neutral'
  const classes: Record<PipelineJobStatus, string> = {
    queued: 'neutral',
    running: 'active',
    waiting_decision: 'warning',
    cancelling: 'warning',
    succeeded: 'success',
    partial_failed: 'warning',
    failed: 'danger',
    interrupted: 'warning',
    cancelled: 'neutral',
  }
  return classes[status]
}

function shortJobId(id: string) {
  return id.length > 18 ? `${id.slice(0, 8)}…${id.slice(-6)}` : id
}

function formatDate(value: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat(locale.value, {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

function formatDateTime(value: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat(locale.value, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(date)
}

function accountLabel(job: PipelineJob) {
  if (job.accountMode === 'auto') return t('pipeline.accountAuto')
  const match = accounts.value.find(account => account.id === job.accountId)
  return match ? `@${match.username}` : t('pipeline.accountId', { id: job.accountId ?? '—' })
}

function hasResult(result: Record<string, unknown>) {
  return result && Object.keys(result).length > 0
}

function formatLegacySummary(result: Record<string, unknown>) {
  return Object.entries(result).map(([key, value]) => {
    if (value === null || value === undefined) return `${key}: —`
    if (Array.isArray(value)) return `${key}: ${value.join(', ')}`
    if (typeof value === 'object') return `${key}: ${Object.keys(value).length} fields`
    return `${key}: ${String(value)}`
  }).join(' · ')
}

function formatResult(result: Record<string, unknown>) {
  return JSON.stringify(result, null, 2)
}

async function pollActiveJob() {
  if (pollInFlight) return
  pollInFlight = true
  try {
    await refreshJobs(false)
    await refreshSelectedJobDetail()
  } finally {
    pollInFlight = false
  }
}

onMounted(async () => {
  await refreshJobs()
  pollTimer = window.setInterval(pollActiveJob, 5000)
})

onUnmounted(() => {
  if (pollTimer !== null) window.clearInterval(pollTimer)
  ++historyRequestToken
  ++detailRequestToken
  ++actionRequestToken
})
</script>

<style scoped>
.pipeline-page { padding-bottom: 48px; }
.page-head { align-items: flex-end; }
.eyebrow {
  margin-bottom: 5px; color: var(--brand-deep); font-family: var(--font-mono);
  font-size: 10px; font-weight: 700; letter-spacing: .12em; text-transform: uppercase;
}
.page-head h1 { font-size: 25px; }
.page-head p { max-width: 720px; text-wrap: pretty; }
.acquisition-creator-shell { margin-bottom: 16px; }
.btn:disabled, .icon-action:disabled { cursor: not-allowed; opacity: .45; }
.console-grid { display: grid; grid-template-columns: minmax(290px, 340px) minmax(0, 1fr); gap: 16px; align-items: start; }
.history-card { overflow: hidden; }
.icon-action {
  display: grid; place-items: center; width: 30px; height: 30px; border: 1px solid var(--border);
  border-radius: 6px; background: var(--surface); color: var(--fg-2); font-size: 17px;
}
.icon-action:hover { background: var(--bg-sub); }
.job-list { max-height: 648px; overflow-y: auto; }
.job-item {
  display: block; width: 100%; padding: 13px 15px; border: 0; border-bottom: 1px solid var(--border);
  background: var(--surface); text-align: left; transition: background .1s;
}
.job-item:hover { background: var(--bg-sub); }
.job-item.selected { background: var(--brand-soft); box-shadow: inset 3px 0 0 var(--brand); }
.job-item-top { display: flex; align-items: center; gap: 6px; }
.job-item-top time { margin-left: auto; color: var(--muted); font-family: var(--font-mono); font-size: 9.5px; }
.job-id { display: block; margin-top: 9px; color: var(--fg); font-size: 11.5px; font-weight: 600; }
.job-item-meta { display: block; margin-top: 3px; color: var(--muted); font-size: 10.5px; }
.platform-badge {
  display: inline-grid; place-items: center; min-width: 25px; height: 20px; padding: 0 5px;
  border-radius: 4px; font-family: var(--font-mono); font-size: 9px; font-weight: 800;
}
.platform-badge.tiktok { background: var(--fg); color: var(--surface); }
.platform-badge.douyin { background: var(--cyan-soft); color: oklch(42% .12 200); }
.platform-badge.large { width: 38px; height: 38px; border-radius: 7px; font-size: 11px; }
.status-badge {
  display: inline-flex; align-items: center; gap: 5px; height: 20px; padding: 0 7px;
  border: 1px solid var(--border); border-radius: 999px; color: var(--fg-2); font-size: 9.5px; font-weight: 600;
}
.status-dot { width: 5px; height: 5px; border-radius: 50%; background: currentColor; }
.status-badge.active { border-color: oklch(87% .08 350); background: var(--brand-soft); color: var(--brand-deep); }
.status-badge.success { border-color: oklch(87% .06 150); background: var(--ok-soft); color: oklch(43% .14 150); }
.status-badge.warning { border-color: oklch(87% .08 75); background: var(--warn-soft); color: oklch(48% .14 75); }
.status-badge.danger { border-color: oklch(87% .08 25); background: var(--err-soft); color: var(--err); }
.status-badge.large { height: 24px; padding: 0 9px; font-size: 10.5px; }
.status-badge.active .status-dot { animation: pulse 1.4s ease-in-out infinite; }
.history-footer {
  display: flex; align-items: center; justify-content: space-between; gap: 10px;
  padding: 11px 14px; border-top: 1px solid var(--border); background: var(--surface-2);
  color: var(--muted); font-size: 10.5px;
}
.history-footer > div { display: flex; gap: 5px; }
.detail-card { min-height: 520px; overflow: hidden; }
.detail-head {
  display: flex; align-items: flex-start; justify-content: space-between; gap: 20px;
  padding: 18px; border-bottom: 1px solid var(--border); background: var(--surface-2);
}
.detail-title-row { display: flex; align-items: center; gap: 12px; min-width: 0; }
.detail-kicker {
  display: block; margin-bottom: 2px; color: var(--muted); font-size: 9.5px; font-weight: 700;
  letter-spacing: .09em; text-transform: uppercase;
}
.detail-title-row h2 {
  max-width: min(520px, 55vw); margin: 0; overflow: hidden; color: var(--fg);
  font-size: 13px; text-overflow: ellipsis; white-space: nowrap;
}
.detail-status-row { display: flex; align-items: center; gap: 9px; margin: 12px 0 0 50px; color: var(--muted); font-size: 11px; }
.detail-actions { display: flex; gap: 7px; flex-shrink: 0; }
.detail-meta { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); border-bottom: 1px solid var(--border); }
.detail-meta > div { min-width: 0; padding: 13px 16px; border-right: 1px solid var(--border); }
.detail-meta > div:last-child { border-right: 0; }
.detail-meta span { display: block; color: var(--muted); font-size: 9.5px; text-transform: uppercase; letter-spacing: .04em; }
.detail-meta b {
  display: block; margin-top: 4px; overflow: hidden; color: var(--fg-2); font-size: 11.5px;
  text-overflow: ellipsis; white-space: nowrap;
}
.detail-notice {
  display: flex; gap: 8px; margin: 14px 18px 0; padding: 9px 11px;
  border: 1px solid var(--border); border-radius: 6px; font-size: 11.5px;
}
.detail-notice.error { border-color: oklch(88% .06 25); background: var(--err-soft); color: oklch(46% .18 25); }
.stage-detail-head { display: flex; align-items: center; justify-content: space-between; padding: 17px 18px 10px; }
.stage-detail-head > div { display: flex; align-items: baseline; gap: 9px; }
.stage-detail-head h3 { margin: 0; font-size: 13px; }
.stage-detail-head span { color: var(--muted); font-size: 10.5px; }
.stage-timeline { padding: 0 18px 18px; }
.stage-node { display: grid; grid-template-columns: 35px 1fr; gap: 10px; min-width: 0; }
.stage-rail { display: flex; flex-direction: column; align-items: center; }
.stage-index {
  display: grid; place-items: center; width: 29px; height: 29px; border: 1px solid var(--border);
  border-radius: 6px; background: var(--surface); color: var(--muted); font-family: var(--font-mono); font-size: 9.5px; font-weight: 700;
}
.rail-line { width: 1px; min-height: 42px; flex: 1; background: var(--border); }
.stage-node.running .stage-index { border-color: var(--brand); background: var(--brand-soft); color: var(--brand-deep); }
.stage-node.succeeded .stage-index { border-color: var(--ok); background: var(--ok-soft); color: oklch(43% .14 150); }
.stage-node.failed .stage-index { border-color: var(--err); background: var(--err-soft); color: var(--err); }
.stage-content { min-width: 0; padding: 4px 0 15px; }
.stage-content-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.stage-content h4 { margin: 0; font-size: 12.5px; }
.stage-content p { margin: 2px 0 0; color: var(--muted); font-size: 10.5px; }
.stage-status {
  padding: 2px 7px; border-radius: 4px; background: var(--bg-sub); color: var(--muted);
  font-size: 9.5px; font-weight: 600;
}
.stage-status.running { background: var(--brand-soft); color: var(--brand-deep); }
.stage-status.succeeded { background: var(--ok-soft); color: oklch(43% .14 150); }
.stage-status.failed { background: var(--err-soft); color: var(--err); }
.stage-status.cancelled { background: var(--warn-soft); color: oklch(48% .14 75); }
.stage-times { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 7px; color: var(--muted); font-family: var(--font-mono); font-size: 9.5px; }
.stage-error { margin-top: 8px; padding: 8px; border-radius: 5px; background: var(--err-soft); color: var(--err); overflow-wrap: anywhere; }
.stage-error code { font-family: var(--font-mono); font-size: 10.5px; }
.legacy-stage-summary {
  margin-top: 9px !important; padding: 9px 11px; border: 1px solid var(--border);
  border-radius: 7px; background: var(--bg-sub); overflow-wrap: anywhere;
}
.technical-diagnostics {
  margin: 0 18px 18px; padding: 11px 13px; border: 1px solid var(--border);
  border-radius: 8px; background: var(--surface-2); color: var(--muted); font-size: 10.5px;
}
.technical-diagnostics summary { min-height: 44px; cursor: pointer; line-height: 44px; }
.technical-diagnostics section + section { margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--border); }
.technical-diagnostics h4 { margin: 0; color: var(--fg-2); font-size: 11px; }
.technical-diagnostics pre {
  max-height: 220px; margin: 6px 0 0; padding: 9px; overflow: auto; border-radius: 5px;
  background: var(--fg); color: var(--surface); font-family: var(--font-mono); font-size: 10px;
}
.state-panel {
  display: flex; min-height: 220px; padding: 30px; flex-direction: column; align-items: center;
  justify-content: center; color: var(--muted); text-align: center;
}
.state-panel.detail-state { min-height: 515px; }
.state-panel strong { color: var(--fg-2); }
.state-panel p { max-width: 320px; margin: 5px 0 13px; font-size: 11.5px; text-wrap: pretty; }
.state-panel.error strong { color: var(--err); }
.empty-mark {
  display: grid; place-items: center; width: 38px; height: 38px; margin-bottom: 10px;
  border: 1px dashed var(--border-strong); border-radius: 6px; font-family: var(--font-mono); font-size: 10px;
}
.spinner {
  display: inline-block; width: 13px; height: 13px; border: 2px solid rgb(255 255 255 / .4);
  border-top-color: currentColor; border-radius: 50%; animation: spin .7s linear infinite;
}
.spinner.dark { color: var(--fg-2); border-color: var(--border); border-top-color: currentColor; }
.spinning { display: inline-block; animation: spin .8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
@keyframes pulse { 50% { opacity: .35; } }

@media (max-width: 1120px) {
  .console-grid { grid-template-columns: minmax(260px, 300px) minmax(0, 1fr); }
  .detail-meta { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .detail-meta > div:nth-child(2) { border-right: 0; }
  .detail-meta > div:nth-child(-n+2) { border-bottom: 1px solid var(--border); }
}
@media (max-width: 820px) {
  .page-head { align-items: flex-start; }
  .console-grid { grid-template-columns: 1fr; }
  .history-card { order: 2; }
  .detail-card { order: 1; }
  .job-list { max-height: 340px; }
}
@media (max-width: 580px) {
  .page-head, .detail-head { align-items: stretch; flex-direction: column; }
  .page-head .btn { justify-content: center; width: 100%; }
  .detail-title-row h2 { max-width: calc(100vw - 150px); }
  .detail-actions { width: 100%; }
  .detail-actions .btn { flex: 1; justify-content: center; }
  .detail-meta { grid-template-columns: 1fr; }
  .detail-meta > div { border-right: 0; border-bottom: 1px solid var(--border); }
  .detail-meta > div:last-child { border-bottom: 0; }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation-duration: .01ms !important; animation-iteration-count: 1 !important; }
}
</style>
