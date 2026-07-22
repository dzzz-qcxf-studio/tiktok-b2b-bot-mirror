<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1>{{ $t('leads.title') }}</h1>
        <p>{{ $t('leads.subtitle') }}</p>
      </div>
    </div>

    <!-- Search bar -->
    <div class="search-bar">
      <div class="search-input-wrap">
        <svg class="search-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
        <input
          v-model="keyword"
          :placeholder="$t('leads.searchPh')"
          class="search-input"
          @keyup.enter="doSearch"
        />
        <button class="btn brand" @click="doSearch" :disabled="!keyword.trim() || loading">
          {{ loading ? $t('common.loading') : $t('leads.search') }}
        </button>
      </div>
      <div class="search-hint">
        {{ $t('leads.searchHint') }}
        <span v-if="results.length" style="margin-left:12px">
          {{ $t('leads.resultCount', { n: results.length }) }}
        </span>
      </div>
    </div>

    <!-- Loading -->
    <div v-if="loading" class="loading-wrap">
      <div class="spinner"></div>
      <span>{{ $t('leads.searching') }}</span>
    </div>

    <!-- Error -->
    <div v-else-if="error" class="error-banner">
      <span class="error-icon">⚠️</span>
      <span>{{ error }}</span>
      <button class="btn sm ghost" @click="doSearch">{{ $t('common.refresh') }}</button>
    </div>

    <!-- Empty state -->
    <div v-else-if="searched && results.length === 0" class="empty-state">
      <div class="empty-icon">🔍</div>
      <div class="empty-title">{{ $t('leads.emptyTitle') }}</div>
      <div class="empty-desc">{{ $t('leads.emptyDesc') }}</div>
    </div>

    <!-- Results -->
    <div v-else-if="results.length > 0" class="leads-grid">
      <div v-for="lead in results" :key="lead.id" class="card lead-card">
        <div class="lead-head">
          <div class="lead-avatar">{{ lead.avatar_initials }}</div>
          <div class="lead-info">
            <div class="lead-name">@{{ lead.username }}</div>
            <div class="lead-nickname">{{ lead.nickname }}</div>
          </div>
          <div class="lead-score" :class="scoreClass(lead.relevance_score)">
            {{ lead.relevance_score }}
          </div>
        </div>
        <div class="lead-bio">{{ lead.bio }}</div>
        <div class="lead-meta">
          <span class="meta-item">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="8" r="4"/><path d="M4 20c0-4 4-7 8-7s8 3 8 7"/></svg>
            {{ fmtK(lead.follower_count) }}
          </span>
          <span class="meta-item">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="2" width="20" height="20" rx="2"/><path d="m7 2 10 20"/><path d="M2 12h20"/></svg>
            {{ lead.video_count }}
          </span>
          <span v-if="lead.country" class="meta-item country">{{ lead.country }}</span>
        </div>
        <div class="lead-kw">
          <span class="kw-tag">{{ lead.matched_keyword }}</span>
        </div>
        <div class="lead-actions">
          <button class="btn sm brand" @click="addLead(lead)" :disabled="addedIds.has(lead.id)">
            {{ addedIds.has(lead.id) ? $t('leads.added') : $t('leads.addToPool') }}
          </button>
          <a :href="lead.url" target="_blank" rel="noopener" class="btn sm ghost">{{ $t('leads.viewProfile') }}</a>
        </div>
      </div>
    </div>

    <!-- Initial state (before first search) -->
    <div v-else class="initial-state">
      <div class="initial-icon">🎯</div>
      <div class="initial-title">{{ $t('leads.initialTitle') }}</div>
      <div class="initial-desc">{{ $t('leads.initialDesc') }}</div>
      <div class="initial-suggestions">
        <button v-for="kw in suggestions" :key="kw" class="btn sm ghost" @click="keyword = kw; doSearch()">
          {{ kw }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { searchLeads } from '../api'

const { t } = useI18n()

interface Lead {
  id: number
  username: string
  nickname: string
  bio: string
  avatar_initials: string
  follower_count: number
  video_count: number
  country: string
  relevance_score: number
  matched_keyword: string
  url: string
}

const keyword = ref('')
const results = ref<Lead[]>([])
const loading = ref(false)
const error = ref('')
const searched = ref(false)
const addedIds = ref(new Set<number>())

const suggestions = ['wholesale', 'importer', 'sourcing agent', 'distributor', 'OEM']

async function doSearch() {
  const kw = keyword.value.trim()
  if (!kw) return
  loading.value = true
  error.value = ''
  searched.value = true
  try {
    const { data } = await searchLeads(kw, 20)
    results.value = Array.isArray(data) ? data as Lead[] : []
  } catch (e: any) {
    error.value = e?.response?.data?.detail || e?.message || t('leads.searchError')
    results.value = []
  } finally {
    loading.value = false
  }
}

function addLead(lead: Lead) {
  addedIds.value.add(lead.id)
  ElMessage.success(t('leads.addedSuccess', { user: lead.username }))
}

function fmtK(n: number) {
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M'
  if (n >= 1000) return (n / 1000).toFixed(1) + 'K'
  return String(n)
}

function scoreClass(score: number) {
  if (score >= 90) return 'excellent'
  if (score >= 75) return 'good'
  if (score >= 60) return 'fair'
  return 'low'
}
</script>

<style scoped>
.search-bar { margin-bottom: 20px; }
.search-input-wrap {
  display: flex; align-items: center; gap: 8px;
  background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
  padding: 4px 4px 4px 14px;
}
.search-icon { flex-shrink: 0; color: var(--muted); }
.search-input {
  flex: 1; border: none; background: transparent; font-size: 13px;
  color: var(--fg); outline: none; height: 38px;
}
.search-input::placeholder { color: var(--muted); }
.search-hint { font-size: 11.5px; color: var(--muted); margin-top: 8px; }

/* Loading */
.loading-wrap {
  display: flex; align-items: center; justify-content: center; gap: 10px;
  padding: 60px 0; color: var(--muted); font-size: 13px;
}
.spinner {
  width: 20px; height: 20px; border: 2px solid var(--border); border-top-color: var(--brand);
  border-radius: 50%; animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg) } }

/* Error */
.error-banner {
  display: flex; align-items: center; gap: 10px;
  padding: 12px 16px; background: var(--err-soft); color: var(--err);
  border-radius: 10px; font-size: 13px; margin-bottom: 16px;
}
.error-icon { font-size: 16px; }

/* Empty */
.empty-state, .initial-state {
  text-align: center; padding: 80px 20px; color: var(--muted);
}
.empty-icon, .initial-icon { font-size: 48px; margin-bottom: 16px; }
.empty-title, .initial-title { font-size: 16px; font-weight: 600; color: var(--fg-2); margin-bottom: 8px; }
.empty-desc, .initial-desc { font-size: 13px; max-width: 400px; margin: 0 auto 20px; }
.initial-suggestions { display: flex; gap: 8px; justify-content: center; flex-wrap: wrap; }

/* Grid */
.leads-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }
.lead-card { padding: 18px; }
.lead-head { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.lead-avatar {
  width: 40px; height: 40px; border-radius: 50%;
  background: linear-gradient(135deg, oklch(58% 0.22 350), oklch(70% 0.14 200));
  color: #fff; font-weight: 700; font-size: 13px;
  display: grid; place-items: center; flex-shrink: 0;
}
.lead-info { flex: 1; min-width: 0; }
.lead-name { font-size: 14px; font-weight: 600; font-family: var(--font-mono); }
.lead-nickname { font-size: 11.5px; color: var(--muted); margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.lead-score {
  width: 36px; height: 36px; border-radius: 50%; font-weight: 700; font-size: 13px;
  display: grid; place-items: center; flex-shrink: 0;
}
.lead-score.excellent { background: var(--ok-soft); color: oklch(42% 0.16 150); }
.lead-score.good { background: var(--brand-soft); color: var(--brand-deep); }
.lead-score.fair { background: var(--warn-soft); color: oklch(45% 0.16 75); }
.lead-score.low { background: var(--bg-sub); color: var(--muted); }

.lead-bio { font-size: 12.5px; color: var(--fg-2); line-height: 1.5; margin-bottom: 12px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.lead-meta { display: flex; gap: 14px; margin-bottom: 10px; }
.meta-item { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; color: var(--muted); }
.meta-item.country { padding: 2px 8px; background: var(--bg-sub); border-radius: 4px; font-weight: 500; }

.lead-kw { margin-bottom: 14px; }
.kw-tag { display: inline-block; padding: 3px 10px; background: var(--brand-soft); color: var(--brand-deep); border-radius: 6px; font-size: 11px; font-weight: 500; }

.lead-actions { display: flex; gap: 6px; padding-top: 12px; border-top: 1px solid var(--border); }
</style>
