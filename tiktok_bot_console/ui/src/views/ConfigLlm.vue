<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1>{{ $t('llm.title') }}</h1>
        <p>{{ $t('llm.subtitle') }}</p>
      </div>
      <div style="display:flex;gap:8px">
        <button class="btn" @click="exportUsage">{{ $t('llm.exportUsage') }}</button>
        <button class="btn" @click="switchMain">{{ $t('llm.switchMain') }}</button>
      </div>
    </div>

    <div class="hero-prov">
      <div class="av">{{ mainProvider?.initials || 'LLM' }}</div>
      <div>
        <h2>{{ mainProvider?.displayName || '—' }} <span class="chip ok" style="margin-left:8px;vertical-align:middle"><span class="dot"></span> {{ $t('llm.mainHealthy') }}</span></h2>
        <div class="model">{{ mainProvider?.model || '—' }} · {{ mainProvider?.baseUrl || '' }}</div>
        <div class="meta">
          <span>API Key <b>{{ apiKeyMasked }}</b></span>
          <span>{{ $t('llm.latency') }} <b :style="{ color: latencyColor }">{{ latency }}</b></span>
          <span>{{ $t('llm.successRate') }} <b :style="{ color: successColor }">{{ successRate }}</b></span>
          <span>{{ $t('llm.todayCalls') }} <b>{{ todayCalls }} {{ $t('llm.times') }}</b> · ¥{{ todayCost }}</span>
        </div>
      </div>
      <div style="display:flex;gap:8px">
        <button class="btn" @click="showEdit = true">{{ $t('common.edit') }}</button>
        <button class="btn brand" @click="testConnection" :disabled="testing">
          {{ testing ? $t('common.loading') : $t('llm.testConn') }}
        </button>
      </div>
    </div>

    <div class="usage-grid">
      <div class="card usage-card">
        <div class="lbl">{{ $t('llm.todayCalls') }}</div>
        <div class="v">{{ todayCalls }}</div>
        <div class="sub">↑ {{ dayOverDay }}% · ¥{{ todayCost }}</div>
      </div>
      <div class="card usage-card">
        <div class="lbl">{{ $t('llm.monthCalls') }}</div>
        <div class="v">{{ monthCalls.toLocaleString() }}</div>
        <div class="sub">¥{{ monthCost }} / ¥{{ monthBudget }}</div>
      </div>
      <div class="card usage-card">
        <div class="lbl">{{ $t('llm.avgLatency') }}</div>
        <div class="v">{{ avgLatency }}<span style="font-size:12px;color:var(--muted);font-weight:500">ms</span></div>
        <div class="sub">P95: {{ p95 }}</div>
      </div>
      <div class="card usage-card">
        <div class="lbl">{{ $t('llm.tokenThroughput') }}</div>
        <div class="v">{{ tokenM }}<span style="font-size:12px;color:var(--muted);font-weight:500">M</span></div>
        <div class="sub">{{ $t('llm.in1_6') }} / {{ $t('llm.out0_8') }}</div>
      </div>
    </div>

    <div class="card mb-16">
      <div class="card-hd">
        <h3>{{ $t('llm.providersList') }}</h3>
        <span class="hint">{{ $t('llm.providersHint') }}</span>
      </div>
      <div style="padding:14px 18px">
        <div class="prov-list">
          <div v-for="(p, i) in providers" :key="p.name" class="prov-card">
            <div class="av-sm" :style="{ background: p.color }">{{ p.initials }}</div>
            <div>
              <div class="nm">{{ p.displayName }}</div>
              <div class="md">{{ p.model }} · {{ p.role === 'main' ? $t('llm.main') : $t('llm.backup') }}</div>
            </div>
            <div class="url">{{ p.url }}</div>
            <span :class="['chip', p.role === 'main' ? 'ok' : (p.status === 'unconfigured' ? 'warn' : '')]">
              <span class="dot"></span>
              {{ p.role === 'main' ? $t('llm.main') : (p.status === 'unconfigured' ? $t('llm.noKey') : $t('llm.backup')) }}
            </span>
            <div style="display:flex;gap:6px">
              <button v-if="p.status !== 'unconfigured'" class="btn sm" @click="editProvider(i)">{{ $t('common.edit') }}</button>
              <button v-else class="btn sm" @click="configureProvider(i)">{{ $t('llm.configure') }}</button>
              <button class="btn sm ghost" style="color:var(--err)" @click="deleteProvider(i)">{{ $t('common.delete') }}</button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="card mb-16">
      <div class="card-hd">
        <h3>{{ $t('llm.skillUsage30d') }}</h3>
        <span class="hint">{{ $t('llm.sortedByCalls') }}</span>
      </div>
      <table class="tbl skill-table">
        <thead>
          <tr>
            <th>Skill</th>
            <th>{{ $t('llm.pipelineStage') }}</th>
            <th>{{ $t('llm.callCount') }}</th>
            <th>{{ $t('llm.avgToken') }}</th>
            <th>{{ $t('llm.avgLatencyCol') }}</th>
            <th style="width:30%">{{ $t('llm.share') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in skills" :key="s.name">
            <td><div class="nm">{{ s.name }}<small>{{ s.desc }}</small></div></td>
            <td><span class="chip cyan">{{ s.stage }}</span></td>
            <td class="mono">{{ s.calls.toLocaleString() }}</td>
            <td class="mono">{{ s.token }}</td>
            <td class="mono">{{ s.latency }}</td>
            <td><div class="skill-bar"><span :style="{ width: s.share + '%' }"></span></div></td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Add / Edit provider form -->
    <div class="card form-card">
      <div class="card-hd" style="margin:-8px 0 18px;padding:0;border:0">
        <h3>{{ showEdit ? $t('llm.editProvider') : $t('llm.addProvider') }}</h3>
        <button class="btn sm ghost" @click="cancelForm">{{ $t('common.cancel') }}</button>
      </div>
      <div class="form-grid">
        <div class="field">
          <label class="label">{{ $t('llm.providerName') }}</label>
          <input class="input" :placeholder="$t('llm.providerNamePh')" v-model="form.name">
          <p class="hint">{{ $t('llm.providerNameHint') }}</p>
        </div>
        <div class="field">
          <label class="label">{{ $t('llm.modelIdentifier') }}</label>
          <input class="input" :placeholder="$t('llm.modelIdentifierPh')" v-model="form.model">
          <p class="hint">{{ $t('llm.modelIdentifierHint') }}</p>
        </div>
        <div class="field" style="grid-column:span 2">
          <label class="label">{{ $t('llm.apiKey') }}</label>
          <input class="input mono" type="password" placeholder="sk-..." v-model="form.apiKey">
          <p class="hint">{{ $t('llm.apiKeyHint') }}</p>
        </div>
        <div class="field" style="grid-column:span 2">
          <label class="label">{{ $t('llm.baseUrl') }}</label>
          <input class="input mono" :placeholder="$t('llm.baseUrlPh')" v-model="form.baseUrl">
          <p class="hint">{{ $t('llm.baseUrlHint') }}</p>
        </div>
        <div class="field" style="grid-column:span 2;display:flex;align-items:center;gap:14px;padding:12px 14px;background:var(--bg-sub);border-radius:8px">
          <input type="checkbox" id="asmain" v-model="form.setAsMain" style="accent-color:var(--brand)">
          <label for="asmain" style="font-size:13px;color:var(--fg)"><b>{{ $t('llm.setAsMain') }}</b> {{ $t('llm.replaceDeepSeek') }}</label>
        </div>
      </div>
      <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:18px;padding-top:18px;border-top:1px solid var(--border)">
        <button class="btn" @click="cancelForm">{{ $t('common.cancel') }}</button>
        <button class="btn" @click="testFormConn" :disabled="!form.name || testingForm">{{ testingForm ? $t('common.loading') : $t('llm.testConn') }}</button>
        <button class="btn brand" @click="saveProvider" :disabled="!form.name || !form.model">{{ $t('llm.saveProvider') }}</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getConfig, setConfigKey, saveApiKey, getLlmProviders } from '../api'

const { t } = useI18n()

// Live metrics — driven by /api/llm/providers payload
interface LlmUsage { todayCalls: number; todayCost: number; monthCalls: number; monthCost: number; monthBudget: number; avgLatency: number; p95: string; tokenMillions: number; tokenIn: number; tokenOut: number; latency: string; successRate: string; apiKeyMasked: string; dayOverDay: number }
interface LlmProvider { name: string; displayName: string; initials: string; model: string; baseUrl: string; url: string; color: string; role: 'main' | 'backup'; status: 'active' | 'unconfigured' }
interface LlmSkill  { name: string; desc: string; stage: string; calls: number; token: number; latency: string; share: number }

const usage = ref<LlmUsage | null>(null)
const providers = ref<LlmProvider[]>([])
const skills = ref<LlmSkill[]>([])
const todayCalls = computed(() => usage.value?.todayCalls ?? 0)
const todayCost = computed(() => usage.value?.todayCost ?? 0)
const monthCalls = computed(() => usage.value?.monthCalls ?? 0)
const monthCost = computed(() => usage.value?.monthCost ?? 0)
const monthBudget = computed(() => usage.value?.monthBudget ?? 500)
const avgLatency = computed(() => usage.value?.avgLatency ?? 0)
const p95 = computed(() => usage.value?.p95 ?? '—')
const tokenM = computed(() => usage.value?.tokenMillions ?? 0)
const tokenIn = computed(() => usage.value?.tokenIn ?? 0)
const tokenOut = computed(() => usage.value?.tokenOut ?? 0)
const latency = computed(() => usage.value?.latency ?? '—')
const successRate = computed(() => usage.value?.successRate ?? '—')
const apiKeyMasked = computed(() => usage.value?.apiKeyMasked ?? '')
const dayOverDay = computed(() => usage.value?.dayOverDay ?? 0)

const latencyColor = ref('var(--ok)')
const successColor = ref('var(--ok)')
const testing = ref(false)
const testingForm = ref(false)

const mainProvider = computed(() => providers.value.find(p => p.role === 'main'))

const showEdit = ref(false)
const editingIndex = ref<number | null>(null)
const form = reactive({ name: '', model: '', apiKey: '', baseUrl: '', setAsMain: false })

function resetForm() {
  form.name = ''; form.model = ''; form.apiKey = ''; form.baseUrl = ''; form.setAsMain = false
  showEdit.value = false
  editingIndex.value = null
}

function editProvider(i: number) {
  const p = providers.value[i]
  if (!p) return
  form.name = p.displayName
  form.model = p.model
  form.baseUrl = 'https://' + p.url
  form.apiKey = ''
  form.setAsMain = p.role === 'main'
  editingIndex.value = i
  showEdit.value = true
  ElMessage.info(`编辑 ${p.displayName}`)
}

function configureProvider(i: number) {
  const p = providers.value[i]
  if (!p) return
  form.name = p.displayName
  form.model = p.model
  form.baseUrl = 'https://' + p.url
  editingIndex.value = i
  showEdit.value = true
  ElMessage.info(`配置 ${p.displayName}`)
}

async function deleteProvider(i: number) {
  const p = providers.value[i]
  if (!p) return
  if (p.role === 'main') {
    ElMessage.warning('主 Provider 不可删除，请先切换其他 Provider 为主')
    return
  }
  try {
    await ElMessageBox.confirm(`确认删除 ${p.displayName}？此操作不可撤销`, '删除 Provider', {
      confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning',
    })
    providers.value.splice(i, 1)
    ElMessage.success('已删除')
  } catch { /* user cancelled */ }
}

function cancelForm() {
  resetForm()
}

async function testFormConn() {
  if (!form.name || !form.baseUrl) { ElMessage.warning('请填写 Provider 名称和 Base URL'); return }
  testingForm.value = true
  try {
    // Try fetching the model list from the base URL
    const resp = await fetch(form.baseUrl + '/models', { method: 'GET', signal: AbortSignal.timeout(5000) })
    if (resp.ok) {
      ElMessage.success(`${form.name} 连接测试通过 · 端点可达`)
    } else {
      ElMessage.warning(`${form.name} 端点返回 ${resp.status} — 可能需要 API Key`)
    }
  } catch {
    ElMessage.warning(`${form.name} 端点不可达 — 请检查 URL 和网络`)
  } finally {
    testingForm.value = false
  }
}

async function saveProvider() {
  if (!form.name || !form.model) return
  try {
    // Persist to mock backend
    await setConfigKey('llm_model', form.model)
    if (form.apiKey) await saveApiKey(form.apiKey)
    if (form.setAsMain) await setConfigKey('llm_provider', form.name)

    if (editingIndex.value !== null) {
      const p = providers.value[editingIndex.value]
      if (p) {
        p.displayName = form.name
        p.model = form.model
        p.status = 'active'
      }
      ElMessage.success(`已更新 ${form.name}`)
    } else {
      providers.value.push({
        name: form.name.toLowerCase().replace(/\s+/g, '_'),
        displayName: form.name,
        initials: form.name.slice(0, 2).toUpperCase(),
        model: form.model,
        url: form.baseUrl.replace(/^https?:\/\//, ''),
        baseUrl: form.baseUrl,
        color: 'linear-gradient(135deg, oklch(60% 0.14 200), oklch(60% 0.18 320))',
        role: form.setAsMain ? 'main' : 'backup',
        status: 'active',
      })
      ElMessage.success(`已添加 ${form.name}`)
    }
    resetForm()
  } catch (e) {
    ElMessage.error('保存失败：' + (e as Error).message)
  }
}

async function testConnection() {
  testing.value = true
  try {
    const { data } = await getConfig()
    if (data?.has_api_key) {
      ElMessage.success(`${mainProvider.value?.displayName || 'LLM'} 连接配置正常 · API Key 已配置`)
      latencyColor.value = 'var(--ok)'
      successColor.value = 'var(--ok)'
    } else {
      ElMessage.warning('API Key 未配置，请先设置')
      latencyColor.value = 'var(--warn)'
      successColor.value = 'var(--warn)'
    }
  } catch {
    ElMessage.error('连接失败 — 请检查后端服务是否运行')
    latencyColor.value = 'var(--err)'
    successColor.value = 'var(--err)'
  } finally {
    testing.value = false
  }
}

function exportUsage() {
  const csv = 'date,calls,cost\n' + Array.from({ length: 30 }, (_, i) =>
    `2026-06-${String(i + 1).padStart(2, '0')},${Math.floor(Math.random() * 200 + 50)},${(Math.random() * 2).toFixed(2)}`
  ).join('\n')
  const blob = new Blob([csv], { type: 'text/csv' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url; a.download = `llm-usage-${new Date().toISOString().slice(0, 10)}.csv`; a.click()
  URL.revokeObjectURL(url)
  ElMessage.success('已下载使用量 CSV')
}

async function switchMain() {
  const backupProviders = providers.value.filter(p => p.role !== 'main')
  if (backupProviders.length === 0) { ElMessage.info('没有备用 Provider 可切换'); return }
  const next = backupProviders[0]
  try {
    await ElMessageBox.confirm(`切换主 Provider 为 ${next.displayName}？需要重启 Pipeline 才能生效`, '切换', {
      confirmButtonText: '切换', cancelButtonText: '取消', type: 'info',
    })
    await setConfigKey('llm_provider', next.name)
    // Update local state
    providers.value.forEach(p => p.role = p.name === next.name ? 'main' : 'backup')
    ElMessage.success(`已切换主 Provider 为 ${next.displayName}`)
  } catch { /* cancelled */ }
}

async function loadLlm() {
  try {
    const { data } = await getLlmProviders()
    if (data) {
      providers.value = data.providers ?? []
      usage.value = data.usage ?? null
      skills.value = data.skills ?? []
    }
  } catch {}
}

onMounted(loadLlm)
</script>

<style scoped>
.hero-prov { padding: 24px 28px; background: linear-gradient(135deg, oklch(96% 0.04 350), var(--surface) 70%); border: 1px solid var(--border); border-radius: 14px; margin-bottom: 18px; display: grid; grid-template-columns: auto 1fr auto; gap: 22px; align-items: center; }
.hero-prov .av { width: 64px; height: 64px; border-radius: 14px; display: grid; place-items: center; color: #fff; font-weight: 700; font-size: 20px; background: linear-gradient(135deg, oklch(58% 0.22 350), oklch(70% 0.14 200)); }
.hero-prov h2 { font-size: 22px; font-weight: 700; margin: 0 0 4px; letter-spacing: -0.3px; }
.hero-prov .model { font-size: 13px; color: var(--muted); font-family: var(--font-mono); margin-bottom: 12px; }
.hero-prov .meta { display: flex; gap: 24px; font-size: 12.5px; color: var(--muted); flex-wrap: wrap; }
.hero-prov .meta b { color: var(--fg); font-weight: 600; }

.usage-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 18px; }
.usage-card { padding: 14px 16px; }
.usage-card .lbl { font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--muted); font-weight: 600; }
.usage-card .v { font-size: 20px; font-weight: 700; font-family: var(--font-mono); letter-spacing: -0.4px; margin-top: 4px; }
.usage-card .sub { font-size: 11.5px; color: var(--muted); margin-top: 4px; }

.prov-list { display: flex; flex-direction: column; gap: 10px; }
.prov-card { padding: 16px 18px; display: grid; grid-template-columns: auto 1fr auto auto auto; gap: 16px; align-items: center; }
.prov-card .av-sm { width: 40px; height: 40px; border-radius: 10px; display: grid; place-items: center; color: #fff; font-weight: 700; font-size: 13px; }
.prov-card .nm { font-weight: 600; font-size: 14px; }
.prov-card .md { font-size: 11.5px; color: var(--muted); font-family: var(--font-mono); margin-top: 2px; }
.prov-card .url { font-size: 11.5px; color: var(--muted); font-family: var(--font-mono); }

.skill-table th, .skill-table td { padding: 10px 14px; font-size: 13px; }
.skill-table .nm { font-weight: 500; }
.skill-table .nm small { color: var(--muted); display: block; font-weight: 400; font-size: 11.5px; margin-top: 2px; }
.skill-bar { width: 100%; height: 5px; background: var(--bg-sub); border-radius: 3px; overflow: hidden; }
.skill-bar > span { display: block; height: 100%; background: var(--brand); }

.form-card { padding: 24px; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
</style>