<template>
  <div class="page llm-page">
    <div class="page-head">
      <div>
        <h1>{{ $t('llm.title') }}</h1>
        <p>{{ $t('llm.subtitle') }}</p>
      </div>
      <div class="head-actions">
        <button class="btn" :disabled="loading" @click="loadAll">{{ $t('llm.refresh') }}</button>
        <button class="btn brand" @click="openCreate">{{ $t('llm.addProvider') }}</button>
      </div>
    </div>

    <div v-if="serviceError" class="notice error-notice" role="alert">
      <div>
        <b>{{ $t('llm.serviceUnavailable') }}</b>
        <p>{{ serviceError }}</p>
      </div>
      <button class="btn sm" @click="loadAll">{{ $t('llm.retry') }}</button>
    </div>

    <div class="summary-grid" aria-label="LLM usage summary">
      <div class="card metric-card">
        <span>{{ $t('llm.providerMetric') }}</span>
        <strong>{{ providers.length }}</strong>
        <small>{{ $t('llm.configuredKeys', { n: configuredCount }) }}</small>
      </div>
      <div class="card metric-card">
        <span>{{ $t('llm.requestCount') }}</span>
        <strong>{{ usage.requestCount.toLocaleString() }}</strong>
        <small>{{ $t('llm.successFailure', { success: usage.successCount, failure: usage.failureCount }) }}</small>
      </div>
      <div class="card metric-card">
        <span>{{ $t('llm.totalTokens') }}</span>
        <strong>{{ usage.totalTokens.toLocaleString() }}</strong>
        <small>{{ $t('llm.inputOutputTokens', { input: usage.inputTokens, output: usage.outputTokens }) }}</small>
      </div>
      <div class="card metric-card">
        <span>{{ $t('llm.averageLatency') }}</span>
        <strong>{{ Math.round(usage.averageLatencyMs) }}<em> ms</em></strong>
        <small>{{ $t('llm.successRateValue', { rate: successRate }) }}</small>
      </div>
    </div>

    <section class="card section-card">
      <div class="card-hd section-head">
        <div>
          <h3>{{ $t('llm.providersSection') }}</h3>
          <span class="hint">{{ $t('llm.providersSecurityHint') }}</span>
        </div>
        <span class="count-chip">{{ $t('llm.count', { n: providers.length }) }}</span>
      </div>

      <div v-if="loading" class="state-box">{{ $t('llm.loadingProviders') }}</div>
      <div v-else-if="providers.length === 0" class="state-box empty-state">
        <b>{{ $t('llm.emptyProviderTitle') }}</b>
        <span>{{ $t('llm.emptyProviderDesc') }}</span>
        <button class="btn brand" @click="openCreate">{{ $t('llm.addFirstProvider') }}</button>
      </div>
      <div v-else class="provider-grid">
        <article v-for="provider in providers" :key="provider.id" class="provider-card">
          <div class="provider-top">
            <div class="provider-avatar">{{ initials(provider.displayName) }}</div>
            <div class="provider-title">
              <h4>{{ provider.displayName }}</h4>
              <code>{{ provider.name }}</code>
            </div>
            <span :class="['status-pill', provider.enabled ? 'enabled' : 'disabled']">
              {{ provider.enabled ? $t('llm.enabled') : $t('llm.disabled') }}
            </span>
          </div>
          <dl class="provider-details">
            <div><dt>{{ $t('llm.model') }}</dt><dd>{{ provider.defaultModel }}</dd></div>
            <div><dt>{{ $t('llm.baseUrl') }}</dt><dd :title="provider.baseUrl">{{ provider.baseUrl }}</dd></div>
            <div><dt>{{ $t('llm.secretEnv') }}</dt><dd>{{ provider.apiKeyEnv }}</dd></div>
            <div>
              <dt>{{ $t('llm.secretStatus') }}</dt>
              <dd :class="provider.configured ? 'text-ok' : 'text-warn'">
                {{ provider.configured ? $t('llm.configured') : $t('llm.unconfigured') }}
              </dd>
            </div>
          </dl>
          <div v-if="testResults[provider.id]" class="test-result" :class="testResults[provider.id]?.reachable ? 'ok' : 'bad'">
            <span>{{ testResults[provider.id]?.reachable ? $t('llm.connectionSuccess') : errorCategoryText(testResults[provider.id]?.errorCategory) }}</span>
            <b>{{ Math.round(testResults[provider.id]?.latencyMs || 0) }} ms</b>
          </div>
          <div class="provider-actions">
            <button class="btn sm" :disabled="testingId === provider.id" @click="runTest(provider)">
              {{ testingId === provider.id ? $t('llm.testing') : $t('llm.testConnection') }}
            </button>
            <button class="btn sm" @click="openEdit(provider)">{{ $t('llm.edit') }}</button>
            <button class="btn sm danger-text" :disabled="deletingId === provider.id" @click="removeProvider(provider)">
              {{ deletingId === provider.id ? $t('llm.deleting') : $t('llm.delete') }}
            </button>
          </div>
        </article>
      </div>
    </section>

    <div
      v-if="formOpen"
      ref="editorOverlay"
      class="provider-editor-overlay"
      tabindex="-1"
      @click.self="closeForm"
      @keydown.esc="closeForm"
      @keydown.tab="trapEditorFocus"
    >
    <section
      class="card editor-card"
      role="dialog"
      aria-modal="true"
      aria-label="Provider editor"
    >
      <div class="editor-head">
        <div>
          <span class="eyebrow">{{ editingId ? $t('llm.editProvider') : $t('llm.newProvider') }}</span>
          <h3>{{ editingId ? form.displayName || 'Provider' : $t('llm.connectProvider') }}</h3>
        </div>
        <button class="btn sm ghost" @click="closeForm">{{ $t('llm.close') }}</button>
      </div>

      <div class="preset-row">
        <button
          v-for="preset in presets"
          :key="preset.key"
          type="button"
          :class="['preset-button', { active: selectedPreset === preset.key }]"
          @click="applyPreset(preset.key)"
        >
          {{ preset.key === 'custom' ? $t('llm.customPreset') : preset.label }}
        </button>
      </div>

      <form class="provider-form" @submit.prevent="saveProvider">
        <label>
          <span>{{ $t('llm.displayName') }}</span>
          <input ref="firstProviderInput" v-model.trim="form.displayName" data-testid="provider-display-name" class="input" maxlength="160" required :placeholder="$t('llm.displayNamePlaceholder')">
        </label>
        <label>
          <span>{{ $t('llm.uniqueName') }}</span>
          <input v-model.trim="form.name" class="input mono" maxlength="100" required :placeholder="$t('llm.uniqueNamePlaceholder')">
        </label>
        <label class="wide">
          <span>{{ $t('llm.baseUrl') }}</span>
          <input v-model.trim="form.baseUrl" class="input mono" maxlength="500" required placeholder="https://api.deepseek.com/v1">
        </label>
        <label>
          <span>{{ $t('llm.defaultModel') }}</span>
          <input v-model.trim="form.defaultModel" class="input mono" maxlength="200" required :placeholder="$t('llm.defaultModelPlaceholder')">
        </label>
        <label>
          <span>{{ $t('llm.secretEnvName') }}</span>
          <input v-model.trim="form.apiKeyEnv" class="input mono" maxlength="160" required :placeholder="$t('llm.secretEnvNamePlaceholder')">
        </label>
        <label>
          <span>{{ $t('llm.timeoutSeconds') }}</span>
          <input v-model.number="form.timeoutSeconds" class="input" type="number" min="1" max="86400" required>
        </label>
        <label class="toggle-field">
          <input v-model="form.enabled" type="checkbox">
          <span>{{ $t('llm.enableProvider') }}</span>
        </label>
        <label class="wide secret-field">
          <span>{{ $t('llm.apiKey') }} <i>{{ editingId ? $t('llm.apiKeyRetain') : $t('llm.apiKeyLater') }}</i></span>
          <input v-model="form.apiKey" class="input mono" type="password" autocomplete="new-password" :placeholder="$t('llm.apiKeyPlaceholder')">
          <small>{{ $t('llm.apiKeySecurityHint') }}</small>
        </label>
        <div v-if="formError" class="form-error wide">{{ formError }}</div>
        <div class="form-actions wide">
          <button type="button" class="btn" :disabled="saving" @click="closeForm">{{ $t('llm.cancel') }}</button>
          <button type="submit" data-testid="save-provider" class="btn brand" :disabled="saving || !formValid">
            {{ saving ? $t('llm.saving') : $t('llm.saveProvider') }}
          </button>
        </div>
      </form>
    </section>
    </div>

    <section class="card section-card routes-section">
      <div class="card-hd section-head">
        <div>
          <h3>{{ $t('llm.routesSection') }}</h3>
          <span class="hint">{{ $t('llm.routesHint') }}</span>
        </div>
        <span class="count-chip">{{ $t('llm.routeCount', { n: 5 }) }}</span>
      </div>

      <div v-if="loading" class="state-box">{{ $t('llm.loadingRoutes') }}</div>
      <div v-else-if="!routesReady" class="state-box empty-state" role="alert">
        <b>{{ $t('llm.routesUnavailable') }}</b>
        <span>{{ $t('llm.routesUnavailableHint') }}</span>
        <button class="btn" @click="loadAll">{{ $t('llm.retry') }}</button>
      </div>
      <div v-else class="route-grid">
        <article v-for="route in routeCards" :key="route.key" class="route-card">
          <div class="route-head">
            <div>
              <span class="route-index">{{ route.order }}</span>
              <h4>{{ $t(route.labelKey) }}</h4>
              <p>{{ $t(route.descriptionKey) }}</p>
            </div>
            <span>{{ $t('llm.nodeCount', { n: route.entries.length }) }}</span>
          </div>

          <div v-if="route.entries.length === 0" class="route-empty">{{ $t('llm.routeNotConfigured') }}</div>
          <div v-else class="route-chain">
            <div v-for="(entry, index) in route.entries" :key="`${entry.providerId}-${index}`" class="route-entry">
              <span class="priority">{{ index + 1 }}</span>
              <div class="route-provider">
                <b>{{ providerName(entry.providerId) }}</b>
                <input v-model.trim="entry.modelOverride" class="mini-input mono" :placeholder="$t('llm.modelOverridePlaceholder')">
              </div>
              <label class="mini-toggle"><input v-model="entry.enabled" type="checkbox">{{ $t('llm.enable') }}</label>
              <div class="order-actions">
                <button class="icon-button" :disabled="index === 0" :title="$t('llm.moveUp')" @click="moveRouteEntry(route, index, -1)">↑</button>
                <button class="icon-button" :disabled="index === route.entries.length - 1" :title="$t('llm.moveDown')" @click="moveRouteEntry(route, index, 1)">↓</button>
                <button class="icon-button remove" :title="$t('llm.remove')" @click="removeRouteEntry(route, index)">×</button>
              </div>
            </div>
          </div>

          <div class="route-footer">
            <select v-model="route.pendingProviderId" class="input compact-select">
              <option value="">{{ $t('llm.addProviderOption') }}</option>
              <option v-for="provider in availableProviders(route)" :key="provider.id" :value="provider.id">
                {{ provider.displayName }}
              </option>
            </select>
            <button class="btn sm" :disabled="!route.pendingProviderId" @click="addRouteEntry(route)">{{ $t('llm.join') }}</button>
            <button class="btn sm brand" :disabled="savingRoute === route.key" @click="saveRoute(route)">
              {{ savingRoute === route.key ? $t('llm.saving') : $t('llm.saveRoute') }}
            </button>
          </div>
          <p v-if="routeErrors[route.key]" class="route-error">{{ routeErrors[route.key] }}</p>
        </article>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useI18n } from 'vue-i18n'
import {
  createLlmProvider,
  deleteLlmProvider,
  getLlmProviders,
  getLlmRoutes,
  getLlmUsage,
  testLlmProvider,
  updateLlmProvider,
  updateLlmProviderSecret,
  updateLlmRoute,
  type LlmConnectionTest,
  type LlmProvider,
  type LlmRouteEntry,
  type LlmUsage,
} from '../api'

type RouteKey = 'collection' | 'qualification' | 'strategy' | 'iteration' | 'default'
interface RouteCard {
  key: RouteKey
  order: string
  labelKey: string
  descriptionKey: string
  entries: LlmRouteEntry[]
  pendingProviderId: string
}

const routeMeta: Array<Omit<RouteCard, 'entries' | 'pendingProviderId'>> = [
  { key: 'collection', order: '01', labelKey: 'llm.routeCollection', descriptionKey: 'llm.routeCollectionDesc' },
  { key: 'qualification', order: '02', labelKey: 'llm.routeQualification', descriptionKey: 'llm.routeQualificationDesc' },
  { key: 'strategy', order: '03', labelKey: 'llm.routeStrategy', descriptionKey: 'llm.routeStrategyDesc' },
  { key: 'iteration', order: '06', labelKey: 'llm.routeIteration', descriptionKey: 'llm.routeIterationDesc' },
  { key: 'default', order: '—', labelKey: 'llm.routeDefault', descriptionKey: 'llm.routeDefaultDesc' },
]

const presets = [
  { key: 'deepseek', label: 'DeepSeek', name: 'deepseek', displayName: 'DeepSeek', baseUrl: 'https://api.deepseek.com/v1', defaultModel: 'deepseek-chat', apiKeyEnv: 'DEEPSEEK_API_KEY' },
  { key: 'openai', label: 'OpenAI', name: 'openai', displayName: 'OpenAI', baseUrl: 'https://api.openai.com/v1', defaultModel: 'gpt-4.1-mini', apiKeyEnv: 'OPENAI_API_KEY' },
  { key: 'qwen', label: '通义千问', name: 'qwen', displayName: '通义千问', baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1', defaultModel: 'qwen-plus', apiKeyEnv: 'DASHSCOPE_API_KEY' },
  { key: 'custom', label: '自定义', name: '', displayName: '', baseUrl: '', defaultModel: '', apiKeyEnv: 'CUSTOM_LLM_API_KEY' },
] as const

const providers = ref<LlmProvider[]>([])
const { t } = useI18n()
const usage = reactive<LlmUsage>({ requestCount: 0, successCount: 0, failureCount: 0, inputTokens: 0, outputTokens: 0, totalTokens: 0, fallbackCount: 0, averageLatencyMs: 0 })
const routeCards = ref<RouteCard[]>(routeMeta.map(item => ({ ...item, entries: [], pendingProviderId: '' })))
const loading = ref(true)
const routesReady = ref(false)
const serviceError = ref('')
const formOpen = ref(false)
const editorOverlay = ref<HTMLElement | null>(null)
const firstProviderInput = ref<HTMLInputElement | null>(null)
let editorRestoreTarget: HTMLElement | null = null
const editingId = ref<string | null>(null)
const selectedPreset = ref('custom')
const saving = ref(false)
const testingId = ref('')
const deletingId = ref('')
const savingRoute = ref('')
const formError = ref('')
const testResults = reactive<Record<string, LlmConnectionTest | undefined>>({})
const routeErrors = reactive<Record<string, string>>({})
const form = reactive({ name: '', displayName: '', baseUrl: '', defaultModel: '', apiKeyEnv: 'CUSTOM_LLM_API_KEY', timeoutSeconds: 30, enabled: true, apiKey: '' })

const configuredCount = computed(() => providers.value.filter(provider => provider.configured).length)
const successRate = computed(() => usage.requestCount ? `${Math.round((usage.successCount / usage.requestCount) * 100)}%` : '—')
const formValid = computed(() => Boolean(form.name && form.displayName && form.baseUrl && form.defaultModel && /^[A-Z][A-Z0-9_]*$/.test(form.apiKeyEnv) && form.timeoutSeconds > 0))

function errorMessage(error: unknown): string {
  const candidate = error as { response?: { data?: { detail?: string | { message?: string } } }; message?: string }
  const detail = candidate.response?.data?.detail
  if (typeof detail === 'string') return detail
  return detail?.message || candidate.message || t('llm.unknownError')
}

function initials(value: string): string {
  return value.trim().split(/\s+/).map(part => part[0]).join('').slice(0, 2).toUpperCase() || 'AI'
}

function resetForm() {
  Object.assign(form, { name: '', displayName: '', baseUrl: '', defaultModel: '', apiKeyEnv: 'CUSTOM_LLM_API_KEY', timeoutSeconds: 30, enabled: true, apiKey: '' })
  formError.value = ''
}

async function openCreate() {
  editorRestoreTarget = document.activeElement instanceof HTMLElement ? document.activeElement : null
  editingId.value = null
  selectedPreset.value = 'deepseek'
  resetForm()
  applyPreset('deepseek')
  formOpen.value = true
  await nextTick()
  firstProviderInput.value?.focus()
}

async function openEdit(provider: LlmProvider) {
  editorRestoreTarget = document.activeElement instanceof HTMLElement ? document.activeElement : null
  editingId.value = provider.id
  selectedPreset.value = 'custom'
  Object.assign(form, { name: provider.name, displayName: provider.displayName, baseUrl: provider.baseUrl, defaultModel: provider.defaultModel, apiKeyEnv: provider.apiKeyEnv, timeoutSeconds: provider.timeoutSeconds, enabled: provider.enabled, apiKey: '' })
  formError.value = ''
  formOpen.value = true
  await nextTick()
  firstProviderInput.value?.focus()
}

async function closeForm() {
  const restoreTarget = editorRestoreTarget
  formOpen.value = false
  editingId.value = null
  editorRestoreTarget = null
  resetForm()
  await nextTick()
  if (restoreTarget?.isConnected) restoreTarget.focus()
}

function trapEditorFocus(event: KeyboardEvent) {
  const overlay = editorOverlay.value
  if (!overlay) return
  const selector = 'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
  const focusable = Array.from(overlay.querySelectorAll<HTMLElement>(selector))
  if (!focusable.length) return
  const first = focusable[0]!
  const last = focusable[focusable.length - 1]!
  const active = document.activeElement
  if (event.shiftKey && (active === first || !overlay.contains(active))) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && (active === last || !overlay.contains(active))) {
    event.preventDefault()
    first.focus()
  }
}

function applyPreset(key: string) {
  const preset = presets.find(item => item.key === key)
  if (!preset) return
  selectedPreset.value = key
  if (key === 'custom') return
  Object.assign(form, { name: preset.name, displayName: preset.displayName, baseUrl: preset.baseUrl, defaultModel: preset.defaultModel, apiKeyEnv: preset.apiKeyEnv })
}

async function saveProvider() {
  if (!formValid.value) return
  saving.value = true
  formError.value = ''
  try {
    const wasEditing = editingId.value !== null
    const payload = { name: form.name, displayName: form.displayName, protocol: 'openai_chat' as const, baseUrl: form.baseUrl, defaultModel: form.defaultModel, apiKeyEnv: form.apiKeyEnv, enabled: form.enabled, timeoutSeconds: Number(form.timeoutSeconds) }
    const response = wasEditing
      ? await updateLlmProvider(editingId.value!, payload)
      : await createLlmProvider(payload)
    const saved = response.data
    if (!wasEditing) editingId.value = saved.id
    if (form.apiKey) await updateLlmProviderSecret(saved.id, form.apiKey)
    ElMessage.success(wasEditing ? t('llm.providerUpdated') : t('llm.providerAdded'))
    closeForm()
    await loadAll()
  } catch (error) {
    formError.value = errorMessage(error)
  } finally {
    saving.value = false
  }
}

async function removeProvider(provider: LlmProvider) {
  try {
    const references = providerRouteReferences(provider.id)
    const confirmation = references.length
      ? t('llm.deleteConfirmReferenced', { name: provider.displayName, routes: references.join(t('llm.routeReferenceSeparator')) })
      : t('llm.deleteConfirm', { name: provider.displayName })
    await ElMessageBox.confirm(confirmation, t('llm.deleteTitle'), { confirmButtonText: t('llm.delete'), cancelButtonText: t('llm.cancel'), type: 'warning' })
    deletingId.value = provider.id
    await deleteLlmProvider(provider.id)
    ElMessage.success(t('llm.providerDeleted'))
    await loadAll()
  } catch (error) {
    if ((error as string) !== 'cancel' && (error as string) !== 'close') ElMessage.error(errorMessage(error))
  } finally {
    deletingId.value = ''
  }
}

function providerRouteReferences(providerId: string): string[] {
  return routeCards.value
    .filter(route => route.entries.some(entry => entry.providerId === providerId))
    .map(route => t(route.labelKey))
}

async function runTest(provider: LlmProvider) {
  testingId.value = provider.id
  try {
    const { data } = await testLlmProvider(provider.id)
    testResults[provider.id] = data
    data.reachable ? ElMessage.success(t('llm.connectionTestPassed')) : ElMessage.warning(errorCategoryText(data.errorCategory))
  } catch (error) {
    ElMessage.error(t('llm.testFailed', { message: errorMessage(error) }))
  } finally {
    testingId.value = ''
  }
}

function errorCategoryText(category?: string): string {
  const labels: Record<string, string> = {
    configuration: 'llm.errorConfiguration',
    authentication: 'llm.errorAuthentication',
    timeout: 'llm.errorTimeout',
    network: 'llm.errorNetwork',
    rate_limit: 'llm.errorRateLimit',
    invalid_request: 'llm.errorInvalidRequest',
    upstream_server: 'llm.errorUpstreamServer',
  }
  return t(labels[category || ''] || 'llm.connectionFailed')
}

function providerName(providerId: string): string {
  return providers.value.find(provider => provider.id === providerId)?.displayName || t('llm.deletedProvider')
}

function availableProviders(route: RouteCard) {
  const used = new Set(route.entries.map(entry => entry.providerId))
  return providers.value.filter(provider => !used.has(provider.id))
}

function addRouteEntry(route: RouteCard) {
  if (!route.pendingProviderId) return
  route.entries.push({ providerId: route.pendingProviderId, priority: (route.entries.length + 1) * 10, modelOverride: null, enabled: true })
  route.pendingProviderId = ''
}

function removeRouteEntry(route: RouteCard, index: number) {
  route.entries.splice(index, 1)
}

function moveRouteEntry(route: RouteCard, index: number, delta: number) {
  const target = index + delta
  if (target < 0 || target >= route.entries.length) return
  const [entry] = route.entries.splice(index, 1)
  if (entry) route.entries.splice(target, 0, entry)
}

async function saveRoute(route: RouteCard) {
  savingRoute.value = route.key
  routeErrors[route.key] = ''
  try {
    const entries = route.entries.map((entry, index) => ({ ...entry, priority: (index + 1) * 10, modelOverride: entry.modelOverride || null, enabled: entry.enabled !== false }))
    const { data } = await updateLlmRoute(route.key, entries)
    route.entries = data.providers.map(entry => ({ ...entry }))
    ElMessage.success(t('llm.routeSaved', { route: t(route.labelKey) }))
  } catch (error) {
    routeErrors[route.key] = errorMessage(error)
  } finally {
    savingRoute.value = ''
  }
}

async function loadAll() {
  loading.value = true
  routesReady.value = false
  serviceError.value = ''
  try {
    const [providerResponse, routeResponse, usageResponse] = await Promise.all([getLlmProviders(), getLlmRoutes(), getLlmUsage()])
    providers.value = providerResponse.data
    Object.assign(usage, usageResponse.data)
    const routeMap = new Map(routeResponse.data.map(route => [route.routeKey, route.providers]))
    routeCards.value = routeMeta.map(meta => ({ ...meta, entries: (routeMap.get(meta.key) || []).map(entry => ({ ...entry })), pendingProviderId: '' }))
    routesReady.value = true
  } catch (error) {
    serviceError.value = errorMessage(error)
  } finally {
    loading.value = false
  }
}

onMounted(loadAll)
</script>

<style scoped>
.llm-page { max-width: 1440px; margin: 0 auto; }
.head-actions, .provider-actions, .form-actions, .route-footer, .order-actions { display: flex; align-items: center; gap: 8px; }
.notice { display: flex; justify-content: space-between; gap: 20px; padding: 15px 18px; border-radius: 10px; margin-bottom: 16px; }
.error-notice { background: color-mix(in oklab, var(--err) 8%, var(--surface)); border: 1px solid color-mix(in oklab, var(--err) 28%, var(--border)); }
.notice p { margin: 3px 0 0; color: var(--muted); font-size: 12px; }
.summary-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 16px; }
.metric-card { padding: 17px 18px; display: flex; flex-direction: column; gap: 5px; }
.metric-card > span { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .06em; font-weight: 650; }
.metric-card strong { font-family: var(--font-mono); font-size: 24px; letter-spacing: -.04em; }
.metric-card em { font-size: 12px; color: var(--muted); font-style: normal; }
.metric-card small { color: var(--muted); font-size: 11.5px; }
.section-card { margin-bottom: 16px; overflow: hidden; }
.section-head { display: flex; align-items: center; justify-content: space-between; padding: 17px 20px; }
.section-head h3 { margin-bottom: 3px; }
.count-chip, .status-pill { border: 1px solid var(--border); border-radius: 999px; padding: 4px 9px; font-size: 11px; color: var(--muted); white-space: nowrap; }
.state-box { min-height: 150px; display: grid; place-items: center; color: var(--muted); }
.empty-state { align-content: center; gap: 8px; padding: 30px; text-align: center; }
.empty-state b { color: var(--fg); font-size: 15px; }
.provider-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; padding: 16px; }
.provider-card { border: 1px solid var(--border); border-radius: 12px; padding: 17px; background: var(--surface); }
.provider-top { display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 11px; }
.provider-avatar { width: 40px; height: 40px; border-radius: 10px; display: grid; place-items: center; color: white; background: linear-gradient(135deg, var(--brand), oklch(66% .15 205)); font-size: 12px; font-weight: 750; }
.provider-title h4, .route-head h4 { margin: 0; font-size: 14px; }
.provider-title code { color: var(--muted); font-size: 10.5px; }
.status-pill.enabled { color: var(--ok); border-color: color-mix(in oklab, var(--ok) 35%, var(--border)); }
.status-pill.disabled { color: var(--muted); background: var(--bg-sub); }
.provider-details { display: grid; grid-template-columns: 1fr 1fr; gap: 10px 18px; margin: 17px 0; padding: 14px; border-radius: 9px; background: var(--bg-sub); }
.provider-details div { min-width: 0; }
.provider-details dt { font-size: 10px; color: var(--muted); margin-bottom: 3px; }
.provider-details dd { margin: 0; font-family: var(--font-mono); font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.text-ok { color: var(--ok); }.text-warn { color: var(--warn); }.danger-text { color: var(--err); }
.provider-actions { justify-content: flex-end; padding-top: 13px; border-top: 1px solid var(--border); }
.test-result { display: flex; justify-content: space-between; padding: 8px 10px; margin: -4px 0 10px; border-radius: 7px; font-size: 11px; }
.test-result.ok { color: var(--ok); background: color-mix(in oklab, var(--ok) 9%, transparent); }
.test-result.bad { color: var(--err); background: color-mix(in oklab, var(--err) 8%, transparent); }
.provider-editor-overlay { position: fixed; inset: 0; z-index: 1200; display: grid; place-items: center; padding: 24px; background: color-mix(in oklab, black 42%, transparent); backdrop-filter: blur(3px); outline: none; }
.editor-card { width: min(760px, 100%); max-height: min(860px, calc(100vh - 48px)); max-height: min(860px, calc(100dvh - 48px)); overflow-y: auto; padding: 22px; border-color: color-mix(in oklab, var(--brand) 25%, var(--border)); box-shadow: 0 24px 80px color-mix(in oklab, black 28%, transparent); }
.editor-head { display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 15px; }
.editor-head h3 { margin: 4px 0 0; }.eyebrow { color: var(--brand); font-size: 10px; font-weight: 750; text-transform: uppercase; letter-spacing: .08em; }
.preset-row { display: flex; gap: 7px; padding-bottom: 16px; border-bottom: 1px solid var(--border); }
.preset-button { border: 1px solid var(--border); background: var(--surface); color: var(--muted); border-radius: 8px; padding: 7px 12px; cursor: pointer; font-size: 12px; }
.preset-button.active { color: var(--brand); border-color: color-mix(in oklab, var(--brand) 45%, var(--border)); background: color-mix(in oklab, var(--brand) 6%, var(--surface)); }
.provider-form { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; margin-top: 17px; }
.provider-form label { display: flex; flex-direction: column; gap: 6px; font-size: 12px; font-weight: 600; }
.provider-form label > span i { color: var(--muted); font-style: normal; font-weight: 400; }
.wide { grid-column: 1 / -1; }.toggle-field { flex-direction: row !important; align-items: center; padding-top: 24px; }.toggle-field input, .mini-toggle input { accent-color: var(--brand); }
.secret-field { padding: 13px; border: 1px dashed var(--border); border-radius: 9px; background: var(--bg-sub); }.secret-field small { color: var(--muted); font-weight: 400; }
.form-error, .route-error { color: var(--err); background: color-mix(in oklab, var(--err) 7%, transparent); padding: 9px 11px; border-radius: 7px; font-size: 12px; }
.form-actions { justify-content: flex-end; padding-top: 16px; border-top: 1px solid var(--border); }
.route-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; padding: 16px; }
.route-card { border: 1px solid var(--border); border-radius: 12px; padding: 16px; min-width: 0; }
.route-card:last-child { grid-column: 1 / -1; }
.route-head { display: flex; justify-content: space-between; gap: 12px; margin-bottom: 13px; }
.route-head > div { position: relative; padding-left: 38px; }.route-index { position: absolute; left: 0; top: 0; font-family: var(--font-mono); font-weight: 750; color: var(--brand); }
.route-head p { margin: 4px 0 0; color: var(--muted); font-size: 11px; }.route-head > span { font-size: 10.5px; color: var(--muted); white-space: nowrap; }
.route-chain { display: flex; flex-direction: column; gap: 7px; }.route-entry { display: grid; grid-template-columns: auto 1fr auto auto; gap: 9px; align-items: center; padding: 9px; border: 1px solid var(--border); border-radius: 8px; background: var(--bg-sub); }
.priority { width: 22px; height: 22px; border-radius: 50%; display: grid; place-items: center; font-family: var(--font-mono); font-size: 10px; background: var(--surface); border: 1px solid var(--border); }
.route-provider { min-width: 0; display: grid; grid-template-columns: minmax(90px, auto) 1fr; gap: 8px; align-items: center; }.route-provider b { font-size: 11.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mini-input { width: 100%; min-width: 80px; border: 1px solid var(--border); background: var(--surface); color: var(--fg); border-radius: 6px; padding: 5px 7px; font-size: 10.5px; outline: none; }.mini-input:focus { border-color: var(--brand); }
.mini-toggle { display: flex; align-items: center; gap: 4px; font-size: 10.5px; color: var(--muted); white-space: nowrap; }
.icon-button { width: 25px; height: 25px; border: 1px solid var(--border); background: var(--surface); color: var(--muted); border-radius: 6px; cursor: pointer; }.icon-button:disabled { opacity: .35; cursor: default; }.icon-button.remove { color: var(--err); }
.route-empty { display: grid; place-items: center; min-height: 64px; color: var(--muted); border: 1px dashed var(--border); border-radius: 8px; font-size: 11px; }
.route-footer { margin-top: 10px; }.compact-select { flex: 1; min-width: 0; padding-top: 6px; padding-bottom: 6px; font-size: 11px; }
.route-error { margin: 8px 0 0; }
@media (max-width: 1050px) { .summary-grid { grid-template-columns: repeat(2, 1fr); }.provider-grid, .route-grid { grid-template-columns: 1fr; }.route-card:last-child { grid-column: auto; } }
@media (max-width: 680px) {
  :global(.sidebar) { width: 64px !important; flex-basis: 64px !important; }
  :global(.sb-brand) { justify-content: center; padding: 12px; }
  :global(.sb-brand > span),
  :global(.sb-section),
  :global(.sb-link > span:not(.icn)),
  :global(.sb-foot .user > div),
  :global(.logout-btn) { display: none; }
  :global(.sb-nav) { padding: 6px; }
  :global(.sb-link) { justify-content: center; padding: 10px; }
  :global(.sb-link.active::before) { left: -6px; }
  :global(.sb-foot) { justify-content: center; padding: 10px 6px; }
  :global(.topbar) { padding: 0 12px; }
  :global(.search) { display: none; }
  :global(.mock-banner) { align-items: flex-start; flex-wrap: wrap; padding: 8px 12px; }
  :global(.api-mode-toggle) { width: 100%; margin-left: 0; }
  .llm-page { padding: 16px 12px; }
  .page-head { align-items: flex-start; }
  .head-actions { width: 100%; }
  .summary-grid { grid-template-columns: 1fr; }
  .provider-grid, .route-grid { padding: 10px; }
  .provider-details, .provider-form { grid-template-columns: 1fr; }
  .wide { grid-column: auto; }
  .provider-actions { flex-wrap: wrap; }
  .route-entry { grid-template-columns: auto 1fr; }
  .mini-toggle, .order-actions { grid-column: 2; }
  .route-provider { grid-template-columns: 1fr; }
  .route-footer { flex-wrap: wrap; }
  .compact-select { flex-basis: 100%; }
  .provider-editor-overlay { place-items: end center; padding: 10px max(10px, env(safe-area-inset-right)) max(10px, env(safe-area-inset-bottom)) max(10px, env(safe-area-inset-left)); }
  .editor-card { max-height: calc(100vh - 20px); max-height: calc(100dvh - 20px); padding: 18px 14px; border-radius: 14px 14px 8px 8px; }
}
</style>
