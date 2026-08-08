<template>
  <div class="page">
    <div class="back-row">
      <button class="btn ghost" @click="goBack" :title="$t('common.back')">
        <span aria-hidden="true">←</span>
        <span>{{ $t('common.back') }}</span>
      </button>
    </div>
    <div class="profile">
      <div class="u-avatar lg">{{ username.slice(0, 2).toUpperCase() }}</div>
      <div>
        <h1>@{{ username }} <span class="chip ok" style="margin-left:8px;vertical-align:middle"><span class="dot"></span> {{ $t('status.qualified') }}</span></h1>
        <div class="meta">{{ metaZh }}</div>
        <div class="badges">
          <span class="chip cyan">{{ $t('persona.distributor') }}</span>
          <span class="chip warn">{{ $t('userDetail.highValue') }}</span>
        </div>
      </div>
      <div class="stats">
        <div class="stat"><div class="v">{{ fmtFollowers(followersCount) }}</div><div class="l">{{ $t('userDetail.followers') }}</div></div>
        <div class="stat"><div class="v">{{ videosCount }}</div><div class="l">{{ $t('userDetail.videos') }}</div></div>
        <div class="stat"><div class="v">{{ fmtFollowers(likesCount) }}</div><div class="l">{{ $t('userDetail.totalLikes') }}</div></div>
        <div class="stat"><div class="v">{{ engagementPct }}%</div><div class="l">{{ $t('userDetail.engagement') }}</div></div>
      </div>
      <div style="display:flex;gap:8px">
        <a v-if="profileUrl" :href="profileUrl" target="_blank" rel="noopener"
           class="btn ghost profile-open" :title="profileUrl">
          <span class="ext-icon">↗</span>
          {{ $t('userDetail.openProfile') }}
          <span class="profile-host">{{ shortHost(profileUrl) }}</span>
        </a>
        <button v-else class="btn ghost profile-open" disabled
                :title="$t('userDetail.openProfileMissing')">
          <span class="ext-icon">↗</span>
          {{ $t('userDetail.openProfile') }}
        </button>
        <button class="btn" @click="saveDraft">{{ $t('userDetail.saveDraft') }}</button>
        <button class="btn brand" @click="addToOutreach" :disabled="queuePendingForMe">{{ addToOutreachLabel }}</button>
      </div>
    </div>

    <div class="sub-tabs">
      <button v-for="(t, i) in tabs" :key="t" :class="['sub-tab', { active: activeTab === i }]" @click="activeTab = i">{{ t }}</button>
    </div>

    <div class="split-2-1">
      <div style="display:flex;flex-direction:column;gap:16px">
        <div class="card" v-show="activeTab === 0">
          <div class="card-hd">
            <h3>{{ $t('userDetail.personaTitle') }}</h3>
            <div class="period-bar">
              <button :class="{active: kwLang === 'en'}" @click="kwLang = 'en'">EN</button>
              <button :class="{active: kwLang === 'cn'}" @click="kwLang = 'cn'">中</button>
            </div>
          </div>
          <div class="card-bd">
            <p class="persona-text">{{ $t('userDetail.personaBody') }}</p>
            <div class="kw-row">
              <span v-for="k in personaKeywords" :key="k.word" :class="['kw', k.cls]">{{ k.word }}</span>
            </div>
          </div>
        </div>

        <div class="card" v-show="activeTab === 2">
          <div class="card-hd"><h3>{{ $t('userDetail.strategyTitle') }}</h3><span class="chip cyan">{{ $t('userDetail.strategyType') }}</span></div>
          <div class="strat-card">
            <div class="strat-hd">
              <h4>{{ $t('userDetail.strategyTemplateTitle') }}</h4>
              <span style="font-size:11.5px;color:var(--muted)">{{ $t('userDetail.generatedBy') }}</span>
            </div>
            <div class="strat-body">{{ strategy?.body || '—' }}</div>
            <div class="strat-meta" v-if="strategy">
              <span>⏱ {{ strategy.window }}</span>
              <span>⏳ {{ strategy.gap }}</span>
              <span>🎯 {{ strategy.expected }}</span>
              <span>📊 {{ strategy.historical }}</span>
            </div>
          </div>
        </div>

        <div class="card" v-show="activeTab === 3">
          <div class="card-hd"><h3>{{ $t('userDetail.videosTitle') }} (4)</h3><span class="hint">{{ $t('userDetail.last30d') }}</span></div>
          <div class="videos">
            <div v-for="v in videos" :key="v.title" class="vid">
              <div class="vid-cover" :class="v.cls"><div class="vid-play"><svg width="14" height="14" viewBox="0 0 24 24" fill="white"><path d="M8 5v14l11-7z"/></svg></div></div>
              <div class="vid-meta"><span class="v">{{ v.title }}</span><span class="mono">{{ v.views }}</span></div>
            </div>
          </div>
        </div>
      </div>

      <div style="display:flex;flex-direction:column;gap:16px">
        <div class="card score-card" v-show="activeTab === 0">
          <div class="card-hd" style="margin:-4px 0 12px;padding:0;border:0">
            <h3>{{ $t('userDetail.scoreTitle') }}</h3>
            <span class="chip ok"><span class="dot"></span> {{ $t('userDetail.scoreExcellent') }}</span>
          </div>
          <div class="score-big">
            <span class="v">92</span>
            <span class="v out-of">/ 100</span>
          </div>
          <div class="bar brand lg"><span style="width:92%"></span></div>
          <div class="score-breakdown">
            <div v-for="b in breakdown" :key="b.name" class="score-row">
              <span class="nm">{{ b.name }}</span>
              <div class="bar" :class="b.cls"><span :style="{ width: b.v + '%' }"></span></div>
              <span class="v">{{ b.v }}</span>
            </div>
          </div>
        </div>

        <div class="card" v-show="activeTab === 1">
          <div class="card-hd"><h3>{{ $t('userDetail.timelineTitle') }}</h3></div>
          <div class="tl">
            <div v-for="e in timeline" :key="e.time" class="tl-row">
              <span class="tl-time">{{ e.time }}</span>
              <div class="tl-dot-col"><div :class="['tl-dot', e.cls]"></div><div class="tl-line"></div></div>
              <div class="tl-body">
                <div class="who">{{ e.who }}</div>
                <div class="desc">{{ e.desc }}</div>
                <div v-if="e.quote" class="quote">"{{ e.quote }}"</div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, watch, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { getWordcloud, getUserDetail } from '../api'
import { useOutreachQueue, useDraftStore } from '../stores/actionStores'

const route = useRoute()
const router = useRouter()

/** 返回用户列表：优先用浏览器历史回到来源页(保留筛选/分页),
 *  若没有可回退历史(直接打开本 URL),则 push 到 /users。 */
function goBack() {
  if (window.history.length > 1) {
    router.back()
  } else {
    router.push('/users')
  }
}
const username = ref(String(route.params.username || 'aroma_house_us'))
const activeTab = ref(0)
const personaKeywords = ref<{ word: string; cls?: string }[]>([])
const kwLang = ref<'en' | 'cn'>('en')
const queue = useOutreachQueue()
const drafts = useDraftStore()

// UserDetail payload from /api/users/:username/detail
interface UserDetail {
  username: string
  profile_url?: string
  profile: { bio_zh: string; meta_zh: string; stats: { followers: number; videos: number; likes: number; engagement_pct: number } }
  breakdown: { name: string; v: number; cls: string }[]
  videos: { title: string; cls: string; views: string }[]
  timeline: { time: string; cls: string; who: string; desc: string; quote?: string }[]
  strategy: { body: string; window: string; gap: string; expected: string; historical: string }
}
const bioZh = ref('')
const metaZh = ref('')
const followersCount = ref(0)
const videosCount = ref(0)
const likesCount = ref(0)
const engagementPct = ref(0)
const profileUrl = ref('')
const breakdown = ref<{ name: string; v: number; cls: string }[]>([])
const videos = ref<{ title: string; cls: string; views: string }[]>([])
const timeline = ref<{ time: string; cls: string; who: string; desc: string; quote?: string }[]>([])
const strategy = ref<UserDetail['strategy'] | null>(null)

// Bucket keyword counts into the three CSS classes used by .kw-row
// (brand / ok / default). Pure presentation — no mock data lives here.
function bucketKw(items: { word: string; count: number }[]) {
  if (!items.length) return []
  const max = Math.max(...items.map(i => i.count))
  const min = Math.min(...items.map(i => i.count))
  return items.map(i => {
    const ratio = max === min ? 1 : (i.count - min) / (max - min)
    let cls: string | undefined
    if (ratio > 0.6) cls = 'brand'
    else if (ratio > 0.3) cls = 'ok'
    return { word: i.word, cls }
  })
}

async function loadKeywords() {
  try {
    // Request top 10 from the wordcloud (real backend can scope by user).
    const { data } = await getWordcloud(kwLang.value, 10)
    personaKeywords.value = bucketKw(Array.isArray(data) ? data : [])
  } catch {
    personaKeywords.value = []
  }
}

async function loadDetail() {
  try {
    const res: any = await getUserDetail(username.value)
    const data = res?.data as UserDetail | undefined
    if (!data) return
    bioZh.value = data.profile.bio_zh
    metaZh.value = data.profile.meta_zh
    followersCount.value = data.profile.stats.followers
    videosCount.value = data.profile.stats.videos
    likesCount.value = data.profile.stats.likes
    engagementPct.value = data.profile.stats.engagement_pct
    breakdown.value = data.breakdown
    videos.value = data.videos
    timeline.value = data.timeline
    strategy.value = data.strategy
    profileUrl.value = data.profile_url || buildProfileUrl(username.value)
  } catch {}
}

onMounted(() => { loadKeywords(); loadDetail() })
watch(kwLang, loadKeywords)
watch(() => route.params.username, (v) => { username.value = String(v || ''); loadDetail() })

// Whether this user is already in the outreach queue (used to disable + relabel button)
const queuePendingForMe = computed(() =>
  queue.items.some(q => q.username === username.value && q.status === 'pending')
)
const addToOutreachLabel = computed(() => queuePendingForMe.value ? '已在触达队列中 · 待审核' : '加入今日触达队列')

function addToOutreach() {
  if (queuePendingForMe.value) {
    ElMessage.info('@' + username.value + ' 已在触达队列中')
    return
  }
  queue.enqueue({ username: username.value, persona: 'distributor', source: 'detail-add' })
  ElMessage.success('已加入今日触达队列 · 在 /pipeline 页审核')
}

function saveDraft() {
  drafts.save('@' + username.value, {
    ts: Date.now(),
    bio_zh: bioZh.value,
    followers: followersCount.value,
    score: breakdown.value.reduce((s, b) => s + b.v, 0) / Math.max(1, breakdown.value.length),
  })
  ElMessage.success('已保存到本地草稿（localStorage）')
}

const tabs = ['画像 & 质量分', '互动时间线', '触达策略', '视频样本 (4)', '原始数据']

function fmtFollowers(n: number) {
  if (n >= 1000) return (n / 1000).toFixed(1) + 'K'
  return String(n)
}

/** 兜底拼接主页链接：仅在 detail 接口未返回 profile_url 时使用。
 *  默认按 tiktok 处理；如未来 UserDetail 暴露 platform 字段,优先用真值。 */
function buildProfileUrl(username: string): string {
  const u = (username || '').replace(/^@/, '').trim()
  return u ? `https://www.tiktok.com/@${u}` : ''
}

function shortHost(url: string): string {
  try {
    return new URL(url).host.replace(/^www\./, '')
  } catch {
    return url
  }
}
</script>

<style scoped>
.profile {
  background: linear-gradient(135deg, oklch(96% 0.04 350), oklch(96% 0.02 280) 50%, var(--surface));
  border: 1px solid var(--border); border-radius: 14px; padding: 24px 28px; margin-bottom: 16px;
  display: grid; grid-template-columns: auto 1fr auto; gap: 22px; align-items: center;
}
.profile .u-avatar.lg { width: 72px; height: 72px; font-size: 26px; }
.profile h1 { font-size: 24px; font-weight: 700; margin: 0 0 4px; letter-spacing: -0.4px; }
.profile .meta { color: var(--muted); font-size: 13.5px; }
.profile .badges { margin-top: 10px; display: flex; gap: 6px; flex-wrap: wrap; }
.profile .stats { display: flex; gap: 28px; }

.back-row { margin-bottom: 12px; }
.profile-open { text-decoration: none; }
.profile-open .ext-icon { font-size: 12px; opacity: .75; }
.profile-open .profile-host { font-size: 11px; color: var(--muted); margin-left: 4px; }
.profile-open:disabled { cursor: not-allowed; opacity: .6; }
.stat .v { font-size: 22px; font-weight: 700; font-family: var(--font-mono); letter-spacing: -0.4px; }
.stat .l { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; margin-top: 2px; }

.score-card { padding: 18px 20px; }
.score-big { display: flex; align-items: baseline; gap: 6px; margin-bottom: 12px; }
.score-big .v { font-size: 38px; font-weight: 700; letter-spacing: -1px; font-family: var(--font-mono); }
.score-big .v.out-of { font-size: 14px; color: var(--muted); font-weight: 500; }
.bar.lg { height: 8px; }
.score-row { display: grid; grid-template-columns: 110px 1fr 40px; gap: 10px; align-items: center; padding: 6px 0; font-size: 12.5px; }
.score-row .nm { color: var(--fg-2); }
.score-row .v { font-family: var(--font-mono); font-weight: 600; text-align: right; }

.sub-tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--border); margin-bottom: 18px; }
.sub-tab { padding: 9px 14px; border: none; background: transparent; font-size: 13px; color: var(--muted); font-weight: 500; border-bottom: 2px solid transparent; margin-bottom: -1px; cursor: pointer; }
.sub-tab.active { color: var(--fg); font-weight: 600; border-bottom-color: var(--fg); }

.tl { padding: 4px 20px 18px; }
.tl-row { display: grid; grid-template-columns: 90px 22px 1fr; gap: 12px; padding: 10px 0; }
.tl-time { font-family: var(--font-mono); font-size: 11.5px; color: var(--muted); padding-top: 2px; }
.tl-dot-col { display: flex; flex-direction: column; align-items: center; }
.tl-dot { width: 10px; height: 10px; border-radius: 50%; background: var(--brand); box-shadow: 0 0 0 2px var(--surface), 0 0 0 3px var(--brand); flex-shrink: 0; }
.tl-dot.ok { background: var(--ok); box-shadow: 0 0 0 2px var(--surface), 0 0 0 3px var(--ok); }
.tl-dot.warn { background: var(--warn); box-shadow: 0 0 0 2px var(--surface), 0 0 0 3px var(--warn); }
.tl-dot.neutral { background: var(--muted); box-shadow: 0 0 0 2px var(--surface), 0 0 0 3px var(--muted); }
.tl-line { width: 1px; flex: 1; background: var(--border); margin-top: 2px; }
.tl-body .who { font-weight: 600; font-size: 13px; margin-bottom: 2px; }
.tl-body .desc { color: var(--muted); font-size: 12.5px; line-height: 1.55; }
.tl-body .quote { background: var(--bg-sub); border-left: 2px solid var(--brand); padding: 8px 12px; border-radius: 0 6px 6px 0; font-size: 12.5px; color: var(--fg-2); margin-top: 6px; font-style: italic; }

.strat-card { padding: 16px 18px; }
.strat-hd { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
.strat-hd h4 { font-size: 13px; font-weight: 600; margin: 0; }
.strat-body { font-size: 12.5px; line-height: 1.65; color: var(--fg-2); padding: 12px 14px; background: var(--bg-sub); border-radius: 8px; white-space: pre-wrap; }
.strat-meta { display: flex; gap: 14px; margin-top: 10px; font-size: 11.5px; color: var(--muted); flex-wrap: wrap; }
.strat-meta span { display: inline-flex; align-items: center; gap: 5px; }

.videos { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; padding: 14px 18px; }
.vid { background: var(--bg-sub); border-radius: 8px; overflow: hidden; }
.vid-cover { aspect-ratio: 9/13; background: linear-gradient(135deg, oklch(75% 0.14 30), oklch(70% 0.16 60)); position: relative; display: grid; place-items: center; }
.vid-cover.b { background: linear-gradient(135deg, oklch(75% 0.12 200), oklch(70% 0.16 240)); }
.vid-cover.c { background: linear-gradient(135deg, oklch(75% 0.18 320), oklch(70% 0.20 350)); }
.vid-cover.d { background: linear-gradient(135deg, oklch(78% 0.10 80), oklch(70% 0.14 100)); }
.vid-play { width: 30px; height: 30px; border-radius: 50%; background: rgba(0,0,0,0.45); display: grid; place-items: center; color: #fff; }
.vid-meta { padding: 8px 10px; font-size: 11.5px; display: flex; justify-content: space-between; }
.vid-meta .v { font-weight: 600; }

.kw-row { display: flex; gap: 6px; flex-wrap: wrap; padding: 0; }
.kw { padding: 3px 9px; border-radius: 999px; background: var(--bg-sub); font-size: 11.5px; color: var(--fg-2); }
.kw.brand { background: var(--brand-soft); color: var(--brand-deep); }
.kw.ok { background: var(--ok-soft); color: oklch(42% 0.16 150); }
.persona-text { margin: 0 0 14px; line-height: 1.7; color: var(--fg-2); font-size: 13.5px; }
</style>