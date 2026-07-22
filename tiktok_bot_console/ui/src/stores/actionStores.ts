import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

/**
 * Action stores — model the human-in-the-loop workflow:
 *
 *  Outreach queue
 *    User selects users in /users → clicks "加入今日触达"
 *    → users land here, awaiting "approve" before any message is sent
 *    → on Approve, the message is added to messageLog (no real send
 *      while in demo mode; in real mode this would invoke the platform API
 *      with a human-confirmed payload).
 *
 *  Message log
 *    Audit trail of every message that the human has approved.
 *    Persisted to localStorage so demo refreshes don't lose context.
 *
 *  Drafts
 *    Pending edits to runtime config / LLM providers / custom reports.
 *    Saved on "保存草稿"; applied via the mock's setConfigKey.
 */
const LS_OUTREACH = 'tiktok-bot:outreach-queue'
const LS_MESSAGES  = 'tiktok-bot:message-log'
const LS_DRAFTS    = 'tiktok-bot:drafts'
const LS_REPORTS   = 'tiktok-bot:custom-reports'

function loadLS<T>(key: string, fallback: T): T {
  if (typeof localStorage === 'undefined') return fallback
  try { const s = localStorage.getItem(key); return s ? JSON.parse(s) as T : fallback } catch { return fallback }
}
function saveLS(key: string, value: unknown) {
  if (typeof localStorage === 'undefined') return
  try { localStorage.setItem(key, JSON.stringify(value)) } catch { /* quota or private mode */ }
}

// ─── Outreach queue ────────────────────────────────────────────
export interface QueuedOutreach {
  id: string
  username: string
  persona: string
  addedAt: number
  source: 'bulk-add' | 'detail-add'
  status: 'pending' | 'approved' | 'rejected'
  note?: string
}
export const useOutreachQueue = defineStore('outreach-queue', () => {
  const items = ref<QueuedOutreach[]>(loadLS<QueuedOutreach[]>(LS_OUTREACH, []))
  function persist() { saveLS(LS_OUTREACH, items.value) }

  function enqueue(payload: { username: string; persona: string; source: 'bulk-add' | 'detail-add' }) {
    items.value.unshift({
      id: 'q-' + Date.now() + '-' + Math.random().toString(36).slice(2, 6),
      username: payload.username,
      persona: payload.persona,
      addedAt: Date.now(),
      source: payload.source,
      status: 'pending',
    })
    persist()
  }
  function approve(id: string) {
    const it = items.value.find(x => x.id === id); if (it) { it.status = 'approved'; persist() }
  }
  function reject(id: string, note?: string) {
    const it = items.value.find(x => x.id === id); if (it) { it.status = 'rejected'; it.note = note; persist() }
  }
  function remove(id: string) {
    items.value = items.value.filter(x => x.id !== id); persist()
  }
  function clear() { items.value = []; persist() }
  function clearApproved() {
    items.value = items.value.filter(x => x.status !== 'approved'); persist()
  }
  const pending = computed(() => items.value.filter(x => x.status === 'pending'))
  const approved = computed(() => items.value.filter(x => x.status === 'approved'))
  const rejected = computed(() => items.value.filter(x => x.status === 'rejected'))
  return { items, pending, approved, rejected, enqueue, approve, reject, remove, clear, clearApproved }
})

// ─── Message log ────────────────────────────────────────────────
export interface MessageLogEntry {
  id: string
  username: string
  channel: 'comment' | 'dm'
  content: string
  approvedAt: number
  sentBy: 'human-via-tool'
}
export const useMessageLog = defineStore('message-log', () => {
  const items = ref<MessageLogEntry[]>(loadLS<MessageLogEntry[]>(LS_MESSAGES, []))

  function record(payload: { username: string; channel: 'comment' | 'dm'; content: string }) {
    items.value.unshift({
      id: 'm-' + Date.now() + '-' + Math.random().toString(36).slice(2, 6),
      username: payload.username,
      channel: payload.channel,
      content: payload.content,
      approvedAt: Date.now(),
      sentBy: 'human-via-tool',
    })
    saveLS(LS_MESSAGES, items.value)
  }
  function clear() { items.value = []; saveLS(LS_MESSAGES, items.value) }
  return { items, record, clear }
})

// ─── Drafts ────────────────────────────────────────────────────
export interface Draft {
  id: string
  name: string
  payload: Record<string, unknown>
  savedAt: number
}
export const useDraftStore = defineStore('drafts', () => {
  const drafts = ref<Draft[]>(loadLS<Draft[]>(LS_DRAFTS, []))

  function save(name: string, payload: Record<string, unknown>) {
    const idx = drafts.value.findIndex(d => d.name === name)
    if (idx >= 0) {
      const existing = drafts.value[idx]
      if (existing) drafts.value[idx] = { id: existing.id, name, payload, savedAt: Date.now() }
    } else {
      drafts.value.unshift({ id: 'd-' + Date.now(), name, payload, savedAt: Date.now() })
    }
    saveLS(LS_DRAFTS, drafts.value)
  }
  function get(name: string): Draft | undefined { return drafts.value.find(d => d.name === name) }
  function remove(name: string) { drafts.value = drafts.value.filter(d => d.name !== name); saveLS(LS_DRAFTS, drafts.value) }
  return { drafts, save, get, remove }
})

// ─── Custom reports ────────────────────────────────────────────
export interface CustomReport {
  id: string
  name: string
  period: 7 | 30 | 90
  createdAt: number
}
export const useCustomReports = defineStore('custom-reports', () => {
  const items = ref<CustomReport[]>(loadLS<CustomReport[]>(LS_REPORTS, []))
  function create(name: string, period: 7 | 30 | 90) {
    items.value.unshift({ id: 'r-' + Date.now(), name, period, createdAt: Date.now() })
    saveLS(LS_REPORTS, items.value)
  }
  function remove(id: string) { items.value = items.value.filter(x => x.id !== id); saveLS(LS_REPORTS, items.value) }
  return { items, create, remove }
})
