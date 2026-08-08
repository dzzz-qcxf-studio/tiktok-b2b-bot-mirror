#!/usr/bin/env node
/**
 * Smoke test — pure Node, no test runner needed.
 * Verifies the project is wired correctly without booting a browser.
 *
 * Run with: `npm run test` or `node scripts/smoke.mjs`
 *
 * Checks:
 *   1. i18n key parity (zh-CN ↔ en-US)
 *   2. mock API returns expected shapes
 *   3. Router has 10 routes registered
 *   4. All 9 view .vue files exist and are non-empty
 *   5. .env.development + design-system.css present
 */

import { readFileSync, existsSync, statSync, readdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const __dirname = dirname(fileURLToPath(import.meta.url))
const root = join(__dirname, '..')

let passed = 0
let failed = 0
const errors = []

function ok(label) { passed++; console.log(`  ✓ ${label}`) }
function fail(label, err) { failed++; errors.push({ label, err }); console.log(`  ✗ ${label}\n      ${err}`) }
function group(name) { console.log(`\n${name}`) }

// ---------- 1. i18n parity ----------
group('i18n key parity (zh-CN ↔ en-US)')

const zhCN = (await import('../src/i18n/zh-CN.ts')).default
const enUS = (await import('../src/i18n/en-US.ts')).default

function flattenKeys(obj, prefix = '') {
  const keys = []
  for (const k of Object.keys(obj || {})) {
    const path = prefix ? `${prefix}.${k}` : k
    const v = obj[k]
    if (v && typeof v === 'object' && !Array.isArray(v)) keys.push(...flattenKeys(v, path))
    else keys.push(path)
  }
  return keys.sort()
}

const zhKeys = new Set(flattenKeys(zhCN))
const enKeys = new Set(flattenKeys(enUS))

const missingInEn = [...zhKeys].filter(k => !enKeys.has(k))
const missingInZh = [...enKeys].filter(k => !zhKeys.has(k))

if (missingInEn.length === 0) ok(`all ${zhKeys.size} zh-CN keys present in en-US`)
else fail('zh-CN keys missing in en-US', missingInEn.join(', '))

if (missingInZh.length === 0) ok(`all ${enKeys.size} en-US keys present in zh-CN`)
else fail('en-US keys missing in zh-CN', missingInZh.join(', '))

// Empty value scan
for (const [name, obj] of [['zh-CN', zhCN], ['en-US', enUS]]) {
  const empties = []
  const walk = (o, p = '') => {
    for (const [k, v] of Object.entries(o || {})) {
      const path = p ? `${p}.${k}` : k
      if (v && typeof v === 'object') walk(v, path)
      else if (v === '' || v == null) empties.push(path)
    }
  }
  walk(obj)
  if (empties.length === 0) ok(`no empty values in ${name}`)
  else fail(`empty values in ${name}`, empties.join(', '))
}

// ---------- 2. mock API shape ----------
group('mock API payload shapes')

const mockMod = await import('../src/api/mock.ts')
const mockApi = mockMod.default || mockMod.mockApi || mockMod
const cases = [
  { name: 'getDashboard', fn: () => mockApi.getDashboard(), expect: d => d.overview && Array.isArray(d.keywords) && d.keywords.length > 0 && d.overview.today_reply_rate > 0 },
  { name: 'getUsers', fn: () => mockApi.getUsers({}), expect: d => Array.isArray(d.items) && d.items.length === d.total && d.total >= 10 },
  { name: 'getUsers[qualified]', fn: () => mockApi.getUsers({ status: 'qualified' }), expect: d => d.items.every(u => u.status === 'qualified') },
  { name: 'getPipelineEvents', fn: () => mockApi.getPipelineEvents(50), expect: d => Array.isArray(d) && d.length > 0 && d[0].timestamp && d[0].type },
  { name: 'getTrendReport(30)', fn: () => mockApi.getTrendReport(30), expect: d => d.length === 30 && d[0].date && d[0].reply_rate >= 0 },
  { name: 'getDailyReport', fn: () => mockApi.getDailyReport(), expect: d => d.date && typeof d.reply_rate === 'number' },
  { name: 'getAccounts', fn: () => mockApi.getAccounts(), expect: d => Array.isArray(d) && d.length >= 3 && d[0] && d[0].platform && d[0].status },
  {
    name: 'updateAccountMetadata persists without changing isolation alias',
    fn: async () => {
      await mockApi.updateAccountMetadata(1, 'Smoke sales account')
      return mockApi.getAccounts()
    },
    expect: d => {
      const account = d.find(item => item.id === 1)
      return account?.display_name === 'Smoke sales account'
        && account.username === 'delong_official_01'
    },
  },
  {
    name: 'getConfig',
    fn: () => mockApi.getConfig(),
    expect: d => d.llm_model === 'deepseek-v4-pro'
      && d.has_api_key === true
      && Number.isInteger(d.douyin_max_concurrency)
      && d.douyin_max_concurrency >= 1,
  },
  { name: 'getWordcloud', fn: () => mockApi.getWordcloud(), expect: d => Array.isArray(d) && d.length > 0 },
  { name: 'login ok', fn: () => mockApi.login('test@x.com', 'pass1234'), expect: d => !!d.access_token },
  { name: 'login rejects short', fn: () => mockApi.login('test@x.com', 'ab'), expect: () => false /* should reject */ },
  {
    name: 'runPipeline explicit compatibility',
    fn: () => mockApi.runPipeline(
      ['collect','filter'],
      { platform: 'douyin', accountMode: 'auto' },
    ),
    expect: d => d.job
      && d.job.platform === 'douyin'
      && d.job.requestedStages.join(',') === 'collect,filter',
  },
  {
    name: 'runPipeline requires platform selection',
    fn: () => mockApi.runPipeline(['collect']),
    expect: () => false /* should reject */,
    errorCode: 'pipeline_selection_required',
  },
  { name: 'addAccount', fn: async () => { await mockApi.addAccount('tiktok', 'test_' + Date.now()); return mockApi.getAccounts() }, expect: d => Array.isArray(d) && d.length >= 4 },
  {
    name: 'createPipelineJob',
    fn: () => mockApi.createPipelineJob({
      platform: 'douyin',
      accountMode: 'specified',
      accountId: 1,
      stages: ['collect', 'filter'],
    }),
    expect: d => d.job
      && d.job.platform === 'douyin'
      && d.job.accountMode === 'specified'
      && d.job.accountId === 1
      && Array.isArray(d.job.requestedStages)
      && d.job.requestedStages.join(',') === 'collect,filter',
  },
  {
    name: 'listPipelineJobs',
    fn: () => mockApi.listPipelineJobs({ platform: 'douyin', limit: 10, offset: 0 }),
    expect: d => Array.isArray(d.items)
      && typeof d.total === 'number'
      && d.limit === 10
      && d.offset === 0
      && d.items.every(job => job.platform === 'douyin'),
  },
  {
    name: 'getPipelineCapabilities',
    fn: () => mockApi.getPipelineCapabilities(),
    expect: d => d.platforms
      && d.platforms.tiktok
      && d.platforms.douyin
      && typeof d.platforms.douyin.maxConcurrency === 'number',
  },
  {
    name: 'listPipelineSchedules',
    fn: () => mockApi.listPipelineSchedules(),
    expect: d => Array.isArray(d.items)
      && typeof d.total === 'number'
      && d.items.every(schedule => typeof schedule.enabled === 'boolean'),
  },
]

for (const c of cases) {
  try {
    const res = await c.fn()
    const data = res.data
    if (c.name === 'login rejects short') {
      fail('login short pwd', 'expected rejection, got success')
      continue
    }
    if (c.expect(data)) ok(c.name)
    else fail(c.name, 'shape mismatch: ' + JSON.stringify(data).slice(0, 120))
  } catch (e) {
    if (c.name === 'login rejects short') ok('login rejects short pwd')
    else if (c.errorCode && (e.code === c.errorCode || e.response?.data?.detail?.code === c.errorCode)) {
      ok(`${c.name} (${c.errorCode})`)
    }
    else fail(c.name, e.message || String(e))
  }
}

group('mock unified pipeline lifecycle')

try {
  const created = (await mockApi.createPipelineJob({
    platform: 'douyin',
    accountMode: 'auto',
    stages: ['collect'],
  })).data.job
  const fetched = (await mockApi.getPipelineJob(created.id)).data.job
  const queuedPage = (await mockApi.listPipelineJobs({
    platform: 'douyin',
    status: 'queued',
    limit: 1,
    offset: 0,
  })).data
  const cancelled = (await mockApi.cancelPipelineJob(created.id)).data.job
  let invalidRetryCode = ''
  try {
    await mockApi.retryPipelineJob(cancelled.id)
  } catch (e) {
    invalidRetryCode = e.code || e.response?.data?.detail?.code || ''
  }
  const failedBeforeCancel = (await mockApi.getPipelineJob('mock-job-tiktok-blocked-history')).data.job
  const failedAfterCancel = (await mockApi.cancelPipelineJob(failedBeforeCancel.id)).data.job
  const retried = (await mockApi.retryPipelineJob(failedBeforeCancel.id)).data.job
  if (
    fetched.id === created.id
    && queuedPage.items.length === 1
    && queuedPage.items[0].status === 'queued'
    && cancelled.status === 'cancelled'
    && invalidRetryCode === 'job_not_retryable'
    && failedAfterCancel.status === 'failed'
    && retried.retryOfJobId === failedBeforeCancel.id
    && retried.status === 'queued'
  ) ok('job get/filter/pagination/cancel/retry state rules')
  else fail('job get/filter/pagination/cancel/retry state rules', 'state transition mismatch')
} catch (e) {
  fail('job get/filter/pagination/cancel/retry state rules', e.message || String(e))
}

try {
  const scheduleConfig = { nested: { limit: 2 } }
  const created = (await mockApi.createPipelineSchedule({
    name: 'smoke disabled schedule',
    platform: 'tiktok',
    accountMode: 'auto',
    stages: ['collect'],
    cronExpression: '0 9 * * *',
    timezone: 'Asia/Shanghai',
    enabled: false,
    configSnapshot: scheduleConfig,
  })).data.schedule
  scheduleConfig.nested.limit = 99
  const disabled = (await mockApi.listPipelineSchedules('tiktok')).data
  const beforeUpdate = Date.now()
  const updated = (await mockApi.updatePipelineSchedule(created.id, {
    name: 'smoke enabled schedule',
    platform: 'douyin',
    accountMode: 'auto',
    stages: ['collect', 'filter'],
    cronExpression: '*/15 * * * *',
    timezone: 'Asia/Shanghai',
    enabled: true,
  })).data.schedule
  await mockApi.deletePipelineSchedule(created.id)
  const afterDelete = (await mockApi.listPipelineSchedules()).data
  if (
    disabled.items.some(schedule => schedule.id === created.id && !schedule.enabled && schedule.nextRunAt === null)
    && disabled.items.find(schedule => schedule.id === created.id)?.config.nested.limit === 2
    && updated.enabled
    && updated.nextRunAt
    && Date.parse(updated.nextRunAt) > beforeUpdate
    && new Date(updated.nextRunAt).getUTCMinutes() % 15 === 0
    && !afterDelete.items.some(schedule => schedule.id === created.id)
  ) ok('schedule CRUD, disabled state, cron and deep config snapshot')
  else fail('schedule CRUD, disabled state, cron and deep config snapshot', 'CRUD state mismatch')
} catch (e) {
  fail('schedule CRUD, disabled state, cron and deep config snapshot', e.message || String(e))
}

try {
  const before = (await mockApi.getConfig()).data.douyin_max_concurrency
  const response = (await mockApi.setConfigKey('douyin_max_concurrency', '4')).data
  const saved = (await mockApi.getConfig()).data.douyin_max_concurrency
  let invalidCode = ''
  let invalidStatus = 0
  try {
    await mockApi.setConfigKey('douyin_max_concurrency', '1.5')
  } catch (e) {
    invalidCode = e.response?.data?.detail?.code || ''
    invalidStatus = e.response?.status || 0
  }
  const afterInvalid = (await mockApi.getConfig()).data.douyin_max_concurrency
  await mockApi.setConfigKey('douyin_max_concurrency', String(before))
  if (
    saved === 4
    && response.restartRequired === true
    && response.value === '4'
    && invalidCode === 'invalid_config_value'
    && invalidStatus === 422
    && afterInvalid === 4
  ) {
    ok('Douyin concurrency config matches real API validation and restart contract')
  } else {
    fail(
      'Douyin concurrency config matches real API validation and restart contract',
      JSON.stringify({ saved, response, invalidCode, invalidStatus, afterInvalid }),
    )
  }
} catch (e) {
  fail('Douyin concurrency config matches real API validation and restart contract', e.message || String(e))
}

try {
  const original = (await mockApi.getConfig()).data
  const originalPayload = {
    daily_users: original.daily_users,
    daily_comment_limit: original.daily_comment_limit,
    daily_dm_limit: original.daily_dm_limit,
    comment_interval_min: original.comment_interval_min,
    comment_interval_max: original.comment_interval_max,
    dm_interval_min: original.dm_interval_min,
    dm_interval_max: original.dm_interval_max,
    comment_dm_gap_hours: original.comment_dm_gap_hours,
    tiktok_keywords: Array.isArray(original.tiktok_keywords)
      ? [...original.tiktok_keywords]
      : String(original.tiktok_keywords).split(',').map(value => value.trim()).filter(Boolean),
    douyin_max_concurrency: original.douyin_max_concurrency,
  }
  const validPayload = {
    ...originalPayload,
    comment_interval_min: 20,
    comment_interval_max: 30,
    dm_interval_min: 25,
    dm_interval_max: 35,
    douyin_max_concurrency: originalPayload.douyin_max_concurrency === 4 ? 3 : 4,
  }
  const validResponse = (await mockApi.updatePipelineConfig(validPayload)).data
  const afterValid = structuredClone((await mockApi.getConfig()).data)
  let zeroCode = ''
  try {
    await mockApi.updatePipelineConfig({
      ...validPayload,
      daily_users: 300,
      comment_interval_min: 0,
    })
  } catch (e) {
    zeroCode = e.response?.data?.detail?.code || ''
  }
  const afterZero = structuredClone((await mockApi.getConfig()).data)
  let pairCode = ''
  try {
    await mockApi.updatePipelineConfig({
      ...validPayload,
      daily_users: 301,
      comment_interval_min: 40,
      comment_interval_max: 30,
    })
  } catch (e) {
    pairCode = e.response?.data?.detail?.code || ''
  }
  const afterPair = structuredClone((await mockApi.getConfig()).data)
  await mockApi.updatePipelineConfig(originalPayload)
  if (
    validResponse.status === 'ok'
    && validResponse.config.comment_interval_min === 20
    && validResponse.config.comment_interval_max === 30
    && validResponse.restartRequired === true
    && afterValid.comment_interval_min === 20
    && afterValid.comment_interval_max === 30
    && zeroCode === 'invalid_config_value'
    && pairCode === 'invalid_config_value'
    && afterZero.daily_users === validPayload.daily_users
    && afterZero.comment_interval_min === validPayload.comment_interval_min
    && afterPair.daily_users === validPayload.daily_users
    && afterPair.comment_interval_min === validPayload.comment_interval_min
    && afterPair.comment_interval_max === validPayload.comment_interval_max
  ) {
    ok('atomic Pipeline config validates before committing and never partially writes')
  } else {
    fail(
      'atomic Pipeline config validates before committing and never partially writes',
      JSON.stringify({ validResponse, afterValid, zeroCode, afterZero, pairCode, afterPair }),
    )
  }
} catch (e) {
  fail('atomic Pipeline config validates before committing and never partially writes', e.message || String(e))
}

try {
  const configSnapshot = { nested: { limit: 3 } }
  const created = (await mockApi.createPipelineJob({
    platform: 'douyin',
    accountMode: 'auto',
    stages: ['collect'],
    configSnapshot,
  })).data.job
  configSnapshot.nested.limit = 88
  const fetched = (await mockApi.getPipelineJob(created.id)).data.job
  if (fetched.configSnapshot.nested.limit === 3) ok('job deep config snapshot')
  else fail('job deep config snapshot', 'caller mutation leaked into stored job')
} catch (e) {
  fail('job deep config snapshot', e.message || String(e))
}

// ---------- 3. Unified pipeline source contract ----------
group('unified pipeline client contract')

const pipelineTypesPath = join(root, 'src', 'types', 'pipeline.ts')
if (!existsSync(pipelineTypesPath)) {
  fail('pipeline types module', 'src/types/pipeline.ts is missing')
} else {
  const pipelineTypesSrc = readFileSync(pipelineTypesPath, 'utf8')
  for (const symbol of [
    'PipelinePlatform',
    'AccountMode',
    'CreatePipelineJobPayload',
    'PipelineJob',
    'PipelineJobStage',
    'Stage',
    'PipelineSchedule',
    'Schedule',
    'PipelineCapabilities',
    'Capabilities',
    'PipelineRuntimeConfigPayload',
    'PipelineRuntimeConfigResponse',
  ]) {
    if (pipelineTypesSrc.includes(` ${symbol}`)) ok(`pipeline type ${symbol}`)
    else fail(`pipeline type ${symbol}`, 'symbol missing')
  }
}

const apiSrc = readFileSync(join(root, 'src', 'api', 'index.ts'), 'utf8')
for (const method of [
  'createPipelineJob',
  'listPipelineJobs',
  'getPipelineJob',
  'cancelPipelineJob',
  'retryPipelineJob',
  'getPipelineCapabilities',
  'createPipelineSchedule',
  'listPipelineSchedules',
  'updatePipelineSchedule',
  'deletePipelineSchedule',
  'updatePipelineConfig',
]) {
  if (apiSrc.includes(`export const ${method}`)) ok(`API export ${method}`)
  else fail(`API export ${method}`, 'named export missing')
}

if (
  /export const runPipeline[\s\S]{0,900}createPipelineJob\(/.test(apiSrc)
  && apiSrc.includes('pipeline_selection_required')
) {
  ok('runPipeline is a guarded createPipelineJob compatibility wrapper')
} else {
  fail('runPipeline compatibility wrapper', 'must require selection and delegate to createPipelineJob')
}

// ---------- 3b. Unified pipeline page contract ----------
group('unified pipeline page contract')

const pipelineViewSrc = readFileSync(join(root, 'src', 'views', 'Pipeline.vue'), 'utf8')
const appShellSrc = readFileSync(join(root, 'src', 'App.vue'), 'utf8')
const mobileShellSrc = appShellSrc.slice(appShellSrc.indexOf('@media (max-width: 700px)'))
const acquisitionCreatorSrc = readFileSync(
  join(root, 'src', 'components', 'AcquisitionJobCreator.vue'),
  'utf8',
)
const pipelineViewChecks = [
  ['single page embeds one acquisition creator', pipelineViewSrc.includes('<AcquisitionJobCreator') && pipelineViewSrc.includes('@created="handleAcquisitionCreated"') && !pipelineViewSrc.includes('data-testid="pipeline-platform-douyin"')],
  ['single page platform selector', acquisitionCreatorSrc.includes('data-testid="acquisition-platform-tiktok"') && acquisitionCreatorSrc.includes('data-testid="acquisition-platform-douyin"')],
  ['account mode and specified account selector', acquisitionCreatorSrc.includes('data-testid="acquisition-account-auto"') && acquisitionCreatorSrc.includes('data-testid="acquisition-account-specified"') && acquisitionCreatorSrc.includes('data-testid="acquisition-account-select"')],
  ['all six selectable stages', pipelineViewSrc.includes("'collect', 'filter', 'strategy', 'outreach', 'report', 'iterate'")],
  ['creates one atomic acquisition job with explicit selection', acquisitionCreatorSrc.includes('createAcquisitionJob(payload)') && acquisitionCreatorSrc.includes('buildAcquisitionJobPayload(draft.value)') && !pipelineViewSrc.includes('createPipelineJob')],
  ['capability preflight renders stable code', acquisitionCreatorSrc.includes('getPipelineCapabilities') && acquisitionCreatorSrc.includes('capability?.code') && acquisitionCreatorSrc.includes('capability?.message')],
  ['blocked capability prioritizes provider message', acquisitionCreatorSrc.includes('v-else-if="capability?.message"') && acquisitionCreatorSrc.includes('{{ capability.message }}')],
  ['job history pagination and refresh', pipelineViewSrc.includes('listPipelineJobs') && pipelineViewSrc.includes('historyOffset') && pipelineViewSrc.includes('refreshJobs')],
  ['job detail supports cancel and retry', pipelineViewSrc.includes('cancelPipelineJob') && pipelineViewSrc.includes('retryPipelineJob') && pipelineViewSrc.includes('canCancel') && pipelineViewSrc.includes('canRetry')],
  ['all durable job states are recognizable', ['queued', 'running', 'cancelling', 'succeeded', 'partial_failed', 'failed', 'interrupted', 'cancelled'].every(status => pipelineViewSrc.includes(`'${status}'`))],
  ['loading empty and error states', pipelineViewSrc.includes('historyLoading') && pipelineViewSrc.includes('historyError') && pipelineViewSrc.includes('pipeline.historyEmpty')],
  ['list refresh never replaces full selected detail', pipelineViewSrc.includes('refreshSelectedJobDetail') && !pipelineViewSrc.includes('selectedJob.value = selectedInPage')],
  ['async reads commit latest matching request only', ['historyRequestToken', 'detailRequestToken', 'offsetSnapshot', 'jobIdSnapshot'].every(token => pipelineViewSrc.includes(token)) && acquisitionCreatorSrc.includes('accountsRequestToken') && acquisitionCreatorSrc.includes('requestToken !== accountsRequestToken')],
  ['polling is non-reentrant and refreshes selected detail', pipelineViewSrc.includes('pollInFlight') && pipelineViewSrc.includes('if (pollInFlight) return') && /pollActiveJob[\s\S]{0,500}refreshSelectedJobDetail/.test(pipelineViewSrc)],
  ['job actions preserve a newer selection', pipelineViewSrc.includes('const targetId = selectedJob.value.id') && pipelineViewSrc.includes('selectedJobId.value === targetId') && pipelineViewSrc.includes('actionRequestToken')],
  ['account validity gates job creation', acquisitionCreatorSrc.includes('loggedInAccounts.value.length === 0') && acquisitionCreatorSrc.includes('draft.value.accountId = null') && acquisitionCreatorSrc.includes("code: 'account_required'")],
  ['segmented controls expose keyboard radio semantics', acquisitionCreatorSrc.includes('role="radio"') && acquisitionCreatorSrc.includes(':aria-checked=') && acquisitionCreatorSrc.includes('@keydown.left') && acquisitionCreatorSrc.includes('@keydown.right')],
  ['mobile shell releases content width and keeps navigation', mobileShellSrc.includes('inset: auto 0 0;') && mobileShellSrc.includes('overflow-x: auto;') && mobileShellSrc.includes('padding-bottom: calc(64px + env(safe-area-inset-bottom));') && /\.sb-foot\s*\{[\s\S]{0,260}display:\s*flex/.test(mobileShellSrc) && /\.sb-foot \.logout-btn\s*\{[\s\S]{0,180}min-height:\s*54px/.test(mobileShellSrc)],
]

for (const [label, condition] of pipelineViewChecks) {
  if (condition) ok(label)
  else fail(label, 'Pipeline.vue and AcquisitionJobCreator.vue do not satisfy the unified task console contract')
}

// ---------- 3c. Unified schedule and provider settings contract ----------
group('unified schedule and provider settings contract')

const configPipelineSrc = readFileSync(join(root, 'src', 'views', 'ConfigPipeline.vue'), 'utf8')
const configAccountsSrc = readFileSync(join(root, 'src', 'views', 'ConfigAccounts.vue'), 'utf8')
const runtimeNumericFields = [
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
const configChecks = [
  [
    'Douyin concurrency reads and persists through config API',
    configPipelineSrc.includes('douyin_max_concurrency')
      && configPipelineSrc.includes('getConfig')
      && configPipelineSrc.includes('updatePipelineConfig')
      && !configPipelineSrc.includes("localStorage.setItem('config_draft'"),
  ],
  [
    'unified schedule CRUD methods are used',
    ['createPipelineSchedule', 'listPipelineSchedules', 'updatePipelineSchedule', 'deletePipelineSchedule']
      .every(method => configPipelineSrc.includes(method)),
  ],
  [
    'schedule editor exposes the complete unified contract',
    ['platform', 'accountMode', 'accountId', 'stages', 'cronExpression', 'timezone', 'enabled']
      .every(field => configPipelineSrc.includes(field)),
  ],
  [
    'disabled TikTok schedule remains saveable when provider is blocked',
    configPipelineSrc.includes('scheduleDraft.enabled')
      && configPipelineSrc.includes("scheduleDraft.platform === 'tiktok'")
      && configPipelineSrc.includes('getPipelineCapabilities'),
  ],
  [
    'schedule errors render stable code and message',
    configPipelineSrc.includes('extractApiError')
      && configPipelineSrc.includes('detail?.code')
      && configPipelineSrc.includes('detail?.message'),
  ],
  [
    'account page renders global platform capability',
    configAccountsSrc.includes('getPipelineCapabilities')
      && configAccountsSrc.includes('fingerprint_provider_unavailable')
      && configAccountsSrc.includes('capability.code')
      && configAccountsSrc.includes('capability.message'),
  ],
  [
    'TikTok account cards show provider and profile configuration',
    configAccountsSrc.includes('browserProvider')
      && configAccountsSrc.includes('browserProfileId')
      && configAccountsSrc.includes('providerConfigured'),
  ],
  [
    'Douyin account page explains isolated configured concurrency',
    configAccountsSrc.includes('douyinConcurrency')
      && configAccountsSrc.includes('maxConcurrency'),
  ],
  [
    'runtime numeric inputs use numeric model modifiers',
    runtimeNumericFields.every(field =>
      configPipelineSrc.includes(`v-model.number="cfg.${field}"`),
    ),
  ],
  [
    'persisted keywords accept string and array forms',
    configPipelineSrc.includes('normalizeKeywords')
      && configPipelineSrc.includes('Array.isArray(value)')
      && configPipelineSrc.includes("typeof value === 'string'"),
  ],
  [
    'persisted numeric values use safe parsing',
    configPipelineSrc.includes('parseConfigNumber')
      && configPipelineSrc.includes('Number.isFinite(parsed)'),
  ],
  [
    'runtime save validates every numeric range and interval ordering',
    configPipelineSrc.includes('validateRuntimeConfig')
      && runtimeNumericFields.every(field => configPipelineSrc.includes(`cfg.${field}`))
      && configPipelineSrc.includes('cfg.comment_interval_min > cfg.comment_interval_max')
      && configPipelineSrc.includes('cfg.dm_interval_min > cfg.dm_interval_max')
      && /const validationError = validateRuntimeConfig\(\)[\s\S]{0,240}return/.test(configPipelineSrc),
  ],
  [
    'runtime intervals use the backend minimum of one',
    ['comment_interval_min', 'comment_interval_max', 'dm_interval_min', 'dm_interval_max']
      .every(field => configPipelineSrc.includes(`cfg.${field}`))
      && !/v-model\.number="cfg\.(?:comment|dm)_interval_(?:min|max)" min="0"/.test(configPipelineSrc)
      && configPipelineSrc.includes("[cfg.comment_interval_min, t('runtime.commentIntervalMin'), 1, 60]")
      && configPipelineSrc.includes("[cfg.dm_interval_min, t('runtime.dmIntervalMin'), 1, 60]"),
  ],
  [
    'runtime save uses one atomic Pipeline config request',
    configPipelineSrc.includes('updatePipelineConfig')
      && /async function saveApply\(\)[\s\S]{0,1800}await updatePipelineConfig\(payload\)/.test(configPipelineSrc)
      && !configPipelineSrc.includes('setConfigKey'),
  ],
  [
    'real client uses the atomic Pipeline config endpoint',
    apiSrc.includes("api.put<PipelineRuntimeConfigResponse>('/api/config/pipeline', payload)"),
  ],
  [
    'account subtitle uses live total and logged-in counts',
    configAccountsSrc.includes("$t('accounts.subtitle', { total: accounts.length, healthy: loggedInCount })")
      && !configAccountsSrc.includes("$t('accounts.subtitle')"),
  ],
  [
    'runtime combined limits use the live logged-in account count',
    configPipelineSrc.includes('loggedInAccountCount')
      && configPipelineSrc.includes('account.status ===')
      && configPipelineSrc.includes("account.status === 'logged_in'")
      && configPipelineSrc.includes('cfg.daily_comment_limit * loggedInAccountCount')
      && configPipelineSrc.includes('cfg.daily_dm_limit * loggedInAccountCount')
      && !configPipelineSrc.includes('cfg.daily_comment_limit * 3')
      && !configPipelineSrc.includes('cfg.daily_dm_limit * 3'),
  ],
]

for (const [label, condition] of configChecks) {
  if (condition) ok(label)
  else fail(label, 'configuration views do not satisfy the unified settings contract')
}

// ---------- 3d. Interactive browser login contract ----------
group('interactive browser login contract')

const interactiveLoginPath = join(root, 'src', 'components', 'InteractiveLoginModal.vue')
const legacyQrModalPath = join(root, 'src', 'components', 'QRScanModal.vue')
if (!existsSync(interactiveLoginPath)) {
  fail('interactive login modal exists', 'InteractiveLoginModal.vue is missing')
} else {
  const interactiveLoginSrc = readFileSync(interactiveLoginPath, 'utf8')
  const interactiveChecks = [
    [
      'uses the four typed login-session APIs',
      [
        'createLoginSession',
        'getLoginSession',
        'verifyLoginSession',
        'cancelLoginSession',
      ].every(method => interactiveLoginSrc.includes(method)),
    ],
    [
      'requires an explicit user verification action',
      interactiveLoginSrc.includes('@click="verifyAndSave"')
        && interactiveLoginSrc.includes("data-test=\"verify-login\""),
    ],
    [
      'success is emitted only for confirmed sessions',
      /function emitSuccessOnce[\s\S]{0,500}data\.status !== 'confirmed'[\s\S]{0,500}emit\('success'/.test(interactiveLoginSrc),
    ],
    [
      'close switch and unmount cancel non-confirmed sessions',
      interactiveLoginSrc.includes('cancelledTokens')
        && interactiveLoginSrc.includes('cancelCurrentSession')
        && interactiveLoginSrc.includes('switchPlatform')
        && interactiveLoginSrc.includes('onBeforeUnmount'),
    ],
    [
      'late async responses are generation guarded',
      interactiveLoginSrc.includes('generation')
        && interactiveLoginSrc.includes('runGeneration !== generation'),
    ],
    [
      'status polling is strictly single-flight and terminal-aware',
      !interactiveLoginSrc.includes('setInterval')
        && interactiveLoginSrc.includes('setTimeout')
        && interactiveLoginSrc.includes('async function pollOnce')
        && interactiveLoginSrc.includes('finally')
        && interactiveLoginSrc.includes('isTerminalLoginStatus')
        && interactiveLoginSrc.includes('canApplyLoginSnapshot'),
    ],
    [
      'renders no QR image or generated code',
      !/<img\b|<svg\b|qrCells|qrcodeUrl|seenRealQR|sessionToken/.test(interactiveLoginSrc),
    ],
    [
      'dialog provides keyboard and live-region accessibility',
      interactiveLoginSrc.includes('aria-modal="true"')
        && interactiveLoginSrc.includes('aria-live="polite"')
        && interactiveLoginSrc.includes("event.key === 'Escape'")
        && interactiveLoginSrc.includes("event.key === 'Tab'")
        && interactiveLoginSrc.includes('prefers-reduced-motion'),
    ],
    [
      'component colors derive from shared design tokens',
      !interactiveLoginSrc.includes('oklch(')
        && interactiveLoginSrc.includes('var(--surface)')
        && interactiveLoginSrc.includes('var(--border)'),
    ],
  ]
  for (const [label, condition] of interactiveChecks) {
    if (condition) ok(label)
    else fail(label, 'InteractiveLoginModal.vue does not satisfy the manual login contract')
  }
}

if (!existsSync(legacyQrModalPath)) ok('legacy QR modal has been removed')
else fail('legacy QR modal has been removed', 'QRScanModal.vue still exists')

if (
  configAccountsSrc.includes("InteractiveLoginModal from '../components/InteractiveLoginModal.vue'")
  && configAccountsSrc.includes('<InteractiveLoginModal')
  && configAccountsSrc.includes('@success="onLoginSuccess"')
  && !configAccountsSrc.includes('QRScanModal')
) {
  ok('account settings uses the interactive login modal')
} else {
  fail('account settings uses the interactive login modal', 'ConfigAccounts.vue still has legacy login wiring')
}

// ---------- 3. LLM configuration contracts ----------
group('LLM configuration contracts')

const configLlmSrc = readFileSync(join(root, 'src', 'views', 'ConfigLlm.vue'), 'utf8')
const mockApiSrc = readFileSync(join(root, 'src', 'api', 'mock.ts'), 'utf8')
const llmApiMethods = [
  'getLlmProviders',
  'createLlmProvider',
  'updateLlmProvider',
  'deleteLlmProvider',
  'testLlmProvider',
  'updateLlmProviderSecret',
  'getLlmRoutes',
  'updateLlmRoute',
  'getLlmUsage',
]

if (llmApiMethods.every(method => configLlmSrc.includes(method))) {
  ok('LLM page uses the complete typed management API')
} else {
  fail('LLM page uses the complete typed management API', 'one or more LLM API methods are missing')
}

if (!configLlmSrc.includes('fetch(') && !configLlmSrc.includes('scrollIntoView')) {
  ok('LLM page performs no browser-side upstream probe or iframe-hostile scrolling')
} else {
  fail('LLM page performs no browser-side upstream probe or iframe-hostile scrolling', 'direct fetch or scrollIntoView found')
}

if (
  configLlmSrc.includes('type="password"')
  && configLlmSrc.includes("apiKey: ''")
  && configLlmSrc.includes("$t('llm.apiKeySecurityHint')")
) {
  ok('LLM secret input is blank-by-default and documents the no-readback boundary')
} else {
  fail('LLM secret input is blank-by-default and documents the no-readback boundary', 'secret form contract is incomplete')
}

if (
  apiSrc.includes('getLlmProviders: realApi.getLlmProviders')
  && mockApiSrc.includes('LLM management deliberately has no mock endpoint')
  && !mockApiSrc.includes('getLlmProviders: ()')
) {
  ok('LLM management always reflects the real backend')
} else {
  fail('LLM management always reflects the real backend', 'mock LLM management path is still active')
}

if (
  apiSrc.includes('api.interceptors.request.use')
  && apiSrc.includes("localStorage.getItem('token')")
  && !apiSrc.includes("api.defaults.headers.common['Authorization']")
) {
  ok('real API requests read the latest authentication token')
} else {
  fail('real API requests read the latest authentication token', 'authorization is still captured only at module load')
}

// ---------- 4. Router routes ----------
group('router routes')

// Router uses Vite-only `import.meta.env.BASE_URL`, can't be imported in Node.
// Parse the source file instead to extract route paths.
const routerSrc = readFileSync(join(root, 'src', 'router', 'index.ts'), 'utf8')
if (!routerSrc.includes('/pipeline/tiktok') && !routerSrc.includes('/pipeline/douyin')) {
  ok('no platform-specific pipeline routes')
} else {
  fail('no platform-specific pipeline routes', 'TikTok and Douyin must share /pipeline')
}
const routeMatches = [...routerSrc.matchAll(/path:\s*['"]([^'"]+)['"]/g)]
const paths = routeMatches.map(m => m[1]).sort()
const expected = ['/', '/config-accounts', '/config-llm', '/config-pipeline', '/dashboard', '/login', '/pipeline', '/reports', '/users', '/users/:username']

for (const p of expected) {
  if (paths.includes(p)) ok(`route ${p}`)
  else fail(`route ${p}`, 'not found, got: ' + paths.join(', '))
}

// Verify redirect '/' exists
if (paths.includes('/')) ok('/ redirects to /dashboard')
else fail('/', 'redirect route missing')

// ---------- 5. View files ----------
group('view files exist & non-empty')

const views = ['Login', 'Dashboard', 'Users', 'UserDetail', 'Pipeline', 'Reports', 'ConfigAccounts', 'ConfigLlm', 'ConfigPipeline']
for (const v of views) {
  const p = join(root, 'src', 'views', `${v}.vue`)
  if (!existsSync(p)) { fail(`${v}.vue exists`, 'file missing'); continue }
  const size = statSync(p).size
  if (size < 1000) { fail(`${v}.vue non-empty`, `only ${size} bytes`); continue }
  ok(`${v}.vue (${(size / 1024).toFixed(1)} KB)`)
}

// ---------- 6. Project files ----------
group('project files present')

const checks = [
  ['.env.development', 'mock config'],
  ['src/assets/design-system.css', 'shared tokens'],
  ['src/assets/main.css', 'app entry CSS'],
  ['src/App.vue', 'shell'],
  ['README.md', 'docs'],
]
for (const [rel, desc] of checks) {
  const p = join(root, rel)
  if (existsSync(p)) ok(`${desc} (${rel})`)
  else fail(`${desc} (${rel})`, 'missing')
}

const devEnv = readFileSync(join(root, '.env.development'), 'utf8')
if (/^VITE_USE_MOCK=false$/m.test(devEnv)) {
  ok('development Auto mode prefers the real backend')
} else {
  fail('development Auto mode prefers the real backend', 'VITE_USE_MOCK must default to false')
}

// ---------- 7. Build dist sanity ----------
group('dist (optional)')

const distExists = existsSync(join(root, 'dist'))
if (distExists) {
  const files = readdirSync(join(root, 'dist'))
  ok(`dist/ has ${files.length} entries`)
} else {
  console.log('  · dist/ not built yet — run `npm run build` first')
}

// ---------- Summary ----------
console.log('')
console.log('='.repeat(60))
console.log(`  PASSED  ${passed}`)
console.log(`  FAILED  ${failed}`)
console.log('='.repeat(60))

if (failed > 0) {
  console.log('\nFailures:')
  for (const e of errors) console.log(`  ✗ ${e.label}: ${e.err}`)
  process.exit(1)
} else {
  console.log('\n  All smoke checks passed.')
  process.exit(0)
}
