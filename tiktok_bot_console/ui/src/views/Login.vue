<template>
  <div class="login-wrap">
    <aside class="login-aside">
      <div class="brand">
        <div class="brand-mark">▣</div>
        <span>TikTok B2B Pipeline Lab</span>
      </div>

      <div class="pitch">
        <div class="eyebrow">{{ $t('login.eyebrow') }}</div>
        <h1>
          {{ $t('login.headline') }}<br>
          <em>{{ $t('login.headlineEmphasis') }}</em><br>
          {{ $t('login.headlineSuffix') }}
        </h1>
        <p>{{ $t('login.pitch') }}</p>

        <div class="stage-row">
          <div v-for="s in brandStages" :key="s.key" class="stage-mini">
            <div class="num">{{ stageIx(s.key) }}</div>
            <div class="nm">{{ stageName(s.key) }}</div>
            <div class="v">{{ s.metric }}</div>
          </div>
        </div>
      </div>

      <div class="login-foot">
        <div class="live">
          <span class="live-dot"></span>
          {{ $t('login.liveStatus') }}
        </div>
        <div>DeepSeek v4 Pro · Ubuntu · Docker</div>
      </div>
    </aside>

    <main class="login-main">
      <div class="form">
        <h2>{{ $t('login.signInTitle') }}</h2>
        <p class="sub">{{ $t('login.signInSub') }}</p>

        <div class="field">
          <label class="label">{{ $t('auth.username') }}</label>
          <input class="input" type="text" :placeholder="$t('auth.usernamePh')" v-model="form.username">
        </div>
        <div class="field">
          <label class="label">{{ $t('auth.password') }}</label>
          <input class="input" type="password" :placeholder="$t('auth.passwordPh')" v-model="form.password">
        </div>
        <div class="form-row-between">
          <label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:12.5px;color:var(--muted)">
            <input type="checkbox" v-model="remember"> {{ $t('login.remember') }}
          </label>
          <a href="#" style="color:var(--brand);font-size:12.5px" @click.prevent="ElMessage.info('请联系管理员重置密码')">{{ $t('login.forgot') }}</a>
        </div>

        <button class="btn primary" style="width:100%;height:42px;font-size:14px" :disabled="loading" @click="submit">
          {{ loading ? $t('login.signing') : $t('auth.login') + ' →' }}
        </button>

        <div class="divider">{{ $t('login.or') }}</div>

        <div class="field" style="margin-bottom:8px">
          <label class="label">{{ $t('auth.apiKeyLogin') }}</label>
          <div class="apikey-row">
            <input class="input mono" type="text" :placeholder="$t('auth.apiKeyPh')" v-model="form.apiKey">
            <button class="btn" @click="submitApiKey">{{ $t('login.verify') }}</button>
          </div>
          <p class="hint">{{ $t('auth.apiKeyHint') }}</p>
        </div>

        <div class="alt">{{ $t('auth.noAccount') }} <a href="#" @click.prevent="isRegister = !isRegister">{{ isRegister ? $t('auth.hasAccount') : $t('login.applyTrial') }}</a></div>

        <div v-if="isRegister" class="register-extra">
          <label class="label">{{ $t('auth.inviteCode') }}</label>
          <input class="input" type="text" v-model="form.invite" :placeholder="$t('auth.inviteCodePh')">
        </div>
      </div>
    </main>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useI18n } from 'vue-i18n'
import { useAuthStore } from '../stores/auth'
import { getPipelineOverview } from '../api'
import { resolvePostLoginTarget } from '../api/authSession'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()
const { t } = useI18n()
const loading = ref(false)
const isRegister = ref(false)

// Brand panel mini-cards — driven by /api/pipeline/overview
const brandStages = ref<{ key: string; metric: string }[]>([])
async function loadBrandPanel() {
  try {
    const { data } = await getPipelineOverview()
    if (data?.brandPanel?.stages) brandStages.value = data.brandPanel.stages
  } catch {}
}
onMounted(loadBrandPanel)
function stageIx(key: string): string {
  return ({ collect: '01 · COLLECT', filter: '02 · FILTER', outreach: '04 · OUTREACH' } as Record<string, string>)[key] || key
}
function stageName(key: string): string {
  return ({ collect: t('login.stageCollect'), filter: t('login.stageFilter'), outreach: t('login.stageOutreach') } as Record<string, string>)[key] || key
}
const remember = ref(true)
const form = reactive({ username: '', password: '', invite: '', apiKey: '' })

async function submit() {
  if (!form.username || !form.password) {
    ElMessage.warning(t('common.errRequired'))
    return
  }
  loading.value = true
  try {
    if (isRegister.value) {
      await auth.register(form.username, form.password, form.invite)
      ElMessage.success(t('auth.registerSuccess') || '注册成功，请登录')
      isRegister.value = false
    } else {
      await auth.login(form.username, form.password)
      router.push(resolvePostLoginTarget(route.query.redirect))
    }
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || t('common.errNetwork'))
  }
  loading.value = false
}

async function submitApiKey() {
  if (!form.apiKey) {
    ElMessage.warning(t('common.errRequired'))
    return
  }
  loading.value = true
  try {
    await auth.login(form.apiKey, form.apiKey)
    router.push(resolvePostLoginTarget(route.query.redirect))
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || t('common.errNetwork'))
  }
  loading.value = false
}
</script>

<style scoped>
.login-wrap { min-height: 100vh; display: grid; grid-template-columns: 1.05fr 1fr; }

.login-aside {
  background:
    radial-gradient(circle at 20% 20%, oklch(30% 0.18 350 / 0.5), transparent 55%),
    radial-gradient(circle at 80% 80%, oklch(30% 0.14 200 / 0.55), transparent 55%),
    linear-gradient(160deg, oklch(14% 0.012 280), oklch(18% 0.025 320));
  padding: 48px 56px;
  display: flex; flex-direction: column; justify-content: space-between;
  position: relative; overflow: hidden;
}
.login-aside::before {
  content: ''; position: absolute; inset: 0;
  background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='40' height='40'><path d='M0 0h40v1H0zM0 0v40h1V0z' fill='white' fill-opacity='0.025'/></svg>");
  pointer-events: none;
}
.brand { display: flex; align-items: center; gap: 12px; font-weight: 700; font-size: 17px; position: relative; color: #fff; }
.brand-mark {
  width: 34px; height: 34px; border-radius: 9px;
  background: linear-gradient(135deg, oklch(62% 0.22 350), oklch(70% 0.14 200));
  display: grid; place-items: center; color: #fff; font-weight: 800;
}
.pitch { position: relative; color: #fff; }
.pitch .eyebrow { font-size: 11.5px; letter-spacing: 2px; text-transform: uppercase; color: oklch(72% 0.08 200); margin-bottom: 14px; font-weight: 600; }
.pitch h1 { font-size: 38px; font-weight: 700; letter-spacing: -1px; line-height: 1.15; margin: 0 0 18px; }
.pitch h1 :deep(em) { font-style: normal; background: linear-gradient(90deg, oklch(70% 0.22 350), oklch(72% 0.16 200)); -webkit-background-clip: text; background-clip: text; color: transparent; }
.pitch p { color: oklch(75% 0.008 280); font-size: 14.5px; line-height: 1.65; max-width: 460px; margin: 0; }

.stage-row { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin-top: 36px; max-width: 520px; }
.stage-mini {
  background: oklch(20% 0.012 280 / 0.6);
  border: 1px solid oklch(28% 0.012 280);
  border-radius: 10px;
  padding: 12px 14px;
}
.stage-mini .num { font-size: 10.5px; color: oklch(60% 0.012 280); font-family: var(--font-mono); letter-spacing: 0.5px; }
.stage-mini .nm { font-size: 12.5px; font-weight: 600; color: #fff; margin-top: 4px; }
.stage-mini .v { font-size: 18px; font-weight: 700; font-family: var(--font-mono); color: oklch(78% 0.12 200); margin-top: 6px; }

.login-foot { position: relative; color: oklch(60% 0.012 280); font-size: 12px; display: flex; justify-content: space-between; }
.live { display: flex; align-items: center; gap: 6px; color: oklch(75% 0.005 280); }
.live-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--ok); box-shadow: 0 0 8px var(--ok); animation: pulse 2s infinite; }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }

.login-main {
  background: var(--surface); color: var(--fg);
  padding: 48px 64px;
  display: flex; flex-direction: column; justify-content: center;
}
.form { max-width: 380px; }
.form h2 { font-size: 26px; font-weight: 700; margin: 0 0 6px; letter-spacing: -0.5px; }
.form .sub { color: var(--muted); font-size: 14px; margin-bottom: 32px; }
.form .field { margin-bottom: 18px; }
.form-row-between { display: flex; justify-content: space-between; align-items: center; margin: -6px 0 14px; }
.btn.primary { background: var(--fg); color: var(--surface); border-color: var(--fg); }
.btn.primary:hover { background: oklch(30% 0.012 280); border-color: oklch(30% 0.012 280); }
.btn.primary:disabled { opacity: 0.6; cursor: not-allowed; }

.divider { display: flex; align-items: center; gap: 12px; color: var(--muted); font-size: 12px; margin: 28px 0; }
.divider::before, .divider::after { content: ''; flex: 1; height: 1px; background: var(--border); }

.apikey-row { display: flex; gap: 8px; }
.apikey-row .input { flex: 1; font-family: var(--font-mono); font-size: 12px; }
.alt { text-align: center; margin-top: 18px; font-size: 13px; color: var(--muted); }
.alt a { color: var(--brand); font-weight: 500; }
.register-extra { margin-top: 14px; padding-top: 14px; border-top: 1px solid var(--border); }

@media (max-width: 900px) {
  .login-wrap { grid-template-columns: 1fr; }
  .login-aside { display: none; }
}
</style>
