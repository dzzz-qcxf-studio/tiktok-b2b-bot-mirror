# 待办事项与项目计划

最后更新：2026-07-19（v0.5 进行中）

## 已完成（v0.5）

### Lead 发现 UI 页面 ✅
- 新建 `Leads.vue`：关键词搜索 + 结果列表 + 一键入库
- 路由 `/leads`，侧边栏导航入口（放大镜图标）
- 展示：头像缩写、用户名、Bio、粉丝数、视频数、相关度评分、来源关键词
- "入库"按钮将选中 Lead 添加到用户池（带已添加状态）
- 初始态展示推荐关键词，空态/错误态/加载态完整覆盖
- i18n：zh-CN + en-US 完整翻译

### 接真实后端 ✅
- 添加 4 个缺失 API 端点：`/api/pipeline/overview`、`/api/users/{username}/detail`、`/api/reports/overview`、`/api/llm/providers`
- 添加 `/api/leads/search` 公开搜索端点
- 修复 `/api/pipeline/events` 返回格式（补充 `level` + `message` 字段）
- 总计 30 个 API 路由全部可用

### 错误态 / 空态 / 加载态统一 ✅
- `<EmptyState>` 通用组件（icon + title + description + slot）
- `<ErrorBanner>` 通用组件（message + retry + dismiss）
- Leads.vue 率先采用完整状态覆盖：初始态/加载中/错误/空/结果

### Pipeline 真实后端修复 ✅
- `/api/pipeline/overview` 返回 6 阶段卡片 + 最近 7 天 + 摘要
- 事件格式转换器 `_format_event()` 自动推断 level/message

---

## 已完成（v0.4）

### MediaCrawler 架构研究 ✅
- 已 clone MediaCrawler_ref/，阅读 douyin/login.py / core.py / client.py
- 提取 Playwright 状态机 + QR 码 DOM 抓取 + cookie 轮询 模式
- **不照搬逆向签名**：QR 登录作为一次性人工操作，服务端只拿 cookie

### 数字孪生监控窗口 ✅
- `stores/agentLiveness.ts`：6 Agent 状态模型 + 事件流 + 配额模拟
- `components/AgentTwinGrid.vue`：状态栏 + 2 列 Agent 卡片 + 暗色事件日志
- 集成到 `Pipeline.vue` 右侧栏
- 每 5s tick 模拟，真实后端可替换为 WebSocket

### Lead 发现 API（公开搜索） ✅
- `mock.ts` 新增 `MOCK_LEADS_POOL`（15 条跨区域 B2B 用户）
- `mockApi.searchLeads(keyword, limit)`：关键词匹配 + 排序 + 模糊 fallback
- `index.ts` real + wrapped + named export
- 约束：纯公开搜索，不涉及登录，带严格限速预留

### 视图里剩余的"硬编码"
仅 UX 演示相关（不需后端）：
- `Pipeline.vue` / `ConfigLlm.vue` 的 `tips[]` 数组（反封号策略提示）— 设计上属于产品文档
- `Login.vue` 表单提交文案
- `App.vue` 顶部 Avatar 用户名（mock 用户合理）

### 接下来（v0.5）

#### Lead 发现 UI 页面
- 新建 `Leads.vue`：关键词搜索 + 结果列表 + 一键入库
- 添加路由 `/leads`，侧边栏导航入口
- 展示：头像缩写、用户名、Bio、粉丝数、相关度评分、来源关键词
- "入库"按钮将选中 Lead 添加到 `MOCK_USERS`
- 限速提示：今日剩余搜索次数

#### 接真实后端
- 替换 `api/index.ts` 中 mock 引用为 real
- 关闭 `VITE_USE_MOCK`
- 验证 `withMockFallback` 兜底不再触发

#### 错误态 / 空态 / 加载态统一
- `<EmptyState>` 组件
- `<ErrorBanner>` 组件
- 全局 `loading.value === false && data.length === 0` 提示

#### 可访问性
- 表单 `<label>` 关联
- 按钮 `aria-label`

#### 测试
- 单元测试（pinia store、computed、API mock）
- 组件测试（vue-test-utils）
- e2e（playwright）

#### CI / CD
- GitHub Actions：`type-check` + `build` + smoke test

---

### mock.ts 数据集扩展

| 数据集 | 当前 | 建议 |
|--------|------|------|
| `MOCK_USERS` | 10 条 | 30–50 条以演示分页 |
| `MOCK_ACCOUNTS` | 3 条 | 5 条（与卡片"3/5 上限"对齐） |
| `MOCK_USER_DETAIL` | 不存在 | 新增：breakdown 6 维度 + videos + timeline + strategy 字段 |
| `MOCK_PIPELINE_JOBS` | 不存在 | 新增：最近 7 天 Pipeline 运行汇总（status/duration/details） |
| `MOCK_REGIONS_REPORT` | 不存在 | 新增：地区回复率分布 + 情感分布 |

### i18n 完整性

| 项 | 状态 |
|----|------|
| en-US 翻译与 zh-CN 同步 | 部分 UI 字面量（如 Dashboard.vue 的 "89/120 触达"）未走 i18n |
| 邮件 / 通知文案 | 暂未国际化 |
| 错误码 → 用户文案映射 | 未做 |

### 错误态 / 空态 / 加载态

- ✅ 加载态：Reports.vue / Dashboard.vue 已实现（`!loading` 分支）
- ❌ 空态：API 返回空数组时无任何提示
- ❌ 错误态：catch 后静默 `events.value = []`，用户看不到失败原因
- 建议：统一 `<EmptyState>` 组件 + `<ErrorBanner>` 组件

### 性能

- 图表 `barRects` / `xAxisLabels` / `xAxisLabels` 每次 computed 都重新生成字符串；大量事件时性能可能下降
- Pipeline 实时事件流 5s 轮询 + 完整重渲染，可考虑 v-memo

## 后续计划（v0.3+）

### 接真实后端

| 步骤 | 内容 |
|------|------|
| 1 | 确认后端 OpenAPI / 接口契约 |
| 2 | 替换 `src/api/index.ts` 中 `mockApi` 引用为真实 `realApi` |
| 3 | 关闭 `VITE_USE_MOCK` |
| 4 | 验证 withMockFallback 兜底逻辑不再触发 |

### 测试

- 当前只有 `scripts/smoke.mjs` 烟雾测试
- 缺失：单元测试（pinia store、computed、API mock）
- 缺失：组件测试（vue-test-utils）
- 缺失：e2e 测试（playwright）

### CI / CD

- 当前无 CI 配置
- 建议：GitHub Actions 跑 `npm run type-check` + `npm run build` + smoke test

### 可访问性

- 部分按钮只有 emoji / svg，无 `aria-label`
- 表单缺 `<label>` 关联
- 颜色对比度需 review

## 项目总览

```
v0.1 (✅)   核心可演示 + 关键 bug 修复 + 词云
v0.2 (✅)   全部视图接入 mock API，消除硬编码 8/8
v0.3 (✅)   MediaCrawler 架构研究 + 数字孪生监控 + Lead 发现 API
v0.4 (📋 进行中)  Lead 发现 UI 页面 + 错误态/空态统一
v0.5 (📋 规划中)  接真实后端 / 测试覆盖 / CI
v1.0 (🎯 目标)    生产环境上线
```