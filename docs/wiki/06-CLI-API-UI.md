# 06 — CLI / API / UI 三层接口

> 关联: [索引](00-索引.md) | [Pipeline](05-Pipeline.md) | [Skills](08-Skills.md)

## 设计原则

```text
Skills (AI 调) → CLI (程序入口) → Core (业务逻辑)
Web UI (人用)  → REST API → Core
```

CLI = 唯一程序入口。API = CLI 的 HTTP 包装。Skills = CLI 的 Hermes 包装。

## CLI 命令设计

```bash
tiktok-bot user list --status qualified --limit 50
tiktok-bot user show @alice
tiktok-bot pipeline run --stages collect,filter
tiktok-bot pipeline run --once           # 全部阶段
tiktok-bot strategy list --user-id 42
tiktok-bot strategy generate --for @alice
tiktok-bot outreach send-comment --target @alice --content "..."
tiktok-bot outreach send-dm --target @alice
tiktok-bot report daily
tiktok-bot report trend --days 30 --out chart.png
tiktok-bot config list
tiktok-bot config set keywords=wholesale,importer
tiktok-bot status
tiktok-bot init
```

## REST API 端点（30+ 路由）

```text
# 健康检查
GET    /                                     # 服务信息
GET    /api/health                           # 健康检查

# 认证
POST   /api/auth/login                       # 用户名密码 / API Key 登录
POST   /api/auth/register                    # 注册新账号
GET    /api/auth/me                          # 当前用户信息

# 用户管理
GET    /api/users                            # 用户列表（支持 status/category 筛选）
GET    /api/users/stats                      # 用户状态统计
GET    /api/users/{user_id}                  # 用户详情（按 ID）
GET    /api/users/{username}/detail          # 用户详情页数据（按用户名）
POST   /api/users                            # 手动添加用户

# Pipeline
POST   /api/pipeline/run                     # 启动 Pipeline（Body: {"stages": [...]}）
GET    /api/pipeline/events                  # 事件历史
GET    /api/pipeline/events/stream           # SSE 实时事件流
GET    /api/pipeline/overview                # Pipeline 总览（6 阶段 + 最近 7 天 + 摘要）

# 报告
GET    /api/reports/daily?d=                 # 日报
GET    /api/reports/trend?days=30            # 趋势
GET    /api/reports/overview                 # 转化漏斗 + 地区 + 情感

# 配置
GET    /api/config                           # 配置列表
PUT    /api/config/{key}                     # 更新配置
POST   /api/config/apikey                    # 更新 LLM API Key

# 统计
GET    /api/stats/dashboard                  # Dashboard 概览
GET    /api/stats/wordcloud                  # 词云数据

# 社交账号（TikTok + 抖音）
GET    /api/accounts                         # 列出账号（可按平台过滤）
POST   /api/accounts                         # 添加账号元信息
DELETE /api/accounts/{aid}                   # 删除账号
PUT    /api/accounts/{aid}/cookies           # 手动更新 cookies
POST   /api/accounts/login-qrcode            # 启动 QR 扫码登录
GET    /api/accounts/login-status            # 查询登录状态（轮询）
GET    /api/accounts/qrcode/{token}          # 下载二维码图片
POST   /api/accounts/{aid}/check-session     # 检测 cookie 有效性

# Lead 发现
GET    /api/leads/search?keyword=&limit=     # 公开搜索潜在客户

# LLM
GET    /api/llm/providers                    # LLM 提供商列表 + 使用统计
```

## Web UI 页面（11 个视图）

```
┌──────────────────────────────────────────────────────┐
│  Pipeline Lab                     [搜索] [中/EN] [🔔] │
├──────────┬───────────────────────────────────────────┤
│ 📊 Dash  │  页面内容                                  │
│ 👥 Users │                                           │
│ 🔍 Leads │                                           │
│ 🔄 Pipe  │                                           │
│ 📈 Rep   │                                           │
│ ─────── │                                           │
│ 👤 Acct  │                                           │
│ 🛡️ LLM   │                                           │
│ ⚙️ Run   │                                           │
└──────────┴───────────────────────────────────────────┘
```

| 路由 | 页面 | 功能 |
| --- | --- | --- |
| `/login` | Login | 登录/注册/API Key 登录 |
| `/dashboard` | Dashboard | 今日概览 + 趋势图 + 词云 + Pipeline 状态 |
| `/users` | Users | 用户列表（筛选/搜索/分页/CSV 导入导出/手动添加） |
| `/users/:username` | UserDetail | 用户画像 + 评分 + 策略 + 时间线 + 视频 |
| `/leads` | Leads | Lead 发现（关键词搜索 + 一键入库） |
| `/pipeline` | Pipeline | 6 阶段 Pipeline 编排 + 事件流 + 触达队列 |
| `/reports` | Reports | 日报/趋势/漏斗/地区/情感 + 自定义报告 |
| `/config-accounts` | ConfigAccounts | 社交账号管理（QR 登录/批量检测/导入 Cookie） |
| `/config-llm` | ConfigLlm | LLM 提供商管理（添加/切换/测试连接） |
| `/config-pipeline` | ConfigPipeline | 运行参数（频率/限速/关键词/cron） |
| `/:pathMatch(.*)` | NotFound | 404 页面 |
