<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1>{{ $t('accounts.title') }}</h1>
        <p>{{ $t('accounts.subtitle') }}</p>
      </div>
      <div style="display:flex;gap:8px">
        <button class="btn" @click="importCookie">{{ $t('accounts.importCookie') }}</button>
        <button class="btn" @click="batchCheck">{{ $t('accounts.batchCheck') }}</button>
        <button class="btn" @click="openQR('tiktok')">📱 {{ $t('accounts.qrLoginTiktok') }}</button>
        <button class="btn brand" @click="openQR('douyin')">📱 {{ $t('accounts.qrLoginDouyin') }}</button>
      </div>
    </div>

    <!-- Risk notice -->
    <div class="card risk-card">
      <div class="risk-icon">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
      </div>
      <div class="txt">
        <b>{{ $t('accounts.riskTitle') }}</b> {{ $t('accounts.riskBody', { c1: '09:00', c2: '14:00', c3: '19:00', cm: 25, dm: 12, imin: 3, imax: 15 }) }}
      </div>
      <button class="btn sm" @click="viewPolicy">{{ $t('accounts.viewPolicy') }}</button>
    </div>

    <!-- Summary -->
    <div class="acct-summary">
      <div class="card sum-card">
        <div class="lbl">{{ $t('accounts.totalAccounts') }}</div>
        <div class="v mono">{{ accounts.length }} <span class="total">/ 5 {{ $t('accounts.upper') }}</span></div>
      </div>
      <div class="card sum-card">
        <div class="lbl">{{ $t('accounts.healthyRunning') }}</div>
        <div class="v mono" style="color:oklch(42% 0.16 150)">{{ healthyCount }}</div>
      </div>
      <div class="card sum-card">
        <div class="lbl">{{ $t('accounts.todayOutreach') }}</div>
        <div class="v mono">{{ todayOutreach }} <span class="total">/ 75 {{ $t('accounts.dailyLimit') }}</span></div>
      </div>
      <div class="card sum-card">
        <div class="lbl">{{ $t('accounts.remaining') }}</div>
        <div class="v mono" style="color:var(--brand)">{{ remainingQuota }}</div>
      </div>
    </div>

    <!-- Toolbar -->
    <div class="toolbar">
      <div class="tabs-inline">
        <button :class="{active: tabFilter === 'all'}" @click="tabFilter = 'all'">{{ $t('common.all') }} <span style="opacity:.6">{{ accounts.length }}</span></button>
        <button :class="{active: tabFilter === 'tiktok'}" @click="tabFilter = 'tiktok'">TikTok <span style="opacity:.6">{{ tiktokCount }}</span></button>
        <button :class="{active: tabFilter === 'douyin'}" @click="tabFilter = 'douyin'">{{ $t('accounts.douyin') }} <span style="opacity:.6">{{ douyinCount }}</span></button>
        <button :class="{active: tabFilter === 'logged_in'}" @click="tabFilter = 'logged_in'">{{ $t('accounts.loggedIn') }} <span style="opacity:.6">{{ loggedInCount }}</span></button>
        <button :class="{active: tabFilter === 'expired'}" @click="tabFilter = 'expired'">{{ $t('accounts.expired') }} <span style="opacity:.6">{{ expiredCount }}</span></button>
      </div>
      <div class="right">
        <select class="select" v-model="sortBy" :aria-label="$t('accounts.sortRecent')" style="width:140px;height:32px;font-size:12.5px">
          <option value="recent">{{ $t('accounts.sortRecent') }}</option>
          <option value="followers">{{ $t('accounts.sortFollowers') }}</option>
          <option value="today">{{ $t('accounts.sortToday') }}</option>
        </select>
      </div>
    </div>

    <div class="acct-grid">
      <div v-for="a in filteredAccounts" :key="a.id" class="card acct-card">
        <div class="acct-head">
          <div :class="['av', a.platform === 'douyin' ? 'dy' : 'tt']">{{ a.username.slice(-2).toUpperCase() }}</div>
          <div style="flex:1;min-width:0">
            <div class="nm">@{{ a.username }} <span :class="['status-pill', a.statusKey]" style="margin-left:6px"><span class="dot"></span> {{ $t('accounts.' + a.statusKey) }}</span></div>
            <div class="handle">{{ a.nickname }}</div>
          </div>
          <span class="plat" :style="{ background: a.platform === 'douyin' ? 'var(--info-soft)' : 'var(--err-soft)', color: a.platform === 'douyin' ? 'oklch(45% 0.16 255)' : 'oklch(48% 0.22 25)' }">{{ a.platform === 'douyin' ? $t('accounts.douyin') : 'TikTok' }}</span>
        </div>
        <div class="acct-stats">
          <div class="s"><div class="v mono">{{ fmtK(a.followers) }}</div><div class="l">{{ $t('userDetail.followers') }}</div></div>
          <div class="s"><div class="v mono">{{ a.videos }}</div><div class="l">{{ $t('userDetail.videos') }}</div></div>
          <div class="s"><div class="v mono">{{ fmtK(a.likes) }}</div><div class="l">{{ $t('userDetail.totalLikes') }}</div></div>
        </div>
        <div class="acct-meta">
          {{ $t('accounts.status') }}<span :class="['status-pill', a.statusKey]"><span class="dot"></span> {{ $t('accounts.' + (a.statusKey === 'off' ? 'cookieExpired' : a.statusKey === 'on' ? 'loggedHealthy' : a.statusKey)) }}</span><br>
          {{ $t('accounts.lastLogin') }} <b>{{ (a.last_login_at || '').slice(0, 10) }} {{ (a.last_login_at || '').slice(11, 16) }}</b><br>
          {{ $t('accounts.todayUsage') }} <b :style="{ color: (a.today?.comments || 0) + (a.today?.dms || 0) > 0 ? 'var(--brand)' : 'inherit' }">{{ a.today?.comments || 0 }} {{ $t('accounts.comments') }} / {{ a.today?.dms || 0 }} {{ $t('accounts.dms') }}</b> · <span style="color:var(--muted)">{{ a.today?.currentTask || '—' }}</span>
        </div>
        <div class="acct-foot">
          <div class="left">
            <button class="btn primary" v-if="a.statusKey === 'off'" @click="openQR(a.platform === 'douyin' ? 'douyin' : 'tiktok')">{{ $t('accounts.qrLogin') }}</button>
            <button class="btn" v-else @click="logout(a)">{{ $t('accounts.logout') }}</button>
            <button class="btn sm ghost" @click="checkCookie(a)">{{ $t('accounts.check') }}</button>
            <button class="btn sm ghost" style="color:var(--err)" @click="deleteAccount(a)">{{ $t('common.delete') }}</button>
          </div>
          <span style="font-size:11.5px;color:var(--muted)">{{ (a.today?.comments || 0) + (a.today?.dms || 0) }} / {{ (a.today?.comments || 0) + (a.today?.dms || 0) + 25 }}</span>
        </div>
      </div>
    </div>

    <div class="card mt-16">
      <div class="card-hd">
        <h3>{{ $t('accounts.todayActivityTitle') }}</h3>
        <span class="hint">{{ $t('accounts.refresh30s') }}</span>
      </div>
      <table class="tbl">
        <thead>
          <tr>
            <th>{{ $t('accounts.colAccount') }}</th>
            <th>{{ $t('accounts.colComments') }}</th>
            <th>{{ $t('accounts.colDMs') }}</th>
            <th>{{ $t('accounts.colReplies') }}</th>
            <th>{{ $t('accounts.colCurrentTask') }}</th>
            <th>{{ $t('users.status') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="a in accounts" :key="a.id">
            <td><div class="u-chip"><div class="u-avatar" :style="{ background: avBg(a) }">{{ a.username.slice(-2).toUpperCase() }}</div><span class="uname" :style="{ color: a.statusKey === 'off' ? 'var(--muted)' : '' }">@{{ a.username }}</span></div></td>
            <td class="mono" :style="{ color: a.statusKey === 'off' ? 'var(--muted)' : '' }">{{ a.today?.comments || 0 }} / 25</td>
            <td class="mono" :style="{ color: a.statusKey === 'off' ? 'var(--muted)' : '' }">{{ a.today?.dms || 0 }} / 12</td>
            <td class="mono" :style="{ color: (a.today?.replies || 0) > 0 ? 'var(--ok)' : '' }">{{ (a.today?.replies || 0) > 0 ? '+' + (a.today?.replies || 0) : '—' }}</td>
            <td style="font-size:12.5px;color:var(--muted)">{{ a.today?.currentTask || '—' }}</td>
            <td><span :class="['status-pill', a.statusKey]"><span class="dot"></span> {{ $t('accounts.' + (a.statusKey === 'on' ? 'running' : a.statusKey === 'off' ? 'offline' : 'idle')) }}</span></td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>

  <QRScanModal
    v-if="qrOpen"
    :platform="qrPlatform"
    @close="qrOpen = false"
    @success="onQRSuccess"
  />
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getAccounts, deleteAccount as apiDeleteAccount, checkAccountSession, updateAccountCookies } from '../api'
import QRScanModal from '../components/QRScanModal.vue'

const { t } = useI18n()

interface Account {
  id: number; platform: string; username: string; login_method: string;
  status: string; last_login_at: string | null; nickname: string;
  followers: number; videos: number; likes: number;
  today: { comments: number; dms: number; replies: number; currentTask: string } | null
  statusKey: 'on' | 'off' | 'warn'
}
const accounts = ref<Account[]>([])
const tabFilter = ref<string>('all')
const sortBy = ref<string>('recent')

// Filtered + sorted accounts
const filteredAccounts = computed(() => {
  let list = [...accounts.value]
  // Tab filter
  if (tabFilter.value === 'tiktok') list = list.filter(a => a.platform === 'tiktok')
  else if (tabFilter.value === 'douyin') list = list.filter(a => a.platform === 'douyin')
  else if (tabFilter.value === 'logged_in') list = list.filter(a => a.status === 'logged_in')
  else if (tabFilter.value === 'expired') list = list.filter(a => a.status === 'expired')
  // Sort
  if (sortBy.value === 'followers') list.sort((a, b) => b.followers - a.followers)
  else if (sortBy.value === 'today') list.sort((a, b) => ((b.today?.comments || 0) + (b.today?.dms || 0)) - ((a.today?.comments || 0) + (a.today?.dms || 0)))
  return list
})

// QR scan modal state
const qrOpen = ref(false)
const qrPlatform = ref<'tiktok' | 'douyin'>('douyin')
function openQR(p: 'tiktok' | 'douyin') {
  qrPlatform.value = p
  qrOpen.value = true
}
async function onQRSuccess(username: string, platform: 'tiktok' | 'douyin') {
  qrOpen.value = false
  ElMessage.success(`已添加 ${platform === 'douyin' ? '抖音' : 'TikTok'} 账号 @${username}`)
  await loadAccounts()  // refresh table
}

async function importCookie() {
  try {
    const { value } = await ElMessageBox.prompt('粘贴完整 Cookie 字符串（用于已添加的账号）', '导入 Cookie', {
      inputPlaceholder: '完整 cookie 字符串',
      inputType: 'textarea',
      confirmButtonText: '导入', cancelButtonText: '取消',
    })
    if (!value?.trim()) return
    // Find the first account to attach cookies to
    const target = accounts.value.find(a => a.statusKey === 'off') || accounts.value[0]
    if (!target) { ElMessage.warning('请先添加账号再导入 Cookie'); return }
    await updateAccountCookies(target.id, value.trim())
    ElMessage.success(`已为 @${target.username} 导入 Cookie`)
    await loadAccounts()
  } catch { /* cancelled */ }
}

async function batchCheck() {
  ElMessage.info('正在批量检测账号 Cookie 状态…')
  let ok = 0, exp = 0
  for (const a of accounts.value) {
    try {
      const { data } = await checkAccountSession(a.id)
      if (data?.valid) ok++
      else exp++
    } catch { exp++ }
  }
  // 重新从后端加载，确保状态持久化
  await loadAccounts()
  ElMessage.success(`检测完成：${ok} 个有效 / ${exp} 个已过期 / 共 ${accounts.value.length} 个`)
}

async function checkCookie(a: Account) {
  ElMessage.info(`正在检测 @${a.username}…`)
  try {
    const { data } = await checkAccountSession(a.id)
    const ok = data?.valid
    // 重新从后端加载，确保状态持久化
    await loadAccounts()
    ElMessage[ok ? 'success' : 'error'](`@${a.username} Cookie ${ok ? '有效' : '已过期'} · ${ok ? '继续使用' : '建议重新登录'}`)
  } catch {
    ElMessage.error(`@${a.username} 检测失败`)
  }
}

async function logout(a: Account) {
  try {
    await ElMessageBox.confirm(`确认退出 @${a.username}？退出后该账号将无法触达。`, '退出账号', {
      confirmButtonText: '退出', cancelButtonText: '取消', type: 'warning',
    })
    await updateAccountCookies(a.id, '')
    // 重新从后端加载，确保状态持久化
    await loadAccounts()
    ElMessage.success(`已退出 @${a.username}`)
  } catch { /* cancelled */ }
}

async function deleteAccount(a: Account) {
  try {
    await ElMessageBox.confirm(`确认删除账号 @${a.username}？此操作不可撤销。`, '删除账号', {
      confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning',
    })
    try {
      await apiDeleteAccount(a.id)
    } catch (err: any) {
      ElMessage.error(`删除失败：${err?.response?.data?.detail || err?.message || '未知错误'}`)
      return
    }
    // 重新从后端加载，确保删除已持久化
    await loadAccounts()
    ElMessage.success(`已删除 @${a.username}`)
  } catch { /* cancelled by user */ }
}

function viewPolicy() {
  ElMessageBox.alert(
    '为保证账号安全，建议每账号 1 设备 / 1 住宅 IP / 1 真实身份认证；日触达量 ≤ 上限 80%，避开 00:00-08:00 高风险时段。',
    '反封号策略详情',
    { confirmButtonText: '知道了' }
  )
}
async function loadAccounts() {
  try {
    const { data } = await getAccounts()
    if (Array.isArray(data)) accounts.value = data as Account[]
  } catch {}
}

function fmtK(n: number) {
  if (n >= 1000) return (n / 1000).toFixed(1) + 'K'
  return String(n)
}
function avBg(a: Account) {
  if (a.platform === 'douyin') return 'linear-gradient(135deg,oklch(58% 0.16 25),oklch(60% 0.22 350))'
  return 'linear-gradient(135deg,oklch(70% 0.12 200),oklch(58% 0.22 350))'
}

const healthyCount = computed(() => accounts.value.filter(a => a.statusKey === 'on').length)
const todayOutreach = computed(() => accounts.value.reduce((s, a) => s + (a.today?.comments || 0) + (a.today?.dms || 0), 0))
const remainingQuota = computed(() => Math.max(0, 75 - todayOutreach.value))
const tiktokCount = computed(() => accounts.value.filter(a => a.platform === 'tiktok').length)
const douyinCount = computed(() => accounts.value.filter(a => a.platform === 'douyin').length)
const loggedInCount = computed(() => accounts.value.filter(a => a.status === 'logged_in').length)
const expiredCount = computed(() => accounts.value.filter(a => a.status === 'expired').length)

onMounted(async () => {
  try {
    const { data } = await getAccounts()
    if (Array.isArray(data)) accounts.value = data as Account[]
  } catch {}
})
</script>

<style scoped>
.acct-summary { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 18px; }
.sum-card { padding: 16px 18px; }
.sum-card .lbl { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600; }
.sum-card .v { font-size: 24px; font-weight: 700; letter-spacing: -0.4px; margin-top: 4px; }
.sum-card .v .total { font-size: 14px; color: var(--muted); font-weight: 500; }

.toolbar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; gap: 12px; }
.toolbar .tabs-inline { display: flex; gap: 4px; }
.toolbar .tabs-inline button { height: 30px; padding: 0 12px; border: 1px solid var(--border); background: var(--surface); border-radius: 7px; font-size: 12.5px; color: var(--fg-2); display: inline-flex; align-items: center; gap: 6px; cursor: pointer; }
.toolbar .tabs-inline button.active { background: var(--fg); color: var(--surface); border-color: var(--fg); font-weight: 600; }
.toolbar .right { display: flex; gap: 8px; align-items: center; }

.risk-card { padding: 14px 16px; margin-bottom: 18px; display: flex; align-items: center; gap: 14px; background: linear-gradient(135deg, oklch(96% 0.03 25), var(--surface)); border-color: oklch(88% 0.06 25); }
.risk-icon { width: 36px; height: 36px; border-radius: 8px; background: var(--err-soft); color: var(--err); display: grid; place-items: center; flex-shrink: 0; }
.risk-card .txt { flex: 1; font-size: 13px; color: var(--fg-2); line-height: 1.5; }
.risk-card .txt b { color: var(--fg); }

.acct-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }
.acct-card { padding: 18px; position: relative; }
.acct-head { display: flex; gap: 12px; align-items: center; margin-bottom: 14px; }
.acct-head .av { width: 44px; height: 44px; border-radius: 50%; display: grid; place-items: center; color: #fff; font-weight: 700; font-size: 14px; }
.acct-head .av.tt { background: linear-gradient(135deg, oklch(58% 0.22 350), oklch(48% 0.22 350)); }
.acct-head .av.dy { background: linear-gradient(135deg, oklch(58% 0.16 25), oklch(60% 0.22 350)); }
.acct-head .nm { font-size: 14px; font-weight: 600; }
.acct-head .handle { font-size: 12px; color: var(--muted); font-family: var(--font-mono); }
.acct-head .plat { font-size: 11px; font-weight: 600; padding: 2px 7px; border-radius: 4px; }

.acct-stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-bottom: 14px; padding: 12px; background: var(--bg-sub); border-radius: 8px; }
.acct-stats .s .v { font-size: 14px; font-weight: 700; font-family: var(--font-mono); }
.acct-stats .s .l { font-size: 10.5px; color: var(--muted); margin-top: 2px; }

.acct-meta { font-size: 11.5px; color: var(--muted); margin-bottom: 14px; line-height: 1.7; }
.acct-meta b { color: var(--fg); font-weight: 600; }

.acct-foot { display: flex; justify-content: space-between; align-items: center; padding-top: 14px; border-top: 1px solid var(--border); }
.acct-foot .left { display: flex; gap: 6px; }
.acct-foot .left .btn { height: 28px; padding: 0 10px; font-size: 12px; }

.status-pill { display: inline-flex; align-items: center; gap: 5px; padding: 3px 9px; border-radius: 999px; font-size: 11px; font-weight: 500; }
.status-pill .dot { width: 6px; height: 6px; border-radius: 50%; }
.status-pill.on { background: var(--ok-soft); color: oklch(42% 0.16 150); }
.status-pill.on .dot { background: var(--ok); }
.status-pill.off { background: var(--err-soft); color: oklch(48% 0.22 25); }
.status-pill.off .dot { background: var(--err); }
.status-pill.warn { background: var(--warn-soft); color: oklch(45% 0.16 75); }
.status-pill.warn .dot { background: var(--warn); }
</style>