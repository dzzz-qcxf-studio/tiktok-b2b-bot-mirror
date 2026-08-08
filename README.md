# TikTok B2B 业务拓展机器人

> 面向越南等海外 B2B 市场的 TikTok 自动获客系统
> Hermes Agent 主控 · DeepSeek v4 Pro · 双平台浏览器 Provider

![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-green?logo=fastapi&logoColor=white)
![Vue](https://img.shields.io/badge/Vue-3-42b883?logo=vuedotjs&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-3-003B57?logo=sqlite&logoColor=white)
![Playwright](https://img.shields.io/badge/Playwright-自动化-2EAD33?logo=playwright&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-yellow)

---

## 这是什么

一个**面向德龙电气等中国外贸 B2B 企业**的 TikTok 自动获客机器人。

系统每天自动完成 6 环节流水线：**搜集 → 筛选 → 策略 → 触达 → 汇总 → 迭代**

识别越南等海外市场潜在 B2B 客户并自动私信推广，支持 **TikTok（国际版）+ 抖音（中国版）** 双平台。

---

## 核心特性

### 双平台支持

| 平台 | 域名 | 登录方式 | Pipeline 运行方式 |
|------|------|---------|------|
| TikTok 国际版 | tiktok.com | Playwright QR 扫码 | 需代码接入指纹 Provider；默认 blocked |
| 抖音中国版 | douyin.com | QR 扫码 | 独立 Playwright Context，可配置并发 |

### 一套统一任务系统

Web UI、REST API、CLI 和定时 Scheduler 都通过同一个 `PipelineJobService`
创建持久化任务。TikTok 与抖音只作为 Job 的 `platform` 字段存在，共用：

- SQLite `pipeline_jobs / pipeline_job_stages / pipeline_schedules` 队列与历史
- `/api/pipeline/jobs`、`/api/pipeline/schedules` 和唯一的 `/pipeline` 页面
- `auto/specified` 账号选择、取消、从失败阶段重试、进程重启恢复和任务内数据隔离

抖音每个运行任务使用独立 Playwright Context，并受
`douyin_max_concurrency`（1..20）限制；并发设置变化在服务重启后生效。
TikTok 默认 Provider 固定返回 `fingerprint_provider_unavailable`，项目尚未接入
具体指纹浏览器厂商，也不会回退到普通 Playwright。必须先在代码中实现并注册具体
指纹浏览器适配器，再按适配器要求为账号配置 Profile；只填写
`browser_provider/browser_profile_id` 字段不会解锁 TikTok Pipeline。

Playwright 在本项目中用于 TikTok/抖音扫码登录，以及抖音 Pipeline 的隔离 Context；
TikTok Pipeline 只允许已注册的指纹浏览器 Provider。

### 6 环节 Pipeline

```
┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
│ 环节 1   │─▶│ 环节 2   │─▶│ 环节 3   │─▶│ 环节 4   │─▶│ 环节 5   │─▶│ 环节 6   │
│ 用户搜集 │  │ 用户筛选 │  │ 策略制定 │  │ 执行触达 │  │ 数据汇总 │  │ 闭环迭代 │
└──────────┘  └──────────┘  └──────────┘  └──────────┘  └──────────┘  └──────────┘
  关键词搜索    Bio 评分      画像分类       评论+私信      日报生成      经验沉淀
  推荐流抓取    LLM 精筛      话术生成       随机间隔       Telegram     ChromaDB
  竞品分析      预筛过滤      执行计划       反封号策略     趋势图表     规则优化
```

### 三层接口

```
┌─────────────────────────────────────────────────────────────┐
│                    Web UI (Vue 3)                           │
│  Dashboard · Users · Leads · Pipeline · Reports · Config    │
└─────────────────────────┬───────────────────────────────────┘
                          │ HTTP
┌─────────────────────────▼───────────────────────────────────┐
│                  REST API (FastAPI)                          │
│         统一 Job/Schedule API · JWT 认证 · CORS              │
└─────────────────────────┬───────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────┐
│                   Core 业务逻辑层                            │
│  ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌─────────────────┐ │
│  │ Models  │ │ Storage  │ │ Services │ │ Plugins         │ │
│  │ (ORM)   │ │(SQLite + │ │(Pipeline │ │(Collector/      │ │
│  │ ORM     │ │ChromaDB) │ │ Auth)    │ │ Channel/Filter) │ │
│  └─────────┘ └──────────┘ └──────────┘ └─────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

---

## 界面预览

### Dashboard — 今日概览

![Dashboard](docs/screenshots/dashboard.png)

KPI 卡片（总用户 / 合格 / 今日新增 / 回复率）+ 30 天趋势图 + Pipeline 状态 + 词云

### Users — 用户管理

![Users](docs/screenshots/users.png)

分状态列表 + 多维筛选（状态/画像/来源/国家/关键词）+ CSV 导入导出 + 手动添加

### Pipeline — 流水线编排

![Pipeline](docs/screenshots/pipeline.png)

6 阶段卡片（搜集→筛选→策略→触达→汇总→迭代）+ 实时事件流 + 触达队列

### Reports — 数据报告

![Reports](docs/screenshots/reports.png)

转化漏斗 + 地区分布 + 情感分析 + 30 天趋势 + 自定义报告

### Leads — 潜在客户发现

![Leads](docs/screenshots/leads.png)

关键词搜索 TikTok 公开用户 + 相关度评分 + 一键入库

### Accounts — 账号管理

![Accounts](docs/screenshots/accounts.png)

QR 扫码登录 + 批量 Cookie 检测 + 账号上限控制（最多 5 个）

### LLM — 模型配置

![LLM Config](docs/screenshots/llm.png)

多 Provider 管理（DeepSeek / Qwen / OpenAI / 自定义）+ 服务端测试连接 + 五类业务 Route
以及真实用量。API Key 只写后端环境变量/被忽略的 `.env`，页面和 API 都不会回显。

### Runtime — 运行参数

![Runtime](docs/screenshots/runtime.png)

每日限速 + 随机间隔 + cron 调度 + 关键词库管理

---

## 架构图

> 使用 draw.io 绘制，源文件在 `docs/diagrams/`

| 图 | 文件 | 内容 |
|---|------|------|
| 系统架构 | [architecture.drawio](docs/diagrams/architecture.drawio) | 三层模型 + 外部服务 |
| Pipeline 流程 | [pipeline.drawio](docs/diagrams/pipeline.drawio) | 6 环节执行流 |
| 数据库 ER | [database-er.drawio](docs/diagrams/database-er.drawio) | 6 张核心表关系 |

---

## 技术栈

| 层 | 技术 | 用途 |
|---|------|------|
| **后端** | Python 3.11+ / FastAPI | REST API 服务（43 个路由装饰器） |
| **ORM** | SQLAlchemy 2.0 | 数据库抽象（13 张表） |
| **向量存储** | ChromaDB | 用户画像语义搜索 |
| **浏览器** | Provider + Playwright | Playwright 用于扫码与抖音；TikTok Pipeline 使用指纹 Provider |
| **LLM** | DeepSeek v4 Pro | 用户筛选 / 策略生成 |
| **前端** | Vue 3 + Element Plus | 管理面板（11 页面） |
| **图表** | ECharts | 趋势图 / 分布图 |
| **数据库** | SQLite | 本地持久化存储 |
| **通知** | Telegram Bot | 日报推送 |

---

## 快速启动

### 环境要求

- Python 3.11+
- Node.js 18+
- Playwright Chromium

### 安装

```bash
git clone https://github.com/tienan2024/tiktok-b2b-bot.git
cd tiktok-b2b-bot

pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
playwright install chromium

cd tiktok_bot_console/ui
npm install --registry https://registry.npmmirror.com
```

### 配置

推荐启动 API 与 UI 后访问 `/config-llm` 添加 Provider、填写密钥并配置 Route。密钥只在
本次提交时进入后端，不会回填。兼容旧配置时也可自行创建项目根目录 `.env` 并设置
`LLM_API_KEY`；该文件已被 `.gitignore` 排除。`.env` 还应设置稳定的 `JWT_SECRET`。
推荐使用 `--env-file .env` 启动以一次加载全部配置；认证模块也会直接读取项目根目录
`.env` 作为 JWT 密钥兜底，避免从 IDE 或脚本启动时漏传参数导致旧登录 token 失效。

### 启动

```bash
# 终端 1：后端
python -m uvicorn tiktok_bot_api.main:app --env-file .env --reload --port 8000

# 终端 2：前端
cd tiktok_bot_console/ui && npm run dev
```

- 前端: http://localhost:5173
- API 文档: http://localhost:8000/docs

LLM 管理端点全部要求 JWT 或 `X-API-Key` 认证。浏览器跨域默认只允许本机 5173/8080
来源；部署到其他域名时使用逗号分隔的 `CORS_ALLOWED_ORIGINS` 显式配置，不能使用 `*`。
API 目前必须保持单 worker（Docker 启动脚本已固定 `--workers 1`），因为浏览器会话、
Pipeline runtime 和 LLM Router 是进程内唯一实例。

---

## 项目结构

```
tiktok-bot-software/
├── tiktok_bot_api/              # FastAPI REST 后端（43 个路由装饰器）
├── tiktok_bot_core/             # 业务核心层
│   ├── models/entities.py       # SQLAlchemy ORM（含统一任务表）
│   ├── services/
│   │   ├── pipeline.py          # 6 环节 Pipeline 编排
│   │   ├── pipeline_jobs.py     # Job Service/Runner/Dispatcher/Runtime
│   │   ├── pipeline_scheduler.py# 持久化 cron 调度
│   │   ├── pipeline_concurrency.py # 并发与账号互斥
│   │   └── auth_service.py      # QR 登录 + Cookie 管理
│   ├── browser/providers.py     # 双平台统一 Provider Registry
│   ├── plugins/                 # Collector / Channel / Filter
│   └── platforms.py             # 双平台抽象 (TikTok + 抖音)
├── tiktok_bot_console/ui/       # Vue 3 前端 (11 页面)
├── tests/                       # 后端 Core/Pipeline/Runtime/API/认证测试
├── skills/                      # 4 个 Hermes Skills
└── docs/                        # 14 篇 wiki + 截图 + 架构图
```

---

## Pipeline API

<details>
<summary>点击展开</summary>

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| POST | `/api/auth/login` | 登录 |
| POST | `/api/auth/register` | 注册 |
| GET | `/api/users` | 用户列表 |
| GET | `/api/users/stats` | 用户统计 |
| GET | `/api/users/{username}/detail` | 用户画像 |
| POST | `/api/users` | 手动添加用户 |
| POST | `/api/pipeline/jobs` | 创建持久化 Job（202） |
| GET | `/api/pipeline/jobs` | 统一任务列表 |
| GET | `/api/pipeline/jobs/{id}` | Job 与阶段详情 |
| POST | `/api/pipeline/jobs/{id}/cancel` | 请求取消 |
| POST | `/api/pipeline/jobs/{id}/retry` | 创建重试 Job（202） |
| GET | `/api/pipeline/capabilities` | Provider/账号/并发预检 |
| POST/GET | `/api/pipeline/schedules` | 创建/列出统一计划 |
| PUT/DELETE | `/api/pipeline/schedules/{id}` | 更新/删除计划 |
| POST | `/api/pipeline/run` | 兼容入口，仅创建持久化 Job（202） |
| GET | `/api/pipeline/events` | 事件历史 |
| GET | `/api/pipeline/overview` | Pipeline 总览 |
| POST | `/api/acquisition/jobs` | 认证后按有序阶段、无凭据快照原子创建 AI 获客 Job + Campaign + Keywords（202） |
| GET | `/api/reports/daily` | 日报 |
| GET | `/api/reports/trend` | 趋势 |
| GET | `/api/reports/overview` | 漏斗+地区+情感 |
| GET | `/api/config` | 配置列表 |
| PUT | `/api/config/{key}` | 更新配置 |
| POST | `/api/config/apikey` | 已弃用的兼容密钥入口（需认证、复用安全 Secret 写入） |
| GET | `/api/stats/dashboard` | Dashboard |
| GET | `/api/accounts` | 账号列表 |
| POST | `/api/accounts` | 添加账号 |
| DELETE | `/api/accounts/{id}` | 删除账号 |
| POST | `/api/accounts/login-qrcode` | QR 登录 |
| GET | `/api/accounts/login-status` | 登录状态 |
| GET | `/api/leads/search` | Lead 搜索 |
| GET/POST | `/api/llm/providers` | Provider 列表/新建（不返回密钥） |
| PUT/DELETE | `/api/llm/providers/{id}` | 更新/删除 Provider |
| POST | `/api/llm/providers/{id}/test` | 服务端连接测试 |
| PUT | `/api/llm/providers/{id}/secret` | 更新密钥（不回显） |
| GET | `/api/llm/routes` | 五类业务 Route |
| PUT | `/api/llm/routes/{routeKey}` | 原子替换有序 Provider 链 |
| GET | `/api/llm/usage` | 真实请求用量聚合 |

</details>

---

## 测试

```bash
python -m pytest tests/ -v
```

2026-07-26 全量验收：`177 passed`（2 条第三方依赖弃用告警，无失败）。

2026-08-01 LLM 配置阶段验收：后端专项 `103 passed`，扩展相关回归 `270 passed`，
配置页组件 `8 passed`，前端 Smoke `128 passed`；类型检查、生产构建以及桌面/390px
真实后端浏览器检查通过。DeepSeek 上游连通性仍以页面“测试连接”的实时结果为准。

2026-08-04 Hermes H1 数据接线验收：AI 获客原子建单/重试与统一业务投影已接入既有
Pipeline、Users、Lead、Dashboard、Reports 和词云；后端全量 `733 passed`。H2/H3 前端
工作台尚不在本阶段范围内，租户隔离仍是 P0 上线阻断项。

---

## 文档

| 文档 | 内容 |
|------|------|
| [01 — 项目概述](docs/wiki/01-项目概述.md) | 核心目标、技术栈 |
| [02 — 架构设计](docs/wiki/02-架构设计.md) | 三层模型 |
| [05 — Pipeline](docs/wiki/05-Pipeline.md) | 6 环节流程 |
| [06 — CLI/API/UI](docs/wiki/06-CLI-API-UI.md) | 统一任务与计划契约 |
| [07 — 数据库](docs/wiki/07-数据库.md) | Job/Stage/Schedule 数据模型 |
| [10 — 账号管理](docs/wiki/10-账号管理.md) | QR 登录 |
| [11 — 双平台](docs/wiki/11-双平台支持.md) | TikTok + 抖音 |

---

## 许可证

MIT License

## 致谢

- [MediaCrawler](https://github.com/NanmiCoder/MediaCrawler) — 抖音登录参考
- [Playwright](https://playwright.dev/) — 浏览器自动化
- [Element Plus](https://element-plus.org/) — Vue 3 UI 组件
