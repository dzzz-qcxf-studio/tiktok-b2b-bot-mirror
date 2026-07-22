# 开发进度记录

按时间倒序排列（最新在上）。

---

## 2026-07-19 · v0.4 数字孪生 + Lead 发现

### 完成项

#### MediaCrawler 架构研究
- 已 clone MediaCrawler_ref/ 到项目同级目录
- 阅读 douyin/login.py（Playwright 浏览器自动化 + QR 码 DOM 抓取 + cookie 轮询）
- 阅读 douyin/core.py + client.py（请求签名、接口封装）
- 提取架构模式：browser-based session、QR extraction、state machine
- **不照搬逆向签名**：真实 QR 登录由用户在浏览器完成（一次性人工操作），服务端只拿 cookie

#### 数字孪生监控窗口
- 新建 `stores/agentLiveness.ts`：Pinia store，6 个 Agent 状态模型 + 事件流 + 日常配额模拟
- 新建 `components/AgentTwinGrid.vue`：状态栏 + 2 列 Agent 卡片（名称/状态/动作/队列/速率条）+ 暗色事件日志
- 集成到 `Pipeline.vue` 右侧栏（触达审核队列卡片之下）
- 每 5s 自动 tick 模拟 Agent 状态变化，真实后端会通过 WebSocket 推送
- 全部使用 OKLCH 设计系统，暗色事件流与主设计系统一致

#### Lead 发现 API（公开搜索）
- 新增 `mock.ts` 数据集 `MOCK_LEADS_POOL`（15 条跨区域 B2B 用户）
- 新增 `mockApi.searchLeads(keyword, limit)`：按关键词匹配 + 排序 + 模糊 fallback
- 新增 `index.ts` real + wrapped 导出，支持 mock/real 运行时切换
- 设计约束：纯公开搜索，不涉及登录，带严格限速预留

### 待续
见 [TODO.md](TODO.md)

---

## 2026-07-13 · v0.1 核心可演示

### 完成项

#### 基础修复
- **Login.vue / App.vue**：`main.ts` 漏 import `./assets/main.css`，导致 design-system.css 整个没加载 → 输入框裸样式、CSS 变量全失效。补 `import './assets/main.css'`。
- **App.vue**：删除内联 `:root` 应急变量块（与设计系统冲突且 brand 色错设），改由 design-system.css 统一提供。

#### 图表 / 视图修复
- **Dashboard.vue 30 天 Pipeline 效果**：
  - 7/14/30 周期按钮从死按钮改为可点击 `setPeriod()`
  - `getTrendReport(period)` 替代硬编码 `14`
  - 重写几何：`yMax` 按数据自适应；柱与折线共用 `xStep`；Y 轴刻度动态；动态 `lastPoint` 替代硬编码 `L 656` / `cx="656"`
  - 柱体改用 `r.qualified` 字段，配图例"合格用户数"
  - X 轴补 6 个日期刻度（`MM-DD`），与 HTML 原型一致
- **Reports.vue 30 天 Pipeline 输出**：
  - 默认 `period=30`（与标题一致，原为 7）
  - **DM 柱从 `Math.random()` 改为 `r.dms` 真实数据**
  - 使用 `url(#cy)` `url(#mg)` 渐变定义（之前定义的渐变从未被引用）
  - X 轴补日期刻度
  - Y 轴 `yMax` 自适应
- **Reports.vue 热力图**：扁平数组导致格子错位 → 改为 2D `heatRows[day][hour]` + `<template v-for>` 按行渲染；按 day seed 生成稳定数据
- **Pipeline.vue 实时事件流**：
  - 删除 17 条硬编码事件数组（之前因为 shape 不匹配，5s 轮询拉到的数据渲染不出来）
  - API → 显示数据 mapper：把 `{timestamp, type, level, message}` 转为 `{ts, tag, tagCls, msg}`
  - `MOCK_PIPELINE_EVENTS` 从 17 → 62 条（17 真实 + 45 程序生成）
  - `getPipelineEvents(limit=60)` 对齐卡片标题"已显示最近 60 条"
  - **.log 容器不填满 .event-card** —— 根因是 `design-system.css` 全局 `.log { max-height: 360px }` 在 scoped 规则里没显式置为 none 时仍然生效（Cascade 是逐属性计算）；修复加 `max-height: none !important`
  - `.log-line` 加 `min-width: 0`、`.log-msg` 加 `overflow-wrap: anywhere` 防长中文撑破网格
  - `.log-body` 改为 `flex-direction: column` 让 `.log` 纵向 fill

#### Hermes Agent Cron
- `MOCK_CONFIG` 新增 cron 字段：`cron_daily_pipeline`、`cron_daily_pipeline_time` 等
- i18n `pipeline.subtitle` 改为 `{time}` 插值，不再硬编码 `09:00`
- Pipeline.vue 在 `onMounted` 调 `getConfig()` 写入 `cronTime`，subtitle / `cron-hint` / `pipe-head .sub` 全部动态绑定
- page-head 加按钮"立即手动触发（绕过 cron）" → `runAll()`
- Dashboard.vue `dashboard.startedAt` 同样改为动态 `cronTime`

#### 词云
- 新建 `src/components/WordCloud.vue`（无新依赖）：阿基米德螺旋 + 碰撞检测 + ~18% 词随机旋转 ±5°~±23°
- 自动按 count 分桶：`b`(大) / `c`(中) / `o`(小) / `n`(默认)，字号 12–28px
- 中英文字符宽度估算分开（CJK ~1.0 em/字）
- `mock.ts` 新增 `MOCK_WORDCLOUD_EN` (22) + `MOCK_WORDCLOUD_CN` (22)
- `getWordcloud(lang='en', limit?)` 接受语言参数
- Reports 词云卡片加 EN / 中 切换按钮；UserDetail 画像 tags 同样加切换
- 视图内不再有 `DEMO_KEYWORDS` / `DEMO_PERSONA_KW` 之类的硬编码 fallback（全部下沉到 mock.ts）

#### 杂项
- mock.ts `login` 改用 `throw new Error()` + 错误对象（消除 S6671/S7746 警告）
- npm 安装时优先用 `npm install --registry=https://registry.npmmirror.com`

### 待续
见 [TODO.md](TODO.md)

---

## 2026-07-13 · v0.2 数据接入：消除视图内硬编码

### 完成项

#### 新增 mock 数据集
- `MOCK_ACCOUNTS` 从 3 条扩到 5 条，加 `today` 字段（comments/dms/replies/currentTask）和 `statusKey`
- `MOCK_PIPELINE_OVERVIEW` 新增：包含 `jobs` (7 天历史) 和 `results` (本轮 6 阶段)
- `MOCK_USER_DETAIL` 新增：`aroma_house_us` 完整画像 + generic fallback（任何 username 都能渲染）
- `MOCK_REPORTS_OVERVIEW` 新增：funnel / regions / sentiment 三个子面板
- `MOCK_LLM_PROVIDERS` + `MOCK_LLM_USAGE` + `MOCK_LLM_SKILLS`：LLM 配置页全部数据
- `MOCK_CONFIG` 扩展：`cron_daily_pipeline` / `cron_daily_report` / `cron_weekly_iterate` / `cron_cookie_check` + 时间字段

#### 新增 API 端点
- `getUserStats()` — 6 项 KPI 聚合数
- `getUserDetail(username)` — 单用户详情（含 generic fallback）
- `getPipelineOverview()` — 7 天历史 + 本轮结果
- `getReportsOverview()` — funnel / regions / sentiment 组合
- `getLlmProviders()` — providers + usage + skills 组合
- `getConfig()` 已有，但本次接入 7 个 rate-limit/interval 字段 + 4 个 cron 字段 + keywords CSV

#### 视图接入（全部由 API 驱动，零硬编码）

| 文件 | 改动 |
|------|------|
| `Users.vue` | 删除 10 条硬编码用户；`load()` 调 `getUsers` + `getUserStats`；KPI、statusOptions、countries、sourceKeywords 全部从加载后的数据派生 |
| `Dashboard.vue` | 删除 5 条硬编码 `feed`；新增 `loadFeed()` 调 `getPipelineEvents(5)`；首屏加载时与 dashboard/trend/config 并行 |
| `Pipeline.vue` | 删除 7 条 jobs + 6 条 results 硬编码；`loadOverview()` 调 `getPipelineOverview()`；空数组初始按需填充 |
| `UserDetail.vue` | 删除 breakdown/videos/timeline/strategy 硬编码；`loadDetail()` 调 `getUserDetail(username)`；avatar 缩写由 username 派生；route param 变化自动重新加载 |
| `ConfigAccounts.vue` | 删除 3 张硬编码账号卡 + 3 行硬编码活动明细；接入 `getAccounts()`；KPI / 平台统计 / 健康&过期计数全部从 `accounts` 派生 |
| `Reports.vue` | 删除硬编码 funnelSteps / regions / sentiment 内联值；新增 `getReportsOverview()`；3 个子面板颜色/dasharray/count 全部 API 驱动；地区排序仍基于加载后的数据 computed 排序 |
| `ConfigLlm.vue` | 删除 `providers` / 6 项 KPI / `skills` 硬编码；新增 `getLlmProviders()`；主 Provider 自动 derived（`role='main'`）；当日调用上涨百分比改读 API |
| `ConfigPipeline.vue` | 删除 `cfg` reactive 全部 8 个字段硬编码 + 4 个 cron 输入硬编码 + keywords 硬编码；`loadConfig()` 调 `getConfig()`；cron 输入和说明文字 9:00/21:00 等时间都从 API 来 |

### 收益
- 全部 9 个页面**零业务数据硬编码**（剩 `tips`/`tabs` 数组是 UX 演示，不需要后端）
- 修改 mock 字段或接真后端时，视图无须改动
- 演示时修改 `MOCK_*` 即可让 KPI / 图表 / 表格实时变化

### v0.2 全部完成。

---

## 2026-07-13 · 初版

项目初始化，仓库结构搭建，9 个页面 + 共享组件初版完成。
- `getUserStats()` — 返回 6 项 KPI 聚合数（已有）
- `getUserDetail(username)` — 单用户详情（新增）
- `getPipelineOverview()` — 7 天历史 + 本轮结果（新增）

#### 视图接入（全部由 API 驱动，零硬编码）

| 文件 | 改动 |
|------|------|
| `Users.vue` | 删除 10 条硬编码用户；`load()` 调 `getUsers` + `getUserStats`；KPI 用聚合数据；新增/筛选/key 派生自 `users.value`；sourceKeywords 从加载后的数据派生 |
| `Dashboard.vue` | 删除 5 条硬编码 `feed`；新增 `loadFeed()` 调 `getPipelineEvents(5)`；首屏加载时与 dashboard/trend/config 并行 |
| `Pipeline.vue` | 删除 7 条 jobs + 6 条 results 硬编码；`loadOverview()` 调 `getPipelineOverview()`；空数组初始，按需填充 |
| `UserDetail.vue` | 删除 breakdown/videos/timeline/strategy 硬编码；新增 `loadDetail()` 调 `getUserDetail(username)`；avatar 缩写由 username 派生；route param 变化时重新加载 |
| `ConfigAccounts.vue` | 删除 3 张硬编码账号卡 + 3 行硬编码活动明细；接入 `getAccounts()`；KPI、平台统计、健康/过期计数全部从 `accounts` 派生；avatar 渐变按 platform 切换 |

### 待续（参见 TODO.md）

- Reports.vue 的 funnel / heatmap / regions / sentiment 仍为半硬编码
- ConfigLlm.vue 的 skills + Provider 列表仍为硬编码
- ConfigPipeline.vue 字段全硬编码
- 错误态 / 空态统一组件

---

## 2026-07-13 · 初版

项目初始化，仓库结构搭建，9 个页面 + 共享组件初版完成。