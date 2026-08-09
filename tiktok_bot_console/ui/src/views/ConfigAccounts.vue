<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1>{{ $t('accounts.title') }}</h1>
        <p>{{ $t('accounts.subtitle', { total: accounts.length, healthy: loggedInCount }) }}</p>
      </div>
      <div style="display:flex;gap:8px">
        <button class="btn" @click="importCookie">{{ $t('accounts.importCookie') }}</button>
        <button class="btn" @click="batchCheck">{{ $t('accounts.batchCheck') }}</button>
        <button class="btn" @click="openLogin('tiktok')">{{ $t('accounts.interactiveLoginTiktok') }}</button>
        <button class="btn brand" @click="openLogin('douyin')">{{ $t('accounts.interactiveLoginDouyin') }}</button>
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

    <div class="capability-grid" aria-live="polite">
      <div
        v-for="platform in (['tiktok', 'douyin'] as const)"
        :key="platform"
        class="card capability-card"
      >
        <div class="capability-head">
          <div>
            <span class="eyebrow">{{ platform === 'tiktok' ? 'TikTok' : $t('accounts.douyin') }}</span>
            <strong>{{ platform === 'tiktok' ? $t('accounts.fingerprintRuntime') : $t('accounts.playwrightRuntime') }}</strong>
          </div>
          <span
            v-if="capabilities?.platforms[platform]"
            :class="['provider-state', capabilities.platforms[platform].available ? 'ready' : 'blocked']"
          >
            {{ capabilities.platforms[platform].available
              ? $t('accounts.providerReady')
              : $t('accounts.providerBlocked') }}
          </span>
        </div>
        <template v-if="capabilitiesLoading">
          <span class="capability-copy">{{ $t('accounts.capabilityLoading') }}</span>
        </template>
        <template
          v-else-if="capabilities?.platforms[platform]"
          v-for="capability in [capabilities.platforms[platform]]"
          :key="`${platform}-capability`"
        >
          <div class="capability-copy">
            <template v-if="platform === 'tiktok'">
              <span>{{ capability.message || $t('accounts.fingerprintReadyHint') }}</span>
              <code v-if="capability.code">{{ capability.code }}</code>
            </template>
            <template v-else>
              <span>
                {{ $t('accounts.douyinConcurrency', { n: capability.maxConcurrency }) }}
              </span>
              <code v-if="capability.code">{{ capability.code }}</code>
              <span v-if="capability.message">{{ capability.message }}</span>
            </template>
          </div>
        </template>
        <div v-else class="capability-copy error">
          <span>{{ capabilitiesError || $t('accounts.capabilityError') }}</span>
        </div>
      </div>
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

    <ErrorBanner v-if="errorMessage" :message="errorMessage" @retry="loadAccounts" @dismiss="errorMessage = ''" />
    <div v-if="loading" class="card account-state">正在加载账号数据…</div>
    <EmptyState
      v-else-if="filteredAccounts.length === 0"
      icon="account"
      :title="accounts.length === 0 ? '暂无账号' : '没有符合筛选条件的账号'"
      :description="accounts.length === 0 ? '请使用右上角交互式浏览器登录添加真实平台账号。' : '请切换筛选条件后重试。'"
    >
      <button v-if="accounts.length === 0" class="btn brand" @click="openLogin('tiktok')">
        {{ $t('accounts.interactiveLoginButton') }}
      </button>
    </EmptyState>
    <div v-else class="acct-grid">
      <div v-for="a in filteredAccounts" :key="a.id" class="card acct-card">
        <div class="acct-head">
          <div :class="['av', a.platform === 'douyin' ? 'dy' : 'tt']">
            <img
              v-if="a.avatarUrl && !a.avatarFailed"
              class="account-avatar"
              :src="a.avatarUrl"
              :alt="$t('accounts.avatarAlt', { name: accountTitle(a) })"
              loading="lazy"
              referrerpolicy="no-referrer"
              @error="a.avatarFailed = true"
            >
            <span v-else>{{ accountInitials(a) }}</span>
          </div>
          <div style="flex:1;min-width:0">
            <div class="nm">{{ accountTitle(a) }} <span :class="['status-pill', a.statusKey]" style="margin-left:6px"><span class="dot"></span> {{ $t('accounts.' + a.statusKey) }}</span></div>
            <div class="handle">{{ a.nickname }} · {{ $t('accounts.aliasShort') }} {{ a.username }}</div>
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
        <div v-if="a.platform === 'tiktok'" :class="['account-provider', providerConfigured(a) ? 'ready' : 'blocked']">
          <div class="account-provider-head">
            <strong>{{ $t('accounts.fingerprintProfile') }}</strong>
            <span>{{ providerConfigured(a) ? $t('accounts.configured') : $t('accounts.notConfigured') }}</span>
          </div>
          <template v-if="providerConfigured(a)">
            <span>{{ $t('accounts.providerLabel') }} <code>{{ a.browserProvider }}</code></span>
            <span>{{ $t('accounts.profileLabel') }} <code>{{ a.browserProfileId }}</code></span>
          </template>
          <template v-else>
            <span>{{ $t('accounts.fingerprintMissing') }}</span>
            <code>fingerprint_provider_unavailable</code>
          </template>
        </div>
        <div v-else class="account-provider ready">
          <div class="account-provider-head">
            <strong>{{ $t('accounts.playwrightIsolation') }}</strong>
            <span>{{ $t('accounts.providerReady') }}</span>
          </div>
          <span>
            {{ $t('accounts.douyinConcurrency', { n: capabilities?.platforms.douyin.maxConcurrency ?? 1 }) }}
          </span>
        </div>
        <div class="acct-foot">
          <div class="left">
            <button
              v-if="a.statusKey === 'off'"
              class="btn primary"
              @click="openLogin(a.platform === 'douyin' ? 'douyin' : 'tiktok', a)"
            >
              {{ $t('accounts.interactiveLoginButton') }}
            </button>
            <button class="btn" v-else @click="logout(a)">{{ $t('accounts.logout') }}</button>
            <button class="btn sm ghost" @click="checkCookie(a)">{{ $t('accounts.check') }}</button>
            <button class="btn sm ghost" :data-testid="`edit-account-${a.id}`" @click="openAccountEditor(a)">{{ $t('accounts.editRemark') }}</button>
            <button class="btn sm ghost" style="color:var(--err)" @click="deleteAccount(a)">{{ $t('common.delete') }}</button>
          </div>
          <span style="font-size:11.5px;color:var(--muted)">{{ (a.today?.comments || 0) + (a.today?.dms || 0) }} / {{ (a.today?.comments || 0) + (a.today?.dms || 0) + 25 }}</span>
        </div>
      </div>
    </div>

    <div v-if="accounts.length > 0" class="card mt-16">
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
            <td><div class="u-chip"><div class="u-avatar" :style="{ background: avBg(a) }"><img v-if="a.avatarUrl && !a.avatarFailed" class="account-avatar" :src="a.avatarUrl" :alt="$t('accounts.avatarAlt', { name: accountTitle(a) })" loading="lazy" referrerpolicy="no-referrer" @error="a.avatarFailed = true"><span v-else>{{ accountInitials(a) }}</span></div><span class="uname" :style="{ color: a.statusKey === 'off' ? 'var(--muted)' : '' }">{{ accountTitle(a) }}</span></div></td>
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

  <div
    v-if="accountEditorOpen"
    class="account-editor-overlay"
    @click.self="closeAccountEditor"
  >
    <section class="card account-editor" role="dialog" aria-modal="true" :aria-label="$t('accounts.editRemark')">
      <div class="account-editor-head">
        <div>
          <span class="eyebrow">{{ $t('accounts.localRemark') }}</span>
          <h3>{{ editingAccount ? accountTitle(editingAccount) : '' }}</h3>
        </div>
        <button class="btn sm ghost" @click="closeAccountEditor">{{ $t('common.cancel') }}</button>
      </div>
      <label class="account-editor-field">
        <span>{{ $t('accounts.displayName') }}</span>
        <input
          v-model="accountDisplayName"
          data-testid="account-display-name-input"
          class="input"
          maxlength="100"
          :placeholder="$t('accounts.displayNamePlaceholder')"
          @keydown.enter.prevent="saveAccountDisplayName"
          @keydown.esc="closeAccountEditor"
        >
        <small>{{ $t('accounts.displayNameHint', { alias: editingAccount?.username || '' }) }}</small>
      </label>
      <div class="account-editor-actions">
        <button class="btn" :disabled="savingAccountName" @click="closeAccountEditor">{{ $t('common.cancel') }}</button>
        <button data-testid="save-account-display-name" class="btn brand" :disabled="savingAccountName" @click="saveAccountDisplayName">
          {{ savingAccountName ? $t('accounts.savingRemark') : $t('accounts.saveRemark') }}
        </button>
      </div>
    </section>
  </div>

  <InteractiveLoginModal
    v-if="loginOpen"
    :platform="loginPlatform"
    :account-alias="loginAccountAlias"
    :account-id="loginAccountId"
    @close="loginOpen = false"
    @success="onLoginSuccess"
  />
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getAccounts,
  getPipelineCapabilities,
  deleteAccount as apiDeleteAccount,
  checkAccountSession,
  updateAccountMetadata,
  updateAccountCookies,
} from '../api'
import type { PipelineCapabilities } from '../types/pipeline'
import InteractiveLoginModal from '../components/InteractiveLoginModal.vue'
import EmptyState from '../components/EmptyState.vue'
import ErrorBanner from '../components/ErrorBanner.vue'

const { t } = useI18n()

interface Account {
  id: number; platform: string; username: string; login_method: string;
  status: string; last_login_at: string | null; nickname: string;
  followers: number; videos: number; likes: number;
  today: { comments: number; dms: number; replies: number; currentTask: string } | null
  statusKey: 'on' | 'off' | 'warn'
  browserProvider: string
  browserProfileId: string
  displayName: string
  avatarUrl: string
  avatarFailed: boolean
}
const accounts = ref<Account[]>([])
const loading = ref(true)
const errorMessage = ref('')
const capabilities = ref<PipelineCapabilities | null>(null)
const capabilitiesLoading = ref(true)
const capabilitiesError = ref('')
const tabFilter = ref<string>('all')
const sortBy = ref<string>('recent')
const accountEditorOpen = ref(false)
const editingAccount = ref<Account | null>(null)
const accountDisplayName = ref('')
const savingAccountName = ref(false)

function accountTitle(account: Account) {
  return account.displayName || account.nickname || account.username
}

function accountInitials(account: Account) {
  return accountTitle(account).trim().slice(0, 2).toUpperCase() || 'AC'
}

function openAccountEditor(account: Account) {
  editingAccount.value = account
  accountDisplayName.value = account.displayName
  accountEditorOpen.value = true
}

function closeAccountEditor() {
  if (savingAccountName.value) return
  accountEditorOpen.value = false
  editingAccount.value = null
  accountDisplayName.value = ''
}

async function saveAccountDisplayName() {
  const account = editingAccount.value
  if (!account) return
  savingAccountName.value = true
  try {
    await updateAccountMetadata(account.id, accountDisplayName.value)
    await loadAccounts()
    ElMessage.success(t('accounts.remarkSaved'))
    accountEditorOpen.value = false
    editingAccount.value = null
    accountDisplayName.value = ''
  } catch (error: unknown) {
    const detail = (error as { response?: { data?: { detail?: string } }; message?: string })
    ElMessage.error(detail.response?.data?.detail || detail.message || t('accounts.remarkSaveFailed'))
  } finally {
    savingAccountName.value = false
  }
}

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

// Interactive browser login modal state
const loginOpen = ref(false)
const loginPlatform = ref<'tiktok' | 'douyin'>('douyin')
const loginAccountAlias = ref<string | undefined>()
const loginAccountId = ref<number | null>(null)
function openLogin(platform: 'tiktok' | 'douyin', account?: Account) {
  loginPlatform.value = platform
  loginAccountAlias.value = account?.username
  loginAccountId.value = account?.id ?? null
  loginOpen.value = true
}
async function onLoginSuccess(alias: string, platform: 'tiktok' | 'douyin') {
  loginOpen.value = false
  ElMessage.success(t('accounts.interactiveLoginSuccess', {
    platform: platform === 'douyin' ? t('accounts.douyin') : 'TikTok',
    alias,
  }))
  await Promise.all([loadAccounts(), loadCapabilities()])
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
  if (accounts.value.length === 0) {
    ElMessage.warning('暂无可检测账号')
    return
  }
  ElMessage.info('正在批量检测账号 Cookie 状态…')
  let ok = 0, exp = 0, unsupported = 0
  for (const a of accounts.value) {
    try {
      const { data } = await checkAccountSession(a.id)
      if (data?.supported === false) unsupported++
      else if (data?.valid) ok++
      else exp++
    } catch { exp++ }
  }
  // 重新从后端加载，确保状态持久化
  await Promise.all([loadAccounts(), loadCapabilities()])
  ElMessage.success(t('accounts.batchCheckResult', { ok, expired: exp, unsupported }))
}

async function checkCookie(a: Account) {
  ElMessage.info(`正在检测 @${a.username}…`)
  try {
    const { data } = await checkAccountSession(a.id)
    if (data?.supported === false) {
      ElMessage.warning(t('accounts.sessionCheckUnsupported'))
      return
    }
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
  loading.value = true
  errorMessage.value = ''
  try {
    const { data } = await getAccounts()
    const raw = Array.isArray(data) ? data : []
    accounts.value = raw.map((account: any) => ({
      ...account,
      browserProvider: String(account.browserProvider ?? account.browser_provider ?? ''),
      browserProfileId: String(account.browserProfileId ?? account.browser_profile_id ?? ''),
      displayName: String(account.displayName ?? account.display_name ?? ''),
      avatarUrl: String(
        account.avatarDataUrl
        ?? account.avatar_data_url
        ?? account.avatarUrl
        ?? account.avatar_url
        ?? ''
      ),
      avatarFailed: false,
    })) as Account[]
  } catch (error: unknown) {
    const err = error as { response?: { data?: { detail?: string } }; message?: string }
    accounts.value = []
    errorMessage.value = err.response?.data?.detail || err.message || '账号数据加载失败，请检查后端连接。'
  } finally {
    loading.value = false
  }
}

async function loadCapabilities() {
  capabilitiesLoading.value = true
  capabilitiesError.value = ''
  try {
    const { data } = await getPipelineCapabilities()
    capabilities.value = data
  } catch (error: unknown) {
    const detail = (error as {
      response?: { data?: { detail?: string | { code?: string; message?: string } } }
    })?.response?.data?.detail
    const code = typeof detail === 'object' ? detail?.code : ''
    const message = typeof detail === 'object'
      ? detail?.message
      : detail || (error as Error)?.message
    capabilities.value = null
    capabilitiesError.value = [code, message].filter(Boolean).join(': ')
  } finally {
    capabilitiesLoading.value = false
  }
}

function providerConfigured(account: Account) {
  return Boolean(account.browserProvider.trim() && account.browserProfileId.trim())
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

onMounted(() => {
  void loadAccounts()
  void loadCapabilities()
})
</script>

<style scoped>
.account-state { min-height: 160px; display: grid; place-items: center; color: var(--muted); font-size: 13px; }
.capability-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 18px; }
.capability-card { padding: 15px 17px; }
.capability-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
.capability-head > div { display: grid; gap: 3px; }
.capability-head .eyebrow { font-size: 10px; color: var(--muted); font-weight: 700; letter-spacing: .06em; text-transform: uppercase; }
.capability-head strong { font-size: 13px; }
.provider-state { display: inline-flex; align-items: center; min-height: 24px; padding: 2px 8px; border-radius: 5px; font-size: 10.5px; font-weight: 600; }
.provider-state.ready { background: var(--ok-soft); color: var(--ok); }
.provider-state.blocked { background: var(--err-soft); color: var(--err); }
.capability-copy { display: flex; flex-wrap: wrap; gap: 6px 10px; margin-top: 10px; color: var(--muted); font-size: 11.5px; line-height: 1.5; }
.capability-copy code { color: var(--fg-2); font-family: var(--font-mono); font-size: 10.5px; }
.capability-copy.error { color: var(--err); }
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
.account-avatar { width: 100%; height: 100%; display: block; border-radius: inherit; object-fit: cover; }
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
.account-provider { display: grid; gap: 5px; min-height: 78px; margin-bottom: 14px; padding: 10px 11px; border: 1px solid var(--border); border-radius: 8px; font-size: 11px; color: var(--muted); }
.account-provider.ready { background: var(--ok-soft); border-color: color-mix(in oklch, var(--ok) 20%, var(--border)); }
.account-provider.blocked { background: var(--err-soft); border-color: color-mix(in oklch, var(--err) 20%, var(--border)); }
.account-provider-head { display: flex; justify-content: space-between; gap: 8px; color: var(--fg-2); }
.account-provider-head strong { font-size: 11.5px; }
.account-provider-head span { font-weight: 600; }
.account-provider.ready .account-provider-head span { color: var(--ok); }
.account-provider.blocked .account-provider-head span { color: var(--err); }
.account-provider code { font-family: var(--font-mono); font-size: 10.5px; color: var(--fg-2); overflow-wrap: anywhere; }

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

.account-editor-overlay { position: fixed; inset: 0; z-index: 1200; display: grid; place-items: center; padding: 24px; background: color-mix(in oklab, black 42%, transparent); backdrop-filter: blur(3px); }
.account-editor { width: min(480px, 100%); padding: 22px; box-shadow: 0 24px 80px color-mix(in oklab, black 28%, transparent); }
.account-editor-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 18px; }
.account-editor-head .eyebrow { color: var(--brand); font-size: 10px; font-weight: 750; letter-spacing: .08em; text-transform: uppercase; }
.account-editor-head h3 { margin: 4px 0 0; font-size: 17px; }
.account-editor-field { display: grid; gap: 7px; font-size: 12px; font-weight: 600; }
.account-editor-field small { color: var(--muted); font-weight: 400; line-height: 1.5; }
.account-editor-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 22px; padding-top: 16px; border-top: 1px solid var(--border); }

@media (max-width: 980px) {
  .acct-grid { grid-template-columns: repeat(2, 1fr); }
  .acct-summary { grid-template-columns: repeat(2, 1fr); }
}

@media (max-width: 680px) {
  .page-head,
  .risk-card,
  .toolbar { align-items: flex-start; flex-direction: column; }
  .page-head > div:last-child,
  .toolbar .tabs-inline { flex-wrap: wrap; }
  .capability-grid,
  .acct-grid,
  .acct-summary { grid-template-columns: 1fr; }
  .acct-foot { align-items: flex-start; gap: 10px; flex-direction: column; }
  .acct-foot .left { flex-wrap: wrap; }
  .account-editor-overlay { place-items: end center; padding: 10px; }
  .account-editor { padding: 18px 14px; border-radius: 14px 14px 8px 8px; }
}
</style>
