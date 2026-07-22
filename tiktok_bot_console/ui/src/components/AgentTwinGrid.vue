<template>
  <div class="twin">
    <!-- Top status bar -->
    <div class="twin-status" :class="`st-${overall}`">
      <span class="twin-dot" :class="`st-${overall}`"></span>
      <span class="twin-status-text">{{ statusText }}</span>
      <span class="twin-status-time">{{ lastEventRelative }}</span>
    </div>

    <!-- Agent grid (digital twin) -->
    <div class="twin-grid">
      <div
        v-for="a in agents"
        :key="a.id"
        class="twin-card"
        :class="`st-${a.status}`"
      >
        <div class="twin-card-hd">
          <span class="twin-card-dot" :class="`st-${a.status}`"></span>
          <span class="twin-card-name">{{ a.name }}</span>
          <span class="twin-card-key">{{ a.id }}</span>
        </div>

        <div class="twin-card-action" :title="a.lastAction">
          <span v-if="a.status === 'working'" class="twin-pulse"></span>
          {{ a.lastAction }}
        </div>

        <div class="twin-card-meta">
          <div class="twin-meta-row">
            <span class="lbl">上次</span>
            <span class="val mono">{{ formatTime(a.lastActionAt) }}</span>
          </div>
          <div class="twin-meta-row">
            <span class="lbl">队列</span>
            <span :class="['val', 'mono', a.queueDepth > 0 ? 'has-queue' : '']">
              {{ a.queueDepth }}
            </span>
          </div>
          <div class="twin-meta-row">
            <span class="lbl">累计</span>
            <span class="val mono">{{ a.processed.toLocaleString() }}</span>
          </div>
        </div>

        <!-- Rate-limit bar -->
        <div class="twin-rl">
          <div class="twin-rl-hd">
            <span class="lbl">今日配额</span>
            <span class="val mono">{{ a.rateLimit.used }} / {{ a.rateLimit.max }}</span>
          </div>
          <div class="twin-rl-bar">
            <div class="twin-rl-fill" :style="{ width: rlPct(a) + '%' }" :class="rlClass(a)"></div>
          </div>
        </div>

        <div v-if="a.status === 'paused'" class="twin-paused-badge">⏸ 人工审核中 · {{ a.queueDepth }} 待处理</div>
      </div>
    </div>

    <!-- Live event log -->
    <div class="twin-log">
      <div class="twin-log-hd">
        <span>📡 实时事件流</span>
        <span class="twin-log-count">{{ events.length }} 条</span>
      </div>
      <div class="twin-log-list">
        <div v-for="e in events.slice(0, 8)" :key="e.id" :class="['twin-log-row', `lv-${e.level}`]">
          <span class="twin-log-time mono">{{ formatTime(e.ts) }}</span>
          <span :class="['twin-log-lv', `lv-${e.level}`]">
            {{ e.level === 'ok' ? '✓' : e.level === 'warn' ? '!' : e.level === 'err' ? '✗' : '·' }}
          </span>
          <span class="twin-log-agent">{{ agentName(e.agentId) }}</span>
          <span class="twin-log-text">{{ e.text }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted } from 'vue'
import { useAgentLiveness, type AgentStatus, type AgentState, type LivenessEvent } from '../stores/agentLiveness'

const liveness = useAgentLiveness()
const agents = computed<AgentState[]>(() => liveness.agents)
const events = computed<LivenessEvent[]>(() => liveness.events)
const overall = computed<AgentStatus>(() => liveness.overallStatus)

let tickHandle: number | null = null
onMounted(() => { tickHandle = window.setInterval(() => liveness.tick(), 5000) })
onUnmounted(() => { if (tickHandle) clearInterval(tickHandle) })

const statusText = computed(() => ({
  working: '全部 Agent 运行中',
  paused:  '部分 Agent 暂停 · 等待人工审核',
  error:   '存在错误 · 需立即处理',
  idle:    '全部 Agent 空闲 · 等待 cron 触发',
  waiting: '等待中',
}[overall.value] || ''))

const lastEvent = computed(() => events.value[0])
const lastEventRelative = computed(() => {
  if (!lastEvent.value) return '—'
  const dt = Math.floor((Date.now() - lastEvent.value.ts) / 1000)
  if (dt < 60) return dt + ' 秒前'
  if (dt < 3600) return Math.floor(dt / 60) + ' 分前'
  return Math.floor(dt / 3600) + ' 时前'
})

function formatTime(ts: number): string {
  const d = new Date(ts)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`
}
function agentName(id: string): string {
  const a = agents.value.find(x => x.id === id)
  return a?.name || id
}
function rlPct(a: AgentState): number {
  if (!a.rateLimit.max) return 0
  return Math.min(100, Math.round(a.rateLimit.used / a.rateLimit.max * 100))
}
function rlClass(a: AgentState): string {
  const p = rlPct(a)
  if (p >= 90) return 'rl-danger'
  if (p >= 70) return 'rl-warn'
  return 'rl-ok'
}
</script>

<style scoped>
.twin { display: flex; flex-direction: column; gap: 12px; }

/* Top status bar */
.twin-status {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 12px; border-radius: 8px;
  background: var(--bg-sub);
  font-size: 12.5px; color: var(--fg-2);
}
.twin-status.st-working { background: oklch(96% 0.025 150); color: oklch(35% 0.16 150); }
.twin-status.st-paused  { background: oklch(96% 0.03 75);  color: oklch(35% 0.14 75); }
.twin-status.st-error   { background: oklch(96% 0.03 25);  color: oklch(40% 0.18 25); }
.twin-status.st-idle    { background: var(--bg-sub); }
.twin-status-time { margin-left: auto; font-family: var(--font-mono); font-size: 11.5px; color: var(--muted); }
.twin-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.twin-dot.st-working { background: var(--ok); box-shadow: 0 0 0 3px oklch(62% 0.16 150 / .25); animation: twin-pulse 1.6s infinite; }
.twin-dot.st-paused  { background: var(--warn); }
.twin-dot.st-error   { background: var(--err); }
.twin-dot.st-idle    { background: var(--muted); }
@keyframes twin-pulse { 0%,100% { opacity: 1 } 50% { opacity: .35 } }

/* Agent grid */
.twin-grid {
  display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px;
}
.twin-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
  position: relative;
  transition: border-color .2s, box-shadow .2s;
  overflow: hidden;
}
.twin-card.st-working { border-color: oklch(62% 0.16 150 / .5); box-shadow: 0 0 0 3px oklch(62% 0.16 150 / .08); }
.twin-card.st-paused  { border-color: oklch(72% 0.16 75 / .5); }
.twin-card.st-error   { border-color: var(--err); }

.twin-card-hd { display: flex; align-items: center; gap: 6px; margin-bottom: 6px; }
.twin-card-dot { width: 6px; height: 6px; border-radius: 50%; }
.twin-card-dot.st-working { background: var(--ok); }
.twin-card-dot.st-paused  { background: var(--warn); }
.twin-card-dot.st-error   { background: var(--err); }
.twin-card-dot.st-idle    { background: var(--muted); }
.twin-card-name { font-weight: 600; font-size: 12.5px; }
.twin-card-key { margin-left: auto; font-family: var(--font-mono); font-size: 10.5px; color: var(--muted); }

.twin-card-action {
  font-size: 11.5px; color: var(--fg-2);
  background: var(--bg-sub); border-radius: 6px;
  padding: 5px 8px; margin-bottom: 6px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  position: relative; min-height: 22px;
}
.twin-pulse {
  display: inline-block;
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--ok); margin-right: 6px;
  animation: twin-pulse 1.2s infinite;
  vertical-align: middle;
}

.twin-card-meta { display: flex; gap: 12px; margin-bottom: 6px; }
.twin-meta-row { display: flex; flex-direction: column; }
.twin-meta-row .lbl { font-size: 9.5px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.3px; }
.twin-meta-row .val { font-size: 12.5px; font-weight: 600; }
.twin-meta-row .val.has-queue { color: var(--brand); }

.twin-rl { margin-top: 4px; }
.twin-rl-hd { display: flex; justify-content: space-between; font-size: 10px; color: var(--muted); margin-bottom: 2px; }
.twin-rl-hd .val { font-size: 10.5px; color: var(--fg-2); }
.twin-rl-bar { height: 4px; background: var(--bg-sub); border-radius: 2px; overflow: hidden; }
.twin-rl-fill { height: 100%; transition: width .4s ease; }
.twin-rl-fill.rl-ok { background: var(--ok); }
.twin-rl-fill.rl-warn { background: var(--warn); }
.twin-rl-fill.rl-danger { background: var(--err); }

.twin-paused-badge {
  margin-top: 6px;
  padding: 4px 8px;
  background: var(--warn-soft); color: oklch(40% 0.16 75);
  border-radius: 5px;
  font-size: 11px; font-weight: 500;
  text-align: center;
}

/* Event log */
.twin-log { background: oklch(13% 0.012 280); color: oklch(85% 0.01 280); border-radius: 8px; padding: 10px 12px; }
.twin-log-hd { display: flex; justify-content: space-between; align-items: center; font-size: 11.5px; color: oklch(70% 0.01 280); margin-bottom: 8px; padding-bottom: 6px; border-bottom: 1px solid oklch(22% 0.012 280); }
.twin-log-count { font-family: var(--font-mono); font-size: 10.5px; color: oklch(60% 0.01 280); }
.twin-log-list { display: flex; flex-direction: column; gap: 4px; max-height: 180px; overflow-y: auto; }
.twin-log-row { display: grid; grid-template-columns: 64px 18px 60px 1fr; gap: 8px; align-items: center; font-size: 11.5px; font-family: var(--font-mono); }
.twin-log-time { color: oklch(55% 0.01 280); }
.twin-log-lv { width: 18px; height: 18px; border-radius: 50%; display: grid; place-items: center; font-weight: 700; font-size: 11px; }
.twin-log-lv.lv-ok   { background: oklch(30% 0.10 150); color: oklch(85% 0.05 150); }
.twin-log-lv.lv-info { background: oklch(30% 0.05 240); color: oklch(85% 0.05 240); }
.twin-log-lv.lv-warn { background: oklch(35% 0.12 75);  color: oklch(85% 0.08 75); }
.twin-log-lv.lv-err  { background: oklch(35% 0.16 25);  color: oklch(85% 0.10 25); }
.twin-log-agent { color: oklch(72% 0.04 200); }
.twin-log-text { color: oklch(82% 0.01 280); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-family: var(--font-sans); }
</style>
