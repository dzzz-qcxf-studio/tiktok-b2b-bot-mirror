<template>
  <div class="qr-overlay" @click.self="close">
    <div class="qr-modal">
      <div class="qr-hd">
        <div class="qr-platform-tabs">
          <button :class="{active: currentPlatform === 'tiktok'}" @click="switchPlatform('tiktok')">TikTok</button>
          <button :class="{active: currentPlatform === 'douyin'}" @click="switchPlatform('douyin')">抖音</button>
        </div>
        <button class="qr-close" @click="close">×</button>
      </div>

      <div class="qr-body">
        <!-- QR code: real image from backend (real mode) or SVG placeholder (mock) -->
        <div class="qr-wrap">
          <div v-if="qrcodeUrl" class="qr-real">
            <img :src="qrcodeUrl" alt="QR Code" width="180" height="180" />
          </div>
          <div v-else class="qr-fake">
            <svg viewBox="0 0 100 100" width="180" height="180">
              <rect width="100" height="100" fill="#fff"/>
              <!-- Three position markers (corners) -->
              <g fill="#0f172a">
                <rect x="4"  y="4"  width="22" height="22" rx="2"/>
                <rect x="74" y="4"  width="22" height="22" rx="2"/>
                <rect x="4"  y="74" width="22" height="22" rx="2"/>
                <rect x="8"  y="8"  width="14" height="14" fill="#fff"/>
                <rect x="78" y="8"  width="14" height="14" fill="#fff"/>
                <rect x="8"  y="78" width="14" height="14" fill="#fff"/>
                <rect x="11" y="11" width="8"  height="8"/>
                <rect x="81" y="11" width="8"  height="8"/>
                <rect x="11" y="81" width="8"  height="8"/>
                <!-- Pseudo-random data cells, seeded by token -->
                <g v-for="(c, i) in qrCells" :key="i">
                  <rect v-if="c" :x="(i % 20) * 4 + 2" :y="Math.floor(i / 20) * 4 + 2" width="3" height="3"/>
                </g>
              </g>
            </svg>
            <!-- Center brand -->
            <div class="qr-brand">▣</div>
          </div>
          <div class="qr-token mono">{{ sessionToken || '—' }}</div>
        </div>

        <!-- Status indicator -->
        <div class="qr-status" :class="`st-${status}`">
          <div class="qr-status-icon">
            <span v-if="status === 'launching'" class="spin">⚙️</span>
            <span v-else-if="status === 'waiting'">⏳</span>
            <span v-else-if="status === 'scanning'">📱</span>
            <span v-else-if="status === 'confirmed'">✓</span>
            <span v-else-if="status === 'expired'">⌛</span>
          </div>
          <div class="qr-status-text">{{ statusText }}</div>
        </div>

        <!-- Steps -->
        <ol class="qr-steps">
          <li :class="{done: status !== 'waiting' || true}">
            <span class="num">1</span>
            <span class="txt">{{ $t('accounts.qrStep1') }}</span>
          </li>
          <li :class="{done: ['scanning','confirmed'].includes(status)}">
            <span class="num">2</span>
            <span class="txt">{{ $t('accounts.qrStep2') }}</span>
          </li>
          <li :class="{done: status === 'confirmed'}">
            <span class="num">3</span>
            <span class="txt">{{ $t('accounts.qrStep3') }}</span>
          </li>
        </ol>

        <div v-if="status === 'expired'" class="qr-expired">
          {{ $t('accounts.qrExpired') }}
          <button class="btn sm brand" @click="restart" style="margin-left:8px">{{ $t('accounts.qrRefresh') }}</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { startQrcodeLogin, getLoginStatus } from '../api'

const props = defineProps<{ platform: 'tiktok' | 'douyin' }>()
const emit = defineEmits<{ (e: 'close'): void; (e: 'success', username: string, platform: 'tiktok' | 'douyin'): void }>()

const { t } = useI18n()
const sessionToken = ref('')
const qrcodeUrl = ref('')                      // real QR image from backend
const status = ref<'launching' | 'waiting' | 'scanning' | 'confirmed' | 'expired'>('launching')
const confirmedUsername = ref('')
const errorMessage = ref('')                   // error message from backend
const currentPlatform = ref<'tiktok' | 'douyin'>(props.platform)
let pollTimer: number | null = null

const titleText = computed(() =>
  currentPlatform.value === 'douyin'
    ? t('accounts.qrLoginDouyin')
    : t('accounts.qrLoginTiktok')
)

const statusText = computed(() => {
  if (status.value === 'expired' && errorMessage.value) {
    return `${t('accounts.qrStatusExpired')}: ${errorMessage.value}`
  }
  switch (status.value) {
    case 'launching': return t('accounts.qrStatusLaunching') || '正在启动浏览器，请稍候...'
    case 'waiting':   return t('accounts.qrStatusWaiting')
    case 'scanning':  return t('accounts.qrStatusScanning')
    case 'confirmed': return t('accounts.qrStatusConfirmed', { user: confirmedUsername.value })
    case 'expired':   return t('accounts.qrStatusExpired')
  }
})

// Pseudo-random cells seeded by session token (so each session has unique pattern)
const qrCells = computed<boolean[]>(() => {
  if (!sessionToken.value) return new Array(400).fill(false)
  const cells: boolean[] = []
  let seed = 0
  for (let i = 0; i < sessionToken.value.length; i++) seed = (seed * 31 + sessionToken.value.charCodeAt(i)) | 0
  for (let i = 0; i < 400; i++) {
    seed = (seed * 1103515245 + 12345) | 0
    cells.push((seed >>> 16) % 5 < 2)
  }
  return cells
})

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000'

async function startSession() {
  try {
    const { data } = await startQrcodeLogin(currentPlatform.value, 'session-' + Date.now())
    sessionToken.value = data?.session_token || ''
    qrcodeUrl.value = ''    // reset; poll() will set it from backend response
    status.value = 'waiting'
    errorMessage.value = ''
    confirmedUsername.value = ''
    if (pollTimer) clearInterval(pollTimer)
    pollTimer = window.setInterval(poll, 1500)
  } catch (e: any) {
    status.value = 'expired'
    errorMessage.value = e?.response?.data?.detail || e?.message || '连接后端失败'
  }
}
async function poll() {
  if (!sessionToken.value) return
  try {
    const { data } = await getLoginStatus(sessionToken.value)
    // Backend returns: launching / waiting / scanning / confirmed / expired
    const s = data?.status || 'expired'
    status.value = ['launching', 'waiting', 'scanning', 'confirmed', 'expired'].includes(s) ? s : 'expired'
    // Capture error message from backend
    if (data?.error) errorMessage.value = data.error
    // Set real QR code image URL from backend (relative → absolute)
    if (data?.qrcode_url) qrcodeUrl.value = API_BASE + data.qrcode_url
    if (data?.username) confirmedUsername.value = data.username
    if (data?.platform === 'tiktok' || data?.platform === 'douyin') currentPlatform.value = data.platform
    if (status.value === 'confirmed') {
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
      emit('success', confirmedUsername.value, currentPlatform.value)
    } else if (status.value === 'expired') {
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
    }
  } catch {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
  }
}
function switchPlatform(p: 'tiktok' | 'douyin') {
  if (currentPlatform.value === p) return
  currentPlatform.value = p
  startSession()
}
function restart() { startSession() }
function close() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
  emit('close')
}

onMounted(() => { startSession() })
onUnmounted(() => { if (pollTimer) clearInterval(pollTimer) })
</script>

<style scoped>
.qr-overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,.45);
  display: grid; place-items: center; z-index: 100;
  animation: qrFade .15s ease;
}
@keyframes qrFade { from { opacity: 0 } to { opacity: 1 } }

.qr-modal {
  width: 360px; background: var(--surface);
  border: 1px solid var(--border); border-radius: 12px;
  box-shadow: 0 20px 50px rgba(0,0,0,.18);
  overflow: hidden;
}
.qr-hd {
  display: flex; align-items: center; justify-content: space-between;
  padding: 14px 18px; border-bottom: 1px solid var(--border);
}
.qr-hd h3 { font-size: 14px; font-weight: 600; margin: 0; }
.qr-platform-tabs { display: inline-flex; gap: 4px; background: var(--bg-sub); border-radius: 7px; padding: 2px; }
.qr-platform-tabs button {
  border: none; background: transparent; padding: 4px 12px;
  font-size: 12px; color: var(--muted); border-radius: 5px;
  cursor: pointer; font-weight: 500;
}
.qr-platform-tabs button.active {
  background: var(--surface); color: var(--fg); font-weight: 600;
  box-shadow: 0 1px 2px rgba(0,0,0,.06);
}
.qr-close {
  border: none; background: transparent; font-size: 22px; line-height: 1;
  color: var(--muted); cursor: pointer; padding: 0 4px;
}
.qr-close:hover { color: var(--fg); }

.qr-body { padding: 18px; }

.qr-wrap { display: flex; flex-direction: column; align-items: center; gap: 10px; }
.qr-fake, .qr-real {
  position: relative; padding: 10px; background: #fff;
  border: 1px solid var(--border); border-radius: 8px;
  display: grid; place-items: center;
}
.qr-real img { display: block; border-radius: 4px; object-fit: contain; }
.qr-brand {
  position: absolute; top: 50%; left: 50%; transform: translate(-50%,-50%);
  width: 32px; height: 32px; background: #fff;
  display: grid; place-items: center;
  font-size: 18px; color: var(--brand);
  border: 1px solid #fff;
}
.qr-token {
  font-size: 10.5px; color: var(--muted); letter-spacing: .3px;
  text-align: center; max-width: 100%; overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; direction: rtl; unicode-bidi: plaintext;
}

.qr-status {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 12px; border-radius: 8px; margin: 14px 0 12px;
  background: var(--bg-sub); font-size: 12.5px;
}
.qr-status-icon { font-size: 18px; }
.qr-status-text { flex: 1; }
.qr-status.st-waiting  { background: oklch(96% 0.02 240); color: oklch(35% 0.10 240); }
.qr-status.st-scanning { background: oklch(96% 0.04 75);  color: oklch(35% 0.14 75); }
.qr-status.st-confirmed { background: oklch(95% 0.04 150); color: oklch(30% 0.16 150); }
.qr-status.st-expired  { background: oklch(96% 0.02 0);   color: oklch(35% 0.14 25); }

.qr-steps {
  list-style: none; padding: 0; margin: 0 0 6px;
  counter-reset: qrStep;
}
.qr-steps li {
  display: flex; align-items: center; gap: 10px;
  padding: 6px 0; font-size: 12.5px; color: var(--muted);
  transition: color .2s;
}
.qr-steps li.done { color: var(--fg-2); }
.qr-steps li .num {
  width: 20px; height: 20px; border-radius: 50%;
  background: var(--bg-sub); color: var(--muted);
  display: grid; place-items: center; font-size: 11px; font-weight: 600;
  border: 1px solid var(--border);
}
.qr-steps li.done .num {
  background: var(--ok); color: #fff; border-color: var(--ok);
}

.qr-expired {
  display: flex; align-items: center;
  padding: 10px 12px; background: var(--err-soft); color: var(--err);
  border-radius: 8px; font-size: 12.5px;
}
.qr-status.st-launching { background: oklch(96% 0.02 240); color: oklch(35% 0.10 240); }
.spin { display: inline-block; animation: spin 1s linear infinite; }
@keyframes spin { from { transform: rotate(0deg) } to { transform: rotate(360deg) } }
</style>
