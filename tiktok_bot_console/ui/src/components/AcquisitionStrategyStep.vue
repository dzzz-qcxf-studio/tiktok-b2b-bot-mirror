<template>
  <div class="strategy-step">
    <div class="section-heading">
      <div>
        <span class="section-kicker">03</span>
        <h4>{{ $t('pipeline.acquisition.strategyTitle') }}</h4>
      </div>
      <p>{{ $t('pipeline.acquisition.strategyHint') }}</p>
    </div>

    <div class="condition-grid">
      <section data-testid="acquisition-hard-conditions" class="strategy-section">
        <header>
          <span class="section-badge">HARD</span>
          <div>
            <h5>{{ $t('pipeline.acquisition.hardConditions.title') }}</h5>
            <p>{{ $t('pipeline.acquisition.hardConditions.hint') }}</p>
          </div>
        </header>

        <div v-for="field in HARD_TAG_FIELDS" :key="field.key" class="compact-field">
          <label :for="`acquisition-hard-${field.testId}`">
            {{ $t(`pipeline.acquisition.hardConditions.${field.key}`) }}
          </label>
          <div class="input-action-row">
            <input
              :id="`acquisition-hard-${field.testId}`"
              v-model="hardTagDrafts[field.key]"
              :data-testid="`acquisition-hard-${field.testId}-input`"
              @keydown.enter.prevent="addHardTag(field.key)"
            >
            <button class="btn" type="button" @click="addHardTag(field.key)">
              {{ $t('common.add') }}
            </button>
          </div>
          <div v-if="hardList(field.key).length" class="tag-list">
            <span v-for="(item, index) in hardList(field.key)" :key="item" class="tag">
              {{ item }}
              <button
                type="button"
                :aria-label="$t('pipeline.acquisition.removeTag', { value: item })"
                @click="removeHardTag(field.key, index)"
              >×</button>
            </span>
          </div>
          <small v-if="hardTagErrors[field.key]" class="field-error" role="alert">
            {{ $t(`pipeline.acquisition.errors.${hardTagErrors[field.key]}`) }}
          </small>
        </div>

        <div class="compact-field">
          <label for="acquisition-must-business">
            {{ $t('pipeline.acquisition.hardConditions.mustBeBusinessAccount') }}
          </label>
          <select id="acquisition-must-business" v-model="draft.campaign.hardConditions.mustBeBusinessAccount">
            <option :value="null">{{ $t('pipeline.acquisition.unspecified') }}</option>
            <option :value="true">{{ $t('pipeline.acquisition.yes') }}</option>
            <option :value="false">{{ $t('pipeline.acquisition.no') }}</option>
          </select>
        </div>
      </section>

      <section data-testid="acquisition-preference-conditions" class="strategy-section preference-section">
        <header>
          <span class="section-badge preference">P02</span>
          <div>
            <h5>{{ $t('pipeline.acquisition.preferences.title') }}</h5>
            <p>{{ $t('pipeline.acquisition.preferences.hint') }}</p>
          </div>
        </header>

        <div data-testid="acquisition-phase02-notice" class="phase02-notice">
          {{ $t('pipeline.acquisition.preferences.phase02Notice') }}
        </div>

        <div class="preference-fields">
          <label>
            <span>{{ $t('pipeline.acquisition.preferences.employeeCount') }}</span>
            <input
              v-model="draft.campaign.preferenceConditions.employeeCount"
              data-testid="acquisition-preference-employee-count"
              @input="notifyChange"
            >
          </label>
          <label>
            <span>{{ $t('pipeline.acquisition.preferences.registeredCapital') }}</span>
            <input
              v-model="draft.campaign.preferenceConditions.registeredCapital"
              data-testid="acquisition-preference-registered-capital"
              @input="notifyChange"
            >
          </label>
          <label>
            <span>{{ $t('pipeline.acquisition.preferences.listingStatus') }}</span>
            <select
              v-model="draft.campaign.preferenceConditions.listingStatus"
              data-testid="acquisition-preference-listing-status"
              @change="notifyChange"
            >
              <option :value="null">{{ $t('pipeline.acquisition.unspecified') }}</option>
              <option value="listed">{{ $t('pipeline.acquisition.preferences.listed') }}</option>
              <option value="unlisted">{{ $t('pipeline.acquisition.preferences.unlisted') }}</option>
              <option value="unknown">{{ $t('pipeline.acquisition.preferences.unknown') }}</option>
            </select>
          </label>
          <label>
            <span>{{ $t('pipeline.acquisition.preferences.companyScale') }}</span>
            <input v-model="draft.campaign.preferenceConditions.companyScale" @input="notifyChange">
          </label>
          <label>
            <span>{{ $t('pipeline.acquisition.preferences.minimumYears') }}</span>
            <input
              v-model.number="draft.campaign.preferenceConditions.minimumYearsEstablished"
              type="number"
              min="0"
              max="500"
              @input="notifyChange"
            >
          </label>
          <label>
            <span>{{ $t('pipeline.acquisition.preferences.maximumYears') }}</span>
            <input
              v-model.number="draft.campaign.preferenceConditions.maximumYearsEstablished"
              type="number"
              min="0"
              max="500"
              @input="notifyChange"
            >
          </label>
        </div>
      </section>
    </div>

    <section class="strategy-section keyword-section">
      <header>
        <span class="section-badge">KEY</span>
        <div>
          <h5>{{ $t('pipeline.acquisition.keywords.title') }}</h5>
          <p>{{ $t('pipeline.acquisition.keywords.hint') }}</p>
        </div>
      </header>
      <div class="keyword-editor">
        <label>
          <span>{{ $t('pipeline.acquisition.keywords.text') }}</span>
          <input v-model="keywordDraft.text" data-testid="acquisition-keyword-text">
        </label>
        <label>
          <span>{{ $t('pipeline.acquisition.keywords.language') }}</span>
          <input v-model="keywordDraft.language" data-testid="acquisition-keyword-language">
        </label>
        <label>
          <span>{{ $t('pipeline.acquisition.keywords.type') }}</span>
          <select v-model="keywordDraft.keywordType" data-testid="acquisition-keyword-type">
            <option value="industry">{{ $t('pipeline.acquisition.keywords.types.industry') }}</option>
            <option value="product">{{ $t('pipeline.acquisition.keywords.types.product') }}</option>
            <option value="problem">{{ $t('pipeline.acquisition.keywords.types.problem') }}</option>
            <option value="scenario">{{ $t('pipeline.acquisition.keywords.types.scenario') }}</option>
          </select>
        </label>
        <button data-testid="acquisition-keyword-add" class="btn brand" type="button" @click="addKeyword">
          {{ $t('pipeline.acquisition.keywords.add') }}
        </button>
      </div>
      <div v-if="draft.keywords.length" class="keyword-list">
        <article v-for="(keyword, index) in draft.keywords" :key="keyword.text + keyword.language">
          <div>
            <strong>{{ keyword.text }}</strong>
            <small>{{ keyword.language || '—' }} · {{ keyword.keywordType }}</small>
          </div>
          <button
            :data-testid="`acquisition-keyword-remove-${index}`"
            type="button"
            :aria-label="$t('pipeline.acquisition.keywords.remove', { value: keyword.text })"
            @click="removeKeyword(index)"
          >×</button>
        </article>
      </div>
      <p
        v-if="keywordError || keywordValidationError"
        data-testid="acquisition-keywords-error"
        class="field-error"
        role="alert"
      >
        {{ $t(`pipeline.acquisition.errors.${keywordError || keywordValidationError}`) }}
      </p>
    </section>

    <section class="strategy-section budget-section">
      <header>
        <span class="section-badge">CAP</span>
        <div>
          <h5>{{ $t('pipeline.acquisition.budget.title') }}</h5>
          <p>{{ $t('pipeline.acquisition.budget.hint') }}</p>
        </div>
      </header>
      <div class="budget-grid">
        <label v-for="field in BUDGET_FIELDS" :key="field.key">
          <span>{{ $t(`pipeline.acquisition.budget.${field.key}`) }}</span>
          <input
            v-model.number="draft.campaign.searchBudget[field.key]"
            :data-testid="`acquisition-budget-${field.key}`"
            type="number"
            :min="field.min"
            :max="field.max"
            @input="notifyChange"
          >
        </label>
      </div>
      <div class="mix-grid">
        <label>
          <span>{{ $t('pipeline.acquisition.keywordMix.effective') }}</span>
          <input
            v-model.number="draft.campaign.keywordMix.effectivePercent"
            data-testid="acquisition-mix-effective"
            type="number"
            min="0"
            max="100"
            @input="notifyChange"
          >
        </label>
        <label>
          <span>{{ $t('pipeline.acquisition.keywordMix.new') }}</span>
          <input
            v-model.number="draft.campaign.keywordMix.newPercent"
            data-testid="acquisition-mix-new"
            type="number"
            min="0"
            max="100"
            @input="notifyChange"
          >
        </label>
        <p>{{ $t('pipeline.acquisition.keywordMix.hint') }}</p>
      </div>
    </section>

    <div v-if="errors.length" data-testid="acquisition-strategy-errors" class="strategy-errors" role="alert">
      <strong>{{ $t('pipeline.acquisition.fixErrors') }}</strong>
      <ul>
        <li v-for="error in errors" :key="error.field + error.code">
          {{ $t(`pipeline.acquisition.errors.${error.code}`) }}
        </li>
      </ul>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive } from 'vue'

import type { AcquisitionSearchBudget } from '../types/pipeline'
import {
  addUniqueKeyword,
  addUniqueListItem,
  type AcquisitionCreatorDraft,
  type AcquisitionValidationError,
} from './acquisitionCreator'

type HardListKey = 'excludedSubjects' | 'requiredKeywords'

const draft = defineModel<AcquisitionCreatorDraft>({ required: true })
const props = defineProps<{ errors: AcquisitionValidationError[] }>()
const emit = defineEmits<{ changed: [] }>()

const HARD_TAG_FIELDS: Array<{ key: HardListKey; testId: string }> = [
  { key: 'excludedSubjects', testId: 'excluded-subjects' },
  { key: 'requiredKeywords', testId: 'required-keywords' },
]
const BUDGET_FIELDS: Array<{
  key: keyof AcquisitionSearchBudget
  min: number
  max: number
}> = [
  { key: 'maxKeywords', min: 1, max: 100 },
  { key: 'maxVideosPerKeyword', min: 1, max: 100 },
  { key: 'maxCommentsPerVideo', min: 1, max: 200 },
  { key: 'maxAuthorVideos', min: 1, max: 20 },
  { key: 'maxPages', min: 1, max: 100 },
  { key: 'maxDurationMinutes', min: 1, max: 1440 },
  { key: 'maxLlmCalls', min: 1, max: 1000 },
]

const hardTagDrafts = reactive<Record<HardListKey, string>>({
  excludedSubjects: '',
  requiredKeywords: '',
})
const hardTagErrors = reactive<Partial<Record<HardListKey, string>>>({})
const keywordDraft = reactive({
  text: '',
  language: draft.value.campaign.languages[0] ?? '',
  keywordType: 'industry',
})
const keywordState = reactive({ error: '' })
const keywordError = computed(() => keywordState.error)
const keywordValidationError = computed(() => (
  props.errors.find(error => error.field === 'keywords')?.code ?? ''
))

function notifyChange() {
  emit('changed')
}

function hardList(field: HardListKey) {
  return draft.value.campaign.hardConditions[field]
}

function addHardTag(field: HardListKey) {
  const result = addUniqueListItem(hardList(field), hardTagDrafts[field])
  if (result.error) {
    hardTagErrors[field] = result.error
    return
  }
  draft.value.campaign.hardConditions[field] = result.items
  hardTagDrafts[field] = ''
  delete hardTagErrors[field]
  notifyChange()
}

function removeHardTag(field: HardListKey, index: number) {
  draft.value.campaign.hardConditions[field] = hardList(field).filter((_, itemIndex) => itemIndex !== index)
  delete hardTagErrors[field]
  notifyChange()
}

function addKeyword() {
  const result = addUniqueKeyword(draft.value.keywords, {
    text: keywordDraft.text,
    language: keywordDraft.language,
    keywordType: keywordDraft.keywordType,
    source: 'manual',
    status: 'new',
  })
  if (result.error) {
    keywordState.error = result.error
    return
  }
  draft.value.keywords = result.items
  keywordDraft.text = ''
  keywordState.error = ''
  notifyChange()
}

function removeKeyword(index: number) {
  draft.value.keywords = draft.value.keywords.filter((_, itemIndex) => itemIndex !== index)
  keywordState.error = ''
  notifyChange()
}
</script>

<style scoped>
.strategy-step {
  display: grid;
  gap: 18px;
}

.section-heading,
.strategy-section > header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
}

.section-heading > div,
.strategy-section > header {
  align-items: center;
}

.section-heading > div {
  display: flex;
  gap: 10px;
}

.section-heading h4,
.strategy-section h5 {
  margin: 0;
  color: var(--fg);
}

.section-heading h4 {
  font-size: 15px;
}

.section-heading p,
.strategy-section header p {
  margin: 4px 0 0;
  color: var(--muted);
  font-size: 11px;
  line-height: 1.55;
}

.section-heading > p {
  max-width: 520px;
  text-align: right;
}

.section-kicker {
  display: inline-grid;
  width: 30px;
  height: 30px;
  place-items: center;
  border: 1px solid var(--border-strong);
  border-radius: 6px;
  color: var(--brand-deep);
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 700;
}

.condition-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.strategy-section {
  min-width: 0;
  padding: 16px;
  border: 1px solid var(--border);
  border-radius: 7px;
  background: var(--surface);
}

.strategy-section > header {
  justify-content: flex-start;
  margin-bottom: 14px;
}

.section-badge {
  display: inline-grid;
  min-width: 34px;
  height: 26px;
  place-items: center;
  border-radius: 5px;
  background: var(--err-soft);
  color: var(--err);
  font-family: var(--font-mono);
  font-size: 9px;
  font-weight: 700;
}

.section-badge.preference {
  background: var(--brand-soft);
  color: var(--brand-deep);
}

.compact-field + .compact-field {
  margin-top: 12px;
}

.compact-field > label,
.preference-fields label > span,
.keyword-editor label > span,
.budget-grid label > span,
.mix-grid label > span {
  display: block;
  margin-bottom: 6px;
  color: var(--fg);
  font-size: 11px;
  font-weight: 700;
}

.input-action-row {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 7px;
}

input,
select {
  width: 100%;
  min-height: 42px;
  box-sizing: border-box;
  padding: 9px 11px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--surface);
  color: var(--fg);
  font: inherit;
  font-size: 12px;
}

input:focus,
select:focus {
  border-color: var(--brand);
  box-shadow: 0 0 0 3px var(--brand-soft);
  outline: 0;
}

.btn {
  min-height: 42px;
}

.tag-list,
.keyword-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}

.tag,
.keyword-list article {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid var(--border);
  border-radius: 5px;
  background: var(--surface-2);
}

.tag {
  padding: 5px 7px;
  font-size: 10.5px;
}

.tag button,
.keyword-list button {
  display: inline-grid;
  width: 28px;
  height: 28px;
  place-items: center;
  padding: 0;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: var(--muted);
  cursor: pointer;
}

.phase02-notice {
  margin-bottom: 13px;
  padding: 10px 11px;
  border-left: 3px solid var(--brand);
  background: var(--brand-soft);
  color: var(--fg);
  font-size: 10.5px;
  line-height: 1.6;
}

.preference-fields,
.budget-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 11px;
}

.keyword-editor {
  display: grid;
  grid-template-columns: minmax(220px, 2fr) minmax(110px, .8fr) minmax(130px, 1fr) auto;
  align-items: end;
  gap: 8px;
}

.keyword-list article {
  justify-content: space-between;
  min-width: 180px;
  padding: 7px 7px 7px 10px;
}

.keyword-list strong,
.keyword-list small {
  display: block;
}

.keyword-list strong {
  color: var(--fg);
  font-size: 11px;
}

.keyword-list small {
  margin-top: 2px;
  color: var(--muted);
  font-size: 9px;
}

.budget-grid {
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

.mix-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(120px, 180px)) 1fr;
  align-items: end;
  gap: 11px;
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px solid var(--border);
}

.mix-grid p {
  margin: 0 0 8px;
  color: var(--muted);
  font-size: 10.5px;
}

.field-error {
  margin: 7px 0 0;
  color: var(--err);
  font-size: 10.5px;
}

.strategy-errors {
  padding: 12px 14px;
  border: 1px solid color-mix(in oklch, var(--err) 30%, var(--border));
  border-radius: 6px;
  background: var(--err-soft);
  color: var(--err);
  font-size: 11px;
}

.strategy-errors ul {
  margin: 5px 0 0;
  padding-left: 18px;
}

@media (max-width: 900px) {
  .condition-grid,
  .keyword-editor,
  .budget-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .section-heading {
    display: block;
  }

  .section-heading > p {
    margin-top: 8px;
    text-align: left;
  }
}

@media (max-width: 560px) {
  .condition-grid,
  .preference-fields,
  .keyword-editor,
  .budget-grid,
  .mix-grid {
    grid-template-columns: 1fr;
  }

  input,
  select,
  .btn,
  .tag button,
  .keyword-list button {
    min-height: 44px;
  }

  .tag button,
  .keyword-list button {
    width: 44px;
  }
}

@media (prefers-reduced-motion: reduce) {
  input,
  select {
    transition: none;
  }
}
</style>
