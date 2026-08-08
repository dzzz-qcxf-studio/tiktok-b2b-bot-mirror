<template>
  <div class="interactive-login-overlay" @click.self="requestClose">
    <section
      ref="dialogElement"
      class="interactive-login-modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="interactive-login-title"
      aria-describedby="interactive-login-description"
      tabindex="-1"
    >
      <header class="interactive-login-header">
        <div>
          <p class="interactive-login-eyebrow">{{ $t('accounts.interactiveLogin') }}</p>
          <h2 id="interactive-login-title">
            {{ $t('accounts.interactiveLoginTitle', { platform: platformLabel }) }}
          </h2>
        </div>
        <button
          class="interactive-login-close"
          type="button"
          data-test="close-login"
          :aria-label="$t('common.close')"
          @click="requestClose"
        >
          <span aria-hidden="true">×</span>
        </button>
      </header>

      <main class="interactive-login-body">
        <div class="interactive-login-tabs" role="group" :aria-label="$t('accounts.loginPlatform')">
          <button
            v-for="candidate in platforms"
            :key="candidate"
            type="button"
            :class="{ active: currentPlatform === candidate }"
            :aria-pressed="currentPlatform === candidate"
            :disabled="busy || (props.accountId != null && currentPlatform !== candidate)"
            :data-test="`login-platform-${candidate}`"
            @click="switchPlatform(candidate)"
          >
            {{ candidate === 'tiktok' ? 'TikTok' : $t('accounts.douyin') }}
          </button>
        </div>

        <label class="interactive-login-field" for="interactive-login-alias">
          <span>{{ $t('accounts.accountAlias') }}</span>
          <input
            id="interactive-login-alias"
            v-model.trim="alias"
            autocomplete="off"
            :disabled="transitioning || verifying || closing || props.accountId != null"
            :aria-invalid="Boolean(aliasError)"
            :aria-describedby="aliasError ? 'interactive-login-alias-error' : undefined"
            @keydown.enter.prevent="restartSession"
          />
          <small
            v-if="aliasError"
            id="interactive-login-alias-error"
            class="interactive-login-error"
          >
            {{ aliasError }}
          </small>
          <small v-else>{{ $t('accounts.accountAliasHint') }}</small>
        </label>

        <div class="browser-notice">
          <span class="browser-notice-mark" aria-hidden="true"></span>
          <div>
            <strong id="interactive-login-description">
              {{ $t('accounts.browserLoginInstruction') }}
            </strong>
            <p>{{ $t('accounts.browserLoginSecurity') }}</p>
          </div>
        </div>

        <ol class="interactive-login-steps">
          <li v-for="step in 3" :key="step">
            <span class="step-number" aria-hidden="true">{{ step }}</span>
            <span>{{ $t(`accounts.interactiveStep${step}`) }}</span>
          </li>
        </ol>

        <div
          class="interactive-login-status"
          :class="`status-${statusTone}`"
          role="status"
          aria-live="polite"
          aria-atomic="true"
          data-test="login-status"
        >
          <span class="status-dot" aria-hidden="true"></span>
          <div>
            <strong>{{ statusTitle }}</strong>
            <p v-if="statusDetail">{{ statusDetail }}</p>
          </div>
        </div>
      </main>

      <footer class="interactive-login-actions">
        <button
          class="btn"
          type="button"
          data-test="cancel-login"
          :disabled="closing"
          @click="requestClose"
        >
          {{ $t('common.cancel') }}
        </button>
        <button
          v-if="canRestart"
          class="btn"
          type="button"
          data-test="restart-login"
          :disabled="busy || !normalizedAlias"
          @click="restartSession"
        >
          {{ $t('accounts.reopenBrowser') }}
        </button>
        <button
          class="btn brand"
          type="button"
          data-test="verify-login"
          :disabled="!canVerify"
          @click="verifyAndSave"
        >
          {{ verifying
            ? $t('accounts.verifyingLogin')
            : $t('accounts.verifyAndSaveLogin') }}
        </button>
      </footer>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  cancelLoginSession,
  createLoginSession,
  getLoginSession,
  verifyLoginSession,
} from '../api'
import type { LoginPlatform, LoginSessionResponse } from '../api'
import {
  canApplyLoginSnapshot,
  isTerminalLoginStatus,
} from './interactiveLoginState'

const props = defineProps<{
  platform: LoginPlatform
  accountAlias?: string
  accountId?: number | null
}>()

const emit = defineEmits<{
  (event: 'close'): void
  (event: 'success', accountAlias: string, platform: LoginPlatform): void
}>()

const { t } = useI18n()
const platforms: LoginPlatform[] = ['tiktok', 'douyin']
const currentPlatform = ref<LoginPlatform>(props.platform)
const alias = ref(
  props.accountAlias === undefined
    ? defaultAlias(props.platform)
    : normalizeAlias(props.accountAlias),
)
const currentSession = ref<LoginSessionResponse | null>(null)
const requestError = ref('')
const aliasError = ref('')
const creating = ref(false)
const verifying = ref(false)
const transitioning = ref(false)
const closing = ref(false)
const disposed = ref(false)
const dialogElement = ref<HTMLElement | null>(null)
const cancelledTokens = new Set<string>()
const successfulTokens = new Set<string>()
let generation = 0
let pollTimer: number | null = null
let pollEpoch = 0
let previouslyFocused: HTMLElement | null = null

const normalizedAlias = computed(() => normalizeAlias(alias.value))
const busy = computed(() =>
  creating.value || verifying.value || transitioning.value || closing.value,
)
const sessionActive = computed(() => {
  const status = currentSession.value?.status
  return Boolean(status && !['confirmed', 'failed', 'expired', 'cancelled'].includes(status))
})
const sessionMatchesAlias = computed(() =>
  normalizeAlias(currentSession.value?.accountAlias || '') === normalizedAlias.value,
)
const canVerify = computed(() =>
  Boolean(
    currentSession.value?.token
      && ['waiting_user', 'persisted'].includes(currentSession.value.status)
      && sessionMatchesAlias.value
      && !busy.value,
  ),
)
const canRestart = computed(() =>
  !currentSession.value
    || !sessionMatchesAlias.value
    || ['failed', 'expired', 'cancelled'].includes(currentSession.value.status),
)
const platformLabel = computed(() =>
  currentPlatform.value === 'tiktok' ? 'TikTok' : t('accounts.douyin'),
)
const statusTone = computed(() => {
  if (requestError.value || ['failed', 'expired'].includes(currentSession.value?.status || '')) {
    return 'error'
  }
  if (currentSession.value?.status === 'confirmed') return 'success'
  if (creating.value || verifying.value || sessionActive.value) return 'working'
  return 'neutral'
})
const statusTitle = computed(() => {
  if (requestError.value) return t('accounts.loginNeedsAttention')
  if (creating.value) return t('accounts.openingBrowser')
  if (verifying.value) return t('accounts.verifyingLogin')
  switch (currentSession.value?.status) {
    case 'launching':
      return t('accounts.openingBrowser')
    case 'waiting_user':
      return t('accounts.waitingForManualLogin')
    case 'verifying':
    case 'persisted':
      return t('accounts.verifyingLogin')
    case 'confirmed':
      return t('accounts.loginSaved')
    case 'failed':
      return t('accounts.loginFailed')
    case 'expired':
      return t('accounts.loginExpired')
    case 'cancelled':
      return t('accounts.loginCancelled')
    default:
      return t('accounts.preparingLogin')
  }
})
const statusDetail = computed(() =>
  requestError.value
    || currentSession.value?.errorMessage
    || (!sessionMatchesAlias.value && currentSession.value
      ? t('accounts.aliasChangedReopen')
      : '')
    || (sessionActive.value ? t('accounts.waitingForManualLoginDetail') : ''),
)

function defaultAlias(platform: LoginPlatform) {
  const now = new Date()
  const stamp = [
    now.getFullYear(),
    String(now.getMonth() + 1).padStart(2, '0'),
    String(now.getDate()).padStart(2, '0'),
    String(now.getHours()).padStart(2, '0'),
    String(now.getMinutes()).padStart(2, '0'),
    String(now.getSeconds()).padStart(2, '0'),
  ].join('')
  return `${platform}_${stamp}`
}

function normalizeAlias(value: string) {
  return value.normalize('NFKC').trim()
}

function extractError(error: unknown) {
  const detail = (error as {
    response?: {
      data?: {
        detail?: string | { code?: string; message?: string }
      }
    }
    message?: string
  })?.response?.data?.detail
  if (typeof detail === 'object' && detail?.message) return detail.message
  if (typeof detail === 'string') return detail
  return (error as Error)?.message || t('accounts.loginRequestFailed')
}

function clearPoll() {
  if (pollTimer !== null) {
    window.clearTimeout(pollTimer)
    pollTimer = null
  }
  pollEpoch += 1
}

function normalizeSession(
  data: LoginSessionResponse,
  fallbackAlias = normalizedAlias.value,
) {
  return {
    ...data,
    accountAlias: normalizeAlias(data.accountAlias) || fallbackAlias,
  }
}

function emitSuccessOnce(data: LoginSessionResponse) {
  if (data.status !== 'confirmed' || successfulTokens.has(data.token)) return
  successfulTokens.add(data.token)
  emit('success', normalizeAlias(data.accountAlias) || normalizedAlias.value, data.platform)
}

function applySession(data: LoginSessionResponse, fallbackAlias?: string) {
  const normalized = normalizeSession(data, fallbackAlias)
  const current = currentSession.value
  if (!canApplyLoginSnapshot(current, normalized)) return false
  currentSession.value = normalized
  if (isTerminalLoginStatus(normalized.status)) {
    clearPoll()
    if (normalized.status === 'confirmed') emitSuccessOnce(normalized)
  }
  return true
}

function schedulePoll(runGeneration: number) {
  clearPoll()
  const token = currentSession.value?.token
  if (!token || isTerminalLoginStatus(currentSession.value?.status || 'cancelled')) return
  const runPollEpoch = pollEpoch
  scheduleNextPoll(runGeneration, token, runPollEpoch)
}

function pollCanContinue(runGeneration: number, token: string, runPollEpoch: number) {
  const session = currentSession.value
  return !disposed.value
    && !closing.value
    && runGeneration === generation
    && runPollEpoch === pollEpoch
    && session?.token === token
    && !isTerminalLoginStatus(session.status)
}

function scheduleNextPoll(runGeneration: number, token: string, runPollEpoch: number) {
  if (!pollCanContinue(runGeneration, token, runPollEpoch)) return
  pollTimer = window.setTimeout(() => {
    pollTimer = null
    void pollOnce(runGeneration, token, runPollEpoch)
  }, 2500)
}

async function pollOnce(runGeneration: number, token: string, runPollEpoch: number) {
  try {
    if (!pollCanContinue(runGeneration, token, runPollEpoch) || busy.value) return
    const { data } = await getLoginSession(token)
    if (!pollCanContinue(runGeneration, token, runPollEpoch)) return
    applySession(data)
  } catch {
    // Polling is informational. The explicit verify action surfaces request errors.
  } finally {
    if (pollCanContinue(runGeneration, token, runPollEpoch)) {
      scheduleNextPoll(runGeneration, token, runPollEpoch)
    }
  }
}

async function cancelToken(token: string) {
  if (!token || cancelledTokens.has(token)) return
  cancelledTokens.add(token)
  try {
    await cancelLoginSession(token)
  } catch {
    // Cancellation remains idempotent from the UI perspective. Server cleanup is retried on expiry.
  }
}

async function cancelCurrentSession() {
  const session = currentSession.value
  clearPoll()
  if (session?.token && session.status !== 'confirmed') {
    await cancelToken(session.token)
  }
  currentSession.value = null
}

async function startSession() {
  const requestedAlias = normalizedAlias.value
  if (!requestedAlias) {
    aliasError.value = t('accounts.accountAliasRequired')
    return
  }

  aliasError.value = ''
  requestError.value = ''
  const runGeneration = ++generation
  creating.value = true
  try {
    const { data } = await createLoginSession({
      platform: currentPlatform.value,
      accountAlias: requestedAlias,
      ...(props.accountId != null ? { accountId: props.accountId } : {}),
    })
    if (disposed.value || runGeneration !== generation) {
      if (data?.token && data.status !== 'confirmed') await cancelToken(data.token)
      return
    }
    const normalized = normalizeSession(data, requestedAlias)
    if (normalizedAlias.value === requestedAlias) alias.value = normalized.accountAlias
    currentSession.value = null
    applySession(normalized, requestedAlias)
    if (!isTerminalLoginStatus(normalized.status)) schedulePoll(runGeneration)
  } catch (error) {
    if (!disposed.value && runGeneration === generation) requestError.value = extractError(error)
  } finally {
    if (runGeneration === generation) creating.value = false
  }
}

async function restartSession() {
  if (busy.value) return
  transitioning.value = true
  const oldSession = currentSession.value
  const transitionGeneration = ++generation
  clearPoll()
  currentSession.value = null
  try {
    if (oldSession?.token && oldSession.status !== 'confirmed') await cancelToken(oldSession.token)
    if (disposed.value || closing.value || transitionGeneration !== generation) return
    await startSession()
  } finally {
    transitioning.value = false
  }
}

async function switchPlatform(platform: LoginPlatform) {
  if (platform === currentPlatform.value || busy.value) return
  transitioning.value = true
  const oldSession = currentSession.value
  const transitionGeneration = ++generation
  clearPoll()
  currentSession.value = null
  try {
    if (oldSession?.token && oldSession.status !== 'confirmed') await cancelToken(oldSession.token)
    if (disposed.value || closing.value || transitionGeneration !== generation) return
    currentPlatform.value = platform
    if (!props.accountAlias) alias.value = defaultAlias(platform)
    await startSession()
  } finally {
    transitioning.value = false
  }
}

async function verifyAndSave() {
  const token = currentSession.value?.token
  if (!token || !canVerify.value) return
  clearPoll()
  const runGeneration = ++generation
  requestError.value = ''
  verifying.value = true
  try {
    const { data } = await verifyLoginSession(token)
    if (disposed.value || runGeneration !== generation || token !== currentSession.value?.token) return
    applySession(data)
    if (data.status !== 'confirmed') {
      schedulePoll(runGeneration)
    }
  } catch (error) {
    if (!disposed.value && runGeneration === generation) requestError.value = extractError(error)
  } finally {
    if (runGeneration === generation) verifying.value = false
  }
}

async function requestClose() {
  if (closing.value) return
  closing.value = true
  ++generation
  await cancelCurrentSession()
  emit('close')
}

function handleGlobalKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    event.preventDefault()
    void requestClose()
  } else if (event.key === 'Tab') {
    trapDialogFocus(event)
  }
}

function trapDialogFocus(event: KeyboardEvent) {
  if (event.key !== 'Tab' || !dialogElement.value) return
  const focusable = [...dialogElement.value.querySelectorAll<HTMLElement>(
    'button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])',
  )]
  if (focusable.length === 0) return
  const first = focusable[0]
  const last = focusable[focusable.length - 1]
  if (!first || !last) return
  const active = document.activeElement as HTMLElement | null
  if (!active || !focusable.includes(active)) {
    event.preventDefault()
    ;(event.shiftKey ? last : first).focus()
  } else if (event.shiftKey && active === first) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && active === last) {
    event.preventDefault()
    first.focus()
  }
}

function focusInitialControl() {
  const input = dialogElement.value?.querySelector<HTMLInputElement>(
    '#interactive-login-alias:not([disabled])',
  )
  const fallback = dialogElement.value?.querySelector<HTMLElement>(
    '[data-test="close-login"], button:not([disabled])',
  )
  ;(input || fallback)?.focus()
}

watch(
  () => props.accountAlias,
  value => {
    if (value === undefined) return
    alias.value = normalizeAlias(value)
    aliasError.value = ''
  },
)

onMounted(async () => {
  previouslyFocused = document.activeElement as HTMLElement | null
  window.addEventListener('keydown', handleGlobalKeydown)
  await nextTick()
  if (disposed.value) return
  const startPromise = startSession()
  await nextTick()
  focusInitialControl()
  await startPromise
})

onBeforeUnmount(() => {
  disposed.value = true
  ++generation
  clearPoll()
  window.removeEventListener('keydown', handleGlobalKeydown)
  const token = currentSession.value?.token
  if (token && currentSession.value?.status !== 'confirmed') void cancelToken(token)
  previouslyFocused?.focus()
})
</script>

<style scoped>
.interactive-login-overlay {
  position: fixed;
  inset: 0;
  z-index: 100;
  display: grid;
  place-items: center;
  padding: 20px;
  background: color-mix(in oklch, var(--fg) 48%, transparent);
  animation: login-overlay-in 150ms ease-out;
}

.interactive-login-modal {
  width: min(440px, 100%);
  max-height: calc(100vh - 40px);
  overflow-y: auto;
  color: var(--fg);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  box-shadow: var(--shadow-pop);
  animation: login-modal-in 180ms ease-out;
}

.interactive-login-modal:focus {
  outline: none;
}

.interactive-login-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: 20px 20px 16px;
  border-bottom: 1px solid var(--border);
}

.interactive-login-header h2 {
  margin: 2px 0 0;
  font-size: var(--t-h2);
  font-weight: 650;
}

.interactive-login-eyebrow {
  margin: 0;
  color: var(--muted);
  font-size: var(--t-xs);
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.interactive-login-close {
  display: grid;
  flex: 0 0 44px;
  width: 44px;
  height: 44px;
  margin: -10px -10px 0 0;
  place-items: center;
  color: var(--muted);
  font-size: 24px;
  line-height: 1;
  background: transparent;
  border: 0;
  border-radius: 8px;
}

.interactive-login-close:hover,
.interactive-login-close:focus-visible {
  color: var(--fg);
  background: var(--bg-sub);
}

.interactive-login-body {
  display: grid;
  gap: 16px;
  padding: 18px 20px;
}

.interactive-login-tabs {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 4px;
  padding: 3px;
  background: var(--bg-sub);
  border-radius: 9px;
}

.interactive-login-tabs button {
  min-height: 44px;
  padding: 8px 12px;
  color: var(--muted);
  font-weight: 600;
  background: transparent;
  border: 0;
  border-radius: 7px;
}

.interactive-login-tabs button.active {
  color: var(--fg);
  background: var(--surface);
  box-shadow: var(--shadow-1);
}

.interactive-login-tabs button:focus-visible,
.interactive-login-actions button:focus-visible,
.interactive-login-close:focus-visible,
.interactive-login-field input:focus-visible {
  outline: 2px solid var(--brand);
  outline-offset: 2px;
}

.interactive-login-field {
  display: grid;
  gap: 6px;
  color: var(--fg-2);
  font-size: var(--t-sm);
  font-weight: 600;
}

.interactive-login-field input {
  width: 100%;
  min-height: 44px;
  padding: 9px 11px;
  color: var(--fg);
  background: var(--surface);
  border: 1px solid var(--border-strong);
  border-radius: 8px;
}

.interactive-login-field input:disabled {
  color: var(--muted);
  background: var(--bg-sub);
}

.interactive-login-field small {
  color: var(--muted);
  font-size: var(--t-xs);
  font-weight: 400;
}

.interactive-login-field .interactive-login-error {
  color: var(--err);
}

.browser-notice {
  display: flex;
  gap: 12px;
  padding: 14px;
  background: var(--cyan-soft);
  border: 1px solid color-mix(in oklch, var(--cyan) 35%, var(--border));
  border-radius: 9px;
}

.browser-notice-mark {
  flex: 0 0 10px;
  width: 10px;
  height: 10px;
  margin-top: 5px;
  background: var(--cyan);
  border-radius: 50%;
  box-shadow: 0 0 0 4px color-mix(in oklch, var(--cyan) 22%, transparent);
}

.browser-notice strong {
  display: block;
  font-size: var(--t-sm);
  font-weight: 650;
}

.browser-notice p,
.interactive-login-status p {
  margin: 3px 0 0;
  color: var(--fg-2);
  font-size: var(--t-xs);
}

.interactive-login-steps {
  display: grid;
  gap: 10px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.interactive-login-steps li {
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--fg-2);
  font-size: var(--t-sm);
}

.step-number {
  display: grid;
  flex: 0 0 24px;
  width: 24px;
  height: 24px;
  place-items: center;
  color: var(--muted);
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 700;
  background: var(--bg-sub);
  border: 1px solid var(--border);
  border-radius: 50%;
}

.interactive-login-status {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  min-height: 58px;
  padding: 12px 14px;
  background: var(--bg-sub);
  border: 1px solid var(--border);
  border-radius: 9px;
}

.interactive-login-status strong {
  font-size: var(--t-sm);
  font-weight: 650;
}

.status-dot {
  flex: 0 0 9px;
  width: 9px;
  height: 9px;
  margin-top: 5px;
  background: var(--muted);
  border-radius: 50%;
}

.status-working .status-dot {
  background: var(--info);
  box-shadow: 0 0 0 4px var(--info-soft);
  animation: status-pulse 1.5s ease-in-out infinite;
}

.status-success {
  background: var(--ok-soft);
  border-color: color-mix(in oklch, var(--ok) 35%, var(--border));
}

.status-success .status-dot {
  background: var(--ok);
}

.status-error {
  background: var(--err-soft);
  border-color: color-mix(in oklch, var(--err) 35%, var(--border));
}

.status-error .status-dot {
  background: var(--err);
}

.interactive-login-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 14px 20px 20px;
  border-top: 1px solid var(--border);
}

.interactive-login-actions .btn {
  min-height: 44px;
}

@keyframes login-overlay-in {
  from { opacity: 0; }
}

@keyframes login-modal-in {
  from { opacity: 0; transform: translateY(8px) scale(0.985); }
}

@keyframes status-pulse {
  50% { opacity: 0.45; }
}

@media (max-width: 520px) {
  .interactive-login-overlay {
    align-items: end;
    padding: 0;
  }

  .interactive-login-modal {
    max-height: calc(100vh - 16px);
    border-radius: 14px 14px 0 0;
  }

  .interactive-login-actions {
    display: grid;
    grid-template-columns: 1fr 1fr;
  }

  .interactive-login-actions .brand {
    grid-column: 1 / -1;
    grid-row: 1;
  }
}

@media (prefers-reduced-motion: reduce) {
  .interactive-login-overlay,
  .interactive-login-modal,
  .status-working .status-dot {
    animation: none;
  }
}
</style>
