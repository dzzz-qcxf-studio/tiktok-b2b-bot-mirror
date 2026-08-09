# 03 · 数据流与 Mock

最后更新：2026-08-09

## API 入口

所有视图只 import `src/api/index.ts`：

```ts
import { getDashboard, getTrendReport, getWordcloud, getConfig, ... } from '../api'
```

`api/index.ts` 内部根据 `VITE_USE_MOCK` 决定路由：

```ts
const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true'

// 读操作：mock 模式 → 直接 mock；非 mock 模式 → real API + withMockFallback
getDashboard: USE_MOCK ? mockApi.getDashboard
  : withMockFallback(realApi.getDashboard, mockApi.getDashboard),

// 写操作：mock 模式 → mock；非 mock 模式 → real API + withMockFallback
runPipeline: USE_MOCK ? mockApi.runPipeline
  : withMockFallback(realApi.runPipeline, mockApi.runPipeline),
```

`withMockFallback(real, fallback)`：先尝试 real，失败（dev 模式才生效）回退到 fallback。

## Mock 数据集（`src/api/mock.ts`）

| 常量 | 内容 | 驱动 |
|------|------|------|
| `MOCK_USERS` | 10 个 TikTok 用户（aroma_house_us 等） | Users 列表 / UserDetail |
| `MOCK_ACCOUNTS` | 3 个社媒账号（delong_official_01/02/cn） | ConfigAccounts |
| `MOCK_DASHBOARD` | overview 数字 + 5 个 top 关键词 + 4 个画像分类 | Dashboard KPI / Top Keywords |
| `MOCK_TREND` | 30 天日数据（new_users, qualified, comments, dms, replies, reply_rate） | Dashboard 30 天图 / Reports 30 天图 |
| `MOCK_PIPELINE_EVENTS` | 62 条事件（17 真实 + 45 程序生成） | Pipeline 实时事件流 |
| `MOCK_CONFIG` | cron / 限流 / 关键词 / 模型配置 | Hermes Cron / Dashboard startedAt |
| `MOCK_WORDCLOUD_EN` | 22 条英文关键词（importer / wholesale / ...） | Reports 词云（英文） |
| `MOCK_WORDCLOUD_CN` | 22 条中文关键词（进口商 / 批发 / ...） | Reports 词云（中文） / UserDetail |

## Mock 函数

| 函数 | 返回 | 备注 |
|------|------|------|
| `login` | `{ access_token, username, role }` | 失败抛 axios 形状错误 |
| `me` | `{ authenticated, username, role }` | |
| `getUsers(params?)` | `{ items, total }` | 支持按 `status` 过滤 |
| `getUserStats()` | `{ total, qualified, pending, contacted, replied, rejected }` | |
| `getDashboard()` | `MOCK_DASHBOARD` | |
| `getPipelineEvents(limit=60)` | `MOCK_PIPELINE_EVENTS.slice(0, limit)` | |
| `runPipeline(stages)` | `{ job_id, started_at, stages, results }` | |
| `getDailyReport()` | `{ date, new_users_found, users_qualified, ..., business_leads }` | |
| `getTrendReport(days=30)` | `MOCK_TREND.slice(-days)` | |
| `getWordcloud(lang='en', limit?)` | 完整或限定的词云池 | |
| `getConfig()` | `MOCK_CONFIG` | |
| `getAccounts()` | `MOCK_ACCOUNTS` | |

## 切换策略

### 开发 / 演示
```bash
# .env.development 或 .env.local
VITE_USE_MOCK=true
```
所有读写走 mock，启动无需后端。

### 接真后端
```bash
VITE_USE_MOCK=false
VITE_API_BASE=http://your-backend:8000
```
读操作走 real，dev 模式下失败自动 fallback；写操作直连。

## 数据形状约定

- mock 返回 axios 形状：`{ data, status, statusText, headers, config }`
- 调用方统一 `.data` 拿实际负载
- 错误抛 axios 风格 Error，含 `.response.data.detail`
- 时间戳一律 ISO8601（`2026-07-11T12:04:18`）
- 计数 / 比率字段统一为 number（不用字符串）
- 真实 `getAccounts()` 可同时返回短期 `avatar_url` 与本地缓存派生的 `avatar_data_url`；
  账号页必须优先后者，避免平台 CDN 签名过期后头像消失。Mock 可只提供原始头像字段。
