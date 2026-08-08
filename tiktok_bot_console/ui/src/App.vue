<template>
  <div class="shell">
    <aside class="sidebar">
      <div class="sb-brand">
        <div class="sb-logo">▣</div>
        <span>Pipeline Lab</span>
      </div>
      <div class="sb-section">{{ $t('nav.sectionNav') }}</div>
      <nav class="sb-nav">
        <router-link v-for="item in navMain" :key="item.path" :to="item.path" class="sb-link" :class="{active:$route.path===item.path}">
          <span class="icn" v-html="item.icon"></span>
          <span>{{ $t('nav.'+item.key) }}</span>
        </router-link>
      </nav>
      <div class="sb-section">{{ $t('nav.sectionSystem') }}</div>
      <nav class="sb-nav">
        <router-link v-for="item in navSystem" :key="item.path" :to="item.path" class="sb-link" :class="{active:$route.path.startsWith(item.path)}">
          <span class="icn" v-html="item.icon"></span>
          <span>{{ $t('nav.'+item.key) }}</span>
        </router-link>
      </nav>
      <div class="sb-foot">
        <div class="user">
          <div class="avatar">{{ initials }}</div>
          <div>
            <div class="user-name">{{ authStore.username }}</div>
            <div class="user-role">admin</div>
          </div>
        </div>
        <button @click="authStore.logout();$router.push('/login')" class="logout-btn">{{ $t('common.logout') }}</button>
      </div>
    </aside>
    <main class="main">
      <header class="topbar">
        <div class="topbar-left">
          <div class="crumb">
            <span>{{ crumbRoot }}</span>
            <span class="sep">/</span>
            <b>{{ crumbCurrent }}</b>
          </div>
        </div>
        <div class="topbar-right">
          <input class="search" :placeholder="$t('common.searchPh')">
          <div class="lang-switch" role="group" :aria-label="$t('common.language')">
            <button :class="{active: locale === 'zh-CN'}" @click="setLang('zh-CN')" title="中文">中</button>
            <button :class="{active: locale === 'en-US'}" @click="setLang('en-US')" title="English">EN</button>
          </div>
          <button class="icon-btn" :title="$t('common.refresh')" @click="refresh">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/><path d="M3 21v-5h5"/></svg>
          </button>
          <button class="icon-btn" :title="$t('common.notify')">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/></svg>
          </button>
          <div class="avatar">{{ initials }}</div>
        </div>
      </header>
      <div v-if="showMockBanner" :class="['mock-banner', bannerVariant]">
        <span class="dot"></span>
        <b>{{ bannerTitle }}</b> · {{ bannerBody }}
        <span v-if="fallbackState.hits > 1" style="opacity:.7;margin-left:6px">({{ fallbackState.hits }}×)</span>
        <div class="api-mode-toggle">
          <span class="lbl">{{ $t('common.apiMode') }}</span>
          <button :class="{active: apiMode === 'auto'}"  @click="setApiMode('auto')">{{ $t('common.apiAuto') }}</button>
          <button :class="{active: apiMode === 'mock'}"  @click="setApiMode('mock')">Mock</button>
          <button :class="{active: apiMode === 'real'}"  @click="setApiMode('real')">{{ $t('common.apiReal') }}</button>
        </div>
      </div>
      <router-view :key="tick" />
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useAuthStore } from './stores/auth'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const { t, locale } = useI18n()

const tick = ref(0)

function setLang(l: 'zh-CN' | 'en-US') {
  locale.value = l
  localStorage.setItem('lang', l)
  // re-render router-view with fresh i18n context
  tick.value++
}

function refresh() {
  tick.value++
}

import { fallbackState } from './api'
import { apiMode, setApiMode } from './composables/useApiMode'

// Banner shows in three distinct cases:
//  1. Mode != 'real'  (mock / auto + env)  → info banner (intentional demo)
//  2. Mode = 'real' + fallbackState.active → hard error banner (user asked for real, got fallback)
//  3. Mode = 'real' + first request pending  → soft info (so user sees current mode)
const showMockBanner = computed(() => true)
const effectiveMock = computed(() => apiMode.value === 'mock' || (apiMode.value === 'auto' && import.meta.env.VITE_USE_MOCK === 'true'))
const bannerVariant = computed(() => {
  if (fallbackState.active && apiMode.value === 'auto') return 'error'
  if (effectiveMock.value) return 'mock'
  return 'info'
})
const bannerTitle = computed(() => {
  if (fallbackState.active && apiMode.value === 'auto') return t('common.backendUnreachable')
  if (effectiveMock.value) return t('common.mockMode')
  return t('common.apiReal')
})
const bannerBody = computed(() => {
  if (fallbackState.active && apiMode.value === 'auto') return t('common.backendUnreachableDetail')
  if (effectiveMock.value) return t('common.demoDataFromMock')
  return t('common.realBackendNote')
})
// re-render router-view when mode flips so views reload data
watch(apiMode, () => { tick.value++ })

const initials = computed(() => (authStore.username || 'OP').slice(0, 2).toUpperCase())

const navMain = [
  { path: '/dashboard',    key: 'dashboard', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>' },
  { path: '/users',        key: 'users',     icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="8" r="4"/><path d="M4 20c0-4 4-7 8-7s8 3 8 7"/></svg>' },
  { path: '/leads',        key: 'leads',     icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/><path d="M11 8v6M8 11h6"/></svg>' },
  { path: '/pipeline',     key: 'pipeline',  icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="6" cy="6" r="3"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="18" r="3"/><path d="M9 6h6M6 9v6M18 9v6M9 18h6"/></svg>' },
  { path: '/reports',      key: 'reports',   icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 3v18h18"/><path d="m7 14 4-4 4 4 5-6"/></svg>' },
]
const navSystem = [
  { path: '/config-accounts', key: 'accounts', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="8" r="4"/><path d="M4 20c0-4 4-7 8-7s8 3 8 7"/></svg>' },
  { path: '/config-llm',      key: 'llm',      icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2 4 6v6c0 5 3.5 9 8 10 4.5-1 8-5 8-10V6z"/></svg>' },
  { path: '/config-pipeline', key: 'runtime',  icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M12 1v2m0 18v2M1 12h2m18 0h2"/></svg>' },
]

const crumbMap: Record<string, [string, string]> = {
  '/dashboard':       ['nav.dashboard', 'dashboard.todayState'],
  '/users':           ['nav.users', 'users.title'],
  '/leads':           ['nav.leads', 'leads.title'],
  '/pipeline':        ['nav.pipeline', 'pipeline.todayProgress'],
  '/reports':         ['nav.reports', 'reports.trend30'],
  '/config-accounts': ['nav.accounts', 'config.accountsTitle'],
  '/config-llm':      ['nav.llm', 'config.llmTitle'],
  '/config-pipeline': ['nav.runtime', 'config.runtimeTitle'],
}
const crumbRoot = computed(() => {
  const m = crumbMap[route.path]
  return m ? t(m[0]) : ''
})
const crumbCurrent = computed(() => {
  const m = crumbMap[route.path]
  return m ? t(m[1]) : ''
})
</script>

<style scoped>
.shell { display: flex; min-height: 100vh; background: var(--bg); }

.sidebar {
  width: var(--sb-w);
  flex-shrink: 0;
  background: var(--sb-bg);
  color: var(--sb-fg);
  display: flex;
  flex-direction: column;
  position: sticky;
  top: 0;
  height: 100vh;
  border-right: 1px solid var(--sb-border);
}
.sb-brand {
  display: flex; align-items: center; gap: 10px;
  padding: 16px 18px; height: var(--top-h);
  border-bottom: 1px solid var(--sb-border);
  font-weight: 700; font-size: 14.5px; letter-spacing: -0.2px;
}
.sb-logo {
  width: 28px; height: 28px; border-radius: 7px;
  background: linear-gradient(135deg, var(--brand), var(--cyan));
  display: grid; place-items: center; color: #fff; font-weight: 800; font-size: 14px;
}
.sb-section { padding: 14px 14px 4px; font-size: 10px; text-transform: uppercase; letter-spacing: 1.2px; color: var(--sb-muted); }
.sb-nav { padding: 6px 8px; flex: 1; overflow-y: auto; }
.sb-link {
  display: flex; align-items: center; gap: 10px;
  padding: 8px 10px; margin-bottom: 1px;
  color: oklch(85% 0.005 280);
  border-radius: 7px;
  font-size: 13.5px;
  transition: background .1s, color .1s;
}
.sb-link:hover { background: var(--sb-bg-2); color: #fff; }
.sb-link.active { background: oklch(24% 0.06 350 / 1); color: #fff; font-weight: 500; position: relative; }
.sb-link.active::before {
  content: ''; position: absolute; left: -8px; top: 50%; transform: translateY(-50%);
  width: 3px; height: 16px; background: var(--brand); border-radius: 2px;
}
.sb-link .icn { width: 16px; height: 16px; opacity: .8; flex-shrink: 0; display: grid; place-items: center; }
.sb-link .icn :deep(svg) { width: 16px; height: 16px; }
.sb-link.active .icn { opacity: 1; }

.sb-foot {
  padding: 12px 14px; border-top: 1px solid var(--sb-border);
  display: flex; align-items: center; justify-content: space-between;
  font-size: 12.5px; color: var(--sb-muted);
}
.sb-foot .user { display: flex; align-items: center; gap: 8px; color: var(--sb-fg); }
.user-name { font-size: 12.5px; color: #fff; font-weight: 500; line-height: 1.2; }
.user-role { font-size: 10.5px; color: var(--sb-muted); line-height: 1.2; margin-top: 1px; }
.avatar {
  width: 26px; height: 26px; border-radius: 50%;
  background: linear-gradient(135deg, var(--brand), var(--cyan));
  display: grid; place-items: center; color: #fff; font-weight: 700; font-size: 11px;
  flex-shrink: 0;
}
.logout-btn { background: none; border: none; color: var(--sb-muted); cursor: pointer; font-size: 11px; }
.logout-btn:hover { color: #fff; }

.main { flex: 1; min-width: 0; }
.topbar {
  height: var(--top-h);
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  display: flex; align-items: center; justify-content: space-between;
  padding: 0 var(--page-px);
  position: sticky; top: 0; z-index: 20;
}
.topbar-left { display: flex; align-items: center; gap: 14px; }
.crumb { display: flex; align-items: center; gap: 8px; color: var(--muted); font-size: 13px; }
.crumb .sep { color: var(--muted-2); }
.crumb b { color: var(--fg); font-weight: 600; }
.search {
  width: 320px; height: 34px; padding: 0 12px 0 32px;
  background: var(--bg-sub); border: 1px solid var(--border);
  border-radius: 8px; font-size: 13px; color: var(--fg);
  background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='14' height='14' viewBox='0 0 24 24' fill='none' stroke='%2371717a' stroke-width='2'><circle cx='11' cy='11' r='7'/><path d='m21 21-4.3-4.3'/></svg>");
  background-repeat: no-repeat; background-position: 11px center;
}
.search:focus { outline: none; border-color: var(--brand); background-color: #fff; }

.topbar-right { display: flex; align-items: center; gap: 6px; }
.icon-btn {
  width: 34px; height: 34px; border-radius: 8px; border: none; background: transparent;
  display: grid; place-items: center; color: var(--fg-2); cursor: pointer;
}
.icon-btn:hover { background: var(--bg-sub); }

.lang-switch {
  display: flex; gap: 0; background: var(--bg-sub); border: 1px solid var(--border);
  border-radius: 8px; padding: 2px; margin: 0 4px;
}
.lang-switch button {
  border: none; background: transparent; padding: 4px 10px; font-size: 12px;
  color: var(--muted); border-radius: 6px; cursor: pointer; font-weight: 500;
  min-width: 32px;
}
.lang-switch button.active { background: var(--surface); color: var(--fg); box-shadow: var(--shadow-1); font-weight: 600; }
.lang-switch button:hover:not(.active) { color: var(--fg); }

.mock-banner {
  display: flex; align-items: center; gap: 10px;
  padding: 8px 32px; margin: 0;
  background: oklch(96% 0.025 75); color: oklch(45% 0.16 75);
  font-size: 12.5px; border-bottom: 1px solid oklch(88% 0.06 75);
}
.mock-banner .dot { width: 7px; height: 7px; border-radius: 50%; background: oklch(72% 0.16 75); flex-shrink: 0; }
.mock-banner b { color: oklch(38% 0.16 75); font-weight: 600; }
.mock-banner.info { background: oklch(97% 0.018 225); color: oklch(43% 0.12 225); border-color: oklch(89% 0.055 225); }
.mock-banner.info .dot { background: oklch(62% 0.15 225); }
.mock-banner.info b { color: oklch(36% 0.14 225); }
.mock-banner.error { background: oklch(96% 0.025 25); color: oklch(45% 0.17 25); border-color: oklch(87% 0.07 25); }
.mock-banner.error .dot { background: var(--err); }
.mock-banner.error b { color: oklch(38% 0.18 25); }
.api-mode-toggle { display: inline-flex; align-items: center; gap: 4px; margin-left: auto; padding: 2px; background: rgba(0,0,0,.04); border-radius: 8px; }
.api-mode-toggle .lbl { font-size: 11px; color: var(--muted); padding: 0 6px; }
.api-mode-toggle button { padding: 3px 10px; border: none; background: transparent; border-radius: 6px; font-size: 11.5px; color: var(--fg-2); cursor: pointer; font-weight: 500; }
.api-mode-toggle button:hover:not(.active) { color: var(--fg); }
.api-mode-toggle button.active { background: var(--surface); color: var(--fg); font-weight: 600; box-shadow: 0 1px 2px rgba(0,0,0,.06); }
</style>