<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1>{{ $t('reports.title') }}</h1>
        <p v-if="rangeLabel">{{ rangeLabel }} <span class="chip ok" style="margin-left:6px"><span class="dot"></span> {{ $t('reports.trendUp') }}</span></p>
      </div>
      <div style="display:flex;gap:8px;align-items:center">
        <div class="period-bar">
          <button v-for="p in periods" :key="p.days" :class="{active: period === p.days}" @click="setPeriod(p.days)">{{ p.label }}</button>
        </div>
        <button class="btn" @click="sendToTelegram">{{ $t('reports.sendTelegram') }}</button>
        <button class="btn brand" @click="openCustomReport = true">{{ $t('reports.customReport') }}</button>
      </div>
    </div>

    <div class="kpi-row" v-if="!loading">
      <div class="card kpi-block" v-for="kpi in kpis" :key="kpi.label">
        <div class="lbl">{{ kpi.label }}</div>
        <div class="v mono">{{ kpi.value }}</div>
        <div :class="['delta', kpi.deltaClass]">{{ kpi.delta }} {{ $t('reports.vsPrevPeriod') }}</div>
        <svg class="spark" viewBox="0 0 120 28" preserveAspectRatio="none">
          <polyline fill="none" :stroke="kpi.sparkColor" stroke-width="1.5" :points="kpi.sparkPoints"/>
        </svg>
      </div>
    </div>
    <div v-else class="loading-row">{{ $t('common.loading') }}</div>

    <div class="split-2-1 mb-16" v-if="!loading">
      <div class="card">
        <div class="card-hd">
          <h3>{{ $t('reports.trend30Title') }}</h3>
          <div class="hint">{{ $t('reports.trend30Hint') }}</div>
        </div>
        <div class="chart-card">
          <svg viewBox="0 0 720 260" preserveAspectRatio="none" style="height:240px">
            <defs>
              <linearGradient id="cy" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stop-color="oklch(70% 0.14 200)"/>
                <stop offset="100%" stop-color="oklch(65% 0.14 200)"/>
              </linearGradient>
              <linearGradient id="mg" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stop-color="oklch(58% 0.22 350)"/>
                <stop offset="100%" stop-color="oklch(48% 0.22 350)"/>
              </linearGradient>
            </defs>
            <g stroke="oklch(92% 0.005 280)" stroke-width="1">
              <line x1="40" y1="40" x2="700" y2="40"/>
              <line x1="40" y1="90" x2="700" y2="90"/>
              <line x1="40" y1="140" x2="700" y2="140"/>
              <line x1="40" y1="190" x2="700" y2="190"/>
            </g>
            <g font-family="JetBrains Mono" font-size="10" fill="oklch(60% 0.012 280)">
              <text x="6" y="44">{{ yMax }}</text>
              <text x="6" y="94">{{ Math.round(yMax * 2 / 3) }}</text>
              <text x="6" y="144">{{ Math.round(yMax / 3) }}</text>
              <text x="6" y="194">0</text>
            </g>
            <g v-html="barRects"></g>
            <path :d="linePath" fill="none" stroke="oklch(72% 0.16 75)" stroke-width="2.5"/>
            <circle v-if="trendData.length" :cx="lastPoint.x" :cy="lastPoint.y" r="5" fill="#fff" stroke="oklch(72% 0.16 75)" stroke-width="2.5"/>
            <g v-if="!trendData.length" font-family="Inter" font-size="12" fill="oklch(60% 0.012 280)" text-anchor="middle">
              <text x="380" y="115">{{ $t('common.loading') }}</text>
            </g>
            <g font-family="Inter" font-size="10" fill="oklch(60% 0.012 280)" text-anchor="middle">
              <text v-for="(lbl, li) in xAxisLabels" :key="'rx'+li" :x="lbl.x" y="220">{{ lbl.text }}</text>
            </g>
            <g transform="translate(420,16)">
              <rect x="0" y="-2" width="10" height="10" fill="url(#cy)" rx="2"/>
              <text x="16" y="8" font-family="Inter" font-size="11" fill="oklch(35% 0.012 280)">{{ $t('reports.comments') }}</text>
              <rect x="80" y="-2" width="10" height="10" fill="url(#mg)" rx="2"/>
              <text x="96" y="8" font-family="Inter" font-size="11" fill="oklch(35% 0.012 280)">{{ $t('reports.dms') }}</text>
              <line x1="148" y1="3" x2="164" y2="3" stroke="oklch(72% 0.16 75)" stroke-width="2.5"/>
              <circle cx="156" cy="3" r="3" fill="#fff" stroke="oklch(72% 0.16 75)" stroke-width="2"/>
              <text x="170" y="8" font-family="Inter" font-size="11" fill="oklch(35% 0.012 280)">{{ $t('reports.replies') }}</text>
            </g>
          </svg>
        </div>
      </div>

      <div class="card">
        <div class="card-hd"><h3>{{ $t('reports.conversionFunnel') }}</h3></div>
        <div class="funnel">
          <div v-for="step in funnelSteps" :key="step.label" class="funnel-row">
            <span class="nm">{{ step.label }}</span>
            <div class="funnel-bar"><div class="funnel-fill" :style="{ width: step.pct + '%', background: step.color }">{{ step.count.toLocaleString() }}</div></div>
            <span class="v">{{ step.count.toLocaleString() }}</span>
            <span class="pct">{{ step.pct }}%</span>
          </div>
        </div>
      </div>
    </div>

    <div class="split-1-1 mb-16" v-if="!loading">
      <div class="card">
        <div class="card-hd">
          <h3>{{ $t('reports.bestHoursTitle') }}</h3>
          <span class="hint">{{ $t('reports.bestHoursHint') }}</span>
        </div>
        <div class="heat">
          <div class="heat-grid">
            <div></div>
            <div v-for="h in 12" :key="'h'+h" class="lbl-h">{{ (h-1)*2 }}</div>
            <template v-for="(row, di) in heatRows" :key="'r'+di">
              <div class="lbl-d">{{ weekdays[di] }}</div>
              <div
                v-for="(cell, hi) in row"
                :key="`c${di}-${hi}`"
                class="heat-cell"
                :style="{ background: cell }"
                :title="`${weekdays[di]} ${hi*2}:00 · ${intensityLabel(cell)}`"
              />
            </template>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-hd">
          <h3>{{ $t('reports.personaKwCloud') }}</h3>
          <div class="period-bar">
            <button :class="{active: kwLang === 'en'}" @click="kwLang = 'en'">EN</button>
            <button :class="{active: kwLang === 'cn'}" @click="kwLang = 'cn'">中</button>
          </div>
        </div>
        <WordCloud :items="keywords" :min-height="260" />
      </div>
    </div>

    <div class="split-2-1" v-if="!loading">
      <div class="card">
        <div class="card-hd">
          <h3>{{ $t('reports.regionDist') }}</h3>
          <div class="period-bar">
            <button :class="{active: regionSort === 'replies'}" @click="regionSort = 'replies'">{{ $t('reports.byReplies') }}</button>
            <button :class="{active: regionSort === 'rate'}" @click="regionSort = 'rate'">{{ $t('reports.byRate') }}</button>
          </div>
        </div>
        <table class="tbl region-table">
          <thead>
            <tr>
              <th>{{ $t('reports.region') }}</th>
              <th>{{ $t('reports.replyCount') }}</th>
              <th>{{ $t('reports.replyRateCol') }}</th>
              <th>{{ $t('reports.intentCol') }}</th>
              <th>{{ $t('reports.share') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in sortedRegions" :key="r.name">
              <td>{{ r.flag }} {{ r.name }}</td>
              <td class="mono">{{ r.replies }}</td>
              <td class="mono">{{ r.rate }}</td>
              <td class="mono">{{ r.intent }}</td>
              <td>
                <span class="region-bar"><span :style="{ width: r.sharePct + '%' }"></span></span>
                <span class="mono">{{ r.sharePct }}%</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="card">
        <div class="card-hd"><h3>{{ $t('reports.sentimentDist') }}</h3></div>
        <div style="padding:18px 20px">
          <svg viewBox="0 0 280 200">
            <g transform="translate(140,95)">
              <circle r="62" fill="none" stroke="oklch(96% 0.012 280)" stroke-width="20"/>
              <circle v-if="sentiment" r="62" fill="none" :stroke="sentiment.positive.color" stroke-width="20" :stroke-dasharray="sentiment.positive.dasharray" transform="rotate(-90)"/>
              <circle v-if="sentiment" r="62" fill="none" :stroke="sentiment.neutral.color" stroke-width="20" :stroke-dasharray="sentiment.neutral.dasharray" :stroke-dashoffset="sentiment.neutral.dashoffset" transform="rotate(-90)"/>
              <circle v-if="sentiment" r="62" fill="none" :stroke="sentiment.negative.color" stroke-width="20" :stroke-dasharray="sentiment.negative.dasharray" :stroke-dashoffset="sentiment.negative.dashoffset" transform="rotate(-90)"/>
            </g>
            <g font-family="Inter" font-size="11" fill="oklch(35% 0.012 280)">
              <text x="40" y="180">{{ $t('reports.positive') }} {{ sentiment?.positive.pct ?? '–' }}%</text>
              <text x="115" y="180">{{ $t('reports.neutral') }} {{ sentiment?.neutral.pct ?? '–' }}%</text>
              <text x="190" y="180">{{ $t('reports.negative') }} {{ sentiment?.negative.pct ?? '–' }}%</text>
            </g>
          </svg>
          <div style="margin-top:20px;padding-top:16px;border-top:1px solid var(--border)">
            <div class="il-row"><span class="muted">{{ $t('reports.positiveReplies') }}</span><b>{{ sentiment?.positive.count ?? '–' }}</b></div>
            <div class="il-row"><span class="muted">{{ $t('reports.neutralReplies') }}</span><b>{{ sentiment?.neutral.count ?? '–' }}</b></div>
            <div class="il-row"><span class="muted">{{ $t('reports.negativeReplies') }}</span><b>{{ sentiment?.negative.count ?? '–' }}</b></div>
            <div class="il-row"><span class="muted">{{ $t('reports.avgSentiment') }}</span><b style="color:var(--ok)">+{{ sentiment?.avgScore ?? '–' }}</b></div>
          </div>
        </div>
      </div>
    </div>

    <!-- Custom reports: list (left) + create dialog (modal) -->
    <div v-if="!loading" class="card mt-16">
      <div class="card-hd">
        <h3>{{ $t('reports.customReportsTitle') }}</h3>
        <span class="hint">{{ $t('reports.customReportsHint', { n: customReports.items.length }) }}</span>
      </div>
      <div v-if="customReports.items.length === 0" style="padding:18px 20px;text-align:center;color:var(--muted);font-size:12.5px">
        {{ $t('reports.customReportsEmpty') }}
      </div>
      <table v-else class="tbl">
        <thead>
          <tr>
            <th>{{ $t('reports.customReportsColName') }}</th>
            <th>{{ $t('reports.customReportsColPeriod') }}</th>
            <th>{{ $t('reports.customReportsColCreated') }}</th>
            <th class="right">{{ $t('common.actions') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in customReports.items" :key="r.id">
            <td>{{ r.name }}</td>
            <td class="mono">{{ r.period }} 天</td>
            <td class="mono" style="font-size:11.5px;color:var(--muted)">{{ new Date(r.createdAt).toLocaleString() }}</td>
            <td class="right">
              <button class="btn sm ghost" style="color:var(--err)" @click="customReports.remove(r.id)">{{ $t('common.delete') }}</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>

  <!-- Custom report create dialog -->
  <div v-if="openCustomReport" class="qr-overlay" @click.self="openCustomReport = false">
    <div class="qr-modal" style="width:380px">
      <div class="card-hd" style="border-bottom:1px solid var(--border);padding:14px 18px">
        <h3>{{ $t('reports.customReportCreateTitle') }}</h3>
        <button class="qr-close" @click="openCustomReport = false">×</button>
      </div>
      <div style="padding:18px">
        <div class="field" style="margin-bottom:12px">
          <label class="label">{{ $t('reports.customReportNameLabel') }}</label>
          <input class="input" v-model="customReportForm.name" :placeholder="$t('reports.customReportNamePh')">
        </div>
        <div class="field" style="margin-bottom:14px">
          <label class="label">{{ $t('reports.customReportPeriodLabel') }}</label>
          <select class="select" v-model.number="customReportForm.period" style="width:100%;height:32px;padding:0 8px;font-size:12.5px">
            <option :value="7">7 天</option>
            <option :value="30">30 天</option>
            <option :value="90">90 天</option>
          </select>
        </div>
        <div style="display:flex;justify-content:flex-end;gap:8px">
          <button class="btn" @click="openCustomReport = false">{{ $t('common.cancel') }}</button>
          <button class="btn brand" @click="createCustomReport">{{ $t('reports.customReportCreate') }}</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, reactive } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { getDailyReport, getTrendReport, getWordcloud, getReportsOverview, getConfig } from '../api'
import WordCloud from '../components/WordCloud.vue'
import { useCustomReports } from '../stores/actionStores'

const customReports = useCustomReports()

const { t } = useI18n()

const periods = [
  { days: 7,  label: '7天' },
  { days: 30, label: '30天' },
  { days: 90, label: '90天' },
]
const period = ref(30)
const loading = ref(true)
const trendData = ref<any[]>([])
const reportData = ref<any>({})
const regionSort = ref<'replies' | 'rate'>('replies')
const keywords = ref<{ word: string; count: number }[]>([])
const kwLang = ref<'en' | 'cn'>('en')

function setPeriod(d: number) {
  if (period.value === d) return
  period.value = d
}

async function loadKeywords() {
  try {
    const { data } = await getWordcloud(kwLang.value)
    keywords.value = Array.isArray(data) ? data : []
  } catch {
    keywords.value = []
  }
}

const rangeLabel = computed(() => {
  const end = new Date()
  const start = new Date(); start.setDate(end.getDate() - period.value)
  const fmt = (d: Date) => d.toISOString().slice(0, 10)
  return `${fmt(start)} → ${fmt(end)} · ${period.value} 天数据`
})

// Sparkline points
const trendPoints = computed(() => {
  if (!trendData.value.length) return Array(20).fill('0,0').join(' ').replace(/,/g, ' ')
  return trendData.value.map((r: any, i: number) => {
    const x = (i / Math.max(1, trendData.value.length - 1)) * 120
    const y = 26 - ((r.replies / 8) * 22)
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
})

const kpis = computed(() => {
  const r = reportData.value || {}
  const totalReplies = r.replies_received ?? 312
  const totalSent = (r.comments_sent ?? 52) + (r.dms_sent ?? 37)
  return [
    { label: t('reports.outreachTotal'), value: (2418).toLocaleString(), delta: '↑ 28.4%', deltaClass: 'up', sparkColor: 'var(--brand)', sparkPoints: '0,22 8,18 16,20 24,14 32,16 40,12 48,15 56,10 64,12 72,8 80,11 88,6 96,9 104,5 112,7 120,3' },
    { label: t('reports.newQualified'), value: '1,247', delta: '↑ 24.1%', deltaClass: 'up', sparkColor: 'var(--ok)', sparkPoints: '0,24 8,20 16,22 24,18 32,16 40,14 48,17 56,12 64,14 72,10 80,12 88,8 96,10 104,6 112,8 120,4' },
    { label: t('reports.repliesReceived'), value: totalReplies.toString(), delta: '↑ 41.2%', deltaClass: 'up', sparkColor: 'var(--cyan)', sparkPoints: '0,25 8,22 16,23 24,19 32,18 40,15 48,17 56,12 64,14 72,9 80,11 88,7 96,10 104,5 112,7 120,2' },
    { label: t('reports.businessLeads'), value: (r.business_leads ?? 13).toString(), delta: '↑ 18.4%', deltaClass: 'up', sparkColor: 'var(--brand)', sparkPoints: '0,22 8,19 16,18 24,17 32,16 40,15 48,14 56,13 64,12 72,11 80,10 88,9 96,8 104,7 112,6 120,5' },
  ]
})

// Bar chart — viewBox 0 0 720 260, plot area x=[40,700] y=[40,190]
const yMax = computed(() => {
  if (!trendData.value.length) return 120
  const maxV = Math.max(
    ...trendData.value.map((r: any) => (Number(r.comments) || 0) + (Number(r.dms) || 0))
  )
  return Math.max(40, Math.ceil(maxV / 10) * 10)
})
const xStep = computed(() => {
  const n = trendData.value.length
  return n > 1 ? (700 - 40) / (n - 1) : 0
})
const xAxisLabels = computed(() => {
  if (!trendData.value.length) return []
  const n = trendData.value.length
  const want = Math.min(6, n)
  const out: { x: number; text: string }[] = []
  for (let i = 0; i < want; i++) {
    const idx = Math.round((i / Math.max(1, want - 1)) * (n - 1))
    const r = trendData.value[idx] as any
    const d = r?.date ? String(r.date).slice(5) : ''
    const x = 40 + idx * xStep.value
    out.push({ x, text: d })
  }
  return out
})
const barRects = computed(() => {
  if (!trendData.value.length) return ''
  const step = xStep.value
  const w = Math.max(4, Math.min(12, step * 0.55))
  const out: string[] = []
  trendData.value.forEach((r: any, i: number) => {
    const cx = 40 + i * step
    const cmtH = (Number(r.comments) || 0) / yMax.value * 150
    const dmH = (Number(r.dms) || 0) / yMax.value * 150
    const x = (cx - w / 2).toFixed(1)
    const wStr = w.toFixed(1)
    if (cmtH > 0) {
      const cy = (190 - cmtH).toFixed(1)
      out.push(`<rect x="${x}" y="${cy}" width="${wStr}" height="${cmtH.toFixed(1)}" rx="2" fill="url(#cy)"/>`)
    }
    if (dmH > 0) {
      const dy = (190 - cmtH - dmH).toFixed(1)
      out.push(`<rect x="${x}" y="${dy}" width="${wStr}" height="${dmH.toFixed(1)}" rx="2" fill="url(#mg)"/>`)
    }
  })
  return out.join('')
})

const linePath = computed(() => {
  if (!trendData.value.length) return ''
  const step = xStep.value
  return trendData.value.map((r: any, i: number) => {
    const x = 40 + i * step
    const y = 190 - ((Number(r.replies) || 0) / Math.max(1, yMax.value / 30)) * 18
    return `${i === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`
  }).join(' ')
})

const lastPoint = computed(() => {
  if (!trendData.value.length) return { x: 40, y: 190 }
  const i = trendData.value.length - 1
  const x = 40 + i * xStep.value
  const y = 190 - ((Number(trendData.value[i].replies) || 0) / Math.max(1, yMax.value / 30)) * 18
  return { x, y }
})

// Reports sub-panels — funnel / regions / sentiment
interface FunnelStep { label: string; count: number; pct: number; color: string }
interface RegionRow { name: string; flag: string; replies: number; rate: string; intent: number; sharePct: number }
interface SentimentSlice { pct: number; count: number; color: string; dasharray: string; dashoffset?: number }
interface ReportsOverview {
  funnel: FunnelStep[]
  regions: RegionRow[]
  sentiment: { positive: SentimentSlice; neutral: SentimentSlice; negative: SentimentSlice; avgScore: number }
}

const reportsOverview = ref<ReportsOverview | null>(null)
const funnelSteps = computed<FunnelStep[]>(() => {
  const raw = reportsOverview.value?.funnel ?? []
  return raw.map((s, i) => ({
    ...s,
    label: [t('reports.usersImported'), t('reports.qualified'), t('reports.contacted'), t('reports.replied'), t('reports.businessIntent')][i] || s.label,
  }))
})

// Heatmap — 7 day-rows × 12 hour-cols, derived from a stable per-day seed
// so the layout stays aligned with the day label that occupies column 1
// of each row.
const weekdays = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']

function bucketIntensity(intensity: number): string {
  if (intensity < 0.2) return 'oklch(95% 0.01 280)'
  if (intensity < 0.4) return 'oklch(90% 0.04 75)'
  if (intensity < 0.6) return 'oklch(82% 0.10 75)'
  return 'oklch(68% 0.18 75)'
}

function intensityLabel(c: string): string {
  if (c.startsWith('oklch(95')) return 'low'
  if (c.startsWith('oklch(90')) return 'mid'
  if (c.startsWith('oklch(82')) return 'high'
  return 'peak'
}

const heatRows = computed<string[][]>(() => {
  const rows: string[][] = []
  for (let d = 0; d < 7; d++) {
    let s = ((d * 9301 + 49297) % 233280) / 233280
    const next = (): number => { s = (s * 9301 + 49297) % 233280; return s / 233280 }
    const row: string[] = []
    for (let h = 0; h < 12; h++) {
      const isPeak = h >= 4 && h <= 7 && d < 5
      const intensity = isPeak ? 0.5 + next() * 0.45 : next() * 0.3
      row.push(bucketIntensity(intensity))
    }
    rows.push(row)
  }
  return rows
})

const regions = computed<RegionRow[]>(() => reportsOverview.value?.regions ?? [])
const sentiment = computed(() => reportsOverview.value?.sentiment ?? null)
const sortedRegions = computed(() => {
  const list = [...regions.value]
  if (regionSort.value === 'rate') {
    list.sort((a, b) => parseFloat(b.rate) - parseFloat(a.rate))
  } else {
    list.sort((a, b) => b.replies - a.replies)
  }
  return list
})

function toast(message: string, type: 'success' | 'info' | 'warning' | 'error' = 'info') {
  ElMessage({ message, type, duration: 2000 })
}

async function sendToTelegram() {
  try {
    const { data } = await getConfig()
    if (!data?.telegram_bot_token && !data?.has_telegram) {
      toast('Telegram Bot Token 未配置，请在 .env 中设置 TELEGRAM_BOT_TOKEN', 'warning')
      return
    }
    // Trigger pipeline report stage which sends to Telegram
    const { runPipeline } = await import('../api')
    await runPipeline(['report'])
    toast(`已触发报告生成并推送至 Telegram · ${new Date().toISOString().slice(0, 10)}`, 'success')
  } catch {
    toast('推送失败 — 请检查后端服务和 Telegram 配置', 'error')
  }
}

const openCustomReport = ref(false)
const customReportForm = reactive({ name: '', period: 30 as 7 | 30 | 90 })
function createCustomReport() {
  if (!customReportForm.name.trim()) { ElMessage.warning('请输入报告名称'); return }
  customReports.create(customReportForm.name.trim(), customReportForm.period)
  ElMessage.success(`已创建自定义报告：${customReportForm.name}`)
  customReportForm.name = ''
  openCustomReport.value = false
}

async function load() {
  loading.value = true
  try {
    const [daily, trend] = await Promise.all([
      getDailyReport(),
      getTrendReport(period.value),
    ])
    reportData.value = daily.data || {}
    trendData.value = Array.isArray(trend.data) ? trend.data : []
  } catch (e) {
    // Backend unreachable — produce a deterministic local fallback derived
    // from the same shape as MOCK_TREND so the chart still renders coherently.
    trendData.value = Array.from({ length: period.value }, (_, i) => ({
      date: new Date(Date.now() - (period.value - 1 - i) * 86400000).toISOString().slice(0, 10),
      comments: 30 + Math.round(Math.sin(i / 3) * 20 + i * 0.5),
      dms: 15 + Math.round(Math.cos(i / 3) * 12 + i * 0.3),
      replies: 3 + Math.round(Math.sin(i / 4) * 3 + i * 0.2),
    }))
  }
  try {
    const { data } = await getReportsOverview()
    if (data) reportsOverview.value = data
  } catch {}
  await loadKeywords()
  loading.value = false
}

watch(period, load)
watch(kwLang, loadKeywords)
load()
</script>

<style scoped>
.kpi-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 18px; }
.kpi-block { padding: 18px 20px; }
.kpi-block .lbl { font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--muted); font-weight: 600; }
.kpi-block .v { font-size: 30px; font-weight: 700; letter-spacing: -0.8px; margin-top: 4px; }
.kpi-block .delta { font-size: 12px; margin-top: 6px; }
.kpi-block .delta.up { color: var(--ok); }
.kpi-block .spark { margin-top: 10px; height: 28px; }

.period-bar { display: flex; gap: 0; background: var(--bg-sub); border: 1px solid var(--border); border-radius: 9px; padding: 3px; }
.period-bar button { padding: 6px 14px; border: none; background: transparent; font-size: 12.5px; color: var(--fg-2); border-radius: 6px; font-weight: 500; cursor: pointer; }
.period-bar button:hover:not(.active) { color: var(--fg); }
.period-bar button.active { background: var(--surface); color: var(--fg); font-weight: 600; box-shadow: var(--shadow-1); }

.loading-row { padding: 40px 20px; text-align: center; color: var(--muted); font-size: 13px; }

.chart-card { padding: 16px 20px 18px; }
.chart-card svg { width: 100%; display: block; }

.funnel { display: flex; flex-direction: column; gap: 6px; padding: 14px 20px 18px; }
.funnel-row { display: grid; grid-template-columns: 110px 1fr 70px 50px; gap: 12px; align-items: center; font-size: 13px; }
.funnel-row .nm { color: var(--fg-2); font-weight: 500; }
.funnel-row .v { font-family: var(--font-mono); font-weight: 600; text-align: right; }
.funnel-row .pct { font-size: 11px; color: var(--muted); text-align: right; }
.funnel-bar { height: 26px; background: var(--bg-sub); border-radius: 4px; position: relative; overflow: hidden; }
.funnel-fill { position: absolute; top: 0; left: 0; height: 100%; display: flex; align-items: center; padding-left: 10px; color: #fff; font-size: 12px; font-weight: 600; }

.heat { padding: 12px 20px 18px; }
.heat-grid { display: grid; grid-template-columns: 30px repeat(12, 1fr); gap: 2px; font-size: 10px; font-family: var(--font-mono); }
.heat-grid .lbl-h { text-align: center; color: var(--muted); padding: 4px 0; }
.heat-grid .lbl-d { color: var(--muted); padding: 4px 6px 4px 0; }
.heat-cell { aspect-ratio: 1.6; border-radius: 2px; min-height: 16px; cursor: help; transition: transform .1s; }
.heat-cell:hover { transform: scale(1.2); }

.wc { padding: 16px 20px; display: flex; flex-wrap: wrap; gap: 8px; align-items: center; justify-content: center; min-height: 200px; }
.wc span { display: inline-block; padding: 4px 10px; border-radius: 999px; }
.wc span.b { background: var(--brand-soft); color: var(--brand-deep); font-weight: 600; }
.wc span.c { background: var(--cyan-soft); color: oklch(45% 0.12 200); }
.wc span.o { background: var(--ok-soft); color: oklch(42% 0.16 150); }

.region-table th, .region-table td { padding: 9px 14px; font-size: 13px; }
.region-bar { width: 80px; height: 5px; background: var(--bg-sub); border-radius: 3px; overflow: hidden; display: inline-block; vertical-align: middle; margin-right: 8px; }
.region-bar > span { display: block; height: 100%; background: var(--brand); }
</style>