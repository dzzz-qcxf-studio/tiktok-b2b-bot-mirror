import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

/**
 * Agent liveness — a "digital twin" view of every agent currently running
 * in the pipeline. Each agent card in the Pipeline page subscribes to one
 * of these entries to render its pulse / status / last action.
 *
 * This is purely UI-state — no platform calls, no anti-bot code. The
 * backend (when wired) would push real events through the same shape.
 */

export type AgentStatus = 'idle' | 'working' | 'waiting' | 'error' | 'paused'

export interface AgentState {
  id: string                          // 'collect' | 'filter' | ...
  name: string
  status: AgentStatus
  lastAction: string                   // human-readable
  lastActionAt: number
  queueDepth: number                   // pending work
  processed: number                    // lifetime processed
  rateLimit: { used: number; max: number }   // today's quota usage
  errorMessage?: string
}

export interface LivenessEvent {
  id: string
  agentId: string
  ts: number
  level: 'info' | 'ok' | 'warn' | 'err'
  text: string
}

const DEFAULT_AGENTS: AgentState[] = [
  { id: 'collect',  name: '用户搜集',  status: 'idle',   lastAction: '等待关键词触发',   lastActionAt: Date.now() - 240_000, queueDepth: 0,  processed: 1247, rateLimit: { used: 12,  max: 25 } },
  { id: 'filter',   name: '用户筛选',  status: 'idle',   lastAction: '等 collect 输出',   lastActionAt: Date.now() - 600_000, queueDepth: 0,  processed: 891,  rateLimit: { used: 0,   max: 500 } },
  { id: 'strategy', name: '策略制定',  status: 'idle',   lastAction: '等 filter 输出',   lastActionAt: Date.now() - 900_000, queueDepth: 0,  processed: 523,  rateLimit: { used: 0,   max: 500 } },
  { id: 'outreach', name: '触达执行',  status: 'paused', lastAction: '人工审核中',       lastActionAt: Date.now() - 60_000,  queueDepth: 3,  processed: 89,   rateLimit: { used: 0,   max: 12 } },
  { id: 'report',   name: '数据汇总',  status: 'idle',   lastAction: '等 21:00 触发',    lastActionAt: Date.now() - 30_000,  queueDepth: 0,  processed: 7,    rateLimit: { used: 0,   max: 1000 } },
  { id: 'iterate',  name: '闭环迭代',  status: 'idle',   lastAction: '周日 22:00 触发',  lastActionAt: Date.now() - 15_000,  queueDepth: 0,  processed: 1,    rateLimit: { used: 0,   max: 1000 } },
]

const DEFAULT_EVENTS: LivenessEvent[] = [
  { id: 'e1', agentId: 'outreach', ts: Date.now() - 5_000,  level: 'info', text: '触达任务 #47 已加入人工审核队列 (3 个用户)' },
  { id: 'e2', agentId: 'collect',  ts: Date.now() - 60_000, level: 'ok',   text: '本轮采集完成：328 个新用户，已交付 filter' },
  { id: 'e3', agentId: 'strategy', ts: Date.now() - 180_000, level: 'ok',  text: '为 @aroma_house_us 生成 soft_sell 策略模板' },
  { id: 'e4', agentId: 'filter',   ts: Date.now() - 300_000, level: 'warn', text: '@delong_cn 数据评分 78，但 cookie 已过期，跳过' },
  { id: 'e5', agentId: 'collect',  ts: Date.now() - 600_000, level: 'ok',  text: '关键词 importer 1688 完成 5 页，共 124 个候选' },
]

export const useAgentLiveness = defineStore('agent-liveness', () => {
  const agents = ref<AgentState[]>([...DEFAULT_AGENTS])
  const events = ref<LivenessEvent[]>([...DEFAULT_EVENTS])

  function tick() {
    // Simulate liveness changes every 5s: flip random agent between working / idle,
    // occasionally log an event. Real backend will replace this with WebSocket push.
    const a = agents.value[Math.floor(Math.random() * agents.value.length)]
    if (!a) return
    const next: AgentStatus[] = ['idle', 'working', 'waiting', 'idle', 'idle']
    a.status = next[Math.floor(Math.random() * next.length)]!
    a.lastActionAt = Date.now()
    if (a.status === 'working') {
      a.processed += 1
      a.lastAction = `处理中：${a.id} #${a.processed}`
    } else if (a.status === 'waiting') {
      a.lastAction = '等待上游输入'
    } else if (a.status === 'idle') {
      a.lastAction = '空闲 · 待 cron 触发'
    } else if (a.status === 'paused') {
      a.lastAction = '人工审核中'
    }
    if (Math.random() < 0.4) {
      events.value.unshift({
        id: 'e-' + Date.now() + '-' + Math.random().toString(36).slice(2, 6),
        agentId: a.id,
        ts: Date.now(),
        level: a.status === 'error' ? 'err' : a.status === 'paused' ? 'warn' : 'ok',
        text: `${a.name} 状态变化 → ${a.status}`,
      })
      if (events.value.length > 30) events.value.pop()
    }
  }

  function pushEvent(agentId: string, level: LivenessEvent['level'], text: string) {
    events.value.unshift({ id: 'e-' + Date.now() + '-' + Math.random().toString(36).slice(2, 6), agentId, ts: Date.now(), level, text })
    if (events.value.length > 30) events.value.pop()
  }

  const overallStatus = computed<AgentStatus>(() => {
    if (agents.value.some(a => a.status === 'error')) return 'error'
    if (agents.value.some(a => a.status === 'working')) return 'working'
    if (agents.value.some(a => a.status === 'paused')) return 'paused'
    return 'idle'
  })

  return { agents, events, overallStatus, tick, pushEvent }
})
