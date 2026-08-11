<template>
  <section class="stage-business-card" aria-labelledby="stage-qualification-title">
    <header class="stage-business-card__header">
      <div>
        <span class="stage-business-card__eyebrow">QUALIFICATION</span>
        <h4 id="stage-qualification-title">{{ t('pipeline.stageResults.qualificationTitle') }}</h4>
      </div>
    </header>

    <p v-if="legacy" class="stage-state" data-testid="stage-02-legacy">
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
      <p
        v-if="summary.totalCandidates === 0"
        class="stage-state"
        data-testid="stage-02-empty"
      >
        {{ t('pipeline.stageResults.qualificationEmpty') }}
      </p>
      <template v-else>
        <div class="metric-grid">
          <article class="metric-card">
            <span>{{ t('pipeline.stageResults.pendingReview') }}</span>
            <strong>{{ summary.pendingHumanReview }}</strong>
          </article>
          <article class="metric-card">
            <span>{{ t('pipeline.stageResults.matchScore') }}</span>
            <strong>{{ formatScore(summary.averageMatchScore) }}</strong>
          </article>
          <article class="metric-card">
            <span>{{ t('pipeline.stageResults.confidenceScore') }}</span>
            <strong>{{ formatScore(summary.averageConfidenceScore) }}</strong>
          </article>
        </div>

        <div class="status-grid" aria-label="Qualification states">
          <button
            v-for="status in qualificationStatuses"
            :key="status"
            type="button"
            class="status-card"
            :class="`status-card--${status}`"
            :data-qualification-status="status"
            @click="emit('filter-candidates', { qualificationStatus: status })"
          >
            <span>{{ t(`pipeline.stageResults.qualificationStatus.${status}`) }}</span>
            <strong>{{ summary.byQualificationStatus[status] ?? 0 }}</strong>
          </button>
        </div>
      </template>
    </template>
  </section>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { getAcquisitionStage02 } from '../api'
import type {
  AcquisitionCandidateListParams,
  AcquisitionStage02Summary,
  CandidateQualificationStatus,
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
const summary = ref<AcquisitionStage02Summary | null>(null)
let generation = 0

const qualificationStatuses: CandidateQualificationStatus[] = [
  'qualified',
  'manual_review',
  'need_enrichment',
  'rejected',
]

function formatScore(score: number | null) {
  return score === null ? '—' : score.toFixed(1)
}

async function load() {
  const currentGeneration = ++generation
  summary.value = null
  error.value = false
  if (props.legacy || props.stageStatus === 'failed' || !props.jobId) {
    loading.value = false
    return
  }
  loading.value = true
  try {
    const response = await getAcquisitionStage02(props.jobId)
    if (currentGeneration !== generation) return
    summary.value = response.data.summary
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
.stage-business-card { margin-top: 12px; padding: 16px; border: 1px solid var(--border); border-radius: var(--card-radius); background: var(--surface); }
.stage-business-card__header { margin-bottom: 14px; }
.stage-business-card__header h4 { margin: 3px 0 0; color: var(--fg); font-size: 15px; }
.stage-business-card__eyebrow { color: var(--brand); font-family: var(--font-mono); font-size: 10px; font-weight: 700; letter-spacing: .12em; }
.metric-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
.metric-card, .status-card { min-width: 0; padding: 10px 12px; border: 1px solid var(--border); border-radius: 9px; background: var(--bg); text-align: left; }
.metric-card span, .status-card span { display: block; color: var(--muted); font-size: 11px; }
.metric-card strong, .status-card strong { display: block; margin-top: 4px; color: var(--fg); font-family: var(--font-mono); font-size: 18px; }
.status-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; margin-top: 14px; }
.status-card { min-height: 58px; cursor: pointer; }
.status-card:hover, .status-card:focus-visible { border-color: var(--brand); outline: none; }
.status-card--qualified { border-left: 3px solid var(--ok); }
.status-card--manual_review, .status-card--need_enrichment { border-left: 3px solid var(--warn); }
.status-card--rejected { border-left: 3px solid var(--err); }
.stage-state { margin: 10px 0 0; padding: 12px; border-radius: 8px; background: var(--bg); color: var(--muted); font-size: 12px; }
.stage-state--error { background: var(--err-soft); color: var(--err); }
@media (max-width: 720px) {
  .metric-grid, .status-grid { grid-template-columns: 1fr 1fr; }
}
@media (prefers-reduced-motion: reduce) {
  .status-card { transition: none; }
}
</style>
