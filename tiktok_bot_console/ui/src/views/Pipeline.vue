<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1>{{ $t('pipeline.title') }}</h1>
        <p v-html="$t('pipeline.subtitle', { jobId: jobId, time: cronTime })"></p>
      </div>
      <div style="display:flex;gap:8px">
        <button class="btn" @click="$router.push('/config-pipeline')">{{ $t('pipeline.viewCron') }}</button>
        <button class="btn brand" @click="runAll">{{ $t('pipeline.manualTrigger') }} →</button>
      </div>
    </div>

    <!-- Toolbar -->
    <div class="toolbar">
      <div class="left">
        <button class="run-btn running" v-if="running" @click="stopRunning">
          <span class="pulse-dot"></span>
          {{ $t('pipeline.runningState', { h: elapsedH, m: elapsedM }) }}
        </button>
        <button class="run-btn" v-else @click="runAll">
          ▶ {{ $t('pipeline.run') }}
        </button>
        <div class="stage-toggles">
          <label v-for="s in stages" :key="s" class="stage-tog">
            <input type="checkbox" v-model="checked" :value="s"> {{ $t('pipeline.' + s) }}
          </label>
        </div>
      </div>
      <span class="cron-hint">{{ $t('pipeline.nextTrigger') }} {{ cronTime }} ({{ $t('common.tomorrow') }})</span>
    </div>

    <!-- Pipeline diagram -->
    <div class="card pipe-card">
      <div class="pipe-head">
        <div>
          <h3>{{ $t('pipeline.todayProgress') }}</h3>
          <div class="sub">JOB #{{ jobId }} · started {{ cronTime }} · {{ stageSubLine }}</div>
        </div>
        <div style="display:flex;gap:12px;align-items:center;font-size:11.5px;color:var(--muted)">
          <span><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--ok);margin-right:5px"></span>{{ $t('pipeline.legendDone') }}</span>
          <span><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--brand);margin-right:5px"></span>{{ $t('pipeline.legendRunning') }}</span>
          <span><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--muted);margin-right:5px"></span>{{ $t('pipeline.legendPending') }}</span>
          <span><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--err);margin-right:5px"></span>{{ $t('pipeline.legendFailed') }}</span>
        </div>
      </div>

      <div class="pipe-canvas">
        <div v-for="ps in pipeStages" :key="ps.key" :class="['pstep', ps.status]">
          <span class="ix">{{ ps.ix }}</span>
          <span :class="['chip', ps.status === 'done' ? 'ok' : ps.status === 'running' ? 'brand' : 'ghost', 'badge']">
            <span class="dot"></span> {{ $t(stageStatusKey(ps.status)) }}
          </span>
          <div class="nm">{{ $t(ps.nameI18n) }}</div>
          <div class="ds">{{ $t(ps.descI18n) }}</div>
          <div class="stat">
            <div>
              <div class="v">{{ ps.metric }}</div>
              <div class="l">{{ $t(ps.metricLabelI18n) }}</div>
            </div>
            <div v-if="ps.extra" style="font-size:11px;color:var(--muted)">{{ ps.extra }}</div>
          </div>
          <div class="time">{{ ps.time }}</div>
        </div>
      </div>

      <div class="pipe-meta">
        <div class="cfg-row"><div class="lbl">{{ $t('pipeline.totalDuration') }}</div><div class="val">{{ summary?.totalDuration || '—' }}</div></div>
        <div class="cfg-row"><div class="lbl">{{ $t('pipeline.llmCalls') }}</div><div class="val">{{ summary?.llmCalls || '—' }} · {{ summary?.llmCost || '—' }}</div></div>
        <div class="cfg-row"><div class="lbl">{{ $t('pipeline.browserOps') }}</div><div class="val">{{ summary?.browserOps || '—' }} · {{ summary?.browserErrors || '—' }}</div></div>
        <div class="cfg-row"><div class="lbl">{{ $t('pipeline.accountSwitches') }}</div><div class="val">{{ summary?.accountSwitches || '—' }}</div></div>
        <div class="cfg-row"><div class="lbl">{{ $t('pipeline.commentsSent') }}</div><div class="val">{{ summary?.commentsSent || '—' }}</div></div>
        <div class="cfg-row"><div class="lbl">{{ $t('pipeline.dmsSent') }}</div><div class="val">{{ summary?.dmsSent || '—' }}</div></div>
      </div>
    </div>

    <div class="split-3-1">
      <div class="card event-card">
        <div class="card-hd">
          <h3>{{ $t('pipeline.eventStreamTitle') }}</h3>
          <span class="hint">{{ $t('pipeline.eventStreamHint') }}</span>
        </div>
        <div class="log-body">
          <div class="log">
            <div v-for="(e, i) in events" :key="`${e.ts}-${i}`" class="log-line">
              <span class="log-ts">{{ e.ts }}</span>
              <span :class="['log-tag', e.tagCls]">{{ e.tag }}</span>
              <span class="log-msg">{{ e.msg }}</span>
            </div>
          </div>
        </div>
      </div>

      <div>
        <div class="card" style="margin-bottom:16px">
          <div class="card-hd">
            <h3>{{ $t('pipeline.outreachQueueTitle') }}</h3>
            <span class="hint">{{ $t('pipeline.outreachQueueHint', { n: queue.items.length }) }}</span>
          </div>
          <div style="padding:10px 18px 14px">
            <div v-if="queue.items.length === 0" style="text-align:center;color:var(--muted);font-size:12.5px;padding:18px 0">
              {{ $t('pipeline.outreachEmpty') }}
            </div>
            <div v-for="it in queue.items" :key="it.id" class="queue-row">
              <span :class="['status-pill', it.status === 'approved' ? 'on' : it.status === 'rejected' ? 'off' : '']">
                <span class="dot"></span>
                {{ $t('pipeline.outreachStatus_' + it.status) }}
              </span>
              <span class="quser">@{{ it.username }}</span>
              <span class="qpersona">{{ $t('persona.' + it.persona) }}</span>
              <span class="qactions" v-if="it.status === 'pending'">
                <button class="btn sm" @click="approveQueueItem(it.id)">{{ $t('pipeline.outreachApprove') }}</button>
                <button class="btn sm ghost" @click="rejectQueueItem(it.id)">{{ $t('pipeline.outreachReject') }}</button>
              </span>
              <span v-else class="qtime">{{ $t('pipeline.outreachDone', { time: new Date(it.addedAt).toLocaleTimeString() }) }}</span>
            </div>
          </div>
        </div>

        <div class="card" style="margin-bottom:16px">
          <div class="card-hd">
            <h3>{{ $t('pipeline.twinTitle') }}</h3>
            <span class="hint">{{ $t('pipeline.twinHint') }}</span>
          </div>
          <div style="padding:14px 18px">
            <AgentTwinGrid />
          </div>
        </div>

        <div class="card" style="margin-bottom:16px">
          <div class="card-hd"><h3>{{ $t('pipeline.recent7days') }}</h3></div>
          <div style="padding:10px 18px 14px">
            <div v-for="j in jobs" :key="j.date" class="job-row">
              <span class="job-id">{{ j.date }}</span>
              <span :class="['chip', j.chipCls]" style="height:18px;font-size:10.5px"><span class="dot"></span>{{ j.status }}</span>
              <span>{{ j.detail }}</span>
              <span class="mono" style="font-size:11.5px;color:var(--muted)">{{ j.duration }}</span>
            </div>
          </div>
        </div>

        <div class="card">
          <div class="card-hd"><h3>{{ $t('pipeline.thisRunResults') }}</h3></div>
          <div style="padding:14px 18px">
            <div v-for="r in results" :key="r.stage" :class="['result-msg', r.cls]">
              <span class="stage">{{ $t('pipeline.stageLabel', { n: r.stage }) }}</span>
              {{ r.msg }}
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { getPipelineEvents, getPipelineOverview, getConfig, runPipeline } from '../api'
import { useOutreachQueue, useMessageLog } from '../stores/actionStores'
import AgentTwinGrid from '../components/AgentTwinGrid.vue'

const { t } = useI18n()
const queue = useOutreachQueue()
const log = useMessageLog()
const running = ref(false)
const startedAt = ref<number>(0)
const elapsedH = ref(0)
const elapsedM = ref(0)
let elapsedTimer: number | null = null
const cronTime = ref('09:00')
const jobId = ref('')
const stages = ['collect', 'filter', 'strategy', 'outreach', 'report']
const checked = ref([...stages])
// Event-stream row shape — derived from the API event stream each poll.
// ts / tag / tagCls / msg are display-friendly projections of the raw event.
interface DisplayEvent { ts: string; tag: string; tagCls: string; msg: string }
interface JobRow { date: string; chipCls: string; status: string; detail: string; duration: string }
interface ResultRow { stage: number; cls: string; msg: string }

const events = ref<DisplayEvent[]>([])
const jobs = ref<JobRow[]>([])
const results = ref<ResultRow[]>([])

// Per-stage live data + run summary — driven by /api/pipeline/overview
interface PipeStage { index: number; key: string; nameI18n: string; descI18n: string; ix: string; status: 'done' | 'running' | 'pending' | 'failed'; metric: string; metricLabelI18n: string; extra: string | null; time: string }
interface PipeSummary { totalDuration: string; llmCalls: string; llmCost: string; browserOps: string; browserErrors: string; accountSwitches: string; commentsSent: string; dmsSent: string }
const pipeStages = ref<PipeStage[]>([])
const summary = ref<PipeSummary | null>(null)

const stageStatusKey = (s: string) => ({
  done: 'pipeline.statusDone',
  running: 'pipeline.statusRunning',
  pending: 'pipeline.statusPending',
  failed: 'pipeline.statusFailed',
} as Record<string, string>)[s] || 'pipeline.statusPending'

const stageSubLine = computed(() => {
  const s = pipeStages.value
  if (!s.length) return t('pipeline.noData') || '暂无运行数据'
  const done = s.filter(x => x.status === 'done').length
  const run = s.filter(x => x.status === 'running').length
  const pending = s.filter(x => x.status === 'pending').length
  return t('pipeline.subLine', { done, total: s.length, running: run, pending }) || `${done}/${s.length} 完成 · ${run} 进行中 · ${pending} 待开始`
})

async function approveQueueItem(id: string) {
  const it = queue.items.find(x => x.id === id)
  if (!it) return
  queue.approve(id)
  log.record({ username: it.username, channel: 'dm', content: `[AI 写话术 demo] 致 @${it.username} — 基于画像 ${it.persona} 的初稿。请人工审阅后再发出。` })
  ElMessage.success(`已通过 @${it.username} · 触达审计已记录`)
}
function rejectQueueItem(id: string) {
  const it = queue.items.find(x => x.id === id)
  if (!it) return
  queue.reject(id, '人工驳回')
  ElMessage.info(`已驳回 @${it.username}`)
}

async function runAll() {
  running.value = true
  startedAt.value = Date.now()
  startElapsedTimer()
  try {
    const res: any = await runPipeline(checked.value)
    ElMessage.success(t('pipeline.startedWith', { n: res?.data?.results?.length || 0 }))
  } catch {
    ElMessage.error(t('common.errNetwork'))
  }
}

function startElapsedTimer() {
  stopElapsedTimer()
  elapsedTimer = window.setInterval(() => {
    const diff = Date.now() - startedAt.value
    elapsedH.value = Math.floor(diff / 3600000)
    elapsedM.value = Math.floor((diff % 3600000) / 60000)
  }, 10000) // update every 10s
}

function stopElapsedTimer() {
  if (elapsedTimer) { clearInterval(elapsedTimer); elapsedTimer = null }
}

function stopRunning() {
  running.value = false
  stopElapsedTimer()
  ElMessage.info('已停止前端监控 · 后端任务将继续运行至完成')
}

watch(running, (val) => {
  if (val) startElapsedTimer()
  else stopElapsedTimer()
})

let pollTimer: number | null = null
function levelToTagCls(level: string | undefined): string {
  if (level === 'ok') return 'ok'
  if (level === 'err') return 'err'
  return ''
}
async function load() {
  try {
    const { data } = await getPipelineEvents(60)
    if (Array.isArray(data) && data.length) {
      events.value = data.map((e: any) => ({
        ts: String(e.timestamp || '').slice(11, 19),  // "12:04:18"
        tag: String(e.type || ''),
        tagCls: levelToTagCls(e.level),
        msg: String(e.message || ''),
      }))
    }
  } catch {}
}
async function loadConfig() {
  try {
    const { data } = await getConfig()
    if (data?.cron_daily_pipeline_time) cronTime.value = data.cron_daily_pipeline_time
  } catch {}
}

async function loadOverview() {
  try {
    const { data } = await getPipelineOverview()
    if (Array.isArray(data?.jobs)) jobs.value = data.jobs
    if (Array.isArray(data?.results)) results.value = data.results
    if (Array.isArray(data?.stages)) {
      pipeStages.value = data.stages
      // 从 stages 推断 running 状态
      running.value = data.stages.some((s: any) => s.status === 'running')
    }
    if (data?.summary) summary.value = data.summary
    // 从 jobs 推断当前 jobId
    if (data?.jobs?.length > 0) jobId.value = data.jobs[0].jobId || ''
    else jobId.value = new Date().toISOString().slice(0, 10).replaceAll('-', '') + '-01'
  } catch {
    jobId.value = new Date().toISOString().slice(0, 10).replaceAll('-', '') + '-01'
  }
}
onMounted(() => { load(); loadConfig(); loadOverview(); pollTimer = window.setInterval(load, 5000) })
onUnmounted(() => { if (pollTimer) { clearInterval(pollTimer) }; stopElapsedTimer() })
</script>

<style scoped>
.toolbar { display: flex; align-items: center; gap: 12px; margin-bottom: 18px; flex-wrap: wrap; }
.toolbar .left { display: flex; gap: 8px; align-items: center; flex: 1; }
.run-btn {
  height: 38px; padding: 0 18px; border-radius: 9px; border: none; cursor: pointer;
  background: var(--fg); color: var(--surface); font-size: 13px; font-weight: 600;
  display: inline-flex; align-items: center; gap: 8px;
}
.run-btn.running { background: var(--brand); }
.run-btn:hover { background: oklch(30% 0.012 280); }
.pulse-dot { width: 8px; height: 8px; border-radius: 50%; background: #fff; animation: pulse 1s infinite; }
@keyframes pulse { 0%,100%{opacity:1;transform:scale(1)} 50%{opacity:.5;transform:scale(1.3)} }
.stage-toggles { display: flex; gap: 4px; }
.stage-tog {
  height: 32px; padding: 0 12px; border-radius: 7px; border: 1px solid var(--border);
  background: var(--surface); font-size: 12.5px; color: var(--fg-2); cursor: pointer;
  display: inline-flex; align-items: center; gap: 6px;
}
.stage-tog input { margin: 0; accent-color: var(--brand); }
.stage-tog:hover { border-color: var(--border-strong); }
.cron-hint { font-size: 12px; color: var(--muted); font-family: var(--font-mono); }

.pipe-card { padding: 24px 24px 20px; margin-bottom: 16px; }
.pipe-head { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 22px; }
.pipe-head h3 { font-size: 14px; font-weight: 600; margin: 0; }
.pipe-head .sub { font-size: 12px; color: var(--muted); margin-top: 4px; font-family: var(--font-mono); }
.pipe-canvas { display: grid; grid-template-columns: repeat(6, 1fr); gap: 4px; align-items: stretch; }
.pstep {
  background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
  padding: 14px 12px; position: relative; cursor: pointer;
  min-height: 158px;
}
.pstep:hover { border-color: var(--brand); transform: translateY(-1px); }
.pstep.done { background: var(--ok-soft); border-color: oklch(85% 0.06 150); }
.pstep.running { background: var(--brand-soft); border-color: oklch(85% 0.10 350); }
.pstep.running::before {
  content: ''; position: absolute; top: -1px; left: -1px; right: -1px; height: 2px;
  background: linear-gradient(90deg, transparent, var(--brand), transparent);
  background-size: 50% 100%; background-repeat: no-repeat;
  animation: stripe 1.6s linear infinite;
  border-radius: 10px 10px 0 0;
}
@keyframes stripe { 0%{background-position: -50% 0;} 100%{background-position: 150% 0;} }
.pstep .ix { font-size: 10.5px; color: var(--muted); font-family: var(--font-mono); letter-spacing: 0.5px; font-weight: 600; }
.pstep .nm { font-size: 14px; font-weight: 600; margin-top: 4px; }
.pstep .ds { font-size: 11.5px; color: var(--muted); margin-top: 4px; line-height: 1.4; }
.pstep .stat { margin-top: 12px; padding-top: 10px; border-top: 1px dashed var(--border); display: flex; justify-content: space-between; align-items: center; }
.pstep .stat .v { font-size: 18px; font-weight: 700; font-family: var(--font-mono); }
.pstep .stat .l { font-size: 10.5px; color: var(--muted); text-transform: uppercase; }
.pstep .badge { position: absolute; top: 12px; right: 12px; }
.pstep .time { font-size: 10.5px; color: var(--muted); margin-top: 6px; font-family: var(--font-mono); }

.pipe-meta { margin-top: 18px; padding-top: 16px; border-top: 1px solid var(--border); display: grid; grid-template-columns: repeat(6, 1fr); gap: 14px; }
.cfg-row { padding: 12px 14px; background: var(--bg-sub); border-radius: 8px; }
.cfg-row .lbl { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600; }
.cfg-row .val { font-size: 14px; font-weight: 600; margin-top: 2px; font-family: var(--font-mono); }

.event-card {
  display: flex; flex-direction: column;
  /* Override inherited .card padding so flex children can stretch edge-to-edge */
  padding: 0 !important;
  height: 100%;
  min-height: 0;
}
.event-card .card-hd { flex-shrink: 0; padding: 14px 18px; }
.log-body {
  flex: 1 1 auto;
  display: flex;
  flex-direction: column;             /* key — let .log fill vertically */
  padding: 0 14px 14px 14px;
  min-height: 0;
}
.log {
  background: oklch(13% 0.012 280); color: oklch(92% 0.01 280);
  border-radius: 10px; padding: 14px 16px;
  font-family: var(--font-mono); font-size: 12px; line-height: 1.7;
  /* Override global .log { max-height: 360px } in design-system.css —
     cascade falls through per-property when scoped rule omits it. */
  max-height: none !important;
  overflow-y: auto; overflow-x: hidden;
  width: 100%; flex: 1 1 0; min-height: 240px;
  box-sizing: border-box;
}
.log-line {
  display: grid;
  grid-template-columns: 78px auto 1fr;
  gap: 12px; width: 100%; min-width: 0;
}
.log-ts { color: oklch(55% 0.01 280); white-space: nowrap; }
.log-tag { color: var(--cyan); font-weight: 500; white-space: nowrap; }
.log-tag.err { color: var(--err); }
.log-tag.ok { color: var(--ok); }
.log-msg {
  color: oklch(82% 0.005 280);
  min-width: 0;
  overflow-wrap: anywhere;
}

.result-msg { padding: 8px 12px; border-radius: 6px; font-size: 12.5px; margin-bottom: 6px; display: flex; align-items: center; gap: 8px; }
.result-msg.ok { background: var(--ok-soft); color: oklch(42% 0.16 150); }
.result-msg.err { background: var(--err-soft); color: oklch(48% 0.22 25); }
.result-msg.brand { background: var(--brand-soft); color: var(--brand-deep); }
.result-msg.pending { background: var(--bg-sub); color: var(--muted); }
.result-msg .stage { font-weight: 600; min-width: 50px; }

.job-row { padding: 8px 0; border-bottom: 1px solid var(--border); display: grid; grid-template-columns: 60px 90px 1fr auto; gap: 12px; align-items: center; font-size: 12.5px; }
.job-row:last-child { border-bottom: 0; }
.job-id { font-family: var(--font-mono); font-size: 11.5px; color: var(--muted); }
.queue-row { display: grid; grid-template-columns: 88px 1fr auto auto; gap: 10px; align-items: center; padding: 8px 0; border-bottom: 1px solid var(--border); font-size: 12.5px; }
.queue-row:last-child { border-bottom: 0; }
.queue-row .quser { font-family: var(--font-mono); font-weight: 500; }
.queue-row .qpersona { font-size: 11px; color: var(--muted); }
.queue-row .qactions { display: flex; gap: 4px; }
.queue-row .qtime { font-size: 11px; color: var(--muted); }
</style>