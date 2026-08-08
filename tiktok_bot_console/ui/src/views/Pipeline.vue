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

    <section class="card creator-card">
      <div class="card-hd creator-head">
        <div>
          <h3>{{ $t('pipeline.createTitle') }}</h3>
          <span class="hint">{{ $t('pipeline.createHint') }}</span>
        </div>
        <span class="system-mark">{{ $t('pipeline.singleSystem') }}</span>
      </div>

      <div class="creator-body">
        <div class="choice-grid">
          <div class="field-block">
            <span class="label">{{ $t('pipeline.platform') }}</span>
            <div class="segmented" role="radiogroup" :aria-label="$t('pipeline.platform')">
              <button
                ref="tiktokPlatformRadio"
                data-testid="pipeline-platform-tiktok"
                type="button"
                role="radio"
                :class="{ active: selectedPlatform === 'tiktok' }"
                :aria-checked="selectedPlatform === 'tiktok'"
                :tabindex="selectedPlatform === 'tiktok' ? 0 : -1"
                @click="selectPlatform('tiktok')"
                @keydown.left.prevent="selectPlatform('douyin', true)"
                @keydown.right.prevent="selectPlatform('douyin', true)"
              >
                <span class="platform-code">TT</span>
                TikTok
              </button>
              <button
                ref="douyinPlatformRadio"
                data-testid="pipeline-platform-douyin"
                type="button"
                role="radio"
                :class="{ active: selectedPlatform === 'douyin' }"
                :aria-checked="selectedPlatform === 'douyin'"
                :tabindex="selectedPlatform === 'douyin' ? 0 : -1"
                @click="selectPlatform('douyin')"
                @keydown.left.prevent="selectPlatform('tiktok', true)"
                @keydown.right.prevent="selectPlatform('tiktok', true)"
              >
                <span class="platform-code">DY</span>
                {{ $t('pipeline.douyin') }}
              </button>
            </div>
          </div>

          <div class="field-block">
            <span class="label">{{ $t('pipeline.accountStrategy') }}</span>
            <div class="segmented compact" role="radiogroup" :aria-label="$t('pipeline.accountStrategy')">
              <button
                ref="autoAccountRadio"
                data-testid="pipeline-account-auto"
                type="button"
                role="radio"
                :class="{ active: accountMode === 'auto' }"
                :aria-checked="accountMode === 'auto'"
                :tabindex="accountMode === 'auto' ? 0 : -1"
                @click="selectAccountMode('auto')"
                @keydown.left.prevent="selectAccountMode('specified', true)"
                @keydown.right.prevent="selectAccountMode('specified', true)"
              >
                {{ $t('pipeline.accountAuto') }}
              </button>
              <button
                ref="specifiedAccountRadio"
                data-testid="pipeline-account-specified"
                type="button"
                role="radio"
                :class="{ active: accountMode === 'specified' }"
                :aria-checked="accountMode === 'specified'"
                :tabindex="accountMode === 'specified' ? 0 : -1"
                @click="selectAccountMode('specified')"
                @keydown.left.prevent="selectAccountMode('auto', true)"
                @keydown.right.prevent="selectAccountMode('auto', true)"
              >
                {{ $t('pipeline.accountSpecified') }}
              </button>
            </div>
          </div>

          <div v-if="accountMode === 'specified'" class="field-block account-field">
            <label class="label" for="pipeline-account">{{ $t('pipeline.account') }}</label>
            <select
              id="pipeline-account"
              v-model.number="selectedAccountId"
              data-testid="pipeline-account-select"
              class="select"
              :disabled="accountsLoading"
            >
              <option :value="null">{{ accountsLoading ? $t('common.loading') : $t('pipeline.selectAccount') }}</option>
              <option v-for="account in loggedInAccounts" :key="account.id" :value="account.id">
                {{ account.nickname || account.username }} · @{{ account.username }}
              </option>
            </select>
          </div>
        </div>

        <div
          v-if="capabilitiesLoading"
          class="preflight neutral"
          role="status"
        >
          <span class="preflight-signal"></span>
          {{ $t('pipeline.checkingCapability') }}
        </div>
        <div
          v-else-if="capabilitiesError"
          class="preflight blocked"
          role="alert"
        >
          <span class="preflight-signal"></span>
          <div>
            <strong>{{ $t('pipeline.capabilityError') }}</strong>
            <span>{{ capabilitiesError }}</span>
          </div>
          <button class="text-action" type="button" @click="loadCapabilities">{{ $t('common.retry') }}</button>
        </div>
        <div
          v-else-if="capability"
          :class="['preflight', capability.available ? 'ready' : 'blocked']"
          :role="capability.available ? 'status' : 'alert'"
        >
          <span class="preflight-signal"></span>
          <div>
            <strong>
              {{ capability.available ? $t('pipeline.preflightReady') : $t('pipeline.preflightBlocked') }}
            </strong>
            <span v-if="!capability.available && capability.message">
              {{ capability.message }}
            </span>
            <span v-else-if="selectedPlatform === 'douyin'">
              {{ $t('pipeline.douyinConcurrency', { n: capability.maxConcurrency }) }}
              · {{ $t('pipeline.loggedInAccounts', { n: capability.accountCount }) }}
            </span>
            <span v-else-if="capability.message">{{ capability.message }}</span>
          </div>
          <code v-if="capability.code">{{ capability.code }}</code>
          <span v-else class="provider-name">{{ capability.provider }}</span>
        </div>

        <p v-if="accountsError" class="inline-error" role="alert">{{ accountsError }}</p>
        <p v-else-if="accountMode === 'specified' && !accountsLoading && loggedInAccounts.length === 0" class="inline-error">
          {{ $t('pipeline.noLoggedInAccounts') }}
        </p>

        <div class="stage-picker">
          <div class="stage-picker-head">
            <div>
              <span class="label">{{ $t('pipeline.stages') }}</span>
              <span class="hint">{{ $t('pipeline.stageSelectionHint') }}</span>
            </div>
            <button class="text-action" type="button" @click="toggleAllStages">
              {{ allStagesSelected ? $t('pipeline.clearAll') : $t('pipeline.selectAll') }}
            </button>
          </div>
          <div class="stage-options">
            <label
              v-for="(stage, index) in ALL_STAGES"
              :key="stage"
              :class="['stage-option', { selected: selectedStages.includes(stage) }]"
            >
              <input v-model="selectedStages" type="checkbox" :value="stage">
              <span class="stage-number">{{ String(index + 1).padStart(2, '0') }}</span>
              <span>
                <b>{{ $t(`pipeline.${stage}`) }}</b>
                <small>{{ $t(`pipeline.${stage}Short`) }}</small>
              </span>
            </label>
          </div>
        </div>

        <div class="creator-footer">
          <p>{{ submitHint }}</p>
          <button
            class="btn brand lg"
            type="button"
            :disabled="!canCreate"
            @click="submitJob"
          >
            <span v-if="createLoading" class="spinner"></span>
            {{ createLoading ? $t('pipeline.creating') : $t('pipeline.createJob') }}
          </button>
        </div>
      </div>
    </section>

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
                <details v-if="hasResult(stage.result)" class="stage-result">
                  <summary>{{ $t('pipeline.stageResult') }}</summary>
                  <pre>{{ formatResult(stage.result) }}</pre>
                </details>
              </div>
            </article>
          </div>
        </template>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useI18n } from 'vue-i18n'
import {
  cancelPipelineJob,
  createPipelineJob,
  getAccounts,
  getPipelineCapabilities,
  getPipelineJob,
  listPipelineJobs,
  retryPipelineJob,
} from '../api'
import type {
  AccountMode,
  PipelineCapabilities,
  PipelineJob,
  PipelineJobStatus,
  PipelinePlatform,
  PipelineStage,
  PipelineStageName,
} from '../types/pipeline'

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
  'cancelling',
  'succeeded',
  'partial_failed',
  'failed',
  'interrupted',
  'cancelled',
]
const PAGE_SIZE = 10
const selectedPlatform = ref<PipelinePlatform>('douyin')
const accountMode = ref<AccountMode>('auto')
const selectedAccountId = ref<number | null>(null)
const selectedStages = ref<PipelineStageName[]>([...ALL_STAGES])
const tiktokPlatformRadio = ref<HTMLButtonElement | null>(null)
const douyinPlatformRadio = ref<HTMLButtonElement | null>(null)
const autoAccountRadio = ref<HTMLButtonElement | null>(null)
const specifiedAccountRadio = ref<HTMLButtonElement | null>(null)
const accounts = ref<SocialAccount[]>([])
const accountsLoading = ref(false)
const accountsError = ref('')
const capabilities = ref<PipelineCapabilities | null>(null)
const capabilitiesLoading = ref(false)
const capabilitiesError = ref('')
const createLoading = ref(false)
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
let pollTimer: number | null = null
let accountsRequestToken = 0
let historyRequestToken = 0
let detailRequestToken = 0
let actionRequestToken = 0
let pollInFlight = false

const capability = computed(() => capabilities.value?.platforms[selectedPlatform.value] || null)
const loggedInAccounts = computed(() =>
  accounts.value.filter(account =>
    account.platform === selectedPlatform.value && account.status === 'logged_in',
  ),
)
const allStagesSelected = computed(() => selectedStages.value.length === ALL_STAGES.length)
const selectedAccountIsValid = computed(() =>
  selectedAccountId.value !== null
  && loggedInAccounts.value.some(account => account.id === selectedAccountId.value),
)
const canCreate = computed(() =>
  !createLoading.value
  && !capabilitiesLoading.value
  && Boolean(capability.value?.available)
  && selectedStages.value.length > 0
  && loggedInAccounts.value.length > 0
  && (accountMode.value === 'auto' || selectedAccountIsValid.value),
)
const submitHint = computed(() => {
  if (!capability.value?.available) return t('pipeline.submitBlocked')
  if (selectedStages.value.length === 0) return t('pipeline.submitNeedStage')
  if (loggedInAccounts.value.length === 0) return t('pipeline.submitNeedAvailableAccount')
  if (accountMode.value === 'specified' && !selectedAccountIsValid.value) {
    return t('pipeline.submitNeedAccount')
  }
  return t('pipeline.submitReady', {
    platform: selectedPlatform.value === 'tiktok' ? 'TikTok' : t('pipeline.douyin'),
    n: selectedStages.value.length,
  })
})
const canCancel = computed(() =>
  Boolean(selectedJob.value && ['queued', 'running'].includes(selectedJob.value.status)),
)
const canRetry = computed(() =>
  Boolean(selectedJob.value && ['failed', 'partial_failed', 'interrupted'].includes(selectedJob.value.status)),
)
const historyFrom = computed(() => historyTotal.value === 0 ? 0 : historyOffset.value + 1)
const historyTo = computed(() => Math.min(historyOffset.value + jobs.value.length, historyTotal.value))
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
const stageProgressText = computed(() => {
  const completed = detailStages.value.filter(stage =>
    ['succeeded', 'failed', 'skipped', 'cancelled'].includes(stage.status),
  ).length
  return t('pipeline.stageProgressCount', { done: completed, total: detailStages.value.length })
})

watch(selectedPlatform, async () => {
  selectedAccountId.value = null
  await loadAccounts()
})
watch(accountMode, mode => {
  if (mode === 'auto') selectedAccountId.value = null
})
watch(loggedInAccounts, availableAccounts => {
  if (
    selectedAccountId.value !== null
    && !availableAccounts.some(account => account.id === selectedAccountId.value)
  ) {
    selectedAccountId.value = null
  }
})

function selectPlatform(platform: PipelinePlatform, focus = false) {
  selectedPlatform.value = platform
  if (focus) {
    nextTick(() => {
      const target = platform === 'tiktok' ? tiktokPlatformRadio.value : douyinPlatformRadio.value
      target?.focus()
    })
  }
}

function selectAccountMode(mode: AccountMode, focus = false) {
  accountMode.value = mode
  if (focus) {
    nextTick(() => {
      const target = mode === 'auto' ? autoAccountRadio.value : specifiedAccountRadio.value
      target?.focus()
    })
  }
}

function toggleAllStages() {
  selectedStages.value = allStagesSelected.value ? [] : [...ALL_STAGES]
}

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

async function loadCapabilities() {
  capabilitiesLoading.value = true
  capabilitiesError.value = ''
  try {
    const { data } = await getPipelineCapabilities()
    capabilities.value = data
  } catch (error) {
    capabilitiesError.value = extractError(error, t('pipeline.capabilityError'))
  } finally {
    capabilitiesLoading.value = false
  }
}

async function loadAccounts() {
  const requestToken = ++accountsRequestToken
  const platformSnapshot = selectedPlatform.value
  accountsLoading.value = true
  accountsError.value = ''
  try {
    const { data } = await getAccounts(platformSnapshot)
    if (requestToken !== accountsRequestToken || platformSnapshot !== selectedPlatform.value) return
    accounts.value = Array.isArray(data) ? data : []
    if (!loggedInAccounts.value.some(account => account.id === selectedAccountId.value)) {
      selectedAccountId.value = null
    }
  } catch (error) {
    if (requestToken !== accountsRequestToken || platformSnapshot !== selectedPlatform.value) return
    accounts.value = []
    accountsError.value = extractError(error, t('pipeline.accountsError'))
  } finally {
    if (requestToken === accountsRequestToken && platformSnapshot === selectedPlatform.value) {
      accountsLoading.value = false
    }
  }
}

async function submitJob() {
  if (!canCreate.value) return
  createLoading.value = true
  try {
    const { data } = await createPipelineJob({
      platform: selectedPlatform.value,
      accountMode: accountMode.value,
      accountId: selectedAccountId.value,
      stages: [...selectedStages.value],
    })
    ElMessage.success(t('pipeline.createdMessage'))
    historyOffset.value = 0
    await refreshJobs(false)
    await selectJob(data.job)
  } catch (error) {
    ElMessage.error(extractError(error, t('pipeline.createError')))
  } finally {
    createLoading.value = false
  }
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
  await Promise.all([loadCapabilities(), loadAccounts(), refreshJobs()])
  pollTimer = window.setInterval(pollActiveJob, 5000)
})

onUnmounted(() => {
  if (pollTimer !== null) window.clearInterval(pollTimer)
  ++accountsRequestToken
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
.creator-card { margin-bottom: 16px; overflow: hidden; }
.creator-head { background: var(--surface-2); }
.system-mark {
  padding: 4px 9px; border: 1px solid var(--border-strong); border-radius: 5px;
  color: var(--fg-2); font-family: var(--font-mono); font-size: 10px; letter-spacing: .05em;
}
.creator-body { padding: 18px; }
.choice-grid {
  display: grid; grid-template-columns: minmax(260px, 1fr) minmax(230px, .8fr) minmax(260px, 1fr);
  gap: 18px; align-items: end;
}
.field-block { min-width: 0; }
.account-field { grid-column: auto; }
.segmented {
  display: grid; grid-template-columns: repeat(2, 1fr); gap: 3px; padding: 3px;
  height: 42px; border: 1px solid var(--border); border-radius: 8px; background: var(--bg-sub);
}
.segmented button {
  display: flex; align-items: center; justify-content: center; gap: 8px; min-width: 0;
  border: 0; border-radius: 5px; background: transparent; color: var(--muted); font-size: 12.5px;
}
.segmented button:hover { color: var(--fg); background: var(--surface-2); }
.segmented button:focus-visible { outline: 2px solid var(--brand); outline-offset: 2px; }
.segmented button.active {
  background: var(--surface); color: var(--fg); box-shadow: var(--shadow-1); font-weight: 600;
}
.platform-code {
  display: inline-grid; place-items: center; width: 24px; height: 20px; border-radius: 4px;
  background: var(--fg); color: var(--surface); font-family: var(--font-mono); font-size: 9px;
}
.segmented button.active .platform-code { background: var(--brand); }
.select { height: 42px; }
.preflight {
  display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 10px;
  margin-top: 16px; padding: 10px 12px; border: 1px solid var(--border); border-radius: 7px;
  color: var(--fg-2); font-size: 12px;
}
.preflight > div { display: flex; gap: 6px; min-width: 0; }
.preflight strong { color: var(--fg); }
.preflight-signal { width: 8px; height: 8px; border-radius: 50%; background: var(--muted-2); }
.preflight.ready { background: var(--ok-soft); border-color: oklch(88% .05 150); }
.preflight.ready .preflight-signal { background: var(--ok); }
.preflight.blocked { background: var(--err-soft); border-color: oklch(88% .06 25); }
.preflight.blocked .preflight-signal { background: var(--err); }
.preflight code, .provider-name {
  overflow: hidden; color: inherit; font-family: var(--font-mono); font-size: 10.5px; text-overflow: ellipsis;
}
.inline-error { margin: 8px 0 0; color: var(--err); font-size: 11.5px; }
.stage-picker { margin-top: 18px; padding-top: 16px; border-top: 1px solid var(--border); }
.stage-picker-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.stage-picker-head .hint { display: block; margin-top: 1px; }
.text-action {
  padding: 0; border: 0; background: transparent; color: var(--brand-deep); font-size: 11.5px; font-weight: 600;
}
.text-action:hover { text-decoration: underline; }
.stage-options { display: grid; grid-template-columns: repeat(6, 1fr); gap: 7px; margin-top: 10px; }
.stage-option {
  position: relative; display: flex; align-items: center; gap: 9px; min-width: 0;
  padding: 10px; border: 1px solid var(--border); border-radius: 7px; background: var(--surface);
  color: var(--muted); cursor: pointer; transition: border-color .12s, background .12s, transform .12s;
}
.stage-option:hover { border-color: var(--border-strong); transform: translateY(-1px); }
.stage-option.selected { border-color: oklch(86% .08 350); background: var(--brand-soft); color: var(--fg); }
.stage-option input { position: absolute; opacity: 0; pointer-events: none; }
.stage-number {
  color: var(--muted-2); font-family: var(--font-mono); font-size: 10px; font-weight: 700;
}
.stage-option b { display: block; font-size: 12px; line-height: 1.3; }
.stage-option small {
  display: block; margin-top: 2px; overflow: hidden; color: var(--muted); font-size: 10px;
  text-overflow: ellipsis; white-space: nowrap;
}
.creator-footer {
  display: flex; align-items: center; justify-content: space-between; gap: 18px;
  margin-top: 16px; padding-top: 14px; border-top: 1px solid var(--border);
}
.creator-footer p { margin: 0; color: var(--muted); font-size: 11.5px; }
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
.stage-result { margin-top: 7px; color: var(--muted); font-size: 10.5px; }
.stage-result summary { cursor: pointer; }
.stage-result pre {
  max-height: 180px; margin: 6px 0 0; padding: 9px; overflow: auto; border-radius: 5px;
  background: oklch(14% .01 280); color: oklch(87% .006 280); font-size: 10px;
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
  .choice-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .account-field { grid-column: 1 / -1; }
  .stage-options { grid-template-columns: repeat(3, 1fr); }
  .console-grid { grid-template-columns: minmax(260px, 300px) minmax(0, 1fr); }
  .detail-meta { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .detail-meta > div:nth-child(2) { border-right: 0; }
  .detail-meta > div:nth-child(-n+2) { border-bottom: 1px solid var(--border); }
}
@media (max-width: 820px) {
  .page-head { align-items: flex-start; }
  .choice-grid, .console-grid { grid-template-columns: 1fr; }
  .account-field { grid-column: auto; }
  .history-card { order: 2; }
  .detail-card { order: 1; }
  .job-list { max-height: 340px; }
}
@media (max-width: 580px) {
  .page-head, .creator-footer, .detail-head { align-items: stretch; flex-direction: column; }
  .page-head .btn, .creator-footer .btn { justify-content: center; width: 100%; }
  .creator-body { padding: 14px; }
  .stage-options { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .preflight { grid-template-columns: auto 1fr; }
  .preflight code, .provider-name { grid-column: 2; }
  .preflight > div { flex-direction: column; gap: 0; }
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
