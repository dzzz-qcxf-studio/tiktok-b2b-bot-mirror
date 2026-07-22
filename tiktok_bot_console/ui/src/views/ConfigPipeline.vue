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
          <input type="number" class="input" v-model="cfg.daily_comment_limit" min="1" max="50">
        </div>
        <div class="impact">
          <span>{{ $t('runtime.combinedDaily', { n: cfg.daily_comment_limit * 3 }) }}</span>
          <span class="muted">{{ $t('runtime.currentAvg', { n: 71 }) }}</span>
        </div>
      </div>

      <div class="setting-row">
        <div>
          <div class="nm">{{ $t('runtime.dmLimit') }}</div>
          <div class="hint-row">{{ $t('runtime.dmLimitHint') }}</div>
        </div>
        <div class="ctrl">
          <input type="number" class="input" v-model="cfg.daily_dm_limit" min="1" max="30">
        </div>
        <div class="impact">
          <span>{{ $t('runtime.combinedDaily', { n: cfg.daily_dm_limit * 3 }) }}</span>
          <span class="muted">{{ $t('runtime.currentAvg', { n: 32 }) }}</span>
        </div>
      </div>

      <div class="setting-row">
        <div>
          <div class="nm">{{ $t('runtime.dailyUsers') }}</div>
          <div class="hint-row">{{ $t('runtime.dailyUsersHint') }}</div>
        </div>
        <div class="ctrl">
          <input type="number" class="input" v-model="cfg.daily_users" min="20" max="500">
        </div>
        <div class="impact">
          <span>{{ $t('runtime.covers', { pct: '15%' }) }}</span>
          <span class="muted">{{ $t('runtime.actual', { n: 89, total: 120 }) }}</span>
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
            <input type="number" class="input" v-model="cfg.comment_interval_min" min="1" max="60">
            <input type="number" class="input" v-model="cfg.comment_interval_max" min="1" max="120">
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
            <input type="number" class="input" v-model="cfg.dm_interval_min" min="1" max="60">
            <input type="number" class="input" v-model="cfg.dm_interval_max" min="1" max="120">
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
      </div>

      <div class="sched-grid">
        <div class="sched-block">
          <h4>📅 {{ $t('runtime.dailyPipeline') }}</h4>
          <div class="cron-input" style="margin-bottom:8px">
            <span>CRON</span>
            <input :value="cronDailyPipeline" readonly>
          </div>
          <div style="font-size:12px;color:var(--muted)">{{ $t('runtime.dailyAt', { time: cronDailyPipelineTime + ' (UTC+8)' }) }} · {{ $t('runtime.duration3_4h') }}</div>
        </div>

        <div class="sched-block">
          <h4>📊 {{ $t('runtime.dailyReportCron') }}</h4>
          <div class="cron-input" style="margin-bottom:8px">
            <span>CRON</span>
            <input :value="cronDailyReport" readonly>
          </div>
          <div style="font-size:12px;color:var(--muted)">{{ $t('runtime.dailyAt', { time: cronDailyReportTime + ' (UTC+8)' }) }} · {{ $t('runtime.pushTg') }}</div>
        </div>

        <div class="sched-block">
          <h4>🔄 {{ $t('runtime.weeklyIterate') }}</h4>
          <div class="cron-input" style="margin-bottom:8px">
            <span>CRON</span>
            <input :value="cronWeeklyIterate" readonly>
          </div>
          <div style="font-size:12px;color:var(--muted)">{{ $t('runtime.sundayAt', { time: cronWeeklyIterateTime }) }}</div>
        </div>

        <div class="sched-block">
          <h4>🍪 {{ $t('runtime.cookieCheck') }}</h4>
          <div class="cron-input" style="margin-bottom:8px">
            <span>CRON</span>
            <input :value="cronCookieCheck" readonly>
          </div>
          <div style="font-size:12px;color:var(--muted)">{{ $t('runtime.every6h') }}</div>
        </div>
      </div>
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
        <button class="btn" @click="saveDraft">{{ $t('runtime.saveDraft') }}</button>
        <button class="btn brand" @click="saveApply">{{ $t('runtime.saveApply') }} →</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref, computed, onMounted, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { getConfig, setConfigKey } from '../api'

// Mock source of truth for daily limits / intervals / cron / keywords.
interface RuntimeConfig {
  daily_comment_limit: number
  daily_dm_limit: number
  daily_users: number
  comment_interval_min: number
  comment_interval_max: number
  dm_interval_min: number
  dm_interval_max: number
  comment_dm_gap_hours: number
  cron_daily_pipeline: string
  cron_daily_pipeline_time: string
  cron_daily_report: string
  cron_daily_report_time: string
  cron_weekly_iterate: string
  cron_weekly_iterate_time: string
  cron_cookie_check: string
  tiktok_keywords: string
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
  comment_interval_min: 3,
  comment_interval_max: 10,
  dm_interval_min: 8,
  dm_interval_max: 20,
  comment_dm_gap_hours: 24,
  cron_daily_pipeline: '0 9 * * *',
  cron_daily_pipeline_time: '09:00',
  cron_daily_report: '0 21 * * *',
  cron_daily_report_time: '21:00',
  cron_weekly_iterate: '0 22 * * 0',
  cron_weekly_iterate_time: '周日 22:00',
  cron_cookie_check: '0 */6 * * *',
  tiktok_keywords: INITIAL_KEYWORDS.join(','),
})

const keywords = ref<string[]>([...INITIAL_KEYWORDS])

// Reactive shortcuts for cron schedule display
const cronDailyPipeline = computed(() => cfg.cron_daily_pipeline)
const cronDailyPipelineTime = computed(() => cfg.cron_daily_pipeline_time)
const cronDailyReport = computed(() => cfg.cron_daily_report)
const cronDailyReportTime = computed(() => cfg.cron_daily_report_time)
const cronWeeklyIterate = computed(() => cfg.cron_weekly_iterate)
const cronWeeklyIterateTime = computed(() => cfg.cron_weekly_iterate_time)
const cronCookieCheck = computed(() => cfg.cron_cookie_check)

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
function hasChanges() {
  return JSON.stringify(cfg) !== JSON.stringify(initialCfg) ||
    JSON.stringify(keywords.value) !== JSON.stringify(initialKeywords)
}
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

async function saveDraft() {
  localStorage.setItem('config_draft', JSON.stringify({ cfg, keywords: keywords.value }))
  ElMessage.success('草稿已保存到本地')
}

async function saveApply() {
  try {
    await setConfigKey('daily_comment_limit', String(cfg.daily_comment_limit))
    await setConfigKey('daily_dm_limit', String(cfg.daily_dm_limit))
    await setConfigKey('daily_users', String(cfg.daily_users))
    await setConfigKey('comment_interval_min', String(cfg.comment_interval_min))
    await setConfigKey('comment_interval_max', String(cfg.comment_interval_max))
    await setConfigKey('dm_interval_min', String(cfg.dm_interval_min))
    await setConfigKey('dm_interval_max', String(cfg.dm_interval_max))
    await setConfigKey('comment_dm_gap_hours', String(cfg.comment_dm_gap_hours))
    await setConfigKey('tiktok_keywords', keywords.value.join(','))
    Object.assign(initialCfg, cfg)
    keywords.value = [...keywords.value]  // refresh initialKeywords reference
    // re-snapshot
    Object.assign(initialCfg, JSON.parse(JSON.stringify(cfg)))
    initialKeywords.splice(0, initialKeywords.length, ...keywords.value)
    ElMessage.success('配置已保存并生效 · 下次 Pipeline 启动时应用')
  } catch (e) {
    ElMessage.error('保存失败：' + (e as Error).message)
  }
}

async function loadConfig() {
  try {
    const { data } = await getConfig()
    if (!data) return
    // Apply numeric settings
    ;(Object.keys(initialCfg) as Array<keyof RuntimeConfig>).forEach((k) => {
      if (k in data && k !== 'tiktok_keywords') {
        (cfg as any)[k] = data[k]
      }
    })
    // Apply keywords (CSV from mock → array)
    if (typeof data.tiktok_keywords === 'string') {
      keywords.value = data.tiktok_keywords.split(',').map((s: string) => s.trim()).filter(Boolean)
    }
    // Re-snapshot baseline AFTER loaded
    const snap = JSON.parse(JSON.stringify(cfg))
    Object.keys(snap).forEach((k) => { initialCfg[k] = snap[k] })
    initialKeywords.splice(0, initialKeywords.length, ...keywords.value)
  } catch {}
}
onMounted(loadConfig)
watch(keywords, () => { /* keep unsavedCount reactive — nothing else to do */ })

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

.sched-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.sched-block { padding: 16px; background: var(--bg-sub); border-radius: 10px; }
.sched-block h4 { font-size: 13px; font-weight: 600; margin: 0 0 12px; }
.cron-input { display: flex; align-items: center; gap: 8px; font-family: var(--font-mono); }
.cron-input span { font-size: 12px; color: var(--muted); }
.cron-input input { flex: 1; font-family: var(--font-mono); font-size: 12px; padding: 6px 10px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); }

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
</style>