<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1>{{ $t('dashboard.title') }}</h1>
        <p>{{ $t('dashboard.todayLine', { date: today, weekday: weekday, stage: 4, total: 6, next: '21:00' }) }}</p>
      </div>
      <div style="display:flex;gap:8px">
        <button class="btn" @click="exportDaily">{{ $t('dashboard.exportDaily') }}</button>
        <button class="btn brand" @click="runNow">{{ $t('dashboard.runNow') }} →</button>
      </div>
    </div>

    <!-- KPI row -->
    <div class="hero-grid" v-if="!loading">
      <div class="kpi-hero">
        <div class="kpi-label">{{ $t('dashboard.replyRate') }}</div>
        <div class="kpi-value">{{ (overview.today_reply_rate * 100).toFixed(1) }}<span class="kpi-unit">%</span></div>
        <div class="kpi-sub"><span style="color:var(--ok)">↑ 3.2pp</span> {{ $t('dashboard.vsYesterday') }} · {{ $t('dashboard.businessIntents', { n: overview.today_leads }) }}</div>
      </div>
      <div class="card kpi">
        <div class="kpi-label">{{ $t('dashboard.qualifiedTotal') }}</div>
        <div class="kpi-value">{{ overview.qualified_users.toLocaleString() }}</div>
        <div class="kpi-sub"><span style="color:var(--ok)">+{{ dashboard?.newQualifiedDelta ?? 0 }}</span> {{ $t('dashboard.todayNewQualified') }}</div>
      </div>
      <div class="card kpi">
        <div class="kpi-label">{{ $t('dashboard.outreachToday') }}</div>
        <div class="kpi-value mono">{{ overview.today_comments + overview.today_dms }}</div>
        <div class="kpi-sub">{{ $t('dashboard.commentsDMs', { n1: overview.today_comments, n2: overview.today_dms }) }}</div>
      </div>
      <div class="card kpi">
        <div class="kpi-label">{{ $t('dashboard.accountsRunning') }}</div>
        <div class="kpi-value mono">{{ dashboard?.accountsRunningLabel ?? '—' }}</div>
        <div class="kpi-sub"><span v-if="dashboard?.accountsRunningHealthy" class="chip ok" style="height:18px;font-size:10.5px"><span class="dot"></span> {{ $t('dashboard.healthy') }}</span></div>
      </div>
    </div>

    <!-- Pipeline strip -->
    <div class="pstrip">
      <div class="pstrip-hd">
        <h3>{{ $t('dashboard.pipelineState') }}</h3>
        <div class="right">
          <span class="chip"><span class="dot" style="color:var(--ok)"></span> {{ dashboard?.doneCount ?? 0 }} {{ $t('dashboard.done') }}</span>
          <span class="chip brand"><span class="dot"></span> {{ dashboard?.runningCount ?? 0 }} {{ $t('dashboard.running') }}</span>
          <span class="chip ghost"><span class="dot"></span> {{ dashboard?.pendingCount ?? 0 }} {{ $t('dashboard.pending') }}</span>
          <span>{{ $t('dashboard.startedAt', { time: cronTime }) }} · {{ $t('dashboard.usedFor', { h: dashboard?.usedHours ?? 0, m: dashboard?.usedMinutes ?? 0 }) }}</span>
        </div>
      </div>
      <div class="pstrip-row">
        <div v-for="s in pipeStages" :key="s.key" :class="['pnode', s.status]">
          <div class="pnode-circ">{{ s.status === 'done' ? '✓' : s.index }}</div>
          <div class="pnode-nm">{{ $t(s.nameI18n) }}</div>
          <div class="pnode-meta">{{ s.metric }} {{ s.extra || '' }}</div>
        </div>
      </div>
    </div>

    <!-- Two col -->
    <div class="split-2-1">
      <div class="card">
        <div class="card-hd">
          <h3>{{ $t('dashboard.trend30') }}</h3>
          <div class="hint">
            <button v-for="p in periods" :key="p" :class="['btn sm', period === p ? '' : 'ghost']" @click="setPeriod(p)">{{ p }}天</button>
          </div>
        </div>
        <div class="chart-bd">
          <svg viewBox="0 0 720 280" preserveAspectRatio="none">
            <defs>
              <linearGradient id="g1" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stop-color="oklch(58% 0.22 350)" stop-opacity="0.28"/>
                <stop offset="100%" stop-color="oklch(58% 0.22 350)" stop-opacity="0"/>
              </linearGradient>
            </defs>
            <g stroke="oklch(92% 0.005 280)" stroke-width="1">
              <line x1="40" y1="40" x2="700" y2="40"/>
              <line x1="40" y1="100" x2="700" y2="100"/>
              <line x1="40" y1="160" x2="700" y2="160"/>
              <line x1="40" y1="220" x2="700" y2="220"/>
            </g>
            <g font-family="JetBrains Mono" font-size="10" fill="oklch(60% 0.012 280)">
              <text x="6" y="44">{{ yMax }}</text>
              <text x="6" y="104">{{ Math.round(yMax * 2 / 3) }}</text>
              <text x="6" y="164">{{ Math.round(yMax / 3) }}</text>
              <text x="6" y="224">0</text>
            </g>
            <g v-html="barRects"></g>
            <path :d="linePath" fill="none" stroke="oklch(58% 0.22 350)" stroke-width="2.5"/>
            <path v-if="trend.length" :d="linePath + ` L ${lastPoint.x} 220 L 48 220 Z`" fill="url(#g1)"/>
            <circle v-if="trend.length" :cx="lastPoint.x" :cy="lastPoint.y" r="5" fill="#fff" stroke="oklch(58% 0.22 350)" stroke-width="2.5"/>
            <g v-if="!trend.length" font-family="Inter" font-size="12" fill="oklch(60% 0.012 280)" text-anchor="middle">
              <text x="380" y="135">{{ $t('common.loading') }}</text>
            </g>
            <g font-family="Inter" font-size="10" fill="oklch(60% 0.012 280)" text-anchor="middle">
              <text v-for="(lbl, li) in xAxisLabels" :key="'x'+li" :x="lbl.x" y="258">{{ lbl.text }}</text>
            </g>
            <g transform="translate(420,16)">
              <rect x="0" y="-2" width="10" height="10" fill="oklch(14% 0.012 280)" rx="2"/>
              <text x="16" y="8" font-family="Inter" font-size="11" fill="oklch(35% 0.012 280)">{{ $t('dashboard.trend30Bar') }}</text>
              <line x1="100" y1="3" x2="116" y2="3" stroke="oklch(58% 0.22 350)" stroke-width="2.5"/>
              <circle cx="108" cy="3" r="3" fill="#fff" stroke="oklch(58% 0.22 350)" stroke-width="2"/>
              <text x="122" y="8" font-family="Inter" font-size="11" fill="oklch(35% 0.012 280)">{{ $t('dashboard.trend30Line') }}</text>
            </g>
          </svg>
        </div>
      </div>

      <div>
        <div class="card" style="margin-bottom:14px">
          <div class="card-hd">
            <h3>{{ $t('dashboard.topKeywords') }}</h3>
            <span class="hint">{{ $t('dashboard.lastDays', { n: 7 }) }}</span>
          </div>
          <div class="mini-list">
            <div v-for="(kw, i) in topKeywords" :key="kw.name" class="mini-row">
              <span class="rk">{{ String(i + 1).padStart(2, '0') }}</span>
              <div>
                <div class="nm">{{ kw.name }}</div>
                <div class="bar brand" style="margin-top:5px"><span :style="{ width: (kw.rate * 100) + '%' }"></span></div>
              </div>
              <span class="v">{{ (kw.rate * 100).toFixed(1) }}%</span>
            </div>
          </div>
        </div>

        <div class="card">
          <div class="card-hd"><h3>{{ $t('dashboard.personaDist') }}</h3></div>
          <div class="chart-bd">
            <svg viewBox="0 0 320 200">
              <g transform="translate(160,100)">
                <circle r="68" fill="none" stroke="oklch(96% 0.012 280)" stroke-width="22"/>
                <circle v-for="(seg, i) in personaSegs" :key="seg.key"
                  r="68" fill="none" :stroke="seg.color" stroke-width="22"
                  :stroke-dasharray="`${seg.dasharray} 427`"
                  :stroke-dashoffset="-(seg.offset || 0)"
                  transform="rotate(-90)"/>
              </g>
              <g font-family="Inter" font-size="10" fill="oklch(35% 0.012 280)">
                <text v-for="(seg, i) in personaSegs" :key="'lbl-'+seg.key"
                  :x="20 + i * 90" y="180">{{ $t('persona.' + seg.key) }} {{ seg.pct }}%</text>
              </g>
            </svg>
          </div>
        </div>
      </div>
    </div>

    <!-- Bottom: feed -->
    <div class="card mt-16">
      <div class="card-hd">
        <h3>{{ $t('dashboard.eventStream') }}</h3>
        <router-link to="/pipeline" class="hint" style="color:var(--brand)">{{ $t('dashboard.viewFullPipeline') }} →</router-link>
      </div>
      <div class="feed">
        <div v-for="(e, i) in feed" :key="i" class="feed-row">
          <span class="feed-time">{{ e.time }}</span>
          <span class="feed-text" v-html="e.text"></span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted, computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { getDashboard, getTrendReport, getDailyReport, getConfig, getPipelineEvents, getPipelineOverview, runPipeline } from '../api'

const { t } = useI18n()
const loading = ref(true)
const overview = reactive({ total_users: 0, qualified_users: 0, today_new: 0, today_comments: 0, today_dms: 0, today_reply_rate: 0, today_leads: 0 })
const topKeywords = ref<{ name: string; rate: number }[]>([])
const trend = ref<any[]>([])
const periods = [7, 14, 30]
const period = ref(30)
const cronTime = ref('09:00')

function setPeriod(p: number) {
  if (period.value === p) return
  period.value = p
}

const today = new Date().toISOString().slice(0, 10)
const weekday = ['日', '一', '二', '三', '四', '五', '六'][new Date().getDay()]

const feed = ref<{ time: string; text: string }[]>([])

// Pipeline overview fields (loaded from /api/pipeline/overview)
interface Stage { index: number; key: string; nameI18n: string; descI18n: string; ix: string; status: 'done' | 'running' | 'pending' | 'failed'; metric: string; metricLabelI18n: string; extra: string | null; time: string }
interface DashStats { newQualifiedDelta: number; accountsRunningLabel: string; accountsRunningHealthy: boolean; doneCount: number; runningCount: number; pendingCount: number; usedHours: number; usedMinutes: number }
interface PersonaSeg { key: string; pct: number; color: string; dasharray: number; offset: number }
const pipeStages = ref<Stage[]>([])
const dashboard = ref<DashStats | null>(null)
const personaSegs = ref<PersonaSeg[]>([])

async function loadOverview() {
  try {
    const { data } = await getPipelineOverview()
    if (!data) return
    pipeStages.value = (data.stages || []) as Stage[]
    dashboard.value = (data.dashboard || null) as DashStats | null
    // Build persona donut segments — each segment's offset = sum of previous pct/100 * 427
    const personaRaw = (data.personaMix || []) as Array<{ key: string; pct: number; color: string }>
    let acc = 0
    personaSegs.value = personaRaw.map(p => {
      const dash = (p.pct / 100) * 427
      const seg = { ...p, dasharray: dash, offset: acc }
      acc += dash
      return seg
    })
  } catch {}
}

// Chart geometry — viewBox 0 0 720 280, plot area x=[48,700] y=[40,220]
const yMax = computed(() => {
  if (!trend.value.length) return 240
  const maxUsers = Math.max(...trend.value.map((r: any) => Number(r.qualified) || Number(r.new_users) || 0))
  const maxRatePct = Math.max(...trend.value.map((r: any) => (Number(r.reply_rate) || 0) * 100))
  const m = Math.max(maxUsers, maxRatePct)
  return Math.max(40, Math.ceil(m / 10) * 10)
})
const xStep = computed(() => {
  const n = trend.value.length
  return n > 1 ? (700 - 48) / (n - 1) : 0
})
const xAxisLabels = computed(() => {
  if (!trend.value.length) return []
  // Pick ~6 evenly-spaced tick indices
  const n = trend.value.length
  const want = Math.min(6, n)
  const out: { x: number; text: string }[] = []
  for (let i = 0; i < want; i++) {
    const idx = Math.round((i / Math.max(1, want - 1)) * (n - 1))
    const r = trend.value[idx] as any
    const d = r?.date ? String(r.date).slice(5) : '' // "MM-DD"
    const x = 48 + idx * xStep.value
    out.push({ x, text: d })
  }
  return out
})
const barRects = computed(() => {
  if (!trend.value.length) return ''
  const step = xStep.value
  const w = Math.max(4, Math.min(10, step * 0.55))
  return trend.value.map((r: any, i: number) => {
    const cx = 48 + i * step
    const v = Number(r.qualified) || Number(r.new_users) || 0
    const h = (v / yMax.value) * 180
    const x = cx - w / 2
    const y = 220 - h
    return `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${w.toFixed(1)}" height="${h.toFixed(1)}" rx="2" fill="oklch(14% 0.012 280)"/>`
  }).join('')
})
const linePath = computed(() => {
  if (!trend.value.length) return ''
  const step = xStep.value
  return trend.value.map((r: any, i: number) => {
    const x = 48 + i * step
    const y = 220 - ((Number(r.reply_rate) || 0) * 100) / yMax.value * 180
    return `${i === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`
  }).join(' ')
})
const lastPoint = computed(() => {
  if (!trend.value.length) return { x: 48, y: 220 }
  const i = trend.value.length - 1
  const x = 48 + i * xStep.value
  const y = 220 - ((Number(trend.value[i].reply_rate) || 0) * 100) / yMax.value * 180
  return { x, y }
})

async function runNow() {
  try {
    await runPipeline(['collect', 'filter', 'strategy', 'outreach', 'report'])
    ElMessage.success('Pipeline 已启动')
  } catch {
    ElMessage.error('启动失败')
  }
}

async function exportDaily() {
  try {
    const { data } = await getDailyReport()
    if (!data || data.message) { ElMessage.info('暂无今日数据可导出'); return }
    const csv = 'metric,value\n' + Object.entries(data).filter(([k]) => k !== 'date').map(([k, v]) => `${k},${v}`).join('\n')
    const blob = new Blob([csv], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = `daily-report-${data.date || 'today'}.csv`; a.click()
    URL.revokeObjectURL(url)
    ElMessage.success('已下载日报 CSV')
  } catch {
    ElMessage.error('导出失败')
  }
}

async function loadTrend() {
  try {
    const { data } = await getTrendReport(period.value)
    trend.value = Array.isArray(data) ? data : []
  } catch {
    trend.value = []
  }
}

async function loadConfig() {
  try {
    const { data } = await getConfig()
    if (data?.cron_daily_pipeline_time) cronTime.value = data.cron_daily_pipeline_time
  } catch {}
}

async function loadFeed() {
  try {
    const { data } = await getPipelineEvents(5)
    if (Array.isArray(data)) {
      feed.value = data.map((e: any) => ({
        time: String(e.timestamp || '').slice(11, 19),  // "12:04:18"
        text: String(e.message || ''),
      }))
    }
  } catch {}
}

watch(period, loadTrend)

onMounted(async () => {
  try {
    const [dash] = await Promise.all([getDashboard(), loadConfig(), loadTrend(), loadFeed(), loadOverview()])
    Object.assign(overview, dash.data.overview)
    topKeywords.value = (dash.data.keywords || []).slice(0, 5)
  } catch {}
  loading.value = false
})
</script>

<style scoped>
.hero-grid { display: grid; grid-template-columns: 1.7fr 1fr 1fr 1fr; gap: 12px; margin-bottom: 18px; }
.kpi-hero {
  background: linear-gradient(135deg, oklch(14% 0.012 280) 0%, oklch(22% 0.06 340) 100%);
  color: #fff; padding: 22px 24px; border-radius: 12px; position: relative; overflow: hidden;
}
.kpi-hero::after {
  content: ''; position: absolute; right: -30px; bottom: -30px;
  width: 180px; height: 180px; border-radius: 50%;
  background: radial-gradient(circle, oklch(70% 0.22 350 / 0.4), transparent 65%);
}
.kpi-hero .kpi-label { color: oklch(70% 0.01 280); }
.kpi-hero .kpi-value { font-size: 44px; }
.kpi-hero .kpi-sub { color: oklch(70% 0.01 280); }

.pstrip {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 12px; padding: 18px 20px; margin-bottom: 18px;
}
.pstrip-hd { display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; }
.pstrip-hd h3 { font-size: 14px; font-weight: 600; margin: 0; }
.pstrip-hd .right { display: flex; align-items: center; gap: 12px; font-size: 12px; color: var(--muted); }
.pstrip-row { display: grid; grid-template-columns: repeat(6, 1fr); gap: 8px; align-items: center; position: relative; }
.pstrip-row::before {
  content: ''; position: absolute; top: 18px; left: 6%; right: 6%;
  height: 1px; background: var(--border); z-index: 0;
}
.pnode { position: relative; z-index: 1; display: flex; flex-direction: column; align-items: center; gap: 8px; }
.pnode-circ {
  width: 36px; height: 36px; border-radius: 50%;
  background: var(--surface); border: 2px solid var(--border-strong);
  display: grid; place-items: center; font-size: 13px; font-weight: 700; color: var(--muted);
}
.pnode.done .pnode-circ { background: var(--ok); border-color: var(--ok); color: #fff; }
.pnode.running .pnode-circ {
  background: var(--brand); border-color: var(--brand); color: #fff;
  box-shadow: 0 0 0 6px oklch(58% 0.22 350 / 0.18);
  animation: ring 1.6s ease-out infinite;
}
@keyframes ring { 0%{box-shadow:0 0 0 0 oklch(58% 0.22 350 / 0.35);} 100%{box-shadow:0 0 0 12px oklch(58% 0.22 350 / 0);} }
.pnode-nm { font-size: 11.5px; font-weight: 500; color: var(--fg-2); }
.pnode-meta { font-size: 10.5px; color: var(--muted); font-family: var(--font-mono); }

.chart-bd { padding: 12px 14px 14px; }
.chart-bd svg { display: block; width: 100%; height: 280px; }

.mini-list { padding: 8px 14px 14px; }
.mini-row { display: grid; grid-template-columns: 24px 1fr 60px; gap: 10px; align-items: center; padding: 7px 0; }
.mini-row .rk { font-size: 11px; color: var(--muted); font-weight: 600; }
.mini-row .nm { font-size: 13px; font-weight: 500; }
.mini-row .v { text-align: right; font-size: 12px; color: var(--fg-2); font-family: var(--font-mono); }

.feed { padding: 8px 18px 14px; }
.feed-row { display: grid; grid-template-columns: 70px 1fr; gap: 12px; padding: 8px 0; border-bottom: 1px solid var(--border); }
.feed-row:last-child { border-bottom: 0; }
.feed-time { font-size: 11px; color: var(--muted); font-family: var(--font-mono); padding-top: 1px; }
.feed-text { font-size: 13px; line-height: 1.5; }
.feed-text :deep(b) { font-weight: 600; }
.feed-text :deep(.who) { color: var(--brand); font-weight: 500; }
</style>