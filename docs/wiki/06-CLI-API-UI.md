# 06 — CLI / API / UI 三层接口

> 关联: [索引](00-索引.md) | [Pipeline](05-Pipeline.md) | [Skills](08-Skills.md)
> 最后更新: 2026-08-08（Hermes H1 数据接线）

## 设计原则

```text
Skills (AI 调) → CLI ───────────┐
Web UI (人用)  → REST API ─────┼→ PipelineJobService → SQLite 队列
Scheduler ──────────────────────┘
```

CLI、API 与 Scheduler 都是统一 Job Service 的适配层。不存在按平台拆分的任务系统，
也不存在绕过 SQLite 队列的同步 Pipeline 执行入口。

## CLI 命令设计

```bash
tiktok-bot user list --status qualified --limit 50
tiktok-bot user show @alice
tiktok-bot pipeline run --platform douyin --account-mode auto --stages collect,filter
tiktok-bot pipeline run --platform douyin --account-mode specified --account-id 1 --once
tiktok-bot strategy list --user-id 42
tiktok-bot strategy generate --for @alice
tiktok-bot outreach send-comment --target @alice --content "..."
tiktok-bot outreach send-dm --target @alice
tiktok-bot report daily
tiktok-bot report trend --days 30 --out chart.png
tiktok-bot browse run --platform douyin --account-id 1 --goal "..." --max-steps 10
tiktok-bot config list
tiktok-bot config set keywords=wholesale,importer
tiktok-bot status
tiktok-bot init
```

## REST API 端点（73 个路由装饰器）

```text
# 健康检查
GET    /                                     # 服务信息
GET    /api/health                           # 健康检查

# 认证
POST   /api/auth/login                       # 用户名密码 / API Key 登录
POST   /api/auth/register                    # 注册新账号
GET    /api/auth/me                          # 当前用户信息

# 用户管理
GET    /api/users                            # 用户列表。Query: status/category/limit(<=500)/offset
                                              # 响应: { total: <SQL count, 不受 limit/offset 影响>,
                                              #         items: [{ id, username, profile_url, ... }] }
GET    /api/users/stats                      # 聚合统计 — /api/users/items 与此处的 total 必须同源
                                              # 响应字段:
                                              #   total/pending/qualified/contacted/replied/rejected
                                              #   new_today        当日 created_at >= today 的新增数
                                              #   by_persona       { distributor, buyer, peer, unknown } 全量分组
GET    /api/users/{user_id}                  # 用户详情（按 ID，含 profile_url）
GET    /api/users/{username}/detail          # 用户详情页数据（按用户名，含 profile_url + profile 块）
POST   /api/users                            # 手动添加用户（body 含 profile_url，留空自动按平台拼接）

# Pipeline 统一 Job
POST   /api/pipeline/jobs                    # 创建持久化 Job，返回 202
GET    /api/pipeline/jobs                    # 列表；platform/status/limit/offset
GET    /api/pipeline/jobs/{job_id}           # Job + 有序 Stage 明细
POST   /api/pipeline/jobs/{job_id}/cancel    # 请求取消
POST   /api/pipeline/jobs/{job_id}/retry     # 创建重试 Job，返回 202
GET    /api/pipeline/capabilities            # 双平台 Provider/账号/并发预检
POST   /api/pipeline/schedules               # 创建统一定时计划
GET    /api/pipeline/schedules               # 计划列表，可按 platform 过滤
PUT    /api/pipeline/schedules/{schedule_id} # 完整更新计划
DELETE /api/pipeline/schedules/{schedule_id} # 删除计划，返回 204
POST   /api/pipeline/run                     # 兼容入口：仅创建 Job，返回 202
GET    /api/pipeline/events                  # 事件历史
GET    /api/pipeline/events/stream           # SSE 实时事件流
GET    /api/pipeline/overview                # Pipeline 总览（6 阶段 + 最近 7 天 + 摘要）

# 获客阶段 01/02（全部需认证，资源严格绑定 jobId）
POST   /api/acquisition/jobs                  # 原子创建 Job + Campaign + 1..100 Keywords，返回 202
POST   /api/acquisition/jobs/{jobId}/campaign # 创建不可变目标画像与七项搜索预算
GET    /api/acquisition/jobs/{jobId}/campaign # 读取安全归一化画像
POST   /api/acquisition/jobs/{jobId}/keywords # 新增关键词
GET    /api/acquisition/jobs/{jobId}/keywords # 分页关键词与效果
PATCH  /api/acquisition/jobs/{jobId}/keywords/{id} # 更新关键词统计
DELETE /api/acquisition/jobs/{jobId}/keywords/{id} # 无证据引用时删除
GET    /api/acquisition/jobs/{jobId}/stage-01 # 发现阶段业务聚合
GET    /api/acquisition/jobs/{jobId}/stage-02 # 资格阶段四状态聚合
GET    /api/acquisition/jobs/{jobId}/candidates # 分页候选、证据预览、最新 AI 评估
GET    /api/acquisition/jobs/{jobId}/candidates/{userId} # 候选详情与证据
POST   /api/acquisition/jobs/{jobId}/candidates/{userId}/approve # 人工通过
POST   /api/acquisition/jobs/{jobId}/candidates/{userId}/reject # 人工淘汰
POST   /api/acquisition/jobs/{jobId}/candidates/{userId}/request-enrichment # 请求补资料
POST   /api/acquisition/jobs/{jobId}/candidates/{userId}/complete-enrichment # 完成补资料，回人工复核
PUT    /api/acquisition/jobs/{jobId}/candidates/{userId}/labels # 人工改标签
GET    /api/acquisition/jobs/{jobId}/candidates/{userId}/audits # 审计分页

# 报告
GET    /api/reports/daily?d=                 # 日报
GET    /api/reports/trend?days=30            # 趋势
GET    /api/reports/overview                 # 转化漏斗 + 地区 + 情感

# 配置
GET    /api/config                           # 配置列表
PUT    /api/config/pipeline                  # 原子替换完整 Pipeline 配置
PUT    /api/config/{key}                     # 更新配置
POST   /api/config/apikey                    # deprecated；需认证，复用安全 Secret 写入并返回 configured/envVar

# 统计
GET    /api/stats/dashboard                  # Dashboard 概览
GET    /api/stats/wordcloud                  # 词云数据

# 社交账号（TikTok + 抖音）
GET    /api/accounts                         # 列出账号（可按平台过滤）
POST   /api/accounts                         # 添加账号元信息
PUT    /api/accounts/{aid}                   # 修改本地展示名称，不改变浏览器隔离 alias
DELETE /api/accounts/{aid}                   # 删除账号
PUT    /api/accounts/{aid}/cookies           # 手动更新 cookies
POST   /api/accounts/login-sessions          # 打开人工交互登录会话，返回 201
GET    /api/accounts/login-sessions/{token}  # 查询会话安全状态
POST   /api/accounts/login-sessions/{token}/verify # 核验并事务保存登录态
POST   /api/accounts/login-sessions/{token}/cancel # 取消并关闭浏览器
POST   /api/accounts/login-qrcode            # deprecated：转发新 Service，无 QR 字段
GET    /api/accounts/login-status            # deprecated：映射新状态，无 QR 字段
GET    /api/accounts/qrcode/{token}          # 已停用：固定 410，不读取文件
POST   /api/accounts/{aid}/check-session     # 抖音检测 cookie 并同步资料；TikTok 暂不支持且不改状态

# Lead 发现
GET    /api/leads/search?keyword=&limit=     # 公开搜索潜在客户

# LLM
GET    /api/llm/providers                    # Provider 列表；仅返回 configured，不返回密钥
POST   /api/llm/providers                    # 新建 OpenAI-compatible Provider
PUT    /api/llm/providers/{provider_id}      # 更新 Provider 元数据
DELETE /api/llm/providers/{provider_id}      # 删除未被 Route 引用的 Provider
POST   /api/llm/providers/{provider_id}/test # 服务端有界连接测试
PUT    /api/llm/providers/{provider_id}/secret # 仅写新密钥，不回显
GET    /api/llm/routes                       # 五类业务 Route 与有序 Provider 链
PUT    /api/llm/routes/{route_key}           # 原子替换单条 Route
GET    /api/llm/usage                        # 真实请求日志聚合
```

### Hermes H1 原子创建契约

`POST /api/acquisition/jobs` 必须携带 JWT 或系统 API Key。请求复用 `platform/accountMode/
accountId/stages/configSnapshot`，并新增严格 `campaign` 与 1—100 个严格 `keywords`；
`stages` 必须包含 `collect`，且只能按 `collect → filter → strategy → outreach → report → iterate`
组成严格有序子序列；规范化后的“关键词文本 + 语言”不可重复。所有请求 DTO
`extra=forbid`，顶层及 Campaign/Keyword 定义不接受 operator、tenant、Cookie、Token、Secret
或未知嵌套字段；`configSnapshot` 沿用现有 Pipeline 的开放快照映射，但 API 与 Service
会递归拒绝 API Key、Cookie、Token、Password、Authorization、Client Secret、Private Key、
Credentials 等凭据类键及常见组合变体；`authHeader/authValue`、`auth: "Bearer ..."` 和
`auth.type + auth.value` 等认证结构同样会被拒绝，并且验证错误不会回显原始键名或值。

服务端先预检账号和 Browser Provider，再在一个事务内创建完整任务。成功响应一次返回
`job/campaign/keywords`，状态码为 202；任一写入失败全部回滚。服务端写入的
`businessMode=ai_acquisition` 与 `acquisitionSchemaVersion=1.0` 只用于展示和诊断，运行时仍以
Campaign 是否存在判断 AI 获客任务。

### 全局业务投影 API

以下既有接口已统一通过 `BusinessReadModel` 读取 AI 获客与 legacy 数据：

- `GET /api/users`、`GET /api/users/stats`：返回投影后的状态与分类，Users 列表要求
  `limit=1..500`、`offset>=0`，越界返回 422，并额外包含
  `business_source/source_job_id/qualification_status/match_score/confidence_score/labels`；
- `GET /api/stats/dashboard`：用户总量、资格统计、分类和关键词效果与 Users 同源；
- `GET /api/stats/wordcloud`：返回前端真实消费的 `word/count`；
- `GET /api/leads/search`：保持公开访问，空白关键词返回 422；搜索公开资料、legacy 来源词
  和最新 Job 的 Evidence 关键词。AI `match_score` 已是 0..100，直接钳制/四舍五入为
  `relevance_score`，不再乘 100；没有 AI 分数时才使用 legacy 启发式分；
- `GET /api/reports/overview`：累计漏斗、地区、情感和商业意向来自真实 User/Message/Reply。
  `businessIntent` 统计已发送消息对应的不同商业意向回复用户。
- `GET /api/reports/daily`：未传 `d` 时读取当前 UTC 日；显式 `d=YYYY-MM-DD` 保持调用方日期。
  `GET /api/pipeline/overview` 的今日评论/私信也使用相同 UTC 日界线；`daily` 与 `trend`
  均在数据库 Session 内完成普通字典序列化，避免关闭 Session 后访问 ORM 导致 500。

全局投影的 AI 状态优先级为 Reply、已发送 Message、最新 Campaign 资格终态；
`manual_review/need_enrichment` 对旧状态字段显示为 `pending`，原始值仍由
`qualification_status` 返回。它不更新数据库中的 `User.status/category`。“今日”口径统一以
UTC 00:00 为边界；日报趋势仍读取 `DailyReport` 历史快照。

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
| `/pipeline` | Pipeline | 双平台/账号选择 + 创建 Job + 统一历史 + 阶段详情/取消/重试 |
| `/reports` | Reports | 日报/趋势/漏斗/地区/情感 + 自定义报告 |
| `/config-accounts` | ConfigAccounts | 社交账号管理 + 人工浏览器登录 + 本地备注名 + 真实平台头像 |
| `/config-llm` | ConfigLlm | Provider 弹窗 CRUD/密钥更新/服务端连接测试/五类 Route/真实用量 |
| `/config-pipeline` | ConfigPipeline | 原子运行配置 + 抖音并发 + 双平台定时计划 CRUD |
| `/:pathMatch(.*)` | NotFound | 404 页面 |

## LLM 管理请求与安全契约

Provider 新增和编辑使用固定定位的 `role=dialog/aria-modal=true` 对话框；触发后焦点进入
第一个可编辑字段，Tab/Shift+Tab 被限制在对话框内，关闭后焦点恢复到原触发按钮。
桌面端完整居中、移动端贴近底部并按动态视口与安全区限制最大高度。对话框不依赖滚动
跳转，因此在嵌入式 WebView、iframe 或长 Provider 列表下都能立即看到操作结果。

上述 9 个 `/api/llm/*` 管理端点全部要求 JWT Bearer 或 `X-API-Key`，未认证固定返回
401。前端 Axios interceptor 在每次请求发出前读取当前 `localStorage.token`，因此登录后
无需重新加载模块即可访问。CORS 默认只允许 `localhost/127.0.0.1` 的 5173 与 8080
UI；其他部署来源必须通过逗号分隔的 `CORS_ALLOWED_ORIGINS` 显式添加，通配符和非
http(s) 来源会在启动时被拒绝。

JWT 签名密钥优先读取进程环境中的 `JWT_SECRET`；若启动器漏传 `--env-file`，认证模块
会直接读取项目根目录 `.env` 中的同名配置。仅当两处都没有配置时才生成进程级临时密钥。
因此正常配置过 `.env` 后，从 IDE、脚本或 Uvicorn 启动都不会因重启随机换密钥而让旧
token 突然失效；部署环境仍推荐显式注入 `JWT_SECRET`。

Axios 响应拦截器对受保护 API 的 401 执行统一会话失效处理：删除本地 token/username，
跳转 `/login` 并携带当前站内路径作为 `redirect`。登录成功后只接受单斜杠开头、且不是
`/login` 的本地返回地址；外部 URL、`//host` 和其他非法值固定回到 `/dashboard`。
`/api/auth/login` 与 `/api/auth/register` 的 401/失败响应不会触发跳转，避免密码错误时
形成重定向循环。并发请求返回 401 时，拦截器还会比较该请求发出时携带的 token 与当前
token；只有两者相同才清理会话，避免迟到的旧请求删除刚登录取得的新 token。

迁移期保留的 `POST /api/config/apikey` 也要求相同认证，并复用 `_write_llm_secret` 的
CR/LF 拒绝、`python-dotenv` 带引号写入和 Router 关闭刷新流程；它不能成为绕过 Provider
Secret 端点的后门。`GET /api/config` 只返回 `has_api_key` 与固定掩码 `***`，不公开
密钥末四位。新 UI 不调用该旧入口。

LLM 管理以 `llm_providers / llm_routes / llm_request_logs` 为唯一权威源。Provider
响应使用 camelCase，只公开 `configured: boolean`，任何列表、创建、更新、连接测试和
错误响应都不会返回 API Key。`apiKeyEnv` 必须是大写环境变量名；密钥更新请求拒绝 CR/LF，
并使用 `python-dotenv` 的带引号写入将新值保存到项目根目录下被 Git 忽略的 `.env`，随后
更新当前进程环境。页面只提供空白密码框，编辑 Provider 时不会读取或回填旧值。
如果新建 Provider 元数据成功但随后密钥请求失败，页面会立即保留服务端返回的 id，
用户重试时执行更新而不是再次 POST 创建重复 Provider。

`POST /api/llm/providers/{id}/test` 在服务端通过对应 adapter 发起最小 OpenAI-compatible
请求，超时上限 5 秒；浏览器不直接访问上游 URL。结果仅包含 `reachable`、`latencyMs`、
`errorCategory` 等诊断字段，不回传完整上游错误正文。

Route 固定为 `collection / qualification / strategy / iteration / default`。更新请求提交
完整有序 Provider 数组，后端先验证全部引用和字段，再在一个事务中替换；被任一 Route
引用的 Provider 删除时返回 409。UI 不保存额外的“主 Provider”状态，顺序完全由数据库
Route 决定。当前 Task 10–11 已完成管理层；Pipeline 业务调用点的显式 Route 映射仍属于
Task 9，不能仅凭页面已配置就认为五条 Route 已全面接管调用。

当前生产接线依赖进程内唯一的 Browser Registry、Pipeline runtime 与 LLM Router，API
因此强制单 worker。应用启动时独占 `data/api-worker.lock`，第二个 worker 会 fail-fast；
可用 `TIKTOK_BOT_API_WORKER_LOCK` 覆盖锁文件位置，但不能借此运行多 worker。Docker
入口明确使用 `--workers 1`。多进程扩展必须先把这些状态迁移到共享协调层。

## 统一请求契约

创建 Job 与 Schedule 共享核心字段：

```json
{
  "platform": "douyin",
  "accountMode": "auto",
  "accountId": null,
  "stages": ["collect", "filter", "strategy"],
  "configSnapshot": {}
}
```

`specified` 必须提供 `accountId`，`auto` 不得携带它。计划额外包含
`name/cronExpression/timezone/enabled`。错误统一放在
`detail.code/detail.message`；例如 TikTok 未配置 Provider 时返回
`fingerprint_provider_unavailable`。

## 交互式登录请求与响应契约

创建会话只接受平台、账号本地标识和可选账号 ID：

```json
{
  "platform": "douyin",
  "accountAlias": "marketing_01",
  "accountId": null
}
```

响应严格使用安全白名单：`token/platform/accountAlias/accountId/status`、
`browserOpened/browserProvider`、`authenticated/persisted`、
`startedAt/expiresAt/errorCode/errorMessage`。API 绝不返回 Cookie、storage state、
Profile 绝对路径、浏览器对象或任何 `qrcode_*` 字段。

HTTP 映射为：未知 token 返回 404；账号租约冲突、同平台规范化 alias 冲突、服务关闭
、全局账号容量已满或 TikTok 指纹 Provider 不可用返回 409；平台、空 alias 或账号
身份无效返回 422；浏览器打开、验证、持久化、账号事务和清理失败返回 500。alias 在
所有入口统一执行 NFKC 与首尾空白清理、保留大小写，纯数字仍是 alias。所有错误均使用稳定的
`detail.code/detail.message`，不会回显内部异常或私密路径。

生产接线在应用生命周期内复用 Browser Registry，并为全部交互登录会话持有同一个
`AccountLeaseManager`。Pipeline 与登录共用该租约管理器仍是后续接线项，当前不宣称
二者已经跨系统互斥。登录确认事务同时写入 `logged_in`、cookies 兼容快照、相对
Profile/storage state 路径、验证时间、版本和实际存在的浏览器元数据；事务失败时整个
更新回滚，会话不能伪装成 `confirmed`。shutdown 调用
`InteractiveLoginService.aclose()`；`login_cleanup_incomplete` 会被明确记录并向上返回。
Pipeline runtime 停止后，lifespan 还会调用 `aclose_llm_router()`；该函数只关闭已创建的
全局 Router，不会为了 shutdown 新建实例，并会等待 Provider 的在途 SDK 请求释放连接。

浏览器隔离键与数据库 ID 明确分离：生产 resolver 始终使用规范化的
`platform:accountAlias` 作为浏览器/Profile/登录租约键；数据库 `accountId` 只用于响应
和事务定位。因此新 alias 首次登录入库后再次登录仍复用同一 Profile，纯数字 alias
也不会被误当成数据库 ID。

同平台账号写入先以 SQLite `BEGIN IMMEDIATE` 取得写保留锁，再在同一事务中完成所有
现有 alias 的规范化扫描、账号上限检查和插入，避免并发首次添加或首次登录的
TOCTOU。resolver 若发现历史库中有多条 NFKC/空白等价 alias，会 fail-closed 返回
`account_alias_conflict`，不会按查询顺序任意选择账号；updater 提交前会重复同一检查。
对于零匹配的新 alias，resolver 还会在打开浏览器前只读预检全局 `MAX_ACCOUNTS`；
updater 仍在写锁事务中保留权威复核。多个首次登录争夺最后名额时只允许一个插入，
其余稳定返回 `account_limit_reached`；已有账号更新不受容量上限阻止。

Provider 与 API 是双重不信任边界。Provider availability/adapter 的未知错误码先归一
为固定 unavailable 码；API 再使用公开白名单，未知码统一为
`interactive_login_failed`（不可用异常为 `interactive_login_unavailable`）。响应、
会话与日志都不回显上游正文、Cookie、私密路径或任意外部错误码。

旧创建端点额外保留安全的 `session_token` 别名供迁移期页面读取；它与新响应的 `token`
值相同，不包含任何认证凭据。新代码只使用 `token`。

前端 `src/api/index.ts` 提供
`createLoginSession/getLoginSession/verifyLoginSession/cancelLoginSession` 四个类型化方法，
`InteractiveLoginModal.vue` 只使用这组会话 API。Modal 创建会话后等待用户在已打开的
独立浏览器中自行完成登录；只有用户点击“验证并保存登录状态”且后端返回 `confirmed`
才通知账号页刷新。关闭、切换平台和组件卸载都会先对非 confirmed token 做幂等取消；
generation 守卫会丢弃旧请求结果，并取消迟到创建的会话，避免快速操作导致串号。

页面 DOM 不包含二维码、截图、Cookie、storage state、Profile 路径或会话 token。
状态使用 `aria-live`，支持 Escape、焦点圈和 reduced-motion；中英文文案均使用人工
浏览器语义。TikTok 指纹 Provider 不可用时展示后端公开错误，不回显任意上游字段。

## Pipeline 配置原子保存

`PUT /api/config/pipeline` 必须一次提交完整配置，包括每日限额、评论/私信间隔、
间隔范围、关键词以及 `douyin_max_concurrency`。后端先完成整体验证再在单个
数据库事务中写入，避免部分字段保存成功。抖音并发范围为 1..20；改变该值时响应
`restartRequired=true`，提示重启服务后生效。
