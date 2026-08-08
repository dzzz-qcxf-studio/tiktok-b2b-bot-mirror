<template>
  <section class="card acquisition-creator">
    <header class="creator-head">
      <div>
        <p class="eyebrow">{{ $t('pipeline.acquisition.eyebrow') }}</p>
        <h3>{{ $t('pipeline.acquisition.title') }}</h3>
        <p class="hint">{{ $t('pipeline.acquisition.hint') }}</p>
      </div>
      <span class="system-mark">{{ $t('pipeline.singleSystem') }}</span>
    </header>

    <nav class="step-nav" :aria-label="$t('pipeline.acquisition.stepNavigation')">
      <button
        v-for="(step, index) in STEP_KEYS"
        :key="step"
        :data-testid="`acquisition-step-${index}`"
        class="step-button"
        :class="{ active: activeStep === index, completed: activeStep > index }"
        type="button"
        :aria-current="activeStep === index ? 'step' : undefined"
        :disabled="index > activeStep"
        @click="activeStep = index"
      >
        <span>{{ String(index + 1).padStart(2, '0') }}</span>
        {{ $t(`pipeline.acquisition.steps.${step}`) }}
      </button>
    </nav>

    <div class="step-panel">
      <template v-if="activeStep === 0">
        <div class="section-heading">
          <div>
            <span class="section-kicker">01</span>
            <h4>{{ $t('pipeline.acquisition.executionTitle') }}</h4>
          </div>
          <p>{{ $t('pipeline.acquisition.executionHint') }}</p>
        </div>

        <div class="choice-grid">
          <fieldset class="field-block">
            <legend>{{ $t('pipeline.platform') }}</legend>
            <div class="segmented" role="radiogroup" :aria-label="$t('pipeline.platform')">
              <button
                data-testid="acquisition-platform-tiktok"
                type="button"
                role="radio"
                :class="{ active: draft.platform === 'tiktok' }"
                :aria-checked="draft.platform === 'tiktok'"
                @click="selectPlatform('tiktok')"
              >
                <span class="platform-code">TT</span>
                TikTok
              </button>
              <button
                data-testid="acquisition-platform-douyin"
                type="button"
                role="radio"
                :class="{ active: draft.platform === 'douyin' }"
                :aria-checked="draft.platform === 'douyin'"
                @click="selectPlatform('douyin')"
              >
                <span class="platform-code">DY</span>
                {{ $t('pipeline.douyin') }}
              </button>
            </div>
          </fieldset>

          <fieldset class="field-block">
            <legend>{{ $t('pipeline.accountStrategy') }}</legend>
            <div class="segmented" role="radiogroup" :aria-label="$t('pipeline.accountStrategy')">
              <button
                data-testid="acquisition-account-auto"
                type="button"
                role="radio"
                :class="{ active: draft.accountMode === 'auto' }"
                :aria-checked="draft.accountMode === 'auto'"
                @click="selectAccountMode('auto')"
              >
                {{ $t('pipeline.accountAuto') }}
              </button>
              <button
                data-testid="acquisition-account-specified"
                type="button"
                role="radio"
                :class="{ active: draft.accountMode === 'specified' }"
                :aria-checked="draft.accountMode === 'specified'"
                @click="selectAccountMode('specified')"
              >
                {{ $t('pipeline.accountSpecified') }}
              </button>
            </div>
          </fieldset>

          <div v-if="draft.accountMode === 'specified'" class="field-block">
            <label for="acquisition-account">{{ $t('pipeline.account') }}</label>
            <select
              id="acquisition-account"
              v-model.number="draft.accountId"
              data-testid="acquisition-account-select"
              :disabled="accountsLoading"
            >
              <option :value="null">{{ $t('pipeline.selectAccount') }}</option>
              <option v-for="account in loggedInAccounts" :key="account.id" :value="account.id">
                {{ account.nickname || account.username }} · @{{ account.username }}
              </option>
            </select>
          </div>
        </div>

        <div
          data-testid="acquisition-preflight"
          class="preflight"
          :class="preflightClass"
          :role="capability?.available ? 'status' : 'alert'"
        >
          <span class="signal"></span>
          <div>
            <strong v-if="capabilitiesLoading">{{ $t('pipeline.checkingCapability') }}</strong>
            <strong v-else-if="capabilitiesError">{{ $t('pipeline.capabilityError') }}</strong>
            <strong v-else-if="capability?.available">{{ $t('pipeline.preflightReady') }}</strong>
            <strong v-else>{{ $t('pipeline.preflightBlocked') }}</strong>
            <small v-if="capabilitiesError">{{ capabilitiesError }}</small>
            <small v-else-if="capability?.message">{{ capability.message }}</small>
          </div>
          <button
            v-if="capabilitiesError"
            class="text-action"
            type="button"
            @click="loadCapabilities"
          >
            {{ $t('common.retry') }}
          </button>
        </div>

        <p v-if="accountsError" class="inline-error" role="alert">{{ accountsError }}</p>

        <fieldset class="stage-fieldset">
          <legend>{{ $t('pipeline.stages') }}</legend>
          <p>{{ $t('pipeline.acquisition.stageHint') }}</p>
          <div class="stage-grid">
            <label
              v-for="(stage, index) in PIPELINE_STAGE_ORDER"
              :key="stage"
              class="stage-option"
              :class="{ selected: draft.stages.includes(stage) }"
            >
              <input
                v-model="draft.stages"
                :data-testid="`acquisition-stage-${stage}`"
                type="checkbox"
                :value="stage"
                :disabled="stage === 'collect'"
                @change="normalizeStages"
              >
              <span class="stage-number">{{ String(index + 1).padStart(2, '0') }}</span>
              <span>
                <b>{{ $t(`pipeline.${stage}`) }}</b>
                <small>{{ $t(`pipeline.${stage}Short`) }}</small>
              </span>
            </label>
          </div>
        </fieldset>
      </template>

      <template v-else-if="activeStep === 1">
        <div class="section-heading">
          <div>
            <span class="section-kicker">02</span>
            <h4>{{ $t('pipeline.acquisition.targetTitle') }}</h4>
          </div>
          <p>{{ $t('pipeline.acquisition.targetHint') }}</p>
        </div>

        <div class="target-grid">
          <div v-if="draft.platform === 'douyin'" class="tag-field">
            <label for="acquisition-douyin-country">
              {{ $t('pipeline.acquisition.fields.countries') }}
            </label>
            <input
              id="acquisition-douyin-country"
              data-testid="acquisition-douyin-country"
              value="CN"
              disabled
            >
            <small>{{ $t('pipeline.acquisition.douyinCountryHint') }}</small>
          </div>

          <div
            v-for="field in visibleTargetFields"
            :key="field.key"
            class="tag-field"
          >
            <label :for="`acquisition-${field.testId}`">
              {{ $t(`pipeline.acquisition.fields.${field.key}`) }}
              <span v-if="field.required" aria-hidden="true">*</span>
            </label>
            <div class="tag-input-row">
              <input
                :id="`acquisition-${field.testId}`"
                v-model="tagDrafts[field.key]"
                :data-testid="`acquisition-${field.testId}-input`"
                :placeholder="$t(`pipeline.acquisition.placeholders.${field.key}`)"
                @keydown.enter.prevent="addTag(field.key)"
              >
              <button class="btn add-button" type="button" @click="addTag(field.key)">
                {{ $t('common.add') }}
              </button>
            </div>
            <div v-if="listFor(field.key).length" class="tag-list">
              <span v-for="(item, index) in listFor(field.key)" :key="item" class="tag">
                {{ item }}
                <button
                  type="button"
                  :data-testid="`acquisition-${field.testId}-remove-${index}`"
                  :aria-label="$t('pipeline.acquisition.removeTag', { item })"
                  @click="removeTag(field.key, index)"
                >
                  ×
                </button>
              </span>
            </div>
            <small
              v-if="tagErrors[field.key]"
              :data-testid="`acquisition-${field.testId}-error`"
              class="field-error"
              role="alert"
            >
              {{ $t(`pipeline.acquisition.errors.${tagErrors[field.key]}`) }}
            </small>
            <small v-else>{{ $t(`pipeline.acquisition.fieldHints.${field.key}`) }}</small>
          </div>
        </div>
      </template>

      <template v-else>
        <div class="placeholder-panel" role="status">
          <span class="section-kicker">{{ String(activeStep + 1).padStart(2, '0') }}</span>
          <h4>{{ $t(`pipeline.acquisition.steps.${STEP_KEYS[activeStep]}`) }}</h4>
          <p>{{ $t('pipeline.acquisition.nextStagePlaceholder') }}</p>
        </div>
      </template>

      <div v-if="visibleErrors.length" class="form-error" role="alert">
        <strong>{{ $t('pipeline.acquisition.fixErrors') }}</strong>
        <ul>
          <li v-for="error in visibleErrors" :key="error.field + error.code">
            {{ $t(`pipeline.acquisition.errors.${error.code}`) }}
          </li>
        </ul>
      </div>

      <footer class="step-footer">
        <button
          v-if="activeStep > 0"
          class="btn"
          type="button"
          @click="goBack"
        >
          {{ $t('pipeline.acquisition.previous') }}
        </button>
        <span v-else></span>
        <button
          v-if="activeStep < 2"
          data-testid="acquisition-next"
          class="btn brand"
          type="button"
          @click="goNext"
        >
          {{ $t('pipeline.acquisition.next') }}
        </button>
      </footer>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { getAccounts, getPipelineCapabilities } from '../api'
import type {
  AccountMode,
  PipelineCapabilities,
  PipelinePlatform,
  PipelineStageName,
} from '../types/pipeline'
import {
  PIPELINE_STAGE_ORDER,
  addUniqueListItem,
  applyPlatformDefaults,
  createAcquisitionDraft,
  normalizeSelectedStages,
  validateExecutionScope,
  validateTargetProfile,
  type AcquisitionValidationError,
} from './acquisitionCreator'

interface SocialAccount {
  id: number
  platform: PipelinePlatform
  username: string
  nickname?: string
  status: string
}

type TargetListKey =
  | 'countries'
  | 'languages'
  | 'industries'
  | 'products'
  | 'customerRoles'
  | 'excludedTargets'

interface TargetField {
  key: TargetListKey
  testId: string
  required: boolean
  tiktokOnly?: boolean
}

const emit = defineEmits<{
  accountsLoaded: [accounts: SocialAccount[]]
}>()

const { t } = useI18n()
const STEP_KEYS = ['execution', 'target', 'strategy', 'confirm'] as const
const TARGET_FIELDS: TargetField[] = [
  { key: 'countries', testId: 'countries', required: true, tiktokOnly: true },
  { key: 'languages', testId: 'languages', required: false },
  { key: 'industries', testId: 'industries', required: true },
  { key: 'products', testId: 'products', required: false },
  { key: 'customerRoles', testId: 'customer-roles', required: true },
  { key: 'excludedTargets', testId: 'excluded-targets', required: false },
]

const activeStep = ref(0)
const draft = ref(createAcquisitionDraft('douyin'))
const visibleErrors = ref<AcquisitionValidationError[]>([])
const tagDrafts = reactive<Record<TargetListKey, string>>({
  countries: '',
  languages: '',
  industries: '',
  products: '',
  customerRoles: '',
  excludedTargets: '',
})
const tagErrors = reactive<Partial<Record<TargetListKey, string>>>({})
const capabilities = ref<PipelineCapabilities | null>(null)
const capabilitiesLoading = ref(false)
const capabilitiesError = ref('')
const accounts = ref<SocialAccount[]>([])
const accountsLoading = ref(false)
const accountsError = ref('')
let accountsRequestToken = 0

const capability = computed(() => capabilities.value?.platforms[draft.value.platform] ?? null)
const loggedInAccounts = computed(() => accounts.value.filter(account =>
  account.platform === draft.value.platform && account.status === 'logged_in',
))
const visibleTargetFields = computed(() => TARGET_FIELDS.filter(field =>
  !field.tiktokOnly || draft.value.platform === 'tiktok',
))
const preflightClass = computed(() => {
  if (capabilitiesLoading.value) return 'neutral'
  if (capabilitiesError.value || !capability.value?.available) return 'blocked'
  return 'ready'
})

function extractError(error: unknown, fallback: string) {
  const candidate = error as {
    message?: string
    response?: { data?: { detail?: string | { code?: string; message?: string } } }
  }
  const detail = candidate.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (detail && typeof detail === 'object') {
    return [detail.code, detail.message].filter(Boolean).join(' · ')
  }
  return candidate.message || fallback
}

async function loadCapabilities() {
  capabilitiesLoading.value = true
  capabilitiesError.value = ''
  try {
    const { data } = await getPipelineCapabilities()
    capabilities.value = data
  } catch (error) {
    capabilities.value = null
    capabilitiesError.value = extractError(error, t('pipeline.capabilityError'))
  } finally {
    capabilitiesLoading.value = false
  }
}

async function loadAccounts(platform: PipelinePlatform) {
  const requestToken = ++accountsRequestToken
  accountsLoading.value = true
  accountsError.value = ''
  try {
    const { data } = await getAccounts(platform)
    if (requestToken !== accountsRequestToken || platform !== draft.value.platform) return
    accounts.value = Array.isArray(data) ? data : []
    emit('accountsLoaded', [...accounts.value])
  } catch (error) {
    if (requestToken !== accountsRequestToken || platform !== draft.value.platform) return
    accounts.value = []
    accountsError.value = extractError(error, t('pipeline.accountsError'))
  } finally {
    if (requestToken === accountsRequestToken) accountsLoading.value = false
  }
}

function selectPlatform(platform: PipelinePlatform) {
  if (platform === draft.value.platform) return
  draft.value = applyPlatformDefaults(draft.value, platform)
  visibleErrors.value = []
  Object.keys(tagErrors).forEach(key => delete tagErrors[key as TargetListKey])
  void loadAccounts(platform)
}

function selectAccountMode(mode: AccountMode) {
  draft.value.accountMode = mode
  if (mode === 'auto') draft.value.accountId = null
  visibleErrors.value = []
}

function normalizeStages() {
  draft.value.stages = normalizeSelectedStages(draft.value.stages as PipelineStageName[])
}

function listFor(field: TargetListKey): string[] {
  return draft.value.campaign[field]
}

function addTag(field: TargetListKey) {
  const result = addUniqueListItem(listFor(field), tagDrafts[field])
  if (result.error) {
    tagErrors[field] = result.error
    return
  }
  draft.value.campaign[field] = result.items
  tagDrafts[field] = ''
  delete tagErrors[field]
  visibleErrors.value = visibleErrors.value.filter(error => error.field !== field)
}

function removeTag(field: TargetListKey, index: number) {
  draft.value.campaign[field] = listFor(field).filter((_, itemIndex) => itemIndex !== index)
  delete tagErrors[field]
}

function executionErrors(): AcquisitionValidationError[] {
  const errors = validateExecutionScope(draft.value)
  if (capabilitiesLoading.value || !capability.value?.available) {
    errors.push({ field: 'capability', code: 'capability_unavailable' })
  }
  if (!accountsLoading.value && loggedInAccounts.value.length === 0) {
    errors.push({ field: 'account', code: 'available_account_required' })
  }
  if (
    draft.value.accountMode === 'specified'
    && !loggedInAccounts.value.some(account => account.id === draft.value.accountId)
  ) {
    errors.push({ field: 'accountId', code: 'account_required' })
  }
  return deduplicateErrors(errors)
}

function goNext() {
  const errors = activeStep.value === 0
    ? executionErrors()
    : validateTargetProfile(draft.value)
  visibleErrors.value = errors
  if (errors.length > 0) return
  if (activeStep.value === 0) normalizeStages()
  activeStep.value += 1
}

function goBack() {
  if (activeStep.value === 0) return
  activeStep.value -= 1
  visibleErrors.value = []
}

function deduplicateErrors(errors: AcquisitionValidationError[]) {
  const seen = new Set<string>()
  return errors.filter((error) => {
    const key = error.field + error.code
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

onMounted(async () => {
  await Promise.all([
    loadCapabilities(),
    loadAccounts(draft.value.platform),
  ])
})
</script>

<style scoped>
.acquisition-creator {
  overflow: hidden;
}

.creator-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 24px;
  padding: 22px 24px 18px;
  border-bottom: 1px solid var(--border);
}

.creator-head h3,
.section-heading h4,
.placeholder-panel h4 {
  margin: 0;
  color: var(--fg);
}

.creator-head h3 {
  font-size: 17px;
}

.eyebrow,
.section-kicker,
.system-mark {
  font-family: var(--font-mono);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: .09em;
  text-transform: uppercase;
}

.eyebrow {
  margin: 0 0 5px;
  color: var(--brand-deep);
}

.hint,
.section-heading p,
.placeholder-panel p {
  margin: 5px 0 0;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.6;
  text-wrap: pretty;
}

.system-mark {
  flex: 0 0 auto;
  padding: 6px 8px;
  border: 1px solid var(--border);
  border-radius: 5px;
  color: var(--muted);
}

.step-nav {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 1px;
  background: var(--border);
  border-bottom: 1px solid var(--border);
}

.step-button {
  min-height: 52px;
  padding: 10px 14px;
  border: 0;
  background: var(--surface);
  color: var(--muted);
  font: inherit;
  font-size: 12px;
  text-align: left;
  cursor: pointer;
  transition: background-color 180ms ease, color 180ms ease;
}

.step-button span {
  display: block;
  margin-bottom: 2px;
  color: var(--muted-2);
  font-family: var(--font-mono);
  font-size: 9px;
}

.step-button.active {
  background: var(--brand-soft);
  color: var(--brand-deep);
  font-weight: 700;
}

.step-button.completed {
  color: var(--fg);
}

.step-button:disabled {
  cursor: not-allowed;
  opacity: .58;
}

.step-button:focus-visible,
.segmented button:focus-visible,
.tag button:focus-visible,
input:focus-visible,
select:focus-visible,
.btn:focus-visible {
  outline: 2px solid var(--brand);
  outline-offset: 2px;
}

.step-panel {
  padding: 24px;
}

.section-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 20px;
}

.section-heading > div {
  display: flex;
  align-items: center;
  gap: 10px;
}

.section-heading h4,
.placeholder-panel h4 {
  font-size: 15px;
}

.section-heading p {
  max-width: 520px;
  margin-top: 0;
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
}

.choice-grid,
.target-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px;
}

fieldset {
  min-width: 0;
  margin: 0;
  padding: 0;
  border: 0;
}

legend,
.field-block > label,
.tag-field > label,
.stage-fieldset > legend {
  display: block;
  margin-bottom: 8px;
  color: var(--fg);
  font-size: 12px;
  font-weight: 700;
}

.segmented {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 6px;
}

.segmented button {
  min-height: 44px;
  padding: 9px 11px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--surface);
  color: var(--muted);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
  transition: border-color 180ms ease, background-color 180ms ease, color 180ms ease;
}

.segmented button.active {
  border-color: var(--brand);
  background: var(--brand-soft);
  color: var(--brand-deep);
  font-weight: 700;
}

.platform-code {
  margin-right: 5px;
  font-family: var(--font-mono);
  font-size: 9px;
}

input,
select {
  width: 100%;
  min-height: 42px;
  box-sizing: border-box;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--surface);
  color: var(--fg);
  font: inherit;
  font-size: 12px;
  padding: 9px 11px;
  transition: border-color 180ms ease, box-shadow 180ms ease;
}

input:focus,
select:focus {
  border-color: var(--brand);
  box-shadow: 0 0 0 3px var(--brand-soft);
}

input:disabled,
select:disabled {
  background: var(--surface-2);
  color: var(--muted);
  cursor: not-allowed;
}

.preflight {
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: center;
  gap: 10px;
  margin-top: 18px;
  padding: 11px 12px;
  border: 1px solid var(--border);
  border-radius: 6px;
}

.preflight .signal {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--muted-2);
}

.preflight strong,
.preflight small {
  display: block;
}

.preflight strong {
  color: var(--fg);
  font-size: 12px;
}

.preflight small {
  margin-top: 2px;
  color: var(--muted);
}

.preflight.ready {
  border-color: color-mix(in oklch, var(--ok) 30%, var(--border));
  background: var(--ok-soft);
}

.preflight.ready .signal {
  background: var(--ok);
}

.preflight.blocked {
  border-color: color-mix(in oklch, var(--err) 30%, var(--border));
  background: var(--err-soft);
}

.preflight.blocked .signal {
  background: var(--err);
}

.text-action {
  border: 0;
  background: none;
  color: var(--brand-deep);
  cursor: pointer;
  font: inherit;
  font-size: 11px;
  font-weight: 700;
}

.stage-fieldset {
  margin-top: 22px;
  padding-top: 18px;
  border-top: 1px solid var(--border);
}

.stage-fieldset > p {
  margin: -3px 0 10px;
  color: var(--muted);
  font-size: 11px;
}

.stage-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 7px;
}

.stage-option {
  position: relative;
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 8px;
  min-width: 0;
  min-height: 54px;
  align-items: center;
  padding: 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--muted);
  cursor: pointer;
  transition: border-color 180ms ease, background-color 180ms ease, transform 180ms ease;
}

.stage-option:hover {
  border-color: var(--border-strong);
  transform: translateY(-1px);
}

.stage-option.selected {
  border-color: color-mix(in oklch, var(--brand) 45%, var(--border));
  background: var(--brand-soft);
  color: var(--fg);
}

.stage-option input {
  position: absolute;
  width: 1px;
  height: 1px;
  min-height: 0;
  opacity: 0;
}

.stage-number {
  color: var(--brand-deep);
  font-family: var(--font-mono);
  font-size: 9px;
}

.stage-option b,
.stage-option small {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.stage-option b {
  font-size: 11px;
}

.stage-option small {
  margin-top: 2px;
  color: var(--muted);
  font-size: 9px;
}

.tag-field {
  min-width: 0;
  padding: 15px;
  border: 1px solid var(--border);
  border-radius: 7px;
  background: var(--surface);
}

.tag-field > small {
  display: block;
  margin-top: 8px;
  color: var(--muted);
  font-size: 10px;
  line-height: 1.5;
}

.tag-field label span {
  color: var(--err);
}

.tag-input-row {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 7px;
}

.add-button {
  min-width: 62px;
  min-height: 42px;
}

.tag-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 9px;
}

.tag {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  max-width: 100%;
  padding: 5px 7px;
  border: 1px solid var(--border);
  border-radius: 5px;
  background: var(--surface-2);
  color: var(--fg);
  font-size: 10.5px;
  overflow-wrap: anywhere;
}

.tag button {
  display: inline-grid;
  width: 22px;
  height: 22px;
  place-items: center;
  padding: 0;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: var(--muted);
  cursor: pointer;
}

.tag button:hover {
  background: var(--err-soft);
  color: var(--err);
}

.field-error,
.inline-error {
  color: var(--err) !important;
}

.placeholder-panel {
  min-height: 190px;
  display: grid;
  place-items: center;
  align-content: center;
  text-align: center;
}

.placeholder-panel .section-kicker {
  margin-bottom: 12px;
}

.form-error {
  margin-top: 18px;
  padding: 12px 14px;
  border: 1px solid color-mix(in oklch, var(--err) 30%, var(--border));
  border-radius: 6px;
  background: var(--err-soft);
  color: var(--err);
  font-size: 11px;
}

.form-error ul {
  margin: 5px 0 0;
  padding-left: 18px;
}

.step-footer {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  margin-top: 22px;
  padding-top: 18px;
  border-top: 1px solid var(--border);
}

.btn {
  min-height: 42px;
}

@media (max-width: 900px) {
  .choice-grid,
  .target-grid {
    grid-template-columns: 1fr;
  }

  .stage-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .section-heading {
    display: block;
  }

  .section-heading p {
    margin-top: 8px;
    text-align: left;
  }
}

@media (max-width: 560px) {
  .creator-head,
  .step-panel {
    padding: 18px 14px;
  }

  .creator-head {
    display: block;
  }

  .system-mark {
    display: inline-block;
    margin-top: 12px;
  }

  .step-nav {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .step-button {
    min-height: 48px;
  }

  .stage-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .preflight {
    grid-template-columns: auto 1fr;
  }

  .preflight .text-action {
    grid-column: 2;
    justify-self: start;
  }
}

@media (prefers-reduced-motion: reduce) {
  .step-button,
  .segmented button,
  input,
  select,
  .stage-option {
    transition: none;
  }
}
</style>
