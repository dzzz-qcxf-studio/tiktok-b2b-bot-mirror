<template>
  <div class="page" style="max-width:1100px">
    <div class="page-head">
      <div>
        <h1>{{ $t('runtime.title') }}</h1>
        <p>{{ $t('runtime.subtitle') }}</p>
      </div>
    </div>

    <div class="card section">
      <div class="section-hd">
        <div>
          <h3>{{ $t('runtime.dailyLimits') }}</h3>
          <p>{{ $t('runtime.dailyLimitsHint') }}</p>
        </div>
        <span class="chip ok"><span class="dot"></span> {{ $t('runtime.antiBanOn') }}</span>
      </div>

      <div class="setting-row">
        <div>
          <div class="nm">{{ $t('runtime.commentLimit') }}</div>
          <div class="hint-row">{{ $t('runtime.commentLimitHint') }}</div>
        </div>
        <div class="ctrl">
          <input type="number" class="input" v-model.number="cfg.daily_comment_limit" min="1" max="50">
        </div>
        <div class="impact">
          <span>{{ $t('runtime.combinedDaily', { n: cfg.daily_comment_limit * loggedInAccountCount }) }}</span>
          <span class="muted">{{ $t('runtime.currentAvg', { n: 71 }) }}</span>
        </div>
      </div>

      <div class="setting-row">
        <div>
          <div class="nm">{{ $t('runtime.dmLimit') }}</div>
          <div class="hint-row">{{ $t('runtime.dmLimitHint') }}</div>
        </div>
        <div class="ctrl">
          <input type="number" class="input" v-model.number="cfg.daily_dm_limit" min="1" max="30">
        </div>
        <div class="impact">
          <span>{{ $t('runtime.combinedDaily', { n: cfg.daily_dm_limit * loggedInAccountCount }) }}</span>
          <span class="muted">{{ $t('runtime.currentAvg', { n: 32 }) }}</span>
        </div>
      </div>

      <div class="setting-row">
        <div>
          <div class="nm">{{ $t('runtime.dailyUsers') }}</div>
          <div class="hint-row">{{ $t('runtime.dailyUsersHint') }}</div>
        </div>
        <div class="ctrl">
          <input type="number" class="input" v-model.number="cfg.daily_users" min="20" max="500">
        </div>
        <div class="impact">
          <span>{{ $t('runtime.covers', { pct: '15%' }) }}</span>
          <span class="muted">{{ $t('runtime.actual', { n: 89, total: 120 }) }}</span>
        </div>
      </div>

      <div class="setting-row">
        <div>
          <div class="nm">{{ $t('runtime.douyinConcurrency') }}</div>
          <div class="hint-row">{{ $t('runtime.douyinConcurrencyHint') }}</div>
        </div>
        <div class="ctrl">
          <input
            v-model.number="cfg.douyin_max_concurrency"
            data-testid="douyin-concurrency"
            type="number"
            class="input"
            min="1"
            max="20"
          >
        </div>
        <div class="impact">
          <span>{{ $t('runtime.isolatedContexts', { n: cfg.douyin_max_concurrency }) }}</span>
          <span class="muted">{{ $t('runtime.accountLockHint') }}</span>
        </div>
      </div>
    </div>

    <div class="card section">
      <div class="section-hd">
        <div>
          <h3>{{ $t('runtime.intervalsTitle') }}</h3>
          <p>{{ $t('runtime.intervalsHint') }}</p>
        </div>
      </div>

      <div class="setting-row">
        <div>
          <div class="nm">{{ $t('runtime.commentInterval') }}</div>
          <div class="hint-row">{{ $t('runtime.commentIntervalHint') }}</div>
        </div>
        <div class="ctrl">
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
            <input type="number" class="input" v-model.number="cfg.comment_interval_min" min="1" max="60">
            <input type="number" class="input" v-model.number="cfg.comment_interval_max" min="1" max="120">
          </div>
        </div>
        <div class="impact">
          <span>{{ $t('runtime.range', { min: cfg.comment_interval_min, max: cfg.comment_interval_max }) }}</span>
          <span class="muted">{{ $t('runtime.recommended5_15') }}</span>
        </div>
      </div>

      <div class="setting-row">
        <div>
          <div class="nm">{{ $t('runtime.dmInterval') }}</div>
          <div class="hint-row">{{ $t('runtime.dmIntervalHint') }}</div>
        </div>
        <div class="ctrl">
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
            <input type="number" class="input" v-model.number="cfg.dm_interval_min" min="1" max="60">
            <input type="number" class="input" v-model.number="cfg.dm_interval_max" min="1" max="120">
          </div>
        </div>
        <div class="impact">
          <span>{{ $t('runtime.range', { min: cfg.dm_interval_min, max: cfg.dm_interval_max }) }}</span>
          <span class="muted">{{ $t('runtime.recommended10_25') }}</span>
        </div>
      </div>

      <div class="setting-row">
        <div>
          <div class="nm">{{ $t('runtime.gapTitle') }}</div>
          <div class="hint-row">{{ $t('runtime.gapHint') }}</div>
        </div>
        <div class="ctrl">
          <div style="display:grid;grid-template-columns:1fr auto;gap:8px;align-items:center">
            <input type="range" min="6" max="72" v-model.number="cfg.comment_dm_gap_hours">
            <span style="font-family:var(--font-mono);font-size:13px;font-weight:600">{{ cfg.comment_dm_gap_hours }} h</span>
          </div>
        </div>
        <div class="impact">
          <span>{{ $t('runtime.currentGap', { n: cfg.comment_dm_gap_hours }) }}</span>
          <span class="muted">{{ $t('runtime.simulateHuman') }}</span>
        </div>
      </div>
    </div>

    <div class="card section">
      <div class="section-hd">
        <div>
          <h3>{{ $t('runtime.scheduleTitle') }}</h3>
          <p>{{ $t('runtime.scheduleHint') }}</p>
        </div>
        <button class="btn brand sm" type="button" @click="beginCreateSchedule">
          {{ $t('runtime.newSchedule') }}
        </button>
      </div>

      <div
        v-if="scheduleError"
        class="schedule-alert error"
        role="alert"
      >
        <div>
          <strong>{{ scheduleError.code || $t('runtime.scheduleError') }}</strong>
          <span>{{ scheduleError.message }}</span>
        </div>
        <button class="btn sm" type="button" @click="loadSchedules">{{ $t('common.retry') }}</button>
      </div>

      <div v-if="schedulesLoading" class="schedule-state">
        {{ $t('runtime.loadingSchedules') }}
      </div>
      <div v-else-if="schedules.length === 0 && !scheduleEditorOpen" class="schedule-state empty">
        <strong>{{ $t('runtime.noSchedules') }}</strong>
        <span>{{ $t('runtime.noSchedulesHint') }}</span>
      </div>
      <div v-else class="schedule-list">
        <article v-for="schedule in schedules" :key="schedule.id" class="schedule-row">
          <div class="schedule-main">
            <div class="schedule-title">
              <strong>{{ schedule.name }}</strong>
              <span :class="['platform-pill', schedule.platform]">
                {{ schedule.platform === 'douyin' ? $t('accounts.douyin') : 'TikTok' }}
              </span>
              <span :class="['enabled-pill', schedule.enabled ? 'on' : 'off']">
                {{ schedule.enabled ? $t('runtime.enabled') : $t('runtime.disabled') }}
              </span>
            </div>
            <div class="schedule-meta">
              <code>{{ schedule.cronExpression }}</code>
              <span>{{ schedule.timezone }}</span>
              <span>{{ accountModeLabel(schedule.accountMode, schedule.accountId) }}</span>
            </div>
            <div class="schedule-stages">
              <span v-for="stage in schedule.stages" :key="stage">{{ $t(`pipeline.${stage}`) }}</span>
            </div>
          </div>
          <div class="schedule-times">
            <span>{{ $t('runtime.nextRun') }}</span>
            <b>{{ formatDateTime(schedule.nextRunAt) }}</b>
          </div>
          <div class="schedule-actions">
            <button class="btn sm" type="button" @click="beginEditSchedule(schedule)">
              {{ $t('common.edit') }}
            </button>
            <button class="btn sm danger" type="button" @click="removeSchedule(schedule)">
              {{ $t('common.delete') }}
            </button>
          </div>
        </article>
      </div>

      <form v-if="scheduleEditorOpen" class="schedule-editor" @submit.prevent="saveSchedule">
        <div class="editor-head">
          <div>
            <strong>{{ scheduleDraft.id ? $t('runtime.editSchedule') : $t('runtime.newSchedule') }}</strong>
            <span>{{ $t('runtime.scheduleEditorHint') }}</span>
          </div>
          <label class="enabled-control">
            <input v-model="scheduleDraft.enabled" type="checkbox">
            <span>{{ scheduleDraft.enabled ? $t('runtime.enabled') : $t('runtime.disabled') }}</span>
          </label>
        </div>

        <div
          v-if="scheduleDraft.platform === 'tiktok' && tiktokCapability && !tiktokCapability.available"
          :class="['schedule-alert', scheduleDraft.enabled ? 'warning' : 'neutral']"
        >
          <div>
            <strong>{{ tiktokCapability.code || 'fingerprint_provider_unavailable' }}</strong>
            <span>
              {{ scheduleDraft.enabled
                ? (tiktokCapability.message || $t('runtime.tiktokProviderBlocked'))
                : $t('runtime.disabledTikTokSaveable') }}
            </span>
          </div>
        </div>

        <div class="editor-grid">
          <label class="field span-2">
            <span>{{ $t('runtime.scheduleName') }}</span>
            <input v-model.trim="scheduleDraft.name" class="input" maxlength="100" required>
          </label>
          <label class="field">
            <span>{{ $t('pipeline.platform') }}</span>
            <select v-model="scheduleDraft.platform" class="select" @change="onSchedulePlatformChange">
              <option value="douyin">{{ $t('accounts.douyin') }}</option>
              <option value="tiktok">TikTok</option>
            </select>
          </label>
          <label class="field">
            <span>{{ $t('pipeline.accountStrategy') }}</span>
            <select v-model="scheduleDraft.accountMode" class="select" @change="onScheduleAccountModeChange">
              <option value="auto">{{ $t('pipeline.accountAuto') }}</option>
              <option value="specified">{{ $t('pipeline.accountSpecified') }}</option>
            </select>
          </label>
          <label v-if="scheduleDraft.accountMode === 'specified'" class="field span-2">
            <span>{{ $t('pipeline.account') }}</span>
            <select v-model="scheduleDraft.accountId" class="select" required>
              <option :value="null" disabled>{{ $t('pipeline.selectAccount') }}</option>
              <option v-for="account in scheduleAccounts" :key="account.id" :value="account.id">
                @{{ account.username }}
              </option>
            </select>
          </label>
          <label class="field">
            <span>CRON</span>
            <input
              v-model.trim="scheduleDraft.cronExpression"
              class="input mono"
              placeholder="0 9 * * *"
              required
            >
          </label>
          <label class="field">
            <span>{{ $t('runtime.timezone') }}</span>
            <select v-model="scheduleDraft.timezone" class="select">
              <option value="Asia/Shanghai">Asia/Shanghai</option>
              <option value="UTC">UTC</option>
              <option value="America/Los_Angeles">America/Los_Angeles</option>
              <option value="Europe/London">Europe/London</option>
            </select>
          </label>
        </div>

        <fieldset class="stage-fieldset">
          <legend>{{ $t('pipeline.stages') }}</legend>
          <label v-for="stage in stageOptions" :key="stage">
            <input v-model="scheduleDraft.stages" type="checkbox" :value="stage">
            <span>{{ $t(`pipeline.${stage}`) }}</span>
          </label>
        </fieldset>

        <div class="editor-actions">
          <button class="btn" type="button" :disabled="scheduleSaving" @click="closeScheduleEditor">
            {{ $t('common.cancel') }}
          </button>
          <button class="btn brand" type="submit" :disabled="scheduleSaving">
            {{ scheduleSaving ? $t('common.saving') : $t('common.save') }}
          </button>
        </div>
      </form>
    </div>

    <div class="card section">
      <div class="section-hd">
        <div>
          <h3>{{ $t('runtime.keywordsTitle') }}</h3>
          <p>{{ $t('runtime.keywordsHint') }}</p>
        </div>
        <div style="display:flex;gap:8px">
          <button class="btn sm" @click="expandFromHistory">{{ $t('runtime.expandFromHistory') }}</button>
          <button class="btn sm" @click="importKeywordsCsv">{{ $t('runtime.importCsv') }}</button>
        </div>
      </div>

      <div class="kw-tags">
        <span v-for="(k, i) in keywords" :key="i" class="kw-tag">{{ k }} <span class="x" @click="keywords.splice(i, 1)">×</span></span>
        <button class="kw-add" @click="addKw">+ {{ $t('runtime.addKeyword') }}</button>
      </div>

      <div style="margin-top:14px;padding:12px 14px;background:var(--cyan-soft);border-radius:8px;font-size:12.5px;color:oklch(45% 0.12 200)">
        💡 <b>{{ $t('runtime.suggestion') }}:</b> {{ $t('runtime.suggestionBody', { kw1: 'importer 1688', r1: '14.2%', kw2: 'wholesale LED', r2: '11.6%' }) }}
      </div>
    </div>

    <div class="card section">
      <div class="section-hd">
        <div>
          <h3>{{ $t('runtime.antiBanTitle') }}</h3>
          <p>{{ $t('runtime.antiBanHint') }}</p>
        </div>
        <span class="chip ok"><span class="dot"></span> {{ $t('runtime.allOn') }}</span>
      </div>

      <div class="tip-list">
        <div v-for="t in tips" :key="t" class="tip"><div class="ic">✓</div><div v-html="t"></div></div>
      </div>
    </div>

    <div class="save-bar">
      <div class="left">⚠️ {{ $t('runtime.unsaved', { n: unsavedCount }) }}</div>
      <div class="right">
        <button class="btn" @click="discard">{{ $t('runtime.discard') }}</button>
        <button class="btn brand" @click="saveApply">{{ $t('runtime.saveApply') }} →</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  createPipelineSchedule,
  deletePipelineSchedule,
  getAccounts,
  getConfig,
  getPipelineCapabilities,
  listPipelineSchedules,
  updatePipelineConfig,
  updatePipelineSchedule,
} from '../api'
import type {
  AccountMode,
  PipelineCapabilities,
  PipelinePlatform,
  PipelineSchedule,
  PipelineSchedulePayload,
  PipelineStageName,
  PipelineRuntimeConfigPayload,
} from '../types/pipeline'

const { t } = useI18n()

interface RuntimeConfig {
  daily_comment_limit: number
  daily_dm_limit: number
  daily_users: number
  douyin_max_concurrency: number
  comment_interval_min: number
  comment_interval_max: number
  dm_interval_min: number
  dm_interval_max: number
  comment_dm_gap_hours: number
  tiktok_keywords: string
}

interface ScheduleAccount {
  id: number
  platform: PipelinePlatform
  username: string
  status: string
}

interface ScheduleDraft extends PipelineSchedulePayload {
  id: number | null
}

interface ApiErrorView {
  code: string
  message: string
}

const INITIAL_KEYWORDS = [
  'importer 1688', 'wholesale LED', 'sourcing agent', 'bulk buy China',
  'retail dropship', 'factory direct', 'private label', 'distributor',
  'OEM supplier', 'B2B marketplace',
]

const cfg = reactive<RuntimeConfig>({
  daily_comment_limit: 25,
  daily_dm_limit: 12,
  daily_users: 120,
  douyin_max_concurrency: 1,
  comment_interval_min: 3,
  comment_interval_max: 10,
  dm_interval_min: 8,
  dm_interval_max: 20,
  comment_dm_gap_hours: 24,
  tiktok_keywords: INITIAL_KEYWORDS.join(','),
})

const runtimeNumericKeys: Array<Exclude<keyof RuntimeConfig, 'tiktok_keywords'>> = [
  'daily_comment_limit',
  'daily_dm_limit',
  'daily_users',
  'douyin_max_concurrency',
  'comment_interval_min',
  'comment_interval_max',
  'dm_interval_min',
  'dm_interval_max',
  'comment_dm_gap_hours',
]

const keywords = ref<string[]>([...INITIAL_KEYWORDS])
const stageOptions: PipelineStageName[] = [
  'collect',
  'filter',
  'strategy',
  'outreach',
  'report',
  'iterate',
]

const schedules = ref<PipelineSchedule[]>([])
const scheduleAccountsAll = ref<ScheduleAccount[]>([])
const capabilities = ref<PipelineCapabilities | null>(null)
const schedulesLoading = ref(true)
const scheduleSaving = ref(false)
const scheduleEditorOpen = ref(false)
const scheduleError = ref<ApiErrorView | null>(null)
const scheduleDraft = reactive<ScheduleDraft>(emptyScheduleDraft())

const tiktokCapability = computed(() => capabilities.value?.platforms.tiktok || null)
const scheduleAccounts = computed(() => scheduleAccountsAll.value.filter(account =>
  account.platform === scheduleDraft.platform && account.status === 'logged_in',
))
const loggedInAccountCount = computed(() => scheduleAccountsAll.value.filter(
  account => account.status === 'logged_in',
).length)

function emptyScheduleDraft(): ScheduleDraft {
  return {
    id: null,
    name: '',
    platform: 'douyin',
    accountMode: 'auto',
    accountId: null,
    stages: [...stageOptions],
    cronExpression: '0 9 * * *',
    timezone: 'Asia/Shanghai',
    enabled: true,
    config: {},
  }
}

function extractApiError(error: unknown, fallback: string): ApiErrorView {
  const responseData = (error as {
    response?: { data?: { detail?: string | { code?: string; message?: string } } }
  })?.response?.data
  const detail = responseData?.detail
  const code = typeof detail === 'object' ? detail?.code || '' : ''
  const message = typeof detail === 'object'
    ? detail?.message || fallback
    : detail || (error as Error)?.message || fallback
  return { code, message }
}

function parseConfigNumber(value: unknown, fallback: number): number {
  const parsed = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

function normalizeKeywords(value: unknown): string[] {
  const raw = Array.isArray(value)
    ? value.map(item => String(item))
    : typeof value === 'string'
      ? value.split(',')
      : []
  return [...new Set(raw.map(item => item.trim()).filter(Boolean))]
}

function integerRangeError(
  value: unknown,
  field: string,
  min: number,
  max: number,
): string {
  return typeof value !== 'number'
    || !Number.isInteger(value)
    || value < min
    || value > max
    ? t('runtime.numberRangeError', { field, min, max })
    : ''
}

function validateRuntimeConfig(): string {
  const rangeChecks: Array<[unknown, string, number, number]> = [
    [cfg.daily_comment_limit, t('runtime.commentLimit'), 1, 50],
    [cfg.daily_dm_limit, t('runtime.dmLimit'), 1, 30],
    [cfg.daily_users, t('runtime.dailyUsers'), 20, 500],
    [cfg.douyin_max_concurrency, t('runtime.douyinConcurrency'), 1, 20],
    [cfg.comment_interval_min, t('runtime.commentIntervalMin'), 1, 60],
    [cfg.comment_interval_max, t('runtime.commentIntervalMax'), 1, 120],
    [cfg.dm_interval_min, t('runtime.dmIntervalMin'), 1, 60],
    [cfg.dm_interval_max, t('runtime.dmIntervalMax'), 1, 120],
    [cfg.comment_dm_gap_hours, t('runtime.gapTitle'), 6, 72],
  ]
  for (const [value, field, min, max] of rangeChecks) {
    const error = integerRangeError(value, field, min, max)
    if (error) return error
  }
  if (cfg.comment_interval_min > cfg.comment_interval_max) {
    return t('runtime.intervalOrderError', { field: t('runtime.commentInterval') })
  }
  if (cfg.dm_interval_min > cfg.dm_interval_max) {
    return t('runtime.intervalOrderError', { field: t('runtime.dmInterval') })
  }
  return ''
}

function beginCreateSchedule() {
  Object.assign(scheduleDraft, emptyScheduleDraft())
  scheduleError.value = null
  scheduleEditorOpen.value = true
}

function beginEditSchedule(schedule: PipelineSchedule) {
  Object.assign(scheduleDraft, {
    id: schedule.id,
    name: schedule.name,
    platform: schedule.platform,
    accountMode: schedule.accountMode,
    accountId: schedule.accountId,
    stages: [...schedule.stages],
    cronExpression: schedule.cronExpression,
    timezone: schedule.timezone,
    enabled: schedule.enabled,
    config: { ...schedule.config },
  })
  scheduleError.value = null
  scheduleEditorOpen.value = true
}

function closeScheduleEditor() {
  scheduleEditorOpen.value = false
  scheduleError.value = null
}

function onSchedulePlatformChange() {
  scheduleDraft.accountId = null
}

function onScheduleAccountModeChange() {
  if (scheduleDraft.accountMode === 'auto') scheduleDraft.accountId = null
}

function accountModeLabel(mode: AccountMode, accountId: number | null) {
  if (mode === 'auto') return t('pipeline.accountAuto')
  const account = scheduleAccountsAll.value.find(item => item.id === accountId)
  return account ? `@${account.username}` : t('runtime.accountUnavailable')
}

function formatDateTime(value: string | null) {
  if (!value) return t('runtime.notScheduled')
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString()
}

async function loadSchedules() {
  schedulesLoading.value = true
  scheduleError.value = null
  try {
    const { data } = await listPipelineSchedules()
    schedules.value = data?.items || []
  } catch (error) {
    schedules.value = []
    scheduleError.value = extractApiError(error, t('runtime.scheduleLoadError'))
  } finally {
    schedulesLoading.value = false
  }
}

async function loadScheduleDependencies() {
  const [accountsResult, capabilitiesResult] = await Promise.allSettled([
    getAccounts(),
    getPipelineCapabilities(),
  ])
  if (accountsResult.status === 'fulfilled') {
    const raw = Array.isArray(accountsResult.value.data) ? accountsResult.value.data : []
    scheduleAccountsAll.value = raw
      .filter((account: any) => account.platform === 'tiktok' || account.platform === 'douyin')
      .map((account: any) => ({
        id: Number(account.id),
        platform: account.platform,
        username: String(account.username || ''),
        status: String(account.status || ''),
      }))
  }
  if (capabilitiesResult.status === 'fulfilled') {
    capabilities.value = capabilitiesResult.value.data
  }
}

async function saveSchedule() {
  if (!scheduleDraft.name.trim()) {
    ElMessage.warning(t('runtime.scheduleNameRequired'))
    return
  }
  if (scheduleDraft.stages.length === 0) {
    ElMessage.warning(t('pipeline.submitNeedStage'))
    return
  }
  if (scheduleDraft.accountMode === 'specified' && !scheduleDraft.accountId) {
    ElMessage.warning(t('pipeline.submitNeedAccount'))
    return
  }

  const payload: PipelineSchedulePayload = {
    name: scheduleDraft.name.trim(),
    platform: scheduleDraft.platform,
    accountMode: scheduleDraft.accountMode,
    accountId: scheduleDraft.accountMode === 'specified' ? scheduleDraft.accountId : null,
    stages: [...scheduleDraft.stages],
    cronExpression: scheduleDraft.cronExpression.trim(),
    timezone: scheduleDraft.timezone,
    enabled: scheduleDraft.enabled,
    config: { ...(scheduleDraft.config || {}) },
  }

  scheduleSaving.value = true
  scheduleError.value = null
  try {
    if (scheduleDraft.id) {
      await updatePipelineSchedule(scheduleDraft.id, payload)
    } else {
      await createPipelineSchedule(payload)
    }
    ElMessage.success(t('runtime.scheduleSaved'))
    scheduleEditorOpen.value = false
    await loadSchedules()
  } catch (error) {
    scheduleError.value = extractApiError(error, t('runtime.scheduleSaveError'))
  } finally {
    scheduleSaving.value = false
  }
}

async function removeSchedule(schedule: PipelineSchedule) {
  try {
    await ElMessageBox.confirm(
      t('runtime.deleteScheduleConfirm', { name: schedule.name }),
      t('runtime.deleteSchedule'),
      {
        confirmButtonText: t('common.delete'),
        cancelButtonText: t('common.cancel'),
        type: 'warning',
      },
    )
    await deletePipelineSchedule(schedule.id)
    ElMessage.success(t('runtime.scheduleDeleted'))
    await loadSchedules()
  } catch (error: any) {
    if (error === 'cancel' || error === 'close') return
    scheduleError.value = extractApiError(error, t('runtime.scheduleDeleteError'))
  }
}

function addKw() {
  const k = window.prompt('新增关键词')
  if (k?.trim()) {
    keywords.value.push(k.trim())
    ElMessage.success(`已添加关键词：${k.trim()}`)
  }
}

async function expandFromHistory() {
  try {
    const { data } = await getConfig()
    const kws = data?.tiktok_keywords
    if (Array.isArray(kws) && kws.length > 0) {
      const newKws = kws.filter((k: string) => !keywords.value.includes(k))
      keywords.value.push(...newKws)
      ElMessage.success(`从历史数据扩展了 ${newKws.length} 个关键词`)
    } else {
      ElMessage.info('暂无历史关键词数据')
    }
  } catch {
    ElMessage.error('获取历史数据失败')
  }
}

function importKeywordsCsv() {
  const input = document.createElement('input')
  input.type = 'file'
  input.accept = '.csv,.txt'
  input.onchange = async (e) => {
    const file = (e.target as HTMLInputElement).files?.[0]
    if (!file) return
    const text = await file.text()
    const lines = text.split(/[\n,]/).map(l => l.trim()).filter(Boolean)
    const newKws = lines.filter(l => !keywords.value.includes(l))
    keywords.value.push(...newKws)
    ElMessage.success(`从 CSV 导入了 ${newKws.length} 个关键词`)
  }
  input.click()
}

const initialCfg = JSON.parse(JSON.stringify(cfg))
const initialKeywords = [...keywords.value]
const unsavedCount = computed(() => {
  let n = 0
  for (const k of Object.keys(cfg) as Array<keyof RuntimeConfig>) if (cfg[k] !== initialCfg[k]) n++
  if (JSON.stringify(keywords.value) !== JSON.stringify(initialKeywords)) n++
  return n
})

function discard() {
  Object.assign(cfg, initialCfg)
  keywords.value = [...initialKeywords]
  ElMessage.info('已撤销所有修改')
}

async function saveApply() {
  const validationError = validateRuntimeConfig()
  if (validationError) {
    ElMessage.warning(validationError)
    return
  }
  const payload: PipelineRuntimeConfigPayload = {
    daily_users: cfg.daily_users,
    daily_comment_limit: cfg.daily_comment_limit,
    daily_dm_limit: cfg.daily_dm_limit,
    comment_interval_min: cfg.comment_interval_min,
    comment_interval_max: cfg.comment_interval_max,
    dm_interval_min: cfg.dm_interval_min,
    dm_interval_max: cfg.dm_interval_max,
    comment_dm_gap_hours: cfg.comment_dm_gap_hours,
    tiktok_keywords: [...keywords.value],
    douyin_max_concurrency: cfg.douyin_max_concurrency,
  }
  try {
    await updatePipelineConfig(payload)
    Object.assign(initialCfg, JSON.parse(JSON.stringify(cfg)))
    initialKeywords.splice(0, initialKeywords.length, ...keywords.value)
    ElMessage.success(t('runtime.configSaved'))
  } catch (error) {
    const apiError = extractApiError(error, t('runtime.configSaveError'))
    ElMessage.error(apiError.code ? `${apiError.code}: ${apiError.message}` : apiError.message)
  }
}

async function loadConfig() {
  try {
    const { data } = await getConfig()
    if (!data) return
    // Persisted KV values may be strings, while mock/settings defaults are numbers.
    runtimeNumericKeys.forEach((key) => {
      if (key in data) {
        ;(cfg as any)[key] = parseConfigNumber(data[key], cfg[key])
      }
    })
    // Settings defaults use an array; persisted KV storage returns CSV.
    if ('tiktok_keywords' in data) {
      keywords.value = normalizeKeywords(data.tiktok_keywords)
    }
    // Re-snapshot baseline AFTER loaded
    const snap = JSON.parse(JSON.stringify(cfg))
    Object.keys(snap).forEach((k) => { initialCfg[k] = snap[k] })
    initialKeywords.splice(0, initialKeywords.length, ...keywords.value)
  } catch (error) {
    const apiError = extractApiError(error, t('runtime.configLoadError'))
    ElMessage.error(apiError.code ? `${apiError.code}: ${apiError.message}` : apiError.message)
  }
}
onMounted(() => {
  void loadConfig()
  void loadSchedules()
  void loadScheduleDependencies()
})

const tips = [
  '随机化操作间隔 <b>3-15 分钟</b>，避免固定节律',
  '穿插<b>浏览 / 点赞 / 观看</b>等正常行为',
  '避开凌晨 <b>00:00 – 08:00</b>，仅在 <b>09:00 – 21:00</b> 操作',
  '每条私信/评论内容由 <b>LLM 动态生成</b>，避免完全重复',
  '<b>1-3 个账号</b>轮换使用，单账号日上限',
  '使用 <b>住宅代理 IP</b>，避免数据中心 IP',
  '新号前 <b>7 天</b> 养护期不执行推广',
  'Cookie 每 <b>6 小时</b>自动检测，过期立即切换',
]
</script>

<style scoped>
.section { padding: 22px 24px; margin-bottom: 16px; }
.section-hd { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 18px; }
.section-hd h3 { font-size: 15px; font-weight: 600; margin: 0 0 4px; }
.section-hd p { font-size: 12.5px; color: var(--muted); margin: 0; }

.setting-row { display: grid; grid-template-columns: 1fr 220px 1fr; gap: 16px; padding: 14px 0; border-bottom: 1px solid var(--border); align-items: start; }
.setting-row:last-child { border-bottom: 0; }
.setting-row .nm { font-size: 13.5px; font-weight: 600; }
.setting-row .hint-row { font-size: 12px; color: var(--muted); margin-top: 4px; line-height: 1.5; }
.setting-row .ctrl input { width: 100%; }
.setting-row .impact { font-size: 11.5px; color: var(--fg-2); display: flex; flex-direction: column; gap: 4px; }
.setting-row .impact b { color: var(--ok); }

input[type="range"] { width: 100%; accent-color: var(--brand); }

.schedule-state {
  min-height: 112px;
  display: grid;
  place-items: center;
  color: var(--muted);
  font-size: 13px;
}
.schedule-state.empty { align-content: center; gap: 5px; }
.schedule-state.empty strong { color: var(--fg); font-size: 14px; }
.schedule-list { display: grid; gap: 8px; }
.schedule-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 170px auto;
  gap: 18px;
  align-items: center;
  padding: 14px 16px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  transition: border-color 140ms ease, background 140ms ease;
}
.schedule-row:hover { border-color: var(--border-strong); background: var(--bg-sub); }
.schedule-title { display: flex; align-items: center; flex-wrap: wrap; gap: 7px; }
.schedule-title strong { font-size: 13.5px; }
.platform-pill,
.enabled-pill {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 2px 7px;
  border-radius: 5px;
  font-size: 10.5px;
  font-weight: 600;
}
.platform-pill.tiktok { background: var(--err-soft); color: var(--err); }
.platform-pill.douyin { background: var(--info-soft); color: var(--info); }
.enabled-pill.on { background: var(--ok-soft); color: var(--ok); }
.enabled-pill.off { background: var(--bg-sub); color: var(--muted); border: 1px solid var(--border); }
.schedule-meta,
.schedule-stages { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 7px; color: var(--muted); font-size: 11.5px; }
.schedule-meta code { color: var(--fg-2); font-family: var(--font-mono); }
.schedule-stages span { padding: 2px 6px; border-radius: 4px; background: var(--bg-sub); }
.schedule-times { display: grid; gap: 4px; font-size: 11px; color: var(--muted); }
.schedule-times b { font-size: 11.5px; color: var(--fg-2); font-weight: 500; }
.schedule-actions { display: flex; gap: 6px; }
.btn.danger { color: var(--err); }
.schedule-alert {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 11px 13px;
  margin-bottom: 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  font-size: 12px;
}
.schedule-alert > div { display: grid; gap: 2px; }
.schedule-alert strong { font-family: var(--font-mono); font-size: 11px; }
.schedule-alert.error { background: var(--err-soft); border-color: color-mix(in oklch, var(--err) 24%, var(--border)); color: var(--err); }
.schedule-alert.warning { background: var(--warn-soft); border-color: color-mix(in oklch, var(--warn) 28%, var(--border)); color: var(--fg-2); }
.schedule-alert.neutral { background: var(--bg-sub); color: var(--fg-2); }
.schedule-editor { margin-top: 16px; padding: 18px; border: 1px solid var(--border-strong); border-radius: 10px; background: var(--bg-sub); }
.editor-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 16px; }
.editor-head > div { display: grid; gap: 4px; }
.editor-head strong { font-size: 14px; }
.editor-head span { color: var(--muted); font-size: 12px; }
.enabled-control { display: inline-flex; align-items: center; gap: 7px; font-size: 12px; color: var(--fg-2); cursor: pointer; }
.editor-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.field { display: grid; gap: 6px; }
.field > span { color: var(--muted); font-size: 11.5px; font-weight: 600; }
.field .select,
.field .input { width: 100%; min-width: 0; }
.field.span-2 { grid-column: span 2; }
.mono { font-family: var(--font-mono); }
.stage-fieldset { display: flex; flex-wrap: wrap; gap: 8px; margin: 16px 0 0; padding: 0; border: 0; }
.stage-fieldset legend { width: 100%; margin-bottom: 2px; color: var(--muted); font-size: 11.5px; font-weight: 600; }
.stage-fieldset label { display: inline-flex; align-items: center; gap: 6px; min-height: 32px; padding: 0 9px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); font-size: 12px; cursor: pointer; }
.editor-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 18px; }

.kw-tags { display: flex; flex-wrap: wrap; gap: 6px; padding: 10px 12px; background: var(--bg-sub); border-radius: 8px; min-height: 44px; }
.kw-tag { display: inline-flex; align-items: center; gap: 5px; padding: 4px 10px; background: var(--surface); border: 1px solid var(--border); border-radius: 999px; font-size: 12.5px; }
.kw-tag .x { color: var(--muted); cursor: pointer; font-size: 14px; }
.kw-tag .x:hover { color: var(--err); }
.kw-add { padding: 4px 12px; background: transparent; border: 1px dashed var(--border-strong); border-radius: 999px; color: var(--muted); font-size: 12px; cursor: pointer; }

.tip-list { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; margin-top: 16px; }
.tip { padding: 12px 14px; background: var(--bg-sub); border-radius: 8px; display: flex; gap: 10px; align-items: flex-start; font-size: 12.5px; color: var(--fg-2); line-height: 1.5; }
.tip .ic { width: 22px; height: 22px; border-radius: 50%; flex-shrink: 0; background: var(--ok-soft); color: oklch(42% 0.16 150); display: grid; place-items: center; font-size: 12px; font-weight: 700; }
.tip b { color: var(--fg); }

.save-bar { position: sticky; bottom: 16px; display: flex; justify-content: space-between; align-items: center; padding: 12px 18px; background: oklch(14% 0.012 280); color: oklch(92% 0.005 280); border-radius: 10px; margin-top: 18px; box-shadow: 0 12px 32px oklch(0% 0 0 / 0.18); }
.save-bar .left { font-size: 13px; }
.save-bar .left b { color: #fff; }
.save-bar .right { display: flex; gap: 8px; }
.save-bar .btn { background: oklch(20% 0.012 280); color: #fff; border-color: oklch(28% 0.012 280); }
.save-bar .btn.brand { background: var(--brand); border-color: var(--brand); color: #fff; }

@media (max-width: 900px) {
  .setting-row { grid-template-columns: 1fr 180px; }
  .setting-row .impact { grid-column: 1 / -1; }
  .schedule-row { grid-template-columns: minmax(0, 1fr) auto; }
  .schedule-times { grid-column: 1; }
  .schedule-actions { grid-column: 2; grid-row: 1 / span 2; }
}

@media (max-width: 640px) {
  .section { padding: 18px 16px; }
  .section-hd { gap: 12px; }
  .setting-row { grid-template-columns: 1fr; }
  .setting-row .impact { grid-column: auto; }
  .schedule-row { grid-template-columns: 1fr; }
  .schedule-times,
  .schedule-actions { grid-column: auto; grid-row: auto; }
  .editor-grid { grid-template-columns: 1fr; }
  .field.span-2 { grid-column: auto; }
  .save-bar { align-items: flex-start; gap: 12px; }
}

@media (prefers-reduced-motion: reduce) {
  .schedule-row { transition: none; }
}
</style>
