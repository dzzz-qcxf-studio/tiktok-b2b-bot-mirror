# TikTok B2B 业务拓展机器人

> 面向越南等海外 B2B 市场的 TikTok 自动获客系统
> Hermes Agent 主控 · DeepSeek v4 Pro · Playwright 浏览器自动化

![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-green?logo=fastapi&logoColor=white)
![Vue](https://img.shields.io/badge/Vue-3-42b883?logo=vuedotjs&logoColor=white)
![Element Plus](https://img.shields.io/badge/Element%20Plus-2.x-409eff)
![SQLite](https://img.shields.io/badge/SQLite-3-003B57?logo=sqlite&logoColor=white)
![Playwright](https://img.shields.io/badge/Playwright-自动化-2EAD33?logo=playwright&logoColor=white)
![Tests](https://img.shields.io/badge/Tests-36%20passed-brightgreen)
![License](https://img.shields.io/badge/License-MIT-yellow)

---

## 这是什么

一个**面向德龙电气等中国外贸 B2B 企业**的 TikTok 自动获客机器人。

系统每天自动完成 6 环节流水线：

```
搜集 → 筛选 → 策略 → 触达 → 汇总 → 迭代
```

识别越南等海外市场潜在 B2B 客户并自动私信推广，支持 **TikTok（国际版）+ 抖音（中国版）** 双平台。

---

## 核心特性

### 双平台支持

| 平台 | 域名 | 登录方式 | 状态 |
|------|------|---------|------|
| TikTok 国际版 | tiktok.com | QR 扫码 | ✅ 已支持 |
| 抖音中国版 | douyin.com | QR 扫码 | ✅ 已支持 |

通过 `Platform` 抽象层统一封装，业务代码无需 `if/else` 平台判断。

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
│              31 个端点 · JWT 认证 · CORS                     │
└─────────────────────────┬───────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────┐
│                   Core 业务逻辑层                            │
│  ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌─────────────────┐ │
│  │ Models  │ │ Storage  │ │ Services │ │ Plugins         │ │
│  │ (ORM)   │ │(SQLite + │ │(Pipeline │ │(Collector/      │ │
│  │ 9 张表  │ │ChromaDB) │ │ Auth)    │ │ Channel/Filter) │ │
│  └─────────┘ └──────────┘ └──────────┘ └─────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

---

## 界面预览

### Dashboard — 今日概览

```
┌─────────────────────────────────────────────────────────────────┐
│  Pipeline Lab                                    [搜索] [中/EN] │
├──────────┬──────────────────────────────────────────────────────┤
│ 📊 Dash  │  今日概览                                            │
│ 👥 Users │  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐               │
│ 🔍 Leads │  │ 1247 │ │  891 │ │  47  │ │ 14.6%│               │
│ 🔄 Pipe  │  │ 总用户│ │ 合格 │ │ 今日新│ │ 回复率│               │
│ 📈 Rep   │  └──────┘ └──────┘ └──────┘ └──────┘               │
│ ─────── │                                                      │
│ 👤 Acct  │  30 天 Pipeline 趋势                                 │
│ 🛡️ LLM   │  ┌─────────────────────────────────────────────┐    │
│ ⚙️ Run   │  │  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓  合格用户             │    │
│          │  │  ─────────────────────  回复率 %              │    │
│          │  └─────────────────────────────────────────────┘    │
└──────────┴──────────────────────────────────────────────────────┘
```

### Users — 用户管理

```
┌─────────────────────────────────────────────────────────────────┐
│  用户管理                    [导入CSV] [导出] [手动添加]         │
├─────────────────────────────────────────────────────────────────┤
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐       │
│  │ 1247 │ │  47  │ │  891 │ │  96  │ │  14  │ │  64  │       │
│  │ 总计 │ │ 今日新│ │ 合格 │ │已触达│ │已回复│ │已淘汰│       │
│  └──────┘ └──────┘ └──────┘ └──────┘ └──────┘ └──────┘       │
│                                                                 │
│  [全部] [待筛选] [合格] [已触达] [已回复] [已淘汰]              │
│  [国家 ▾] [来源关键词 ▾] [排序 ▾]                              │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ ☐ 用户名       粉丝    评分  状态    来源     操作       │   │
│  │ ☐ @aroma_house 128K    92   已回复  关键词   [查看]      │   │
│  │ ☐ @led_whole   56K     85   合格    关键词   [查看]      │   │
│  │ ☐ @korean_beau 214K    78   已触达  推荐     [查看]      │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                     1/10 页 →  │
└─────────────────────────────────────────────────────────────────┘
```

### Pipeline — 流水线编排

```
┌──────────────────────────────────────────────────────────────────┐
│  Pipeline 流水线                                    [运行]       │
├──────────────────────────────────────────────────────────────────┤
│  今日 Pipeline 进度                                              │
│  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ │
│  │ 01     │ │ 02     │ │ 03     │ │ 04     │ │ 05     │ │ 06     │ │
│  │ COLLECT│ │ FILTER │ │STRATEGY│ │OUTREACH│ │ REPORT │ │ITERATE │ │
│  │ ✅ 328 │ │ ✅ 47  │ │ ✅ 47  │ │ 🔄 89  │ │ ⏳     │ │ ⏳     │ │
│  │ 用户   │ │ 合格   │ │ 策略   │ │ 触达   │ │ 报告   │ │ 迭代   │ │
│  └────────┘ └────────┘ └────────┘ └────────┘ └────────┘ └────────┘ │
│                                                                    │
│  实时事件流                                    最近 60 条          │
│  ┌──────────────────────────────────────────────────────────────┐ │
│  │ 12:04  [collect]   @aroma_house_us 视频「精油进货流程」评论   │ │
│  │ 12:01  [filter.ok] 筛选完成 · 合格 47 · 淘汰 281            │ │
│  │ 11:58  [strategy]  @korean_beauty_hub 策略生成 · soft_sell   │ │
│  │ 11:54  [outreach]  Cookie 过期 · 已切换备用账号              │ │
│  └──────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

### Accounts — 账号管理

```
┌─────────────────────────────────────────────────────────────────┐
│  社交账号管理        [导入Cookie] [批量检测] [📱抖音扫码登录]    │
├─────────────────────────────────────────────────────────────────┤
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐                          │
│  │  3   │ │  3   │ │  52  │ │  23  │                          │
│  │总账号│ │健康中│ │今日触达│ │剩余配额│                          │
│  └──────┘ └──────┘ └──────┘ └──────┘                          │
│                                                                 │
│  ┌─────────────────────────────────────┐                       │
│  │ 🔴 @delong_01  TikTok  Cookie已过期  │                       │
│  │    12.8K粉丝 · 今日: 0评论/0私信     │                       │
│  │    [重新登录] [检测] [删除]          │                       │
│  ├─────────────────────────────────────┤                       │
│  │ 🟢 @delong_02  TikTok  登录正常      │                       │
│  │    8.4K粉丝 · 今日: 18评论/8私信     │                       │
│  │    [登出] [检测] [删除]              │                       │
│  └─────────────────────────────────────┘                       │
└─────────────────────────────────────────────────────────────────┘
```

### Leads — 潜在客户发现

```
┌─────────────────────────────────────────────────────────────────┐
│  Lead 发现                                                     │
├─────────────────────────────────────────────────────────────────┤
│  [🔍 输入关键词搜索潜在客户...                    ] [搜索]      │
│  搜索 TikTok 公开用户资料 · 无需登录 · 带限速保护              │
│                                                                 │
│  ┌──────────────────────┐ ┌──────────────────────┐             │
│  │ SP                   │ │ WK                   │             │
│  │ @sourcing_pro_ny     │ │ @wholesale_king_dubai│             │
│  │ Sourcing Pro NY      │ │ Wholesale King Dubai │             │
│  │ Product sourcing...  │ │ Wholesale electronics│             │
│  │ 🇺🇸 34K · 89视频     │ │ 🇦🇪 87K · 214视频    │             │
│  │ [sourcing agent] 94  │ │ [wholesale] 91       │             │
│  │ [入库] [查看主页]    │ │ [入库] [查看主页]    │             │
│  └──────────────────────┘ └──────────────────────┘             │
└─────────────────────────────────────────────────────────────────┘
```

---

## 技术栈

| 层 | 技术 | 用途 |
|---|------|------|
| **后端** | Python 3.11+ / FastAPI | REST API 服务（31 端点） |
| **ORM** | SQLAlchemy 2.0 | 数据库抽象（9 张表） |
| **向量存储** | ChromaDB | 用户画像语义搜索 |
| **浏览器** | Playwright | TikTok/抖音自动化操作 |
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
# 克隆仓库
git clone https://github.com/tienan2024/tiktok-b2b-bot.git
cd tiktok-b2b-bot

# 安装后端依赖（国内镜像）
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 安装 Playwright 浏览器
playwright install chromium

# 安装前端依赖
cd tiktok_bot_console/ui
npm install --registry https://registry.npmmirror.com
```

### 配置

```bash
# 复制配置模板
cp .env.example .env

# 编辑 .env，填入：
# DEEPSEEK_API_KEY=sk-xxx        # DeepSeek API Key
# browser_headless=false          # 调试时显示浏览器窗口
```

### 初始化数据库

```bash
# 创建表 + 插入演示数据
python -m tiktok_bot_api.seed
```

### 启动

```bash
# 方式一：分别启动（开发模式）
# 终端 1：后端
python -m uvicorn tiktok_bot_api.main:app --reload --port 8000

# 终端 2：前端
cd tiktok_bot_console/ui
npm run dev

# 方式二：Docker 一键启动
make up
```

访问：
- 前端：http://localhost:5173
- API 文档：http://localhost:8000/docs

### 登录

任意用户名 + 4 位以上密码（首次需注册）

---

## 项目结构

```
tiktok-bot-software/
├── tiktok_bot_api/              # FastAPI REST 后端
│   ├── main.py                  # 31 个 API 端点
│   ├── auth.py                  # JWT 认证
│   └── seed.py                  # 种子数据脚本
│
├── tiktok_bot_core/             # 业务核心层
│   ├── models/entities.py       # 9 张 SQLAlchemy ORM 表
│   ├── storage/
│   │   ├── database.py          # SQLite 引擎
│   │   ├── sqlite_store.py      # CRUD 仓库
│   │   └── vector_store.py      # ChromaDB 向量存储
│   ├── services/
│   │   ├── pipeline.py          # 6 环节 Pipeline 编排
│   │   └── auth_service.py      # QR 登录 + Cookie 管理
│   ├── plugins/
│   │   ├── collectors/          # 搜集插件（关键词/推荐/竞品）
│   │   ├── channels/            # 触达插件（评论/私信）
│   │   └── filters/             # 筛选插件（关键词/LLM）
│   ├── platforms.py             # 双平台抽象（TikTok + 抖音）
│   ├── events/bus.py            # 异步事件总线
│   ├── llm/client.py            # DeepSeek 客户端
│   └── settings.py              # Pydantic 配置
│
├── tiktok_bot_console/          # 前端
│   └── ui/
│       ├── src/
│       │   ├── views/           # 11 个页面组件
│       │   ├── components/      # 通用组件
│       │   ├── api/             # API 层（real + mock）
│       │   ├── stores/          # Pinia 状态管理
│       │   ├── i18n/            # 中英双语
│       │   └── router/          # Vue Router
│       └── scripts/smoke.mjs    # 烟雾测试
│
├── tests/                       # 36 个后端测试
├── skills/                      # 4 个 Hermes Skills
├── docs/wiki/                   # 14 篇开发文档
├── Dockerfile
├── docker-compose.yml
└── Makefile
```

---

## API 端点一览

<details>
<summary>点击展开全部 31 个端点</summary>

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 服务信息 |
| GET | `/api/health` | 健康检查 |
| POST | `/api/auth/login` | 登录 |
| POST | `/api/auth/register` | 注册 |
| GET | `/api/auth/me` | 当前用户 |
| GET | `/api/users` | 用户列表 |
| GET | `/api/users/stats` | 用户统计 |
| GET | `/api/users/{id}` | 用户详情 |
| GET | `/api/users/{username}/detail` | 用户画像 |
| POST | `/api/users` | 手动添加用户 |
| POST | `/api/pipeline/run` | 启动 Pipeline |
| GET | `/api/pipeline/events` | 事件历史 |
| GET | `/api/pipeline/events/stream` | SSE 实时流 |
| GET | `/api/pipeline/overview` | Pipeline 总览 |
| GET | `/api/reports/daily` | 日报 |
| GET | `/api/reports/trend` | 趋势 |
| GET | `/api/reports/overview` | 漏斗+地区+情感 |
| GET | `/api/config` | 配置列表 |
| PUT | `/api/config/{key}` | 更新配置 |
| POST | `/api/config/apikey` | 更新 API Key |
| GET | `/api/stats/dashboard` | Dashboard |
| GET | `/api/stats/wordcloud` | 词云 |
| GET | `/api/accounts` | 账号列表 |
| POST | `/api/accounts` | 添加账号 |
| DELETE | `/api/accounts/{id}` | 删除账号 |
| PUT | `/api/accounts/{id}/cookies` | 更新 Cookie |
| POST | `/api/accounts/login-qrcode` | QR 登录 |
| GET | `/api/accounts/login-status` | 登录状态 |
| GET | `/api/accounts/qrcode/{token}` | 二维码图片 |
| POST | `/api/accounts/{id}/check-session` | 检测 Cookie |
| GET | `/api/leads/search` | Lead 搜索 |
| GET | `/api/llm/providers` | LLM 配置 |

</details>

---

## 数据库设计

```erDiagram
    users ||--o{ strategies : has
    users ||--o{ messages : has
    messages ||--o{ replies : has

    users {
        int id PK
        string platform
        string tiktok_id UK
        string username
        string bio
        int follower_count
        string status
        string category
        string source
    }

    strategies {
        int id PK
        int user_id FK
        string persona
        string strategy_type
        text comment_template
        text dm_template
        text action_plan
        int priority
    }

    messages {
        int id PK
        int user_id FK
        string message_type
        text content
        string status
        datetime sent_at
    }

    replies {
        int id PK
        int message_id FK
        text reply_content
        string sentiment
        boolean is_business_intent
    }

    tiktok_accounts {
        int id PK
        string platform
        string username UK
        text cookies_json
        string status
        string login_method
        datetime last_login_at
    }

    daily_reports {
        int id PK
        date report_date UK
        int new_users_found
        int comments_sent
        int dms_sent
        float reply_rate
        int business_leads
    }
```

---

## 测试

```bash
# 运行全部后端测试
python -m pytest tests/ -v

# 运行前端烟雾测试
cd tiktok_bot_console/ui
node scripts/smoke.mjs
```

```
tests/test_core.py           ✅ 10 passed
tests/test_plugins.py        ✅ 8 passed
tests/test_pipeline.py       ✅ 5 passed
tests/test_platforms_auth.py ✅ 13 passed
─────────────────────────────────────
Total                        ✅ 36 passed
```

---

## 文档

| 文档 | 内容 |
|------|------|
| [01 — 项目概述](docs/wiki/01-项目概述.md) | 一句话描述、核心目标、技术栈 |
| [02 — 架构设计](docs/wiki/02-架构设计.md) | 三层模型、依赖方向 |
| [03 — Core 层](docs/wiki/03-Core层.md) | 数据模型、存储层、事件总线 |
| [04 — Plugin 层](docs/wiki/04-Plugin层.md) | Collector/Channel/Filter 插件 |
| [05 — Pipeline](docs/wiki/05-Pipeline.md) | 6 环节执行流程 |
| [06 — CLI/API/UI](docs/wiki/06-CLI-API-UI.md) | 三层接口设计 |
| [07 — 数据库](docs/wiki/07-数据库.md) | 表结构 + ChromaDB 集合 |
| [10 — 账号管理](docs/wiki/10-账号管理.md) | QR 登录 + Cookie 持久化 |
| [11 — 双平台](docs/wiki/11-双平台支持.md) | TikTok + 抖音差异 |
| [12 — 测试报告](docs/wiki/12-测试报告.md) | 测试覆盖率明细 |

---

## 设计决策

- **业务方向**：B2B 外贸获客，针对德龙电气等中国制造业出口企业
- **技术框架**：Hermes Agent（自学习闭环）+ DeepSeek v4 Pro（中文能力强）
- **目标市场**：TikTok + 抖音双平台，运营规模 1-3 个小账号起步
- **核心风险**：执行触达环节的封号风险 → 通过随机间隔、Cookie 轮换、限流避免
- **账号上限**：最多 5 个账号，防止过度分散

---

## 许可证

MIT License

---

## 致谢

- [MediaCrawler](https://github.com/NanmiCoder/MediaCrawler) — 抖音登录流程参考
- [Playwright](https://playwright.dev/) — 浏览器自动化框架
- [Element Plus](https://element-plus.org/) — Vue 3 UI 组件库
