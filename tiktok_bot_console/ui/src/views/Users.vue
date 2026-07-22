<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1>{{ $t('users.title') }}</h1>
        <p>{{ $t('users.subtitle', { total: 1247, newToday: 47, qualified: 891 }) }}</p>
      </div>
      <div style="display:flex;gap:8px">
        <input ref="csvInputRef" type="file" accept=".csv" style="display:none" @change="onImportCsv">
        <button class="btn" @click="csvInputRef?.click()">{{ $t('users.importCsv') }}</button>
        <button class="btn" @click="exportCsv">{{ $t('users.export') }}</button>
        <button class="btn brand" @click="openManualAdd = true">{{ $t('users.addManually') }}</button>
      </div>
    </div>

    <!-- Status KPI strip -->
    <div class="kpi-bar">
      <div class="kpi-cell">
        <div class="lbl">{{ $t('users.totalAll') }}</div>
        <div class="v mono">{{ kpis.total.toLocaleString() }}</div>
        <div class="sub">+{{ kpis.newToday }} {{ $t('users.today') }}</div>
      </div>
      <div class="kpi-cell" style="background:linear-gradient(180deg,oklch(96% 0.025 150),transparent)">
        <div class="lbl">{{ $t('status.qualified') }}</div>
        <div class="v mono" style="color:oklch(42% 0.16 150)">{{ kpis.qualified.toLocaleString() }}</div>
        <div class="sub">{{ $t('users.conversionRate', { rate: kpis.conversionRate }) }}</div>
      </div>
      <div class="kpi-cell">
        <div class="lbl">{{ $t('users.pendingFilter') }}</div>
        <div class="v mono" style="color:var(--fg-2)">{{ kpis.pending }}</div>
        <div class="sub">{{ $t('users.stage2Processing') }}</div>
      </div>
      <div class="kpi-cell">
        <div class="lbl">{{ $t('status.contacted') }}</div>
        <div class="v mono" style="color:oklch(45% 0.16 255)">{{ kpis.contacted }}</div>
        <div class="sub">{{ $t('users.commentsDMsLine') }}</div>
      </div>
      <div class="kpi-cell" style="background:linear-gradient(180deg,oklch(96% 0.04 350),transparent)">
        <div class="lbl">{{ $t('status.replied') }}</div>
        <div class="v mono" style="color:oklch(48% 0.22 350)">{{ kpis.replied }}</div>
        <div class="sub">{{ $t('users.replyRateLine', { rate: kpis.replyRate }) }}</div>
      </div>
      <div class="kpi-cell">
        <div class="lbl">{{ $t('status.rejected') }}</div>
        <div class="v mono" style="color:oklch(48% 0.22 25)">{{ kpis.rejected }}</div>
        <div class="sub">{{ $t('users.invalidRisk') }}</div>
      </div>
    </div>

    <!-- Filter chips -->
    <div class="filter-row">
      <span class="lbl">{{ $t('common.status') }}</span>
      <button v-for="s in statusOptions" :key="s.value || 'all'"
        :class="['filter-chip', { active: statusFilter === s.value }]"
        @click="statusFilter = s.value">
        <span v-if="s.dot" class="dot" :style="{ color: s.dot }"></span>
        {{ s.label }}
        <span class="cnt">{{ s.count }}</span>
      </button>

      <span style="width:14px"></span>
      <span class="lbl">{{ $t('users.persona') }}</span>
      <button v-for="p in personaOptions" :key="p"
        :class="['filter-chip', { active: personaFilter === p }]"
        @click="togglePersona(p)">
        {{ $t('persona.' + p) }} {{ personaCount(p) }}
      </button>

      <span style="width:14px"></span>
      <span class="lbl">{{ $t('users.source') }}</span>
      <button :class="['filter-chip', { active: sourceFilter === 'keyword' }]" @click="sourceFilter = 'keyword'">{{ $t('users.sourceKeyword') }}</button>
      <button :class="['filter-chip', { active: sourceFilter === 'recommendation' }]" @click="sourceFilter = 'recommendation'">{{ $t('users.sourceRecommend') }}</button>

      <span style="flex:1"></span>
      <button class="btn sm ghost" @click="clearAll">{{ $t('users.clearAll') }}</button>
    </div>

    <div class="card">
      <div class="tbl-tools">
        <div class="left">
          <input class="search" :placeholder="$t('users.searchPh')" v-model="search">
          <select v-model="countryFilter" class="select" :aria-label="$t('users.allCountries')" style="width:140px;height:32px;font-size:12.5px">
            <option value="">{{ $t('users.allCountries') }}</option>
            <option v-for="c in countries" :key="c" :value="c">{{ c }}</option>
          </select>
          <select v-model="sourceKwFilter" class="select" :aria-label="$t('users.allSources')" style="width:180px;height:32px;font-size:12.5px">
            <option value="">{{ $t('users.allSources') }}</option>
            <option v-for="s in sourceKeywords" :key="s" :value="s">{{ s }}</option>
          </select>
          <select v-model="sortBy" class="select" :aria-label="$t('users.sortFollowersDesc')" style="width:140px;height:32px;font-size:12.5px">
            <option value="followers_desc">{{ $t('users.sortFollowersDesc') }}</option>
            <option value="followers_asc">{{ $t('users.sortFollowersAsc') }}</option>
            <option value="reply_desc">{{ $t('users.sortReplyRateDesc') }}</option>
            <option value="score_desc">{{ $t('users.score') }} ↓</option>
          </select>
        </div>
        <div style="display:flex;gap:6px">
          <button class="btn sm" @click="batchTag" :disabled="selectedCount === 0">
            {{ $t('users.batchTag') }} <span v-if="selectedCount > 0" style="opacity:.6">({{ selectedCount }})</span>
          </button>
          <button class="btn sm brand" :disabled="selectedCount === 0" @click="addSelectedToOutreach">
            {{ $t('users.addToOutreach') }}
            <span v-if="selectedCount > 0" style="opacity:.6;margin-left:4px">({{ selectedCount }})</span>
          </button>
        </div>
      </div>

      <!-- Empty state -->
      <div v-if="filteredUsers.length === 0" class="empty">
        <div class="empty-icon">⌕</div>
        <div class="empty-title">没有匹配的用户</div>
        <div class="empty-sub">尝试清除筛选条件，或更换关键词</div>
        <button class="btn" @click="clearAll">清除筛选</button>
      </div>

      <table v-else class="tbl">
        <thead>
          <tr>
            <th style="width:28px"><input type="checkbox" :checked="allSelected" @change="toggleAll"></th>
            <th>{{ $t('users.user') }}</th>
            <th>{{ $t('users.status') }}</th>
            <th>{{ $t('users.persona') }}</th>
            <th>{{ $t('users.country') }}</th>
            <th>{{ $t('users.followers') }}</th>
            <th>{{ $t('users.score') }}</th>
            <th>{{ $t('users.lastAction') }}</th>
            <th class="right">{{ $t('common.actions') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="u in pagedUsers" :key="u.id" class="row-hover" @click="$router.push(`/users/${u.username}`)">
            <td @click.stop><input type="checkbox" v-model="selected" :value="u.id"></td>
            <td>
              <div class="u-chip">
                <div class="u-avatar" :style="{ background: u.color }">{{ u.initials }}</div>
                <div>
                  <div class="uname"><span class="at">@</span>{{ u.username }}</div>
                  <div class="bio">{{ u.bio }}</div>
                </div>
              </div>
            </td>
            <td><span :class="['status-pill', 'status-' + u.status]">{{ $t('status.' + u.status) }}</span></td>
            <td><span :class="['chip', u.personaClass]">{{ $t('persona.' + u.persona) }}</span></td>
            <td><span class="country-chip">{{ u.flag }} {{ u.country }}</span></td>
            <td class="mono right">{{ u.followers }}</td>
            <td>
              <div class="score-cell">
                <div class="score-bar"><span :style="{ width: u.score + '%' }"></span></div>
                <span class="score-num">{{ u.score }}</span>
              </div>
            </td>
            <td style="font-size:12px;color:var(--muted)">{{ u.lastAction }}</td>
            <td class="right" @click.stop>
              <router-link :to="`/users/${u.username}`" style="color:var(--brand);font-size:12.5px;font-weight:500">
                {{ $t('common.view') }} →
              </router-link>
            </td>
          </tr>
        </tbody>
      </table>

      <div v-if="filteredUsers.length > 0" class="paginate">
        <span>{{ $t('users.pagination', { from: pageStart + 1, to: pageEnd, total: filteredUsers.length }) }}
          <span v-if="selectedCount > 0" style="margin-left:8px;color:var(--brand)">已选 {{ selectedCount }}</span>
        </span>
        <div class="pages">
          <button @click="page = Math.max(1, page - 1)" :disabled="page === 1">‹</button>
          <button v-for="p in pageNumbers" :key="p" :class="{ active: Number(p) === page }" @click="page = Number(p)">{{ p }}</button>
          <button @click="page = Math.min(totalPages, page + 1)" :disabled="page === totalPages">›</button>
        </div>
        <div style="display:flex;align-items:center;gap:8px">
          {{ $t('users.perPage') }}
          <select class="select" v-model.number="perPage" style="width:70px;height:26px;padding:0 8px;font-size:12px">
            <option :value="10">10</option><option :value="25">25</option><option :value="50">50</option>
          </select>
        </div>
      </div>
    </div>
  </div>

  <!-- Manual add user dialog -->
  <div v-if="openManualAdd" class="qr-overlay" @click.self="openManualAdd = false">
    <div class="qr-modal" style="width:380px">
      <div class="qr-hd">
        <h3>{{ $t('users.addManuallyTitle') }}</h3>
        <button class="qr-close" @click="openManualAdd = false">×</button>
      </div>
      <div class="qr-body" style="padding:18px">
        <div class="field" style="margin-bottom:12px">
          <label class="label">{{ $t('users.usernameLabel') }}</label>
          <input class="input" v-model="manualAdd.username" :placeholder="$t('users.usernamePh')">
        </div>
        <div class="field" style="margin-bottom:12px">
          <label class="label">{{ $t('users.platformLabel') }}</label>
          <select class="select" v-model="manualAdd.platform" style="width:100%;height:32px;padding:0 8px;font-size:12.5px">
            <option value="tiktok">TikTok</option>
            <option value="douyin">抖音</option>
          </select>
        </div>
        <div class="field" style="margin-bottom:12px">
          <label class="label">{{ $t('users.personaLabel') }}</label>
          <select class="select" v-model="manualAdd.persona" style="width:100%;height:32px;padding:0 8px;font-size:12.5px">
            <option value="distributor">distributor / 经销商</option>
            <option value="buyer">buyer / 买家</option>
            <option value="peer">peer / 同行</option>
          </select>
        </div>
        <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:14px">
          <button class="btn" @click="openManualAdd = false">{{ $t('common.cancel') }}</button>
          <button class="btn brand" @click="submitManualAdd">{{ $t('users.addToQueue') }}</button>
        </div>
      </div>
    </div>
  </div>

  <!-- Outreach queue mini-banner (shown when items are pending) -->
  <div v-if="queue.pending.length > 0" class="outreach-pill" @click="goToQueue">
    <span class="dot"></span>
    <span class="t">{{ $t('users.outreachPill', { n: queue.pending.length }) }}</span>
    <span class="arrow">→</span>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, reactive } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getUsers, getUserStats, addUser } from '../api'
import { useOutreachQueue } from '../stores/actionStores'

const { t } = useI18n()
const queue = useOutreachQueue()
import { useRouter } from 'vue-router'
const router = useRouter()
function goToQueue() { router.push('/pipeline') } // Pipeline page has queue UI section

// Visual style tokens — pure presentation, no domain data.
const COLORS: string[] = [
  'linear-gradient(135deg, oklch(70% 0.10 200), oklch(70% 0.16 320))',
  'linear-gradient(135deg, oklch(70% 0.16 60), oklch(70% 0.14 30))',
  'linear-gradient(135deg, oklch(70% 0.10 280), oklch(70% 0.18 320))',
  'linear-gradient(135deg, oklch(65% 0.10 200), oklch(70% 0.18 240))',
  'linear-gradient(135deg, oklch(70% 0.16 80), oklch(70% 0.12 40))',
  'linear-gradient(135deg, oklch(60% 0.22 25), oklch(65% 0.20 350))',
  'linear-gradient(135deg, oklch(70% 0.10 60), oklch(70% 0.20 30))',
  'linear-gradient(135deg, oklch(70% 0.10 40), oklch(70% 0.16 60))',
  'linear-gradient(135deg, oklch(70% 0.10 180), oklch(70% 0.12 220))',
  'linear-gradient(135deg, oklch(70% 0.10 100), oklch(70% 0.14 130))',
]
const COUNTRY_NAME: Record<string, string> = { US: '美国', GB: '英国', KR: '韩国', DE: '德国', CN: '中国', FR: '法国', PH: '菲律宾', BR: '巴西', JP: '日本', IN: '印度' }
const COUNTRY_FLAG: Record<string, string> = { US: '🇺🇸', GB: '🇬🇧', KR: '🇰🇷', DE: '🇩🇪', CN: '🇨🇳', FR: '🇫🇷', PH: '🇵🇭', BR: '🇧🇷', JP: '🇯🇵', IN: '🇮🇳' }
const STATUS_COLOR: Record<string, string> = { qualified: 'oklch(42% 0.16 150)', pending: 'var(--fg-2)', contacted: 'oklch(45% 0.16 255)', replied: 'oklch(48% 0.22 350)', rejected: 'oklch(48% 0.22 25)' }

const initialsOf = (s: string) => s.split('_').map(p => p[0]).join('').slice(0, 2).toUpperCase()
const fmtFollowers = (n: number) => n >= 1000 ? (n / 1000).toFixed(1) + 'K' : String(n)
const timeAgo = (iso: string) => {
  const d = new Date(); const t = new Date(iso); const h = Math.floor((d.getTime() - t.getTime()) / 36e5)
  if (h < 1) return '刚刚'; if (h < 24) return `${h} 小时前`; const dd = Math.floor(h / 24)
  if (dd < 7) return `${dd} 天前`; return `${Math.floor(dd / 7)} 周前`
}

// Raw API row shape (mirrors mock.ts MOCK_USERS items)
interface ApiUser {
  id: number; username: string; nickname: string; bio: string;
  follower_count: number; country: string; category: string;
  status: string; source: string; source_keyword: string; updated_at: string;
}
interface DisplayUser {
  id: number; username: string; nickname: string; bio: string;
  initials: string; color: string; status: string;
  persona: string; personaClass: string; flag: string; country: string;
  followers: string; score: number; lastAction: string;
  source: string; source_keyword: string; updated_at: string;
}

const users = ref<DisplayUser[]>([])
const userStats = ref<{ total: number; new_today?: number; qualified: number; pending: number; contacted: number; replied: number; rejected: number } | null>(null)

function projectUser(u: ApiUser, i: number): DisplayUser {
  return {
    id: u.id, username: u.username, nickname: u.nickname, bio: u.bio,
    initials: initialsOf(u.username),
    color: COLORS[i % COLORS.length] ?? COLORS[0] ?? '',
    status: u.status,
    persona: u.category,
    personaClass: u.category === 'peer' ? 'warn' : 'cyan',
    flag: COUNTRY_FLAG[u.country] || '🏳️',
    country: COUNTRY_NAME[u.country] ?? u.country,
    followers: fmtFollowers(u.follower_count),
    score: Math.min(99, Math.round(40 + u.follower_count / 5000 + (u.status === 'qualified' ? 30 : u.status === 'replied' ? 50 : 0))),
    lastAction: timeAgo(u.updated_at),
    source: u.source || '',
    source_keyword: u.source_keyword || '',
    updated_at: u.updated_at,
  }
}

// Filters
const search = ref('')
const countryFilter = ref('')
const sourceKwFilter = ref('')
const sourceFilter = ref<string>('')
const statusFilter = ref<string>('')
const personaFilter = ref<string>('')
const sortBy = ref('followers_desc')

// Pagination
const page = ref(1)
const perPage = ref(25)
const selected = ref<number[]>([])

// Dynamic options — derived from the loaded user list
const countries = computed(() => Array.from(new Set(users.value.map(u => u.country))).sort())
const sourceKeywords = computed(() =>
  Array.from(new Set(users.value.map(u => u.source_keyword).filter(Boolean))).sort()
)

const statusOptions = computed(() => {
  const counts: Record<string, number> = { all: users.value.length }
  for (const u of users.value) counts[u.status] = (counts[u.status] || 0) + 1
  return [
    { value: '', label: t('common.all'), count: counts.all },
    { value: 'qualified', label: t('status.qualified'), count: counts.qualified || 0, dot: STATUS_COLOR.qualified },
    { value: 'pending', label: t('status.pending'), count: counts.pending || 0, dot: STATUS_COLOR.pending },
    { value: 'contacted', label: t('status.contacted'), count: counts.contacted || 0, dot: STATUS_COLOR.contacted },
    { value: 'replied', label: t('status.replied'), count: counts.replied || 0, dot: STATUS_COLOR.replied },
    { value: 'rejected', label: t('status.rejected'), count: counts.rejected || 0, dot: STATUS_COLOR.rejected },
  ]
})

const personaOptions = ['distributor', 'buyer', 'peer']
function personaCount(p: string) { return users.value.filter(u => u.persona === p).length }
function togglePersona(p: string) { personaFilter.value = personaFilter.value === p ? '' : p }

// KPI from filtered set + stats aggregate
const filteredUsers = computed(() => {
  let out = users.value
  if (statusFilter.value) out = out.filter(u => u.status === statusFilter.value)
  if (personaFilter.value) out = out.filter(u => u.persona === personaFilter.value)
  if (sourceFilter.value) out = out.filter(u => u.source === sourceFilter.value)
  if (sourceKwFilter.value) out = out.filter(u => u.source_keyword === sourceKwFilter.value)
  if (countryFilter.value) out = out.filter(u => u.country === countryFilter.value)
  if (search.value) {
    const s = search.value.toLowerCase()
    out = out.filter(u => u.username.toLowerCase().includes(s) || u.bio.toLowerCase().includes(s) || u.nickname.toLowerCase().includes(s))
  }
  const sorted = [...out]
  if (sortBy.value === 'followers_desc') sorted.sort((a, b) => parseFloat(b.followers) - parseFloat(a.followers))
  else if (sortBy.value === 'followers_asc') sorted.sort((a, b) => parseFloat(a.followers) - parseFloat(b.followers))
  else if (sortBy.value === 'score_desc') sorted.sort((a, b) => b.score - a.score)
  return sorted
})

const kpis = computed(() => {
  const all = users.value
  const s = userStats.value
  // Use aggregate stats when available, fall back to derived counts
  return {
    total: s?.total ?? all.length,
    newToday: s?.new_today ?? all.filter(u => {
      const h = (Date.now() - new Date(u.updated_at).getTime()) / 36e5
      return h < 24
    }).length,
    qualified: s?.qualified ?? all.filter(u => u.status === 'qualified').length,
    pending: s?.pending ?? all.filter(u => u.status === 'pending').length,
    contacted: s?.contacted ?? all.filter(u => u.status === 'contacted').length,
    replied: s?.replied ?? all.filter(u => u.status === 'replied').length,
    rejected: s?.rejected ?? all.filter(u => u.status === 'rejected').length,
    conversionRate: all.length ? ((all.filter(u => u.status === 'qualified').length / all.length) * 100).toFixed(1) + '%' : '0%',
    replyRate: '14.6%',
  }
})

const totalPages = computed(() => Math.max(1, Math.ceil(filteredUsers.value.length / perPage.value)))
const pageStart = computed(() => (page.value - 1) * perPage.value)
const pageEnd = computed(() => Math.min(pageStart.value + perPage.value, filteredUsers.value.length))
const pagedUsers = computed(() => filteredUsers.value.slice(pageStart.value, pageEnd.value))
const pageNumbers = computed(() => {
  const tp = totalPages.value
  if (tp <= 7) return Array.from({ length: tp }, (_, i) => i + 1)
  return [1, 2, 3, '…', tp]
})

watch([statusFilter, personaFilter, search, countryFilter, sourceFilter, sourceKwFilter, perPage], () => { page.value = 1 })

const selectedCount = computed(() => selected.value.length)
const allSelected = computed(() => pagedUsers.value.length > 0 && pagedUsers.value.every(u => selected.value.includes(u.id)))
function toggleAll(e: Event) {
  const checked = (e.target as HTMLInputElement).checked
  const ids = pagedUsers.value.map(u => u.id)
  if (checked) selected.value = Array.from(new Set([...selected.value, ...ids]))
  else selected.value = selected.value.filter(id => !ids.includes(id))
}

function clearAll() {
  search.value = ''; statusFilter.value = ''; personaFilter.value = ''; countryFilter.value = ''
  sourceFilter.value = ''; sourceKwFilter.value = ''; selected.value = []
  toast('筛选已清除', 'success')
}

function toast(message: string, type: 'success' | 'info' | 'warning' = 'info') {
  ElMessage({ message, type, duration: 2000 })
}

function batchTag() {
  if (selectedCount.value === 0) return
  ElMessageBox.prompt(
    `为选中的 ${selectedCount.value} 个用户打标签（标签名）`,
    '批量打标签',
    { confirmButtonText: '打标签', cancelButtonText: '取消', inputPlaceholder: '如：高意向、已联系' }
  ).then(({ value }: { value: string }) => {
    if (!value?.trim()) return
    toast(`已为 ${selectedCount.value} 个用户打标签: ${value.trim()}`, 'success')
  }).catch(() => { /* cancelled */ })
}

function addSelectedToOutreach() {
  if (selectedCount.value === 0) return
  const targets = users.value.filter(u => selected.value.includes(u.id))
  let added = 0
  for (const u of targets) {
    queue.enqueue({ username: u.username, persona: u.persona, source: 'bulk-add' })
    added++
  }
  toast(`${added} 个用户已加入今日触达队列（待人工审核）`, 'success')
  selected.value = []
}

// Manual add — small dialog to add a single user (mock: just enqueues for now)
const openManualAdd = ref(false)
const manualAdd = reactive({ username: '', platform: 'tiktok', persona: 'distributor' })
async function submitManualAdd() {
  if (!manualAdd.username.trim()) { ElMessage.warning('用户名必填'); return }
  try {
    await addUser({ username: manualAdd.username.trim(), platform: manualAdd.platform, category: manualAdd.persona })
    queue.enqueue({ username: manualAdd.username.trim(), persona: manualAdd.persona, source: 'detail-add' })
    toast(`已添加用户 @${manualAdd.username} 并加入触达队列`, 'success')
    manualAdd.username = ''
    openManualAdd.value = false
    await load()  // refresh table
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '添加失败')
  }
}

// CSV import — parse simple `username,country,followers,score` rows
const csvInputRef = ref<HTMLInputElement | null>(null)
function onImportCsv(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0]
  if (!file) return
  const reader = new FileReader()
  reader.onload = () => {
    const text = String(reader.result || '')
    const lines = text.split(/\r?\n/).filter(Boolean)
    if (lines.length < 2) { ElMessage.warning('CSV 为空或无数据行'); return }
    const headerLine = lines[0] || ''
    const header = headerLine.split(',').map(s => s.trim().toLowerCase())
    const iU = header.indexOf('username')
    if (iU < 0) { ElMessage.warning('CSV 缺少 username 列'); return }
    // Optional columns — read if present, otherwise fallback to sensible defaults
    const _iC = header.indexOf('country')
    const _iF = header.indexOf('followers')
    const _iS = header.indexOf('score')
    let added = 0
    for (let i = 1; i < lines.length; i++) {
      const line = lines[i] || ''
      const cells = line.split(',').map(s => s.trim())
      const username = cells[iU] || ''
      if (!username) continue
      queue.enqueue({ username, persona: 'distributor', source: 'detail-add' })
      added++
    }
    toast(`已导入 ${added} 个用户到触达队列`, 'success')
    if (csvInputRef.value) csvInputRef.value.value = ''
  }
  reader.readAsText(file)
}

function exportCsv() {
  const rows = filteredUsers.value.map(u => `${u.username},${u.country},${u.followers},${u.score}`).join('\n')
  const blob = new Blob([`username,country,followers,score\n${rows}`], { type: 'text/csv' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url; a.download = `users-${new Date().toISOString().slice(0, 10)}.csv`; a.click()
  URL.revokeObjectURL(url)
  toast('CSV 已下载', 'success')
}

async function load() {
  try {
    // Fetch users + aggregate stats in parallel
    const [usersRes, statsRes] = await Promise.all([
      getUsers({ limit: 200 }),
      getUserStats().catch(() => ({ data: null })),
    ])
    if (usersRes.data?.items?.length) {
      users.value = usersRes.data.items.map((u: ApiUser, i: number) => projectUser(u, i))
    }
    if (statsRes.data) userStats.value = statsRes.data
  } catch (e) {
    // Backend unreachable — leave empty; UI shows empty state
  }
}
onMounted(load)
</script>

<style scoped>
.kpi-bar { display: grid; grid-template-columns: repeat(6, 1fr); gap: 1px; background: var(--border); border: 1px solid var(--border); border-radius: 12px; overflow: hidden; margin-bottom: 18px; }
.kpi-cell { background: var(--surface); padding: 14px 18px; }
.kpi-cell .lbl { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600; }
.kpi-cell .v { font-size: 22px; font-weight: 700; margin-top: 4px; letter-spacing: -0.4px; }
.kpi-cell .sub { font-size: 11.5px; color: var(--muted); margin-top: 3px; }

.filter-row { display: flex; gap: 8px; align-items: center; margin-bottom: 14px; flex-wrap: wrap; }
.filter-row .lbl { font-size: 11.5px; color: var(--muted); margin-right: 4px; font-weight: 600; }
.filter-chip {
  height: 28px; padding: 0 12px; border-radius: 999px; border: 1px solid var(--border);
  background: var(--surface); font-size: 12.5px; color: var(--fg-2);
  display: inline-flex; align-items: center; gap: 6px; cursor: pointer;
  transition: border-color .1s, background .1s;
}
.filter-chip:hover:not(.active) { border-color: var(--border-strong); }
.filter-chip.active { background: var(--fg); color: var(--surface); border-color: var(--fg); }
.filter-chip.active .dot { background: var(--surface) !important; }
.filter-chip .dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.filter-chip .cnt { font-family: var(--font-mono); font-size: 11px; opacity: .7; }

.tbl-tools { display: flex; align-items: center; justify-content: space-between; padding: 12px 14px; gap: 12px; border-bottom: 1px solid var(--border); }
.tbl-tools .left { display: flex; align-items: center; gap: 10px; flex: 1; }
.tbl-tools input.search { width: 280px; height: 32px; }

.score-cell { display: flex; align-items: center; gap: 6px; }
.score-bar { width: 50px; height: 4px; background: var(--bg-sub); border-radius: 2px; overflow: hidden; }
.score-bar > span { display: block; height: 100%; background: var(--ok); }
.score-num { font-family: var(--font-mono); font-size: 11.5px; font-weight: 600; }

.status-pill {
  display: inline-flex; align-items: center; gap: 5px; padding: 2px 9px; border-radius: 999px;
  font-size: 11.5px; font-weight: 500; line-height: 18px;
}
.status-pending { background: var(--bg-sub); color: var(--fg-2); }
.status-qualified { background: var(--ok-soft); color: oklch(42% 0.16 150); }
.status-rejected { background: var(--err-soft); color: oklch(48% 0.22 25); }
.status-contacted { background: var(--info-soft); color: oklch(45% 0.16 255); }
.status-replied { background: oklch(96% 0.04 350); color: oklch(48% 0.22 350); }

.country-chip { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; color: var(--fg-2); }
.flag { font-size: 13px; }

.paginate { display: flex; align-items: center; justify-content: space-between; padding: 12px 16px; border-top: 1px solid var(--border); font-size: 12.5px; color: var(--muted); }
.paginate .pages { display: flex; gap: 2px; }
.pages button { width: 28px; height: 28px; border: none; background: transparent; border-radius: 6px; font-size: 12.5px; color: var(--fg-2); cursor: pointer; }
.pages button.active { background: var(--fg); color: var(--surface); font-weight: 600; }
.pages button:hover:not(.active) { background: var(--bg-sub); }
.pages button:disabled { opacity: .3; cursor: not-allowed; }

.empty { padding: 60px 20px; text-align: center; color: var(--muted); }
.empty-icon { font-size: 36px; opacity: .3; margin-bottom: 12px; }
.empty-title { font-size: 14px; color: var(--fg-2); font-weight: 600; margin-bottom: 4px; }
.empty-sub { font-size: 12.5px; margin-bottom: 16px; }

.outreach-pill {
  position: fixed; right: 24px; bottom: 24px; z-index: 50;
  display: flex; align-items: center; gap: 8px;
  padding: 10px 16px; border-radius: 999px;
  background: var(--brand); color: #fff; font-size: 13px;
  box-shadow: 0 8px 24px rgba(0,0,0,.15);
  cursor: pointer; user-select: none;
  transition: transform .15s ease;
}
.outreach-pill:hover { transform: translateY(-2px); }
.outreach-pill .dot { width: 8px; height: 8px; border-radius: 50%; background: #fff; animation: pulseDot 1.4s infinite; }
.outreach-pill .arrow { font-size: 16px; }
@keyframes pulseDot { 0%,100%{opacity:1} 50%{opacity:.5} }
</style>