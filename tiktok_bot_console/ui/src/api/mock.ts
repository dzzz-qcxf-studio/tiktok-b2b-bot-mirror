/**
 * Mock API — used when VITE_USE_MOCK=true or backend unreachable.
 * Returns shaped responses that mirror the real axios responses
 * (`{ data: ... }`) so call sites do not need to change.
 */

import type {
  AcquisitionCampaign,
  AcquisitionKeyword,
  CreateAcquisitionJobPayload,
  CreateAcquisitionJobResponse,
  CreatePipelineJobPayload,
  PipelineJob,
  PipelineJobListParams,
  PipelineSchedule,
  PipelineSchedulePayload,
  PipelineStage,
  PipelineStageName,
  PipelineRuntimeConfigPayload,
  PipelineRuntimeConfigResponse,
} from '../types/pipeline'

const sleep = (ms: number) => new Promise(r => setTimeout(r, ms))

// ---- Mock dataset --------------------------------------------------------

// Keyword pool — used by /api/stats/wordcloud. The full set drives the
// Reports page cloud and the UserDetail persona tag row. View components
// only bucket and render — they don't own this data.
const MOCK_WORDCLOUD_EN = [
  { word: 'importer',          count: 287 },
  { word: 'wholesale',         count: 245 },
  { word: 'retailer',          count: 167 },
  { word: 'distributor',       count: 154 },
  { word: 'sourcing',          count: 132 },
  { word: 'brand',             count: 118 },
  { word: 'OEM',               count: 104 },
  { word: 'sourcing agent',    count: 98  },
  { word: 'bulk order',        count: 92  },
  { word: 'factory direct',    count: 84  },
  { word: 'private label',     count: 76  },
  { word: 'MOQ',               count: 68  },
  { word: 'FDA',               count: 62  },
  { word: 'CE',                count: 58  },
  { word: 'B2B',               count: 52  },
  { word: 'catalog',           count: 46  },
  { word: 'logistics',         count: 41  },
  { word: 'sample',            count: 38  },
  { word: 'dropship',          count: 33  },
  { word: '1688',              count: 29  },
  { word: 'bulk pricing',      count: 24  },
  { word: 'retail',            count: 19  },
]

// Same semantic ordering as the English pool, so a user toggling lang
// sees roughly the same shape on the page.
const MOCK_WORDCLOUD_CN = [
  { word: '进口商',       count: 287 },
  { word: '批发',         count: 245 },
  { word: '零售商',       count: 167 },
  { word: '分销商',       count: 154 },
  { word: '代采',         count: 132 },
  { word: '品牌方',       count: 118 },
  { word: '代工',         count: 104 },
  { word: '采购代理',     count: 98  },
  { word: '大订单',       count: 92  },
  { word: '工厂直供',     count: 84  },
  { word: '私标',         count: 76  },
  { word: '起订量',       count: 68  },
  { word: 'FDA注册',      count: 62  },
  { word: 'CE认证',       count: 58  },
  { word: 'B2B',          count: 52  },
  { word: '目录',         count: 46  },
  { word: '物流',         count: 41  },
  { word: '样品',         count: 38  },
  { word: '代发',         count: 33  },
  { word: '1688',         count: 29  },
  { word: '批量价',       count: 24  },
  { word: '零售',         count: 19  },
]

const buildProfileUrl = (platform: string | undefined, username: string) => {
  const u = (username || '').replace(/^@/, '').trim()
  if (!u) return ''
  const p = (platform || 'tiktok').toLowerCase()
  if (p === 'tiktok') return `https://www.tiktok.com/@${u}`
  if (p === 'douyin') return `https://www.douyin.com/user/${u}`
  return ''
}

const MOCK_USERS: any[] = [
  { id: 1, tiktok_id: 'aroma_house_us', platform: 'tiktok', username: 'aroma_house_us', nickname: 'Aroma House US', bio: 'Wholesale essential oils · Importer based in TX · DM open', follower_count: 128400, following_count: 412, like_count: 2100000, video_count: 412, country: 'US', category: 'distributor', status: 'replied', source: 'keyword_search', source_keyword: 'importer 1688', created_at: '2026-06-18T09:18:00', updated_at: '2026-07-11T12:04:00' },
  { id: 2, tiktok_id: 'led_wholesale_uk', platform: 'tiktok', username: 'led_wholesale_uk', nickname: 'LED Wholesale UK', bio: 'LED lighting distributor · bulk pricing for retailers', follower_count: 56200, following_count: 89, like_count: 421000, video_count: 87, country: 'GB', category: 'distributor', status: 'qualified', source: 'keyword_search', source_keyword: 'wholesale LED', created_at: '2026-06-19T10:00:00', updated_at: '2026-07-10T14:22:00' },
  { id: 3, tiktok_id: 'korean_beauty_hub', platform: 'tiktok', username: 'korean_beauty_hub', nickname: 'K-Beauty Hub', bio: 'K-beauty sourcing for US/EU · Brand collabs welcome', follower_count: 214700, following_count: 312, like_count: 1860000, video_count: 218, country: 'KR', category: 'buyer', status: 'contacted', source: 'recommendation', source_keyword: '', created_at: '2026-06-20T08:30:00', updated_at: '2026-07-11T11:58:00' },
  { id: 4, tiktok_id: 'sourcing_brothers_de', platform: 'tiktok', username: 'sourcing_brothers_de', nickname: 'Sourcing Brothers', bio: 'Berlin · Agent for European brands sourcing from Asia', follower_count: 38100, following_count: 156, like_count: 198000, video_count: 64, country: 'DE', category: 'buyer', status: 'qualified', source: 'keyword_search', source_keyword: 'sourcing agent', created_at: '2026-06-21T14:15:00', updated_at: '2026-07-09T16:08:00' },
  { id: 5, tiktok_id: 'factory_direct_cn', platform: 'douyin', username: 'factory_direct_cn', nickname: 'Factory Direct CN', bio: 'Factory direct · OEM/ODM · 电子产品 17年', follower_count: 8600, following_count: 23, like_count: 24000, video_count: 18, country: 'CN', category: 'peer', status: 'rejected', source: 'recommendation', source_keyword: '', created_at: '2026-06-22T11:42:00', updated_at: '2026-07-09T11:42:00' },
  { id: 6, tiktok_id: 'maison_zara_fr', platform: 'tiktok', username: 'maison_zara_fr', nickname: 'Maison Zara FR', bio: 'Boutique mode · cherche fournisseur textile MOQ 200', follower_count: 12300, following_count: 78, like_count: 89000, video_count: 32, country: 'FR', category: 'buyer', status: 'pending', source: 'keyword_search', source_keyword: 'bulk buy China', created_at: '2026-06-23T09:00:00', updated_at: '2026-07-11T10:30:00' },
  { id: 7, tiktok_id: 'tech_retailer_ph', platform: 'tiktok', username: 'tech_retailer_ph', nickname: 'Tech Retailer PH', bio: 'Tech retail · 50 stores nationwide · open for OEM deals', follower_count: 76800, following_count: 234, like_count: 412000, video_count: 156, country: 'PH', category: 'distributor', status: 'qualified', source: 'keyword_search', source_keyword: 'sourcing agent', created_at: '2026-06-24T15:30:00', updated_at: '2026-07-10T11:30:00' },
  { id: 8, tiktok_id: 'brazil_import_br', platform: 'tiktok', username: 'brazil_import_br', nickname: 'Brazil Import', bio: 'Import electronics from China · 8 anos no mercado', follower_count: 42100, following_count: 189, like_count: 256000, video_count: 92, country: 'BR', category: 'distributor', status: 'pending', source: 'keyword_search', source_keyword: 'importer 1688', created_at: '2026-06-25T13:20:00', updated_at: '2026-07-09T15:00:00' },
  { id: 9, tiktok_id: 'japan_craft_tokyo', platform: 'tiktok', username: 'japan_craft_tokyo', nickname: 'Japan Craft', bio: 'Tokyo · Specialty importer of EU/US lifestyle goods', follower_count: 34500, following_count: 67, like_count: 178000, video_count: 45, country: 'JP', category: 'buyer', status: 'qualified', source: 'keyword_search', source_keyword: 'retail dropship', created_at: '2026-06-26T17:00:00', updated_at: '2026-07-09T17:00:00' },
  { id: 10, tiktok_id: 'india_wholesale_in', platform: 'tiktok', username: 'india_wholesale_in', nickname: 'India Wholesale', bio: 'Mumbai · Wholesale distributor · D2C brand sourcing', follower_count: 67200, following_count: 201, like_count: 380000, video_count: 124, country: 'IN', category: 'distributor', status: 'contacted', source: 'recommendation', source_keyword: '', created_at: '2026-06-27T12:30:00', updated_at: '2026-07-10T16:00:00' },
]

/** 程序化生成 1237 条额外 mock 用户(补到 1247),让列表分页 / 筛选 /
 *  关键词搜索 demo 时数据量与 MOCK_DASHBOARD.overview.total_users (1247) 自洽。
 *  数据按状态分布加权:replied < qualified < contacted < rejected < pending,
 *  与真实后端 /api/users/stats 的典型分布一致。 */
const MOCK_USER_COUNT = 1247
const COUNTRIES = ['US', 'GB', 'KR', 'DE', 'CN', 'FR', 'PH', 'BR', 'JP', 'IN', 'VN', 'TH', 'AU', 'CA', 'MX', 'IT', 'ES', 'NL', 'PL', 'TR']
const CATEGORIES = ['distributor', 'buyer', 'peer']
const STATUSES = ['pending', 'pending', 'pending', 'pending', 'qualified', 'qualified', 'qualified', 'contacted', 'contacted', 'replied', 'rejected', 'rejected']
const SOURCES = ['keyword_search', 'keyword_search', 'keyword_search', 'recommendation', 'competitor']
const KEYWORDS = ['importer 1688', 'wholesale LED', 'sourcing agent', 'bulk buy China', 'retail dropship', 'factory direct', 'OEM partner', 'B2B sourcing', 'private label', 'distributor wanted']
const NICHE_PREFIX = ['aroma', 'led', 'korean_beauty', 'sourcing', 'factory', 'maison', 'tech', 'brazil', 'japan', 'india', 'global', 'pacific', 'euro', 'asia', 'metro', 'urban', 'eco', 'pure', 'natural', 'smart']

function pad(n: number) { return String(n).padStart(2, '0') }

for (let i = MOCK_USERS.length + 1; i <= MOCK_USER_COUNT; i++) {
  const country = COUNTRIES[(i * 7) % COUNTRIES.length] ?? 'US'
  const category = CATEGORIES[(i * 7) % CATEGORIES.length]  // 7 与 3 互质,3 个画像均匀分布
  const status = STATUSES[(i * 5) % STATUSES.length]
  const source = SOURCES[(i * 11) % SOURCES.length]
  const platform = (i % 9 === 0) ? 'douyin' : 'tiktok'
  const countrySuffix = country.toLowerCase().slice(0, 2)
  const niche = NICHE_PREFIX[i % NICHE_PREFIX.length]
  const username = `${niche}_${countrySuffix}${pad(i)}`
  const keyword = source === 'keyword_search' ? KEYWORDS[(i * 13) % KEYWORDS.length] : ''
  const follower = Math.max(200, Math.round(800 + ((i * 9973) % 240000)))
  const likes = follower * (8 + (i % 12))
  const videos = 5 + (i % 80)
  // 让一部分用户的 updated_at 落在 24h 内,支撑 kpis.newToday 计算
  const isFresh = (i % 23) === 0
  const updated = isFresh
    ? new Date(Date.now() - (i % 20) * 3600 * 1000).toISOString().slice(0, 19)
    : new Date(Date.now() - (3 + (i % 90)) * 86400 * 1000).toISOString().slice(0, 19)
  const created = new Date(Date.now() - (10 + (i % 120)) * 86400 * 1000).toISOString().slice(0, 19)
  MOCK_USERS.push({
    id: i,
    tiktok_id: `${platform}:${username}`,
    platform,
    username,
    nickname: `${niche} ${country}`,
    bio: `${category} · ${country} · mock #${i}`,
    follower_count: follower,
    following_count: 20 + (i % 400),
    like_count: likes,
    video_count: videos,
    country,
    category,
    status,
    source,
    source_keyword: keyword,
    created_at: created,
    updated_at: updated,
    profile_url: buildProfileUrl(platform, username),
  })
}

const MOCK_ACCOUNTS = [
  { id: 1, platform: 'tiktok', username: 'delong_official_01', login_method: 'qr', status: 'expired',    last_login_at: '2026-07-09T14:22:00', nickname: '德龙电气官方 · 主账号',       followers: 12800, videos: 142, likes: 68200, today: { comments: 0, dms: 0, replies: 0, currentTask: 'Cookie 已过期 · 已暂停' },  statusKey: 'offline' },
  { id: 2, platform: 'tiktok', username: 'delong_official_02', login_method: 'qr', status: 'logged_in',  last_login_at: '2026-07-11T11:54:00', nickname: '德龙电气 · 备用账号',         followers:  8400, videos:  87, likes: 42100, today: { comments: 18, dms: 8, replies: 5, currentTask: '@maison_zara_fr 私信发送中...' }, statusKey: 'on' },
  { id: 3, platform: 'douyin', username: 'delong_cn',         login_method: 'qr', status: 'logged_in',  last_login_at: '2026-07-10T09:18:00', nickname: '德龙电气 · 抖音同步',         followers: 24600, videos: 218, likes: 186000, today: { comments: 9,  dms: 4, replies: 2, currentTask: '空闲 · 等待下批' },              statusKey: 'on' },
  { id: 4, platform: 'tiktok', username: 'delong_official_03', login_method: 'qr', status: 'logged_in',  last_login_at: '2026-07-11T08:30:00', nickname: '德龙电气 · 海外版',           followers:  3100, videos:  34, likes: 12800, today: { comments: 6,  dms: 3, replies: 1, currentTask: '评论已发 · 等回复' },           statusKey: 'on' },
  { id: 5, platform: 'tiktok', username: 'delong_official_04', login_method: 'qr', status: 'pending',    last_login_at: '2026-07-11T07:00:00', nickname: '德龙电气 · 新号 · 养护中',    followers:   120, videos:   4, likes:   280, today: { comments: 0,  dms: 0, replies: 0, currentTask: '7 天养护期 · 不执行推广' },  statusKey: 'warn' },
]

const MOCK_DASHBOARD = {
  overview: {
    total_users: 1247,
    qualified_users: 891,
    today_new: 47,
    today_comments: 52,
    today_dms: 37,
    today_reply_rate: 0.146,
    today_leads: 13,
  },
  keywords: [
    { name: 'importer 1688', rate: 0.142 },
    { name: 'wholesale LED', rate: 0.116 },
    { name: 'sourcing agent', rate: 0.098 },
    { name: 'bulk buy China', rate: 0.071 },
    { name: 'retail dropship', rate: 0.054 },
  ],
  categories: [
    { category: '经销商', count: 525 },
    { category: '买家', count: 367 },
    { category: '同行', count: 145 },
    { category: '未知', count: 210 },
  ],
}

const MOCK_TREND = (() => {
  const days = 30
  const out: any[] = []
  const start = new Date('2026-06-12')
  for (let i = 0; i < days; i++) {
    const d = new Date(start.getTime() + i * 86400000)
    out.push({
      date: d.toISOString().slice(0, 10),
      new_users: 35 + i * 1.5 + Math.sin(i / 2) * 8,
      qualified: 5 + Math.round(i * 0.3 + Math.cos(i / 3) * 3),
      comments: 40 + i * 1.2 + Math.sin(i / 1.5) * 6,
      dms: 25 + i * 0.9 + Math.cos(i / 2) * 4,
      replies: 4 + Math.round(i * 0.4 + Math.sin(i / 4) * 2),
      reply_rate: 0.1 + i * 0.005 + Math.sin(i / 3) * 0.02,
    })
  }
  return out
})()

// Pipeline event stream. Shape: { timestamp, type, level, message, payload }
// — `level` is one of 'ok' | 'err' | '' (normal info) — drives the row tag color.
// — `message` is the human-readable line shown in the log.
//   Entries are sorted newest-first; mock returns up to 60 on each call to
// power the "每 5s 刷新 · 已显示最近 60 条" UI.
type PipelineEvent = {
  timestamp: string; type: string; level: '' | 'ok' | 'err'; message: string; payload?: any
}

// QR login session store — keyed by session_token
// Lifecycle: waiting (0-3s) → scanning (3-7s) → confirmed (7s+, assigned username)
//                ↘ expired (after 60s if not confirmed)
const MOCK_QR_SESSIONS: Record<string, { platform: string; username: string; createdAt: number; confirmed: boolean; expiresAt: number }> = {}

// Reports sub-panels on Reports.vue — funnel, regions, sentiment.
// One combined endpoint to keep network calls low.
const MOCK_LLM_SKILLS = [
  { name: 'tiktok-filter',    desc: '用户筛选评分',     stage: '环节 2',   calls: 1247, token: 842,  latency: '920ms', share: 90 },
  { name: 'tiktok-strategy',  desc: '用户画像 + 话术生成', stage: '环节 3', calls: 1156, token: 1420, latency: '1.2s',  share: 84 },
  { name: 'tiktok-collect',   desc: '搜索关键词 + 推荐流', stage: '环节 1', calls: 684,  token: 320,  latency: '450ms', share: 50 },
  { name: 'tiktok-iterate',   desc: '经验沉淀 + 规则优化', stage: '环节 6', calls: 412,  token: 2180, latency: '1.8s',  share: 30 },
  { name: 'tiktok-report',    desc: '日报生成 + 情感分析', stage: '环节 5', calls: 248,  token: 980,  latency: '780ms', share: 18 },
  { name: 'reply-classify',   desc: '回复情感 + 商业意图', stage: '环节 4 后',calls: 100,  token: 520,  latency: '620ms', share:  7 },
]

const MOCK_REPORTS_OVERVIEW = {
  funnel: [
    { label: 'imported',     count: 8742, pct: 100, color: 'oklch(14% 0.012 280)' },
    { label: 'qualified',    count: 2801, pct:  32, color: 'oklch(70% 0.12 200)' },
    { label: 'contacted',    count: 2418, pct:  28, color: 'oklch(58% 0.22 350)' },
    { label: 'replied',      count:  312, pct:   4, color: 'oklch(72% 0.16 75)' },
    { label: 'businessIntent', count:  87, pct:   1, color: 'oklch(62% 0.16 150)' },
  ],
  regions: [
    { name: '美国',     flag: '🇺🇸', replies: 82, rate: '14.8%', intent: 28, sharePct: 26.3 },
    { name: '英国',     flag: '🇬🇧', replies: 54, rate: '13.2%', intent: 18, sharePct: 17.3 },
    { name: '德国',     flag: '🇩🇪', replies: 42, rate: '15.6%', intent: 14, sharePct: 13.5 },
    { name: '韩国',     flag: '🇰🇷', replies: 31, rate: '11.8%', intent:  9, sharePct:  9.9 },
    { name: '法国',     flag: '🇫🇷', replies: 28, rate: '12.4%', intent:  7, sharePct:  9.0 },
    { name: '菲律宾',   flag: '🇵🇭', replies: 22, rate: '10.2%', intent:  4, sharePct:  7.1 },
    { name: '日本',     flag: '🇯🇵', replies: 19, rate: '13.8%', intent:  3, sharePct:  6.1 },
    { name: '巴西',     flag: '🇧🇷', replies: 14, rate:  '9.4%', intent:  2, sharePct:  4.5 },
    { name: '印度',     flag: '🇮🇳', replies: 12, rate:  '8.6%', intent:  1, sharePct:  3.8 },
    { name: '其他',     flag: '🌐', replies:  8, rate:  '7.1%', intent:  1, sharePct:  2.5 },
  ],
  sentiment: {
    positive: { pct: 60, count: 187, color: 'oklch(62% 0.16 150)', dasharray: '234 390' },
    neutral:  { pct: 28, count:  87, color: 'oklch(60% 0.08 280)', dasharray: '109 390', dashoffset: -234 },
    negative: { pct: 12, count:  38, color: 'oklch(60% 0.22 25)',  dasharray:  '47 390', dashoffset: -343 },
    avgScore: 0.62,
  },
}

// Per-user detail page (UserDetail.vue) — keyed by username.
// Real backend can populate these from the same DB rows + LLM artifacts.
const MOCK_USER_DETAIL: Record<string, {
  profile: { bio_zh: string; meta_zh: string; stats: { followers: number; videos: number; likes: number; engagement_pct: number } }
  breakdown: { name: string; v: number; cls: string }[]
  videos: { title: string; cls: string; views: string }[]
  timeline: { time: string; cls: string; who: string; desc: string; quote?: string }[]
  strategy: { body: string; window: string; gap: string; expected: string; historical: string }
}> = {
  aroma_house_us: {
    profile: {
      bio_zh: '主营精油、芳疗产品批发，覆盖美国南部 12 州线下门店。Bio 明确标注「Wholesale & Importer」「DM open for business」，定位为典型 B2B 经销商。视频内容以产品展示、批发流程、合作案例为主，目标客户为小型零售店与电商品卖家。',
      meta_zh: 'Aroma House US · Wholesale essential oils · Importer based in TX · 加入 2026-06-18 · 更新于 2 小时前',
      stats: { followers: 128400, videos: 412, likes: 2100000, engagement_pct: 4.8 },
    },
    breakdown: [
      { name: 'Bio 商业关键词', v: 96, cls: 'brand' },
      { name: '视频内容相关',   v: 94, cls: 'brand' },
      { name: '粉丝量级健康',   v: 88, cls: 'cyan' },
      { name: '互动率真实',     v: 82, cls: 'cyan' },
      { name: '地区匹配',       v: 100, cls: 'ok' },
      { name: '更新频率',       v: 76, cls: '' },
    ],
    videos: [
      { title: '精油进货流程',  cls: '',  views: '214K' },
      { title: '批发价格表',    cls: 'b', views: '187K' },
      { title: 'DM 收到报价',   cls: 'c', views: '96K' },
      { title: '新品薰衣草',    cls: 'd', views: '142K' },
    ],
    timeline: [
      { time: '07-11 11:48', cls: 'ok',       who: '收到用户回复',  desc: '商业意图识别 · sentiment: positive', quote: "Hi! Yes we'd love to chat about your OEM options. Do you have a catalog for the new lavender line? MOQ?" },
      { time: '07-10 14:22', cls: '',         who: '私信已发送',    desc: '使用策略 soft_sell · @delong_official_02 账号' },
      { time: '07-10 09:14', cls: '',         who: '评论已发送',    desc: '视频「精油进货流程」下 · 内容匹配批发流程关键词' },
      { time: '07-09 16:08', cls: 'warn',     who: '策略生成',      desc: 'soft_sell 经销商型 · 预计回复率 14-18%' },
      { time: '07-09 11:42', cls: 'ok',       who: '筛选合格',      desc: '环节 2 · LLM 判定为「经销商 / 高价值」' },
      { time: '07-09 09:18', cls: 'neutral',  who: '入库',          desc: '环节 1 搜集 · 来源关键词「importer 1688」' },
    ],
    strategy: {
      body: 'Hi! 看到你们做精油批发，我们工厂专注芳疗产品 OEM/ODM，私标起订 100 件，FDA 注册工厂，6 年稳定出货美国市场。如果你们对新品线或现有品类扩品有兴趣，可以聊聊合作模式 🎯',
      window: '触达窗口 9:00 – 21:00 (UTC-6)',
      gap: '评论→私信间隔 24h',
      expected: '期望回复率 14–18%',
      historical: '历史同画像 16.2%',
    },
  },
}

// Pipeline overview — feeds the "最近 7 天运行" history list and the
// "本轮结果" stage cards on the Pipeline page.
const MOCK_PIPELINE_OVERVIEW = {
  jobs: [
    { date: '07-11', status: '运行中',   statusCls: 'brand', detail: '89/120 触达',          duration: '3h 12m', jobId: '20260711-01' },
    { date: '07-10', status: '完成',     statusCls: 'ok',    detail: '312 用户 / 41 合格 / 14.6%', duration: '3h 41m', jobId: '20260710-01' },
    { date: '07-09', status: '完成',     statusCls: 'ok',    detail: '298 用户 / 38 合格 / 13.8%', duration: '3h 22m', jobId: '20260709-01' },
    { date: '07-08', status: '部分失败', statusCls: 'warn',  detail: '环节 4 限流 · 重试 2 次', duration: '4h 05m', jobId: '20260708-01' },
    { date: '07-07', status: '完成',     statusCls: 'ok',    detail: '341 用户 / 44 合格 / 14.1%', duration: '3h 28m', jobId: '20260707-01' },
    { date: '07-06', status: '完成',     statusCls: 'ok',    detail: '周日 · 含环节 6 迭代', duration: '4h 12m', jobId: '20260706-01' },
    { date: '07-05', status: '完成',     statusCls: 'ok',    detail: '329 用户 / 39 合格 / 12.9%', duration: '3h 35m', jobId: '20260705-01' },
  ],
  results: [
    { stage: 1, cls: 'ok',       msg: '搜集 328 用户 · 42m 12s' },
    { stage: 2, cls: 'ok',       msg: '合格 47 · 淘汰 281 · 28m 04s' },
    { stage: 3, cls: 'ok',       msg: '47 策略生成 · 18m 33s' },
    { stage: 4, cls: 'brand',    msg: '运行中 · 89/120 · 预计 25m' },
    { stage: 5, cls: 'pending',  msg: '待开始 · 21:00' },
    { stage: 6, cls: 'pending',  msg: '待开始 · 周日 22:00' },
  ],

  // Per-stage live data for the pipe-canvas / Dashboard pipeline strip.
  // Powers the 6 cards in both Dashboard.vue and Pipeline.vue.
  stages: [
    { index: 1, key: 'collect',  nameI18n: 'pipeline.collect',  descI18n: 'pipeline.collectDs',  ix: '01 / COLLECT',  status: 'done',    metric: '328',     metricLabelI18n: 'pipeline.usersStored',    extra: null,         time: '⏱ 42m 12s' },
    { index: 2, key: 'filter',   nameI18n: 'pipeline.filter',   descI18n: 'pipeline.filterDs',   ix: '02 / FILTER',   status: 'done',    metric: '47',      metricLabelI18n: 'pipeline.qualifiedCount', extra: '14.3% conversion', time: '⏱ 28m 04s' },
    { index: 3, key: 'strategy', nameI18n: 'pipeline.strategy', descI18n: 'pipeline.strategyDs', ix: '03 / STRATEGY', status: 'done',    metric: '47',      metricLabelI18n: 'pipeline.strategyGenerated', extra: '3 personas',  time: '⏱ 18m 33s' },
    { index: 4, key: 'outreach', nameI18n: 'pipeline.outreach', descI18n: 'pipeline.outreachDs', ix: '04 / OUTREACH', status: 'running', metric: '89 / 120', metricLabelI18n: 'pipeline.reached',       extra: '74%',         time: '⏱ 1h 04m · 预计 25m' },
    { index: 5, key: 'report',   nameI18n: 'pipeline.report',   descI18n: 'pipeline.reportDs',   ix: '05 / REPORT',   status: 'pending', metric: '—',       metricLabelI18n: 'pipeline.triggerAt',      extra: null,         time: '⏱ 预计 18m' },
    { index: 6, key: 'iterate',  nameI18n: 'pipeline.iterate',  descI18n: 'pipeline.iterateDs',  ix: '06 / ITERATE',  status: 'pending', metric: '—',       metricLabelI18n: 'pipeline.weeklySun',      extra: null,         time: '⏱ 预计 25m' },
  ],

  // Pipe-meta summary cards (Pipeline.vue only).
  summary: {
    totalDuration:    '3h 12m 38s',
    llmCalls:         '142 次',
    llmCost:          '¥8.42',
    browserOps:       '89 步',
    browserErrors:    '0 异常',
    accountSwitches:  '3 次',
    commentsSent:     '52 条',
    dmsSent:          '37 条',
  },

  // Dashboard top KPI strip + pipeline state chips.
  dashboard: {
    newQualifiedDelta: 47,                          // for "+47" line
    accountsRunningLabel: '3 / 3',                  // for the kpi-value
    accountsRunningHealthy: true,                   // for the chip
    doneCount: 4, runningCount: 1, pendingCount: 1, // for the 3 chips
    usedHours: 3, usedMinutes: 12,                 // for "3h 12m" in the strip
    pipeSubLine: '4/5 完成 · 1 进行中 · 1 待开始', // full sub-line
  },

  // Persona distribution donut (Dashboard right column).
  personaMix: [
    { key: 'distributor', pct: 42, color: 'oklch(58% 0.22 350)' },
    { key: 'buyer',      pct: 28, color: 'oklch(70% 0.12 200)' },
    { key: 'peer',       pct: 18, color: 'oklch(62% 0.16 150)' },
    { key: 'unknown',    pct: 12, color: 'oklch(72% 0.16 75)' },
  ],

  // Brand panel mini-cards (Login.vue left aside).
  brandPanel: {
    stages: [
      { key: 'collect', metric: '328' },
      { key: 'filter',  metric: '47' },
      { key: 'outreach', metric: '89' },
    ],
  },
}

const MOCK_PIPELINE_EVENTS: PipelineEvent[] = [
  // Most recent (matches the original 17 events, augmented with level + message)
  { timestamp: '2026-07-11T12:04:18', type: 'collect',  level: '',  message: '对 @aroma_house_us 视频「精油进货流程」发布评论', payload: { user: 'aroma_house_us', video: '精油进货流程' } },
  { timestamp: '2026-07-11T12:01:42', type: 'filter.done', level: 'ok', message: '筛选完成 · 合格 47 · 淘汰 281 · 耗时 28m', payload: { qualified: 47, rejected: 281, duration_min: 28 } },
  { timestamp: '2026-07-11T11:58:09', type: 'strategy', level: '',  message: '@korean_beauty_hub 策略生成 · soft_sell · DM 模板 KR-v3', payload: { user: 'korean_beauty_hub', type: 'soft_sell', template: 'KR-v3' } },
  { timestamp: '2026-07-11T11:54:31', type: 'outreach.fail', level: 'err', message: '@delong_official_01 Cookie 过期 · 已切换至 @delong_official_02', payload: { from: 'delong_official_01', to: 'delong_official_02' } },
  { timestamp: '2026-07-11T11:50:00', type: 'outreach', level: '',  message: '批量评论 12 条 · 平均间隔 6.4 min · 全部 200 OK', payload: { count: 12, avg_interval_min: 6.4 } },
  { timestamp: '2026-07-11T11:42:18', type: 'outreach', level: '',  message: '@tech_retailer_ph 评论已发 · 内容：OEM supplier inquiry', payload: { user: 'tech_retailer_ph', content: 'OEM supplier inquiry' } },
  { timestamp: '2026-07-11T11:36:05', type: 'llm.call', level: '',  message: 'DeepSeek v4 Pro · tokens 1842 → 612 · ¥0.21', payload: { model: 'deepseek-v4-pro', input_tokens: 1842, output_tokens: 612, cost: 0.21 } },
  { timestamp: '2026-07-11T11:30:22', type: 'outreach', level: '',  message: '@maison_zara_fr 私信已发 · 触达 1/3', payload: { user: 'maison_zara_fr', step: '1/3' } },
  { timestamp: '2026-07-11T11:24:51', type: 'outreach', level: '',  message: '@led_wholesale_uk 评论已发 · 3 条策略中第 1 条', payload: { user: 'led_wholesale_uk', step: '1/3' } },
  { timestamp: '2026-07-11T11:18:09', type: 'outreach', level: '',  message: '@sourcing_brothers_de 私信已发 · 触达 1/1', payload: { user: 'sourcing_brothers_de', step: '1/1' } },
  { timestamp: '2026-07-11T11:12:44', type: 'outreach.start', level: '', message: '环节 4 启动 · 47 个待触达用户 · 预计 1h 50m', payload: { pending: 47, estimated_min: 110 } },
  { timestamp: '2026-07-11T10:48:30', type: 'strategy.done', level: 'ok', message: '策略生成完成 · 47 套 · 平均 23.5s/套', payload: { count: 47, avg_s: 23.5 } },
  { timestamp: '2026-07-11T10:25:14', type: 'strategy', level: '', message: '@korean_beauty_hub 画像分类: buyer · 区域 KR · 评分 78', payload: { user: 'korean_beauty_hub', persona: 'buyer', region: 'KR', score: 78 } },
  { timestamp: '2026-07-11T10:15:00', type: 'strategy', level: '', message: '@aroma_house_us 画像分类: distributor · 区域 US · 评分 92', payload: { user: 'aroma_house_us', persona: 'distributor', region: 'US', score: 92 } },
  { timestamp: '2026-07-11T10:02:18', type: 'filter.start', level: '', message: '环节 2 启动 · 328 待筛 · 启用 6 维度评分', payload: { pending: 328, dimensions: 6 } },
  { timestamp: '2026-07-11T09:48:42', type: 'collect.done', level: 'ok', message: '搜集完成 · 328 用户 · 17 关键词命中 · 耗时 42m', payload: { count: 328, keywords: 17, duration_min: 42 } },
  { timestamp: '2026-07-11T09:00:00', type: 'pipeline.start', level: '', message: 'JOB #20260711-01 启动 · cron daily-pipeline · 6 环节', payload: { job_id: '20260711-01', stages: 6 } },

  // Synthetic earlier events — same shape, progressively older, mixed ok/err
  ...Array.from({ length: 45 }, (_, i): PipelineEvent => {
    const t = new Date('2026-07-11T08:59:50')
    t.setSeconds(t.getSeconds() - i * 75)
    const stages: Array<{ type: string; tmpl: string; lvl: '' | 'ok' | 'err' }> = [
      { type: 'collect',  tmpl: '@{u} 视频「{v}」命中关键词 importer 1688', lvl: '' },
      { type: 'llm.call', tmpl: 'DeepSeek v4 Pro · tokens {i} → {o} · ¥{c}', lvl: '' },
      { type: 'filter',   tmpl: '@{u} Bio 评分 {s} · {p}/{d}', lvl: 'ok' },
      { type: 'outreach', tmpl: '@{u} 评论已发 · 触达 {k}/{n}', lvl: '' },
      { type: 'strategy', tmpl: '@{u} 策略 {t} 已生成', lvl: '' },
      { type: 'outreach.fail', tmpl: '@{u} 触达失败 · 网络超时 · 已加入重试队列', lvl: 'err' },
      { type: 'collect',  tmpl: '关键词 "{k}" 新增 {n} 个候选用户', lvl: '' },
      { type: 'filter',   tmpl: '低质量账号剔除 · {n} 个', lvl: '' },
      { type: 'cookie',   tmpl: '@{u} Cookie 检测 · 仍在有效期', lvl: 'ok' },
      { type: 'reply',    tmpl: '@{u} 已回复 · sentiment: positive · 商业意图 {s}%', lvl: 'ok' },
    ]
    const u: string[] = ['aroma_house_us', 'led_wholesale_uk', 'korean_beauty_hub', 'sourcing_brothers_de', 'factory_direct_cn', 'maison_zara_fr', 'tech_retailer_ph', 'brazil_import_br', 'japan_craft_tokyo', 'india_wholesale_in']
    const v: string[] = ['精油进货流程', '批发价格表', 'OEM supplier inquiry', 'factory tour', 'bulk pricing', 'MOQ 详情', 'shipping options', 'catalog 2026']
    const k: string[] = ['importer 1688', 'wholesale LED', 'sourcing agent', 'bulk buy China', 'retail dropship', 'factory direct', 'private label']
    const pick = stages[i % stages.length] as { type: string; tmpl: string; lvl: '' | 'ok' | 'err' }
    const message = pick.tmpl
      .replace('{u}', u[i % u.length] ?? '')
      .replace('{v}', v[i % v.length] ?? '')
      .replace('{k}', k[i % k.length] ?? '')
      .replace('{s}', String(60 + (i * 7) % 40))
      .replace('{p}', String(20 + (i * 3) % 30))
      .replace('{d}', String(80 + (i * 5) % 20))
      .replace('{i}', String(800 + (i * 37) % 1200))
      .replace('{o}', String(300 + (i * 19) % 500))
      .replace('{c}', (0.1 + (i % 9) * 0.07).toFixed(2))
      .replace('{n}', String(5 + (i * 4) % 25))
      .replace('{t}', (['soft_sell', 'value_first', 'direct_offer', 'follow_up'] as string[])[i % 4] ?? '')
    return { timestamp: t.toISOString().slice(0, 19), type: pick.type, level: pick.lvl, message }
  }),
]

const MOCK_CONFIG = {
  has_api_key: true,
  llm_model: 'deepseek-v4-pro',
  llm_base_url: 'https://api.deepseek.com/v1',
  douyin_max_concurrency: 3,
  daily_comment_limit: 25,
  daily_dm_limit: 12,
  daily_users: 120,
  comment_interval_min: 3,
  comment_interval_max: 10,
  dm_interval_min: 8,
  dm_interval_max: 20,
  comment_dm_gap_hours: 24,
  tiktok_keywords: ['importer 1688', 'wholesale LED', 'sourcing agent', 'bulk buy China', 'retail dropship'],
  cron_daily_pipeline: '0 9 * * *',
  cron_daily_pipeline_time: '09:00',
  cron_daily_report: '0 21 * * *',
  cron_daily_report_time: '21:00',
  cron_weekly_iterate: '0 22 * * 0',
  cron_weekly_iterate_time: '周日 22:00',
  cron_cookie_check: '0 */6 * * *',
}

// ---- Lead search mock dataset --------------------------------------------
// Public TikTok search results (no login required) — used by the lead discovery
// feature in the Pipeline page. Returns profiles that match the keyword query.
interface LeadResult {
  id: number
  username: string
  nickname: string
  bio: string
  avatar_initials: string
  follower_count: number
  video_count: number
  country: string
  relevance_score: number           // 0–100, higher = more relevant
  matched_keyword: string
  url: string
}

const MOCK_LEADS_POOL: LeadResult[] = [
  { id: 1,  username: 'sourcing_pro_ny',      nickname: 'Sourcing Pro NY',      bio: 'Product sourcing & import/export · NYC based · DM for collab',         avatar_initials: 'SP', follower_count: 34200, video_count: 89,   country: 'US', relevance_score: 94, matched_keyword: 'sourcing agent',         url: 'https://www.tiktok.com/@sourcing_pro_ny' },
  { id: 2,  username: 'wholesale_king_dubai',  nickname: 'Wholesale King Dubai', bio: 'Wholesale electronics & appliances · Dubai · Global shipping',          avatar_initials: 'WK', follower_count: 87100, video_count: 214,  country: 'AE', relevance_score: 91, matched_keyword: 'wholesale',            url: 'https://www.tiktok.com/@wholesale_king_dubai' },
  { id: 3,  username: 'importer_michael',      nickname: 'Importer Michael',     bio: 'Importing from China 10+ yrs · MOQ 500+ · Open for new products',       avatar_initials: 'IM', follower_count: 12800, video_count: 45,   country: 'US', relevance_score: 97, matched_keyword: 'importer 1688',        url: 'https://www.tiktok.com/@importer_michael' },
  { id: 4,  username: 'berlin_b2b_agent',      nickname: 'Berlin B2B Agent',     bio: 'Sourcing agent for EU brands · OEM/ODM expert · DM open',              avatar_initials: 'BA', follower_count: 24100, video_count: 67,   country: 'DE', relevance_score: 88, matched_keyword: 'sourcing agent',         url: 'https://www.tiktok.com/@berlin_b2b_agent' },
  { id: 5,  username: 'london_fashion_buyer',  nickname: 'London Fashion Buyer', bio: 'Fashion buyer · sourcing sustainable fabrics & packaging',              avatar_initials: 'LF', follower_count: 56300, video_count: 134,  country: 'GB', relevance_score: 85, matched_keyword: 'bulk order',           url: 'https://www.tiktok.com/@london_fashion_buyer' },
  { id: 6,  username: 'dubai_logistics_hub',   nickname: 'Dubai Logistics Hub',  bio: 'Freight forwarding & warehousing · China ↔ Middle East',               avatar_initials: 'DL', follower_count: 18700, video_count: 52,   country: 'AE', relevance_score: 82, matched_keyword: 'logistics',            url: 'https://www.tiktok.com/@dubai_logistics_hub' },
  { id: 7,  username: 'toronto_retail_chain',  nickname: 'Toronto Retail Chain', bio: 'Retail chain owner · looking for new suppliers & private label',        avatar_initials: 'TR', follower_count: 42100, video_count: 98,   country: 'CA', relevance_score: 79, matched_keyword: 'private label',        url: 'https://www.tiktok.com/@toronto_retail_chain' },
  { id: 8,  username: 'sao_paulo_importer',    nickname: 'São Paulo Importer',   bio: 'Importando da China · distribuidor de eletrônicos · 5 anos',           avatar_initials: 'SP', follower_count: 31200, video_count: 76,   country: 'BR', relevance_score: 76, matched_keyword: 'importer 1688',        url: 'https://www.tiktok.com/@sao_paulo_importer' },
  { id: 9,  username: 'milan_fashion_dist',    nickname: 'Milan Fashion Dist',   bio: 'Fashion distributor · B2B only · MOQ 200 pcs · worldwide shipping',     avatar_initials: 'MF', follower_count: 69400, video_count: 156,  country: 'IT', relevance_score: 90, matched_keyword: 'wholesale',            url: 'https://www.tiktok.com/@milan_fashion_dist' },
  { id: 10, username: 'singapore_tech_sourcing', nickname: 'SG Tech Sourcing',   bio: 'Tech component sourcing · Asia Pacific distribution · ISO certified', avatar_initials: 'TS', follower_count: 15600, video_count: 34,   country: 'SG', relevance_score: 87, matched_keyword: 'sourcing agent',         url: 'https://www.tiktok.com/@singapore_tech_sourcing' },
  { id: 11, username: 'mexico_wholesale_mx',   nickname: 'México Wholesale',     bio: 'Mayoreo de productos · importación directa · 20+ años experiencia',      avatar_initials: 'MW', follower_count: 22400, video_count: 58,   country: 'MX', relevance_score: 73, matched_keyword: 'wholesale',            url: 'https://www.tiktok.com/@mexico_wholesale_mx' },
  { id: 12, username: 'australia_import_au',   nickname: 'Australia Import',     bio: 'Importing home & living products · looking for new OEM partners',       avatar_initials: 'AI', follower_count: 28100, video_count: 72,   country: 'AU', relevance_score: 84, matched_keyword: 'OEM',                  url: 'https://www.tiktok.com/@australia_import_au' },
  { id: 13, username: 'dutch_food_importer',   nickname: 'Dutch Food Importer',  bio: 'Food & beverage importer · EU organic certification required',           avatar_initials: 'DF', follower_count: 9800,  video_count: 23,   country: 'NL', relevance_score: 81, matched_keyword: 'bulk order',           url: 'https://www.tiktok.com/@dutch_food_importer' },
  { id: 14, username: 'seoul_kbeauty_dist',    nickname: 'Seoul K-Beauty Dist',  bio: 'K-Beauty distributor · worldwide shipping · brand partnership open',    avatar_initials: 'SK', follower_count: 52300, video_count: 187,  country: 'KR', relevance_score: 92, matched_keyword: 'private label',        url: 'https://www.tiktok.com/@seoul_kbeauty_dist' },
  { id: 15, username: 'lagos_trade_ng',        nickname: 'Lagos Trade',          bio: 'West Africa import/export · electronics & machinery · MOQ negotiable', avatar_initials: 'LT', follower_count: 11400, video_count: 31,   country: 'NG', relevance_score: 68, matched_keyword: 'importer 1688',        url: 'https://www.tiktok.com/@lagos_trade_ng' },
]

// ---- Mock responses ------------------------------------------------------

const wrap = <T,>(data: T) => Promise.resolve({ data, status: 200, statusText: 'OK', headers: {}, config: {} as any })
const clone = <T,>(value: T): T => structuredClone(value)

const PIPELINE_STAGE_ORDER: PipelineStageName[] = [
  'collect',
  'filter',
  'strategy',
  'outreach',
  'report',
  'iterate',
]

let mockPipelineSequence = 2
let mockScheduleSequence = 2
let mockAcquisitionSequence = 0

const mockStages = (
  stages: PipelineStageName[],
  status: PipelineStage['status'] = 'pending',
): PipelineStage[] => stages.map((stage, index) => ({
  id: mockPipelineSequence * 100 + index,
  stage,
  order: PIPELINE_STAGE_ORDER.indexOf(stage),
  status,
  attempt: status === 'pending' ? 0 : 1,
  result: {},
  errorMessage: '',
  startedAt: status === 'pending' ? null : new Date().toISOString(),
  finishedAt: status === 'succeeded' ? new Date().toISOString() : null,
}))

const MOCK_PIPELINE_JOBS: PipelineJob[] = [
  {
    id: 'mock-job-douyin-running',
    triggerType: 'manual',
    scheduleId: null,
    platform: 'douyin',
    accountMode: 'specified',
    accountId: 3,
    requestedStages: ['collect', 'filter'],
    stages: [
      { ...mockStages(['collect'], 'succeeded')[0]!, id: 101 },
      { ...mockStages(['filter'], 'running')[0]!, id: 102, order: 1 },
    ],
    configSnapshot: {},
    status: 'running',
    currentStage: 'filter',
    priority: 100,
    retryOfJobId: null,
    errorSummary: '',
    queuedAt: '2026-07-26T08:00:00Z',
    startedAt: '2026-07-26T08:00:02Z',
    finishedAt: null,
    createdAt: '2026-07-26T08:00:00Z',
    updatedAt: '2026-07-26T08:00:05Z',
  },
  {
    id: 'mock-job-tiktok-blocked-history',
    triggerType: 'schedule',
    scheduleId: 1,
    platform: 'tiktok',
    accountMode: 'auto',
    accountId: null,
    requestedStages: ['collect'],
    stages: mockStages(['collect'], 'failed'),
    configSnapshot: {},
    status: 'failed',
    currentStage: 'collect',
    priority: 100,
    retryOfJobId: null,
    errorSummary: 'fingerprint_provider_unavailable',
    queuedAt: '2026-07-25T08:00:00Z',
    startedAt: '2026-07-25T08:00:01Z',
    finishedAt: '2026-07-25T08:00:02Z',
    createdAt: '2026-07-25T08:00:00Z',
    updatedAt: '2026-07-25T08:00:02Z',
  },
]

const MOCK_PIPELINE_SCHEDULES: PipelineSchedule[] = [
  {
    id: 1,
    name: '每日抖音线索任务',
    platform: 'douyin',
    accountMode: 'auto',
    accountId: null,
    stages: ['collect', 'filter'],
    cronExpression: '0 9 * * *',
    timezone: 'Asia/Shanghai',
    enabled: true,
    config: {},
    nextRunAt: '2026-07-27T01:00:00Z',
    lastRunAt: '2026-07-26T01:00:00Z',
    createdAt: '2026-07-20T08:00:00Z',
    updatedAt: '2026-07-26T01:00:00Z',
  },
  {
    id: 2,
    name: '暂停的 TikTok 计划',
    platform: 'tiktok',
    accountMode: 'auto',
    accountId: null,
    stages: ['collect'],
    cronExpression: '0 10 * * *',
    timezone: 'Asia/Shanghai',
    enabled: false,
    config: {},
    nextRunAt: null,
    lastRunAt: null,
    createdAt: '2026-07-20T08:00:00Z',
    updatedAt: '2026-07-20T08:00:00Z',
  },
]

const mockNotFound = (kind: 'job' | 'schedule', id: string | number): never => {
  const error = new Error(`${kind} ${id} not found`) as Error & {
    response: { status: number; data: { detail: { code: string; message: string } } }
  }
  error.response = {
    status: 404,
    data: {
      detail: {
        code: `${kind}_not_found`,
        message: `${kind} ${id} not found`,
      },
    },
  }
  throw error
}

const mockPipelineError = (code: string, message: string, status = 409): never => {
  const error = new Error(message) as Error & {
    code: string
    response: { status: number; data: { detail: { code: string; message: string } } }
  }
  error.code = code
  error.response = {
    status,
    data: { detail: { code, message } },
  }
  throw error
}

const validateMockPipelineConfig = (
  payload: PipelineRuntimeConfigPayload,
): PipelineRuntimeConfigPayload => {
  const ranges: Record<
    Exclude<keyof PipelineRuntimeConfigPayload, 'tiktok_keywords'>,
    [number, number]
  > = {
    daily_users: [20, 500],
    daily_comment_limit: [1, 50],
    daily_dm_limit: [1, 30],
    comment_interval_min: [1, 60],
    comment_interval_max: [1, 120],
    dm_interval_min: [1, 60],
    dm_interval_max: [1, 120],
    comment_dm_gap_hours: [6, 72],
    douyin_max_concurrency: [1, 20],
  }
  const normalized = {} as PipelineRuntimeConfigPayload
  for (const [key, [minimum, maximum]] of Object.entries(ranges) as Array<
    [Exclude<keyof PipelineRuntimeConfigPayload, 'tiktok_keywords'>, [number, number]]
  >) {
    const value = payload?.[key]
    if (!Number.isInteger(value) || value < minimum || value > maximum) {
      return mockPipelineError(
        'invalid_config_value',
        `${key} 必须是 ${minimum}..${maximum} 范围内的整数`,
        422,
      )
    }
    ;(normalized as any)[key] = value
  }
  if (normalized.comment_interval_min > normalized.comment_interval_max) {
    return mockPipelineError(
      'invalid_config_value',
      'comment_interval_min 不能大于 comment_interval_max',
      422,
    )
  }
  if (normalized.dm_interval_min > normalized.dm_interval_max) {
    return mockPipelineError(
      'invalid_config_value',
      'dm_interval_min 不能大于 dm_interval_max',
      422,
    )
  }
  if (!Array.isArray(payload?.tiktok_keywords)) {
    return mockPipelineError(
      'invalid_config_value',
      'tiktok_keywords 必须是字符串数组',
      422,
    )
  }
  const keywords: string[] = []
  const seen = new Set<string>()
  for (const item of payload.tiktok_keywords) {
    if (typeof item !== 'string') {
      return mockPipelineError(
        'invalid_config_value',
        'tiktok_keywords 只能包含字符串',
        422,
      )
    }
    const keyword = item.trim()
    if (keyword && !seen.has(keyword)) {
      seen.add(keyword)
      keywords.push(keyword)
    }
  }
  normalized.tiktok_keywords = keywords
  return normalized
}

const parseCronField = (
  source: string,
  min: number,
  max: number,
  normalize: (value: number) => number = value => value,
): Set<number> => {
  const values = new Set<number>()
  for (const rawPart of source.split(',')) {
    const [rangePart, rawStep] = rawPart.split('/')
    const step = rawStep === undefined ? 1 : Number(rawStep)
    if (!Number.isInteger(step) || step <= 0 || !rangePart) {
      throw new Error(`invalid cron field: ${source}`)
    }
    let start: number
    let end: number
    if (rangePart === '*') {
      start = min
      end = max
    } else if (rangePart.includes('-')) {
      const [rawStart, rawEnd] = rangePart.split('-')
      start = Number(rawStart)
      end = Number(rawEnd)
    } else {
      start = Number(rangePart)
      end = start
    }
    if (
      !Number.isInteger(start)
      || !Number.isInteger(end)
      || start < min
      || end > max
      || start > end
    ) {
      throw new Error(`invalid cron field: ${source}`)
    }
    for (let value = start; value <= end; value += step) {
      values.add(normalize(value))
    }
  }
  if (!values.size) throw new Error(`invalid cron field: ${source}`)
  return values
}

const nextMockCronRun = (
  cronExpression: string,
  timezone: string,
  after = new Date(),
): string => {
  const fields = cronExpression.trim().split(/\s+/)
  if (fields.length !== 5) throw new Error('cron expression must have five fields')
  const [minuteField, hourField, dayField, monthField, weekdayField] = fields
  const minutes = parseCronField(minuteField!, 0, 59)
  const hours = parseCronField(hourField!, 0, 23)
  const days = parseCronField(dayField!, 1, 31)
  const months = parseCronField(monthField!, 1, 12)
  const weekdays = parseCronField(weekdayField!, 0, 7, value => value === 7 ? 0 : value)
  const formatter = new Intl.DateTimeFormat('en-US', {
    timeZone: timezone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  })
  const dayWildcard = dayField === '*'
  const weekdayWildcard = weekdayField === '*'
  const sortedMinutes = [...minutes].sort((left, right) => left - right)
  const candidate = new Date(after)
  candidate.setUTCSeconds(0, 0)
  candidate.setUTCMinutes(candidate.getUTCMinutes() + 1)
  const deadline = new Date(candidate)
  deadline.setUTCFullYear(deadline.getUTCFullYear() + 8)

  while (candidate <= deadline) {
    const parts = Object.fromEntries(
      formatter.formatToParts(candidate)
        .filter(part => part.type !== 'literal')
        .map(part => [part.type, Number(part.value)]),
    )
    const year = parts.year!
    const month = parts.month!
    const day = parts.day!
    const hour = parts.hour!
    const minute = parts.minute!
    const weekday = new Date(Date.UTC(year, month - 1, day)).getUTCDay()
    const dayMatches = days.has(day)
    const weekdayMatches = weekdays.has(weekday)
    const calendarDayMatches = dayWildcard
      ? weekdayMatches
      : weekdayWildcard
        ? dayMatches
        : dayMatches || weekdayMatches
    if (!months.has(month) || !calendarDayMatches || !hours.has(hour)) {
      candidate.setUTCMinutes(candidate.getUTCMinutes() + (60 - minute || 60))
      continue
    }
    if (minutes.has(minute)) {
      return candidate.toISOString()
    }
    const nextMinute = sortedMinutes.find(value => value > minute)
    const advance = nextMinute === undefined
      ? (60 - minute) + sortedMinutes[0]!
      : nextMinute - minute
    candidate.setUTCMinutes(candidate.getUTCMinutes() + advance)
  }
  throw new Error('cron expression has no run in the next eight years')
}

const createMockPipelineJob = (payload: CreatePipelineJobPayload) => {
  const now = new Date().toISOString()
  const job: PipelineJob = {
    id: `mock-job-${Date.now()}-${++mockPipelineSequence}`,
    triggerType: 'manual',
    scheduleId: null,
    platform: payload.platform,
    accountMode: payload.accountMode,
    accountId: payload.accountMode === 'specified' ? (payload.accountId ?? null) : null,
    requestedStages: [...payload.stages],
    stages: mockStages(payload.stages),
    configSnapshot: clone(payload.configSnapshot ?? {}),
    status: 'queued',
    currentStage: '',
    priority: 100,
    retryOfJobId: null,
    errorSummary: '',
    queuedAt: now,
    startedAt: null,
    finishedAt: null,
    createdAt: now,
    updatedAt: now,
  }
  MOCK_PIPELINE_JOBS.unshift(job)
  return wrap({ job: clone(job) })
}

const createMockAcquisitionJob = async (
  payload: CreateAcquisitionJobPayload,
): Promise<{ data: CreateAcquisitionJobResponse }> => {
  const job = (await createMockPipelineJob(payload)).data.job
  const createdAt = job.createdAt
  const campaignInput = payload.campaign
  const campaign: AcquisitionCampaign = {
    id: ++mockAcquisitionSequence,
    jobId: job.id,
    platform: job.platform,
    countries: [...(campaignInput.countries ?? [])],
    languages: [...(campaignInput.languages ?? [])],
    industries: [...(campaignInput.industries ?? [])],
    products: [...(campaignInput.products ?? [])],
    customerRoles: [...(campaignInput.customerRoles ?? [])],
    hardConditions: {
      excludedSubjects: [...(campaignInput.hardConditions?.excludedSubjects ?? [])],
      requiredKeywords: [...(campaignInput.hardConditions?.requiredKeywords ?? [])],
      mustBeBusinessAccount: campaignInput.hardConditions?.mustBeBusinessAccount ?? null,
      notListed: campaignInput.hardConditions?.notListed ?? null,
    },
    preferenceConditions: {
      employeeCount: campaignInput.preferenceConditions?.employeeCount ?? null,
      registeredCapital: campaignInput.preferenceConditions?.registeredCapital ?? null,
      listingStatus: campaignInput.preferenceConditions?.listingStatus ?? null,
      companyScale: campaignInput.preferenceConditions?.companyScale ?? null,
      minimumYearsEstablished:
        campaignInput.preferenceConditions?.minimumYearsEstablished ?? null,
      maximumYearsEstablished:
        campaignInput.preferenceConditions?.maximumYearsEstablished ?? null,
    },
    excludedTargets: [...(campaignInput.excludedTargets ?? [])],
    searchBudget: {
      maxKeywords: campaignInput.searchBudget?.maxKeywords ?? 20,
      maxVideosPerKeyword: campaignInput.searchBudget?.maxVideosPerKeyword ?? 20,
      maxCommentsPerVideo: campaignInput.searchBudget?.maxCommentsPerVideo ?? 30,
      maxAuthorVideos: campaignInput.searchBudget?.maxAuthorVideos ?? 5,
      maxPages: campaignInput.searchBudget?.maxPages ?? 10,
      maxDurationMinutes: campaignInput.searchBudget?.maxDurationMinutes ?? 60,
      maxLlmCalls: campaignInput.searchBudget?.maxLlmCalls ?? 100,
    },
    keywordMix: {
      effectivePercent: campaignInput.keywordMix?.effectivePercent ?? 70,
      newPercent: campaignInput.keywordMix?.newPercent ?? 30,
    },
    createdAt,
  }
  const keywords: AcquisitionKeyword[] = payload.keywords.map((input) => ({
    id: ++mockAcquisitionSequence,
    jobId: job.id,
    platform: job.platform,
    text: input.text,
    language: input.language ?? '',
    keywordType: input.keywordType ?? 'industry',
    source: input.source ?? 'manual',
    status: input.status ?? 'new',
    usageCount: 0,
    videoCount: 0,
    relevantVideoCount: 0,
    candidateCount: 0,
    qualifiedCount: 0,
    replyCount: 0,
    businessLeadCount: 0,
    lastUsedAt: null,
    createdAt,
    updatedAt: createdAt,
  }))
  return wrap({
    job: clone(job),
    campaign: clone(campaign),
    keywords: clone(keywords),
  })
}

const scheduleFromPayload = (
  id: number,
  payload: PipelineSchedulePayload,
  createdAt = new Date().toISOString(),
): PipelineSchedule => {
  const now = new Date().toISOString()
  return {
    id,
    name: payload.name,
    platform: payload.platform,
    accountMode: payload.accountMode,
    accountId: payload.accountMode === 'specified' ? (payload.accountId ?? null) : null,
    stages: [...payload.stages],
    cronExpression: payload.cronExpression,
    timezone: payload.timezone,
    enabled: payload.enabled,
    config: clone(payload.config ?? payload.configSnapshot ?? {}),
    nextRunAt: payload.enabled
      ? nextMockCronRun(payload.cronExpression, payload.timezone)
      : null,
    lastRunAt: null,
    createdAt,
    updatedAt: now,
  }
}

export const mockApi = {
  // Auth
  login: (username: string, password: string) => sleep(200).then(() => {
    const fail = (detail: string) => {
      const err = new Error(detail) as Error & { response: { data: { detail: string } } }
      err.response = { data: { detail } }
      throw err
    }
    if (!username || !password) fail('用户名或密码不能为空')
    if (password.length < 4) fail('密码至少 4 位')
    return wrap({ access_token: 'mock-token-' + Date.now(), username, role: 'admin' })
  }),
  register: () => sleep(200).then(() => wrap({ ok: true })),
  me: () => wrap({ authenticated: true, username: 'ops@delong', role: 'admin' }),

  // Users
  getUsers: (params: any = {}) => sleep(150).then(() => {
    const { status, category, limit, offset = 0 } = params || {}
    let items = MOCK_USERS
    if (status)   items = items.filter((u: any) => u.status === status)
    if (category) items = items.filter((u: any) => u.category === category)
    const total = items.length
    // Mock 默认返回全量（让前端可以客户端筛选 + 分页,与 subtitle total 自洽）；
    // 真实后端会自己用 limit 截断。
    const sliced = typeof limit === 'number' ? items.slice(offset, offset + limit) : items
    return wrap({ items: sliced, total })
  }),
  getUserStats: () => {
    // 根据 MOCK_USERS 实际分布计算,保证 /api/users 与 /api/users/stats
    // 返回的数字自洽(subtitle / kpis / 表格分页用同一来源)。
    const counts: Record<string, number> = { pending: 0, qualified: 0, contacted: 0, replied: 0, rejected: 0 }
    const byPersona: Record<string, number> = { distributor: 0, buyer: 0, peer: 0, unknown: 0 }
    for (const u of MOCK_USERS) {
      counts[u.status] = (counts[u.status] || 0) + 1
      byPersona[u.category] = (byPersona[u.category] || 0) + 1
    }
    return wrap({
      total: MOCK_USERS.length,
      pending: counts.pending,
      qualified: counts.qualified,
      contacted: counts.contacted,
      replied: counts.replied,
      rejected: counts.rejected,
      // persona 维度(对齐真实后端 /api/users/stats 的 by_persona),
      // 让前端 chip "经销商/买家/同行" 也走权威源。
      by_persona: byPersona,
    })
  },
  // Per-user detail page — falls back to a generic skeleton for users without
  // a bespoke entry, so demo still renders something for any username.
  getUserDetail: (username: string) => sleep(150).then(() => {
    const known = MOCK_USER_DETAIL[username]
    if (known) return wrap({ username, ...known })
    return wrap({
      username,
      profile: {
        bio_zh: `${username} 是该平台上的活跃 B2B 客户，主要通过关键词搜索和推荐流进入系统。Bio 与视频内容显示其有明确的采购或合作意向，目前由 Hermes Agent 的多环节 Pipeline 处理中。`,
        meta_zh: `@${username} · 跨境采购 · 加入 2026-06 · 数据由 demo 占位`,
        stats: { followers: 50000, videos: 120, likes: 800000, engagement_pct: 3.2 },
      },
      breakdown: [
        { name: 'Bio 商业关键词', v: 72, cls: 'brand' },
        { name: '视频内容相关',   v: 68, cls: 'brand' },
        { name: '粉丝量级健康',   v: 76, cls: 'cyan' },
        { name: '互动率真实',     v: 70, cls: 'cyan' },
        { name: '地区匹配',       v: 80, cls: 'ok' },
        { name: '更新频率',       v: 64, cls: '' },
      ],
      videos: [
        { title: '产品展示',  cls: '',  views: '85K' },
        { title: '批发流程',  cls: 'b', views: '62K' },
        { title: '合作案例',  cls: 'c', views: '47K' },
      ],
      timeline: [
        { time: '07-10 09:14', cls: '',         who: '入库',         desc: '环节 1 搜集 · 来源关键词推荐' },
        { time: '07-10 11:42', cls: 'ok',       who: '筛选合格',     desc: '环节 2 · LLM 判定为潜在客户' },
        { time: '07-10 16:08', cls: 'warn',     who: '策略生成',     desc: '标准话术 · 预计回复率 10-15%' },
      ],
      strategy: {
        body: 'Hi! 我们专注 B2B 跨境采购合作，看到你们的内容与我们的产品线契合，可以聊聊合作模式 🎯',
        window: '触达窗口 9:00 – 21:00 (本地时区)',
        gap: '评论→私信间隔 24h',
        expected: '期望回复率 10–15%',
        historical: '历史同画像 12%',
      },
    })
  }),

  // Pipeline / Dashboard
  getDashboard: () => sleep(150).then(() => wrap(MOCK_DASHBOARD)),
  getPipelineEvents: (limit = 60) => sleep(150).then(() => wrap(MOCK_PIPELINE_EVENTS.slice(0, limit))),
  getPipelineOverview: () => sleep(100).then(() => wrap(MOCK_PIPELINE_OVERVIEW)),
  createAcquisitionJob: (payload: CreateAcquisitionJobPayload) =>
    sleep(100).then(() => createMockAcquisitionJob(payload)),
  createPipelineJob: (payload: CreatePipelineJobPayload) =>
    sleep(100).then(() => createMockPipelineJob(payload)),
  listPipelineJobs: (params: PipelineJobListParams = {}) => sleep(80).then(() => {
    const limit = params.limit ?? 50
    const offset = params.offset ?? 0
    const filtered = MOCK_PIPELINE_JOBS.filter(job =>
      (!params.platform || job.platform === params.platform)
      && (!params.status || job.status === params.status),
    )
    return wrap({
      items: filtered.slice(offset, offset + limit).map(clone),
      total: filtered.length,
      limit,
      offset,
    })
  }),
  getPipelineJob: (jobId: string) => sleep(50).then(() => {
    const job = MOCK_PIPELINE_JOBS.find(item => item.id === jobId)
    return job ? wrap({ job: clone(job) }) : mockNotFound('job', jobId)
  }),
  cancelPipelineJob: (jobId: string) => sleep(80).then(() => {
    const job = MOCK_PIPELINE_JOBS.find(item => item.id === jobId)
    if (!job) return mockNotFound('job', jobId)
    const now = new Date().toISOString()
    if (job.status === 'queued') {
      job.status = 'cancelled'
      job.finishedAt = now
      job.updatedAt = now
    } else if (job.status === 'running') {
      job.status = 'cancelling'
      job.updatedAt = now
    }
    return wrap({ job: clone(job) })
  }),
  retryPipelineJob: (jobId: string) => sleep(80).then(async () => {
    const original = MOCK_PIPELINE_JOBS.find(item => item.id === jobId)
    if (!original) return mockNotFound('job', jobId)
    if (!['failed', 'partial_failed', 'interrupted'].includes(original.status)) {
      return mockPipelineError(
        'job_not_retryable',
        `cannot retry pipeline job in ${original.status} state`,
      )
    }
    const response = await createMockPipelineJob({
      platform: original.platform,
      accountMode: original.accountMode,
      accountId: original.accountId,
      stages: original.requestedStages,
      configSnapshot: original.configSnapshot,
    })
    response.data.job.triggerType = 'retry'
    response.data.job.retryOfJobId = original.id
    const stored = MOCK_PIPELINE_JOBS.find(item => item.id === response.data.job.id)!
    stored.triggerType = 'retry'
    stored.retryOfJobId = original.id
    return response
  }),
  getPipelineCapabilities: () => sleep(60).then(() => wrap({
    platforms: {
      tiktok: {
        available: false,
        providerAvailable: false,
        provider: 'fingerprint',
        code: 'fingerprint_provider_unavailable',
        message: '尚未配置 TikTok 指纹浏览器 Provider',
        accountCount: MOCK_ACCOUNTS.filter(account =>
          account.platform === 'tiktok' && account.status === 'logged_in',
        ).length,
        maxConcurrency: 1,
      },
      douyin: {
        available: MOCK_ACCOUNTS.some(account =>
          account.platform === 'douyin' && account.status === 'logged_in',
        ),
        providerAvailable: true,
        provider: 'playwright',
        code: '',
        message: '',
        accountCount: MOCK_ACCOUNTS.filter(account =>
          account.platform === 'douyin' && account.status === 'logged_in',
        ).length,
        maxConcurrency: 3,
      },
    },
  })),
  createPipelineSchedule: (payload: PipelineSchedulePayload) => sleep(100).then(() => {
    const schedule = scheduleFromPayload(++mockScheduleSequence, payload)
    MOCK_PIPELINE_SCHEDULES.unshift(schedule)
    return wrap({ schedule: clone(schedule) })
  }),
  listPipelineSchedules: (platform?: CreatePipelineJobPayload['platform']) =>
    sleep(60).then(() => {
      const items = platform
        ? MOCK_PIPELINE_SCHEDULES.filter(schedule => schedule.platform === platform)
        : MOCK_PIPELINE_SCHEDULES
      return wrap({ items: items.map(clone), total: items.length })
    }),
  updatePipelineSchedule: (
    scheduleId: number,
    payload: PipelineSchedulePayload,
  ) => sleep(100).then(() => {
    const index = MOCK_PIPELINE_SCHEDULES.findIndex(item => item.id === scheduleId)
    if (index < 0) return mockNotFound('schedule', scheduleId)
    const existing = MOCK_PIPELINE_SCHEDULES[index]!
    const schedule = scheduleFromPayload(scheduleId, payload, existing.createdAt ?? undefined)
    schedule.lastRunAt = existing.lastRunAt
    MOCK_PIPELINE_SCHEDULES[index] = schedule
    return wrap({ schedule: clone(schedule) })
  }),
  deletePipelineSchedule: (scheduleId: number) => sleep(80).then(() => {
    const index = MOCK_PIPELINE_SCHEDULES.findIndex(item => item.id === scheduleId)
    if (index < 0) return mockNotFound('schedule', scheduleId)
    MOCK_PIPELINE_SCHEDULES.splice(index, 1)
    return wrap(undefined)
  }),
  runPipeline: (
    stages: PipelineStageName[],
    opts: Partial<Omit<CreatePipelineJobPayload, 'stages'>> = {},
  ) => {
    if (!opts.platform || !opts.accountMode) {
      return mockPipelineError(
        'pipeline_selection_required',
        '请选择执行平台和账号模式',
        422,
      )
    }
    return createMockPipelineJob({
      platform: opts.platform,
      accountMode: opts.accountMode,
      accountId: opts.accountId,
      configSnapshot: opts.configSnapshot,
      stages,
    })
  },
  streamPipelineEvents: () => sleep(150).then(() => wrap({})),

  // Reports
  getDailyReport: () => sleep(100).then(() => wrap({
    date: new Date().toISOString().slice(0, 10),
    new_users_found: 47,
    users_qualified: 13,
    users_rejected: 281,
    comments_sent: 52,
    dms_sent: 37,
    replies_received: 14,
    reply_rate: 0.146,
    positive_replies: 8,
    business_leads: 3,
  })),
  getTrendReport: (days = 30) => sleep(150).then(() => wrap(MOCK_TREND.slice(-days))),
  // Wordcloud — `lang` switches the demo pool ('en' | 'cn').
  // `limit` lets callers request the top-N for compact surfaces.
  getWordcloud: (lang: 'en' | 'cn' = 'en', limit?: number) => sleep(100).then(() => {
    const pool = lang === 'cn' ? MOCK_WORDCLOUD_CN : MOCK_WORDCLOUD_EN
    return wrap(typeof limit === 'number' ? pool.slice(0, limit) : pool)
  }),
  // Reports sub-panels — funnel, regions, sentiment (one round-trip)
  getReportsOverview: () => sleep(150).then(() => wrap(MOCK_REPORTS_OVERVIEW)),
  // LLM management deliberately has no mock endpoint: secrets, routes, and
  // connectivity checks must always reflect the real backend.
  // Config
  getConfig: () => sleep(100).then(() => wrap(MOCK_CONFIG)),
  updatePipelineConfig: (payload: PipelineRuntimeConfigPayload) => sleep(100).then(() => {
    const normalized = validateMockPipelineConfig(payload)
    const previousConcurrency = MOCK_CONFIG.douyin_max_concurrency
    const committed: PipelineRuntimeConfigPayload = {
      ...normalized,
      tiktok_keywords: [...normalized.tiktok_keywords],
    }
    Object.assign(MOCK_CONFIG, committed)
    const response: PipelineRuntimeConfigResponse = {
      status: 'ok',
      config: {
        ...committed,
        tiktok_keywords: [...committed.tiktok_keywords],
      },
      restartRequired: previousConcurrency !== committed.douyin_max_concurrency,
    }
    return wrap(response)
  }),
  setConfigKey: (key: string, value: string) => sleep(100).then(() => {
    let persistedValue: string | number = value
    let responseValue = value
    let restartRequired = false
    if (key === 'douyin_max_concurrency') {
      const parsed = Number(value.trim())
      if (!Number.isInteger(parsed)) {
        return mockPipelineError(
          'invalid_config_value',
          '抖音并发数必须是整数',
          422,
        )
      }
      if (parsed < 1 || parsed > 20) {
        return mockPipelineError(
          'invalid_config_value',
          '抖音并发数必须在 1..20 范围内',
          422,
        )
      }
      persistedValue = parsed
      responseValue = String(parsed)
      restartRequired = true
    }
    const numericKeys = new Set([
      'daily_comment_limit',
      'daily_dm_limit',
      'daily_users',
      'comment_interval_min',
      'comment_interval_max',
      'dm_interval_min',
      'dm_interval_max',
      'comment_dm_gap_hours',
    ])
    if (numericKeys.has(key)) persistedValue = Number(value)
    ;(MOCK_CONFIG as Record<string, unknown>)[key] = persistedValue
    return wrap({
      status: 'ok',
      key,
      value: responseValue,
      restartRequired,
    })
  }),
  saveApiKey: () => sleep(150).then(() => wrap({ ok: true })),

  // Accounts
  getAccounts: () => sleep(150).then(() => wrap(MOCK_ACCOUNTS)),
  addAccount: (platform: string, username: string) => sleep(150).then(() => {
    const id = MOCK_ACCOUNTS.length + 1
    MOCK_ACCOUNTS.push({
      id, platform, username, login_method: 'qr', status: 'pending',
      last_login_at: '', nickname: '', followers: 0, videos: 0, likes: 0,
      today: { comments: 0, dms: 0, replies: 0, currentTask: '待配置' }, statusKey: 'warn',
    })
    return wrap({ id, platform, username, status: 'pending' })
  }),
  addUser: (data: any) => sleep(150).then(() => {
    const id = MOCK_USERS.length + 1
    const platform = data.platform || 'tiktok'
    const profile_url = (data.profile_url || '').trim() || buildProfileUrl(platform, data.username)
    MOCK_USERS.push({
      id, tiktok_id: `${platform}:${data.username}`, platform, username: data.username,
      nickname: '', bio: data.bio || '', follower_count: data.follower_count || 0,
      following_count: 0, like_count: 0, video_count: 0,
      country: data.country || '', category: data.category || 'unknown',
      status: 'pending', source: 'manual', source_keyword: '',
      profile_url,
      created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
    })
    return wrap({ id, username: data.username, platform, status: 'pending', profile_url })
  }),
  deleteAccount: (id: number) => sleep(100).then(() => {
    const i = MOCK_ACCOUNTS.findIndex(a => a.id === id)
    if (i >= 0) MOCK_ACCOUNTS.splice(i, 1)
    return wrap({ ok: true })
  }),
  updateAccountCookies: () => sleep(100).then(() => wrap({ ok: true })),
  updateAccountMetadata: (id: number, displayName: string) => sleep(100).then(() => {
    const account = MOCK_ACCOUNTS.find(item => item.id === id)
    if (account) Object.assign(account, { display_name: displayName.trim() })
    return wrap({
      ...(account || { id }),
      display_name: displayName.trim(),
    })
  }),
  startQrcodeLogin: (platform: string) => sleep(200).then(() => {
    const username = platform === 'douyin' ? 'new_douyin_user' : 'new_tiktok_user'
    const session_token = 'mock-session-' + Date.now() + '-' + Math.random().toString(36).slice(2, 8)
    MOCK_QR_SESSIONS[session_token] = {
      platform,
      username,
      createdAt: Date.now(),
      confirmed: false,
      expiresAt: Date.now() + 60_000,  // 60s
    }
    return wrap({ session_token })
  }),

  getLoginStatus: (token: string) => sleep(150).then(() => {
    const s = MOCK_QR_SESSIONS[token]
    if (!s) return wrap({ status: 'expired' })
    const now = Date.now()
    const elapsed = now - s.createdAt
    if (now > s.expiresAt && !s.confirmed) return wrap({ status: 'expired' })
    if (s.confirmed) {
      // Append the new account to MOCK_ACCOUNTS so the table refreshes
      const exists = MOCK_ACCOUNTS.find(a => a.username === s.username)
      if (!exists) {
        const id = MOCK_ACCOUNTS.length + 1
        MOCK_ACCOUNTS.push({
          id,
          platform: s.platform,
          username: s.username,
          login_method: 'qr',
          status: 'logged_in',
          last_login_at: new Date().toISOString().slice(0, 19),
          nickname: s.platform === 'douyin' ? '抖音新用户' : 'TikTok 新用户',
          followers: 0, videos: 0, likes: 0,
          today: { comments: 0, dms: 0, replies: 0, currentTask: '待配置' },
          statusKey: 'on',
        })
      }
      return wrap({ status: 'confirmed', username: s.username, platform: s.platform })
    }
    if (elapsed < 3000)  return wrap({ status: 'waiting' })
    if (elapsed < 7000)  return wrap({ status: 'scanning' })
    // 7s+: auto-confirm (in real life, the user tapped "确认登录" on phone)
    s.confirmed = true
    return wrap({ status: 'confirmed', username: s.username, platform: s.platform })
  }),

  getQrcodeUrl: (token: string) => `/api/accounts/qrcode/${token}`,
  checkAccountSession: (id: number) => sleep(150).then(() => wrap({ valid: true, account_id: id })),

  // Lead discovery — public TikTok search (no login required)
  searchLeads: (keyword: string, limit = 20) => sleep(250).then(() => {
    const kw = keyword.toLowerCase()
    const results = MOCK_LEADS_POOL
      .filter(l => l.matched_keyword.toLowerCase().includes(kw) || l.bio.toLowerCase().includes(kw) || l.nickname.toLowerCase().includes(kw))
      .map(l => ({ ...l, relevance_score: l.relevance_score + Math.round(Math.random() * 8 - 4) })) // jitter ±4
      .sort((a, b) => b.relevance_score - a.relevance_score)
      .slice(0, limit)
    // If the keyword doesn't match anything from the pool, return some top results
    // with a reduced score to simulate "fuzzy match"
    if (results.length === 0) {
      return wrap(
        MOCK_LEADS_POOL.slice(0, Math.min(limit, 6))
          .map(l => ({ ...l, relevance_score: Math.max(20, l.relevance_score - 50), matched_keyword: keyword }))
      )
    }
    return wrap(results)
  }),
}

export default mockApi
