<template>
  <section class="stage-business-card" aria-labelledby="stage-discovery-title">
    <header class="stage-business-card__header">
      <div>
        <span class="stage-business-card__eyebrow">DISCOVERY</span>
        <h4 id="stage-discovery-title">{{ t('pipeline.stageResults.discoveryTitle') }}</h4>
      </div>
    </header>

    <p v-if="legacy" class="stage-state" data-testid="stage-01-legacy">
      {{ t('pipeline.stageResults.legacy') }}
    </p>
    <p v-else-if="stageStatus === 'failed'" class="stage-state stage-state--error" role="alert">
      {{ t('pipeline.stageResults.failed') }}
    </p>
    <p v-else-if="loading" class="stage-state" aria-live="polite">
      {{ t('pipeline.stageResults.loading') }}
    </p>
    <p v-else-if="error" class="stage-state stage-state--error" role="alert">
      {{ t('pipeline.stageResults.unavailable') }}
    </p>

    <template v-else-if="summary">
      <div v-if="truncationReasons.length" class="budget-warning" data-testid="stage-01-truncated">
        <strong>{{ t('pipeline.stageResults.truncated') }}</strong>
        <span>{{ truncationReasons.join(' · ') }}</span>
      </div>

      <div class="metric-grid" aria-label="Discovery metrics">
        <article class="metric-card">
          <span>{{ t('pipeline.stageResults.candidates') }}</span>
          <strong>{{ summary.totalCandidates }}</strong>
        </article>
        <article class="metric-card">
          <span>{{ t('pipeline.stageResults.evidence') }}</span>
          <strong>{{ summary.evidenceCount }}</strong>
        </article>
        <article class="metric-card">
          <span>{{ t('pipeline.stageResults.keywords') }}</span>
          <strong>{{ summary.keywordCount }}</strong>
        </article>
      </div>

      <p
        v-if="summary.totalCandidates === 0 && summary.evidenceCount === 0"
        class="stage-state"
        data-testid="stage-01-empty"
      >
        {{ t('pipeline.stageResults.discoveryEmpty') }}
      </p>

      <div v-else class="result-sections">
        <section>
          <h5>{{ t('pipeline.stageResults.discoveryStatuses') }}</h5>
          <div class="filter-grid">
            <button
              v-for="status in discoveryStatuses"
              :key="status"
              type="button"
              class="filter-card"
              :data-discovery-status="status"
              @click="emit('filter-candidates', { discoveryStatus: status })"
            >
              <span>{{ t(`pipeline.stageResults.discoveryStatus.${status}`) }}</span>
              <strong>{{ summary.byDiscoveryStatus[status] ?? 0 }}</strong>
            </button>
          </div>
        </section>

        <section v-if="sourceEntries.length">
          <h5>{{ t('pipeline.stageResults.sources') }}</h5>
          <div class="filter-grid">
            <button
              v-for="([sourceType, count]) in sourceEntries"
              :key="sourceType"
              type="button"
              class="filter-card filter-card--source"
              :data-source-type="sourceType"
              @click="emit('filter-candidates', { sourceType })"
            >
              <span>{{ sourceType }}</span>
              <strong>{{ count }}</strong>
            </button>
          </div>
        </section>

        <section>
          <h5>{{ t('pipeline.stageResults.keywordPerformance') }}</h5>
          <p v-if="keywords.length === 0" class="stage-state">
            {{ t('pipeline.stageResults.noKeywords') }}
          </p>
          <div v-else class="keyword-list">
            <button
              v-for="keyword in keywords"
              :key="keyword.id"
              type="button"
              class="keyword-row"
              :data-keyword-id="keyword.id"
              @click="emit('filter-candidates', { keywordId: keyword.id })"
            >
              <span class="keyword-row__name">{{ keyword.text }}</span>
              <span>{{ t('pipeline.stageResults.videos') }} {{ keyword.videoCount }}</span>
              <span>{{ t('pipeline.stageResults.relevantVideos') }} {{ keyword.relevantVideoCount }}</span>
              <span>{{ t('pipeline.stageResults.foundCandidates') }} {{ keyword.candidateCount }}</span>
            </button>
          </div>
        </section>
      </div>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { getAcquisitionStage01, listAcquisitionKeywords } from '../api'
import type {
  AcquisitionCandidateListParams,
  AcquisitionKeyword,
  AcquisitionStage01Summary,
  CandidateDiscoveryStatus,
  PipelineStageStatus,
} from '../types/pipeline'

const props = defineProps<{
  jobId: string
  stageStatus: PipelineStageStatus
  stageResult: Record<string, unknown>
  legacy: boolean
  refreshToken: number
}>()

const emit = defineEmits<{
  (event: 'filter-candidates', filter: AcquisitionCandidateListParams): void
}>()

const { t } = useI18n()
const loading = ref(false)
const error = ref(false)
const summary = ref<AcquisitionStage01Summary | null>(null)
const keywords = ref<AcquisitionKeyword[]>([])
let generation = 0

const discoveryStatuses: CandidateDiscoveryStatus[] = [
  'candidate',
  'needs_more_evidence',
  'obvious_irrelevant',
  'duplicate',
  'blocked',
]

const sourceEntries = computed(() =>
  Object.entries(summary.value?.bySourceType ?? {}).sort((left, right) =>
    right[1] - left[1],
  ),
)

const truncationReasons = computed(() => {
  const raw = props.stageResult.truncation_reasons
    ?? props.stageResult.truncationReasons
  if (!Array.isArray(raw)) return []
  return raw.filter((item): item is string => typeof item === 'string' && item.length > 0)
})

async function load() {
  const currentGeneration = ++generation
  summary.value = null
  keywords.value = []
  error.value = false
  if (props.legacy || props.stageStatus === 'failed' || !props.jobId) {
    loading.value = false
    return
  }
  loading.value = true
  try {
    const [stageResponse, keywordResponse] = await Promise.all([
      getAcquisitionStage01(props.jobId),
      listAcquisitionKeywords(props.jobId, { limit: 100, offset: 0 }),
    ])
    if (currentGeneration !== generation) return
    summary.value = stageResponse.data.summary
    keywords.value = keywordResponse.data.items
  } catch {
    if (currentGeneration !== generation) return
    error.value = true
  } finally {
    if (currentGeneration === generation) loading.value = false
  }
}

watch(
  () => [props.jobId, props.refreshToken, props.legacy, props.stageStatus],
  load,
  { immediate: true },
)
</script>

<style scoped>
.stage-business-card { margin-top: 12px; padding: 16px; border: 1px solid var(--border); border-radius: 12px; background: var(--surface); }
.stage-business-card__header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; }
.stage-business-card__header h4 { margin: 3px 0 0; color: var(--text); font-size: 15px; }
.stage-business-card__eyebrow { color: var(--brand); font-family: var(--font-mono); font-size: 10px; font-weight: 700; letter-spacing: .12em; }
.metric-grid, .filter-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
.metric-card, .filter-card { min-width: 0; padding: 10px 12px; border: 1px solid var(--border); border-radius: 9px; background: var(--bg); text-align: left; }
.metric-card span, .filter-card span { display: block; overflow: hidden; color: var(--muted); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.metric-card strong, .filter-card strong { display: block; margin-top: 4px; color: var(--text); font-family: var(--font-mono); font-size: 18px; }
.filter-card, .keyword-row { min-height: 44px; cursor: pointer; }
.filter-card:hover, .filter-card:focus-visible, .keyword-row:hover, .keyword-row:focus-visible { border-color: var(--brand); outline: none; }
.result-sections { display: grid; gap: 15px; margin-top: 15px; }
.result-sections h5 { margin: 0 0 7px; color: var(--text); font-size: 12px; }
.filter-card--source { grid-column: span 1; }
.keyword-list { display: grid; gap: 6px; }
.keyword-row { display: grid; grid-template-columns: minmax(140px, 1fr) repeat(3, auto); gap: 12px; align-items: center; min-height: 44px; padding: 8px 11px; border: 1px solid var(--border); border-radius: 8px; background: var(--bg); color: var(--muted); font-size: 11px; text-align: left; }
.keyword-row__name { overflow: hidden; color: var(--text); font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
.stage-state { margin: 10px 0 0; padding: 12px; border-radius: 8px; background: var(--bg); color: var(--muted); font-size: 12px; }
.stage-state--error { background: rgba(239, 68, 68, .08); color: var(--danger); }
.budget-warning { display: flex; gap: 8px; align-items: baseline; margin-bottom: 12px; padding: 9px 11px; border: 1px solid rgba(245, 158, 11, .35); border-radius: 8px; background: rgba(245, 158, 11, .08); color: #a16207; font-size: 11px; }
@media (max-width: 720px) {
  .metric-grid, .filter-grid { grid-template-columns: 1fr 1fr; }
  .keyword-row { grid-template-columns: 1fr 1fr; }
  .keyword-row__name { grid-column: 1 / -1; }
}
@media (prefers-reduced-motion: reduce) {
  .filter-card, .keyword-row { transition: none; }
}
</style>
