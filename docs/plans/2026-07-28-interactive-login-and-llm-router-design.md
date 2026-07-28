# 交互式登录与统一 LLM Router 设计

> 状态：已批准  
> 最后更新：2026-07-28

## 1. 决策摘要

本设计同时解决两项互相关联但必须分阶段交付的问题：

1. 社媒账号登录从“后台自动点击并把二维码搬到前端”改为“打开隔离浏览器，由用户在浏览器内完成全部验证，再持久化登录态”。
2. 大模型调用从单例 `LLMClient` 改为项目内部唯一的 `LLMRouter`。Router 负责供应商、业务路由、故障转移、熔断与用量日志，不依赖 cc-switch 常驻。

用户已选择内部统一路由方案（方案 A）。cc-switch 仅作为架构参考，不成为运行时依赖，也不形成第二套配置源。

## 2. 现状与根因

### 2.1 登录失败不是单一选择器问题

当前流程为：

```text
前端 QRScanModal
  -> POST /api/accounts/login-qrcode
  -> Playwright 临时 Browser + 临时 Context
  -> 自动点击登录
  -> 自动切换二维码 Tab
  -> DOM 提取二维码
  -> 元素截图
  -> 整页截图兜底
  -> 前端展示图片并轮询 Cookie
```

已确认的缺陷：

- 抖音/TikTok 页面结构变化后，自动点击选择器不能稳定命中。
- 最近一次抖音兜底图片是仍显示“登录”按钮的完整首页，并不是二维码。
- 前端把任意 `qrcode_url` 都标记为真实二维码，整页截图也会令 `seenRealQR=true`。
- TikTok 登录页首先展示登录方式选择，二维码并不一定已经生成。
- 抖音扫码后可能继续要求短信、滑块或其他验证，前端二维码无法承载这些流程。
- 登录浏览器使用临时 Context，任务结束后只保存 cookies；localStorage、IndexedDB、设备环境和浏览器 Profile 不完整。
- 前端还会在没有真实二维码时生成伪二维码，容易让用户误认为可扫码。

结论：继续维护登录按钮、二维码 Tab 和二维码 DOM 选择器只能短期缓解，不能建立可靠登录链路。

### 2.2 LLM 配置目前不是路由系统

当前实现只有一个进程级 `LLMClient`：

- 启动时读取一个 `base_url/api_key/model`。
- 所有收集、筛选、策略与迭代任务使用同一个模型。
- 没有供应商持久化、按业务任务路由、失败分类、熔断、用量日志和动态重载。
- `/api/llm/providers` 只返回一条由 Settings 拼出的 DeepSeek 数据。
- `ConfigLlm.vue` 的新增/删除供应商主要是前端临时状态。
- 连接测试由浏览器直接请求供应商 `/models`，会遇到 CORS，并把网络与密钥职责放错到前端。
- API Key 更新逻辑只识别 `DEEPSEEK_API_KEY`。

## 3. 目标

### 3.1 登录系统

- 用户点击账号登录后，始终打开一个可见且隔离的浏览器环境。
- 程序不再主动点击平台登录按钮，不再提取或截图二维码。
- 用户可在浏览器内完成二维码、密码、短信、滑块和其他验证。
- 后端只负责观察、验证和持久化登录态。
- 同一账号后续任务复用同一登录身份，不重复主动登录。
- 抖音账号使用独立 Playwright Profile；TikTok 账号使用指纹浏览器 Profile。
- Cookie、storage state、Profile 元数据都属于同一个社媒账号。
- 同一账号的登录会话和 Pipeline 任务互斥。

### 3.2 LLM Router

- 项目内部只有一个大模型调用入口。
- 供应商、模型、业务路由和故障转移顺序持久化。
- 支持 OpenAI Chat Completions 兼容供应商。
- 连接测试、模型发现和实际调用都由后端执行。
- 按业务任务选择路由，不要求调用方知道供应商细节。
- 对可重试错误执行有限故障转移，对鉴权/请求格式错误快速失败。
- 提供基本熔断、延迟、成功率和 Token 用量记录。
- 密钥不硬编码、不返回前端、不进入日志或 Git。

## 4. 非目标

本轮不包含：

- 自动绕过短信、验证码、滑块或平台风控。
- 自动识别或代替用户点击登录方式。
- 同一个浏览器 Profile 并行运行多个实例。
- TikTok 在未配置指纹浏览器时回退到普通 Playwright。
- 复制 MediaCrawler 受非商业许可证限制的代码。
- 将 cc-switch 二进制嵌入项目，或要求它随项目启动。
- Anthropic/Gemini 原生协议转换；第一阶段以 OpenAI Chat Completions 兼容协议为统一边界。
- 成本优化、智能模型选择、语义缓存或分布式熔断。

## 5. 统一交互式登录架构

### 5.1 组件

```text
InteractiveLoginModal
        │
        ▼
Login Session API
        │
        ▼
InteractiveLoginService
   ┌────┴──────────────────┐
   ▼                       ▼
DouyinPersistentProvider   FingerprintBrowserProvider
   │                       │
Playwright Profile         TikTok Provider Profile
   └──────────┬────────────┘
              ▼
       LoginStateStore
  cookies + storage state + profile metadata
```

`InteractiveLoginService` 是唯一会话入口。前端、API 和 Pipeline 不自行管理浏览器进程。

### 5.2 登录会话状态

```text
launching
  -> waiting_user
  -> verifying
  -> persisted
  -> confirmed

任何非终态 -> cancelled
任何非终态 -> expired
任何非终态 -> failed
```

会话字段：

| 字段 | 说明 |
| --- | --- |
| `token` | 随机会话标识 |
| `platform` | `tiktok` / `douyin` |
| `account_alias` | 用户为账号设置的本地标识 |
| `account_id` | 已有账号重登时使用，可空 |
| `status` | 会话状态 |
| `browser_provider` | `playwright` 或具体指纹 Provider |
| `browser_profile_id` | Profile 标识 |
| `started_at/expires_at` | 生命周期 |
| `error_code/error_message` | 可解释错误 |
| `authenticated` | 可靠登录判据是否通过 |
| `persisted` | 登录态是否已完成持久化 |

会话只保存在进程内；服务重启后未完成会话统一视为过期。持久化账号状态保存在数据库和受保护数据目录。

### 5.3 抖音登录

每个账号使用独立目录：

```text
data/browser_profiles/douyin/<account-id-or-slug>/
```

通过 `chromium.launch_persistent_context(user_data_dir=...)` 启动有头浏览器。程序导航到 `https://www.douyin.com/` 后停止自动交互，用户自己点击登录并完成所有验证。

约束：

- 不使用用户日常 Chrome 的默认 User Data 目录。
- 同一目录一次只能启动一个浏览器实例。
- Profile 路径由后端生成，不能接受前端传入任意文件路径。
- 浏览器关闭前保存 `storage_state(indexed_db=True)`。

### 5.4 TikTok 登录

TikTok 登录只能通过已注册的 `FingerprintBrowserProvider`：

```python
class FingerprintBrowserProvider(Protocol):
    async def open_interactive_login(self, account) -> InteractiveBrowserSession: ...
    async def read_storage_state(self, session) -> dict: ...
    async def verify_authenticated(self, session) -> AuthVerification: ...
    async def close(self, session) -> None: ...
```

未配置具体 Provider 时：

- API 返回 `fingerprint_provider_unavailable`。
- 不创建普通 Playwright 回退会话。
- 前端明确提示先配置指纹浏览器。

Profile 本身是 TikTok 登录身份的权威载体，cookies/storage state 是校验和恢复快照。

### 5.5 登录验证

“页面看起来像已登录”不能作为唯一判据。

验证顺序：

1. 读取服务端会话 Cookie。
2. 检查平台可靠 Cookie 标识。
3. 请求一个轻量的登录后页面或 API，确认没有重定向到登录。
4. 抖音 `HasUserLogin=1` 只作为诊断信号，不能单独判定成功。
5. 验证成功后再次读取 cookies 和完整 storage state。

前端提供“我已完成登录，验证并保存”按钮；后台可以低频自动检测，但只有用户明确触发或可靠判据通过时才进入持久化。

### 5.6 登录态持久化

账号表增量字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `storage_state_path` | VARCHAR(500) | 相对受保护数据根目录的路径 |
| `profile_path` | VARCHAR(500) | 抖音本地 Profile 相对路径 |
| `auth_verified_at` | DATETIME | 最近可靠验证时间 |
| `auth_version` | INTEGER | 登录态格式版本 |

保留现有：

- `cookies_json`：兼容现有 Browser Provider 和诊断。
- `browser_provider`：Profile 所属 Provider。
- `browser_profile_id`：TikTok 指纹 Profile 或抖音本地 Profile 标识。

第一阶段不删除旧字段。`login_method` 新值为 `interactive_browser`。

### 5.7 安全边界

- `data/browser_profiles/`、`data/auth_states/`、`data/qrcodes/` 全部加入 `.gitignore`。
- API 不返回 cookies、storage state、Profile 绝对路径或密钥。
- 日志只记录 Cookie 名称和数量，不记录值。
- 删除账号时默认删除数据库关联；删除 Profile 是单独、明确且可审计的动作。
- 前端账号列表只展示 `logged_in/expired/pending` 与最近验证时间。
- storage state 文件在支持的平台使用本机密钥保护；无法加密时至少限制目录权限并在启动时给出安全告警。

### 5.8 账号锁

统一 `AccountLeaseManager` 管理：

- 交互式登录；
- 登录态检测；
- Pipeline Browser Session。

同一账号同一时间只允许一个租约。登录时已有 Pipeline 任务则返回 409；Pipeline 认领到正在登录的账号时保持排队。

### 5.9 API

新端点：

```text
POST   /api/accounts/login-sessions
GET    /api/accounts/login-sessions/{token}
POST   /api/accounts/login-sessions/{token}/verify
POST   /api/accounts/login-sessions/{token}/cancel
```

创建请求：

```json
{
  "platform": "douyin",
  "accountAlias": "marketing_01",
  "accountId": null
}
```

状态响应不再包含二维码：

```json
{
  "token": "...",
  "platform": "douyin",
  "accountAlias": "marketing_01",
  "status": "waiting_user",
  "browserOpened": true,
  "authenticated": false,
  "persisted": false,
  "expiresAt": "...",
  "error": null
}
```

旧端点兼容策略：

- `/api/accounts/login-qrcode` 暂时转发为创建交互式登录会话，并返回弃用标识。
- `/api/accounts/login-status` 映射新状态，但不再返回 `qrcode_url/qrcode_payload`。
- 一个版本后删除二维码图片端点和旧前端 API。

### 5.10 前端

`QRScanModal.vue` 攦为 `InteractiveLoginModal.vue`；为减少一次性改动，第一阶段可保留旧文件名并立即删除所有 QR 语义，随后统一重命名引用。

界面只展示：

- 当前平台与账号标识；
- “浏览器已打开，请在浏览器中完成登录和验证”；
- 登录步骤说明；
- “验证并保存”“取消”“重新打开”操作；
- 登录成功、失败或超时信息。

必须删除：

- 二维码图片；
- 伪二维码；
- `seenRealQR`；
- `qrcode_payload/qrcode_url`；
- “扫码中”等只适用于 QR 的状态。

## 6. 内部统一 LLM Router 架构

### 6.1 单一入口

```text
Collectors / Filters / Pipeline
              │
              ▼
          LLMRouter
     ┌────────┼─────────┐
     ▼        ▼         ▼
RoutePolicy ProviderRegistry UsageRecorder
     │        │
     └────┬───┘
          ▼
 OpenAICompatibleAdapter
          │
     Provider Chain
```

所有调用方使用：

```python
await router.chat(route="strategy", ...)
await router.json_completion(route="qualification", ...)
```

调用方不传 `base_url`、API Key 或供应商名称。

### 6.2 业务路由键

第一阶段固定五个路由：

| Route | 调用场景 |
| --- | --- |
| `collection` | AI 辅助采集与内容理解 |
| `qualification` | LLMFilter 用户筛选 |
| `strategy` | 营销策略与话术生成 |
| `iteration` | 复盘与迭代 |
| `default` | 未迁移或通用调用兜底 |

新增路由必须在一个注册表中声明，不能由页面任意生成字符串。

### 6.3 数据模型

#### `llm_providers`

| 字段 | 说明 |
| --- | --- |
| `id` | UUID |
| `name/display_name` | 唯一名称与显示名 |
| `protocol` | 第一阶段固定 `openai_chat` |
| `base_url` | 上游根地址 |
| `default_model` | 默认模型 |
| `api_key_env` | 密钥对应的环境变量名 |
| `enabled` | 是否可被路由 |
| `timeout_seconds` | 单次请求超时 |
| `created_at/updated_at` | 时间 |

#### `llm_routes`

| 字段 | 说明 |
| --- | --- |
| `route_key` | 五种业务路由之一 |
| `provider_id` | 供应商 |
| `priority` | 越小越优先 |
| `model_override` | 可空 |
| `enabled` | 是否加入链路 |

唯一约束：`(route_key, provider_id)`。

#### `llm_request_logs`

记录：

- route、provider、model；
- 状态、错误分类；
- input/output/total tokens；
- latency；
- 是否发生故障转移；
- 时间。

不记录 prompt、response、API Key 或完整上游错误正文。

### 6.4 路由执行

1. 加载 route 对应的已启用 Provider，按 priority 排序。
2. 跳过处于 Open 熔断状态的 Provider。
3. 使用 Provider 对应模型执行。
4. 成功则记录用量并返回。
5. 网络错误、超时、HTTP 408/429/5xx 可切换下一 Provider。
6. HTTP 400/401/403、参数错误和安全错误不盲目重试。
7. 所有 Provider 均失败时抛出包含 route 和错误分类的统一异常。

第一阶段每个 Provider 每次业务请求最多尝试一次，整个链路最多尝试三家，避免重试风暴。

### 6.5 熔断

进程内维护简化状态：

```text
closed -> open -> half_open -> closed/open
```

默认参数：

- 连续 3 次可重试失败后 Open。
- 60 秒后进入 Half Open。
- Half Open 一次成功则 Closed，一次失败则重新 Open。

熔断状态不作为跨重启权威数据；请求日志持久化，用于 UI 统计和人工判断。

### 6.6 配置与密钥

- 数据库只保存 `api_key_env`，不保存明文 API Key。
- 密钥写入项目 `.env` 或系统环境变量；`.env` 已被 Git 忽略。
- API Key 更新端点按 Provider 的 `api_key_env` 更新，不再硬编码 DeepSeek。
- API 响应只返回 `configured: true/false` 和末四位掩码。
- 连接测试由后端调用 `/models` 或最小 Chat 请求，不从浏览器直连供应商。
- 修改 Provider/Route 后 Router 下一次请求读取新版本配置，不要求重启整个服务。

### 6.7 cc-switch 边界

本项目不调用 cc-switch 管理 API，也不读取它的数据库。

未来如需使用 cc-switch，可把其本地 OpenAI 兼容地址登记为一个普通 Provider；对本项目而言它仍只是上游，业务路由、日志和配置权威仍在本项目内部。

## 7. 兼容与迁移

启动迁移执行：

1. 创建三张 LLM 表。
2. 为账号表追加登录态字段。
3. 如果没有 LLM Provider，则把现有 `llm_base_url/llm_model` 迁移为 `legacy-default` Provider。
4. `legacy-default.api_key_env` 使用现有环境变量映射，不复制密钥。
5. 为五个 Route 创建指向 `legacy-default` 的默认记录。
6. 保留 `get_llm_client()` 兼容函数，但内部返回 Router facade；所有调用点逐步显式传 route。
7. 旧 QR API 在迁移期继续可调用，但响应明确标记 deprecated。

迁移必须幂等，不能破坏现有 SQLite 数据。

## 8. 分阶段验收

### 阶段 1：交互式登录后端

验收：

- 抖音点击登录后打开独立可见浏览器。
- 程序不点击页面、不生成二维码或截图。
- 用户人工完成登录后，点击验证可保存 cookies、storage state 与 Profile。
- 未完成登录时不会误判成功。
- 同一 Profile 不能并发打开。

### 阶段 2：登录前端与 Pipeline 复用

验收：

- 前端不再展示二维码或截图。
- 登录状态与错误可解释。
- 重启服务后，抖音账号可凭持久化状态打开已登录页面。
- TikTok 未配置指纹 Provider 时明确阻止登录和任务。

### 阶段 3：LLM Router 后端

验收：

- 供应商与五类 Route 可持久化。
- 每个业务调用走指定 Route。
- 主供应商可重试失败时自动切备用。
- 401/400 不触发错误的全链路重试。
- 用量和延迟写入日志。

### 阶段 4：LLM 配置 UI

验收：

- 新增、编辑、启停和删除 Provider 都真实持久化。
- Route 顺序真实持久化。
- 连接测试从后端执行。
- 页面刷新后配置不丢失。
- 前端无法读取 API Key 明文。

### 阶段 5：回归、文档与服务验收

验收：

- 登录、账号、Pipeline、LLM 全部自动测试通过。
- UI 构建和 smoke test 通过。
- 文档与实际 API/数据表一致。
- 重启前后端服务后，健康检查与页面访问正常。

## 9. 风险与处理

| 风险 | 处理 |
| --- | --- |
| Profile 被并发占用 | 统一账号租约 + 文件锁 |
| 用户关闭浏览器 | 会话转 failed，可重新打开同一 Profile |
| Cookie 有效但页面仍要求验证 | 受保护页面验证，不只检查 Cookie 名 |
| storage state 泄露 | Git 忽略、目录权限、可用时本机加密 |
| TikTok 指纹 Provider 尚未实现 | 明确 unavailable，不回退普通浏览器 |
| 上游 LLM 大面积故障 | 有界故障转移 + 熔断 |
| LLM 配置形成两套真相 | 数据库为唯一权威；Settings 只负责首次迁移和默认值 |
| 现有调用方一次性迁移风险 | facade 兼容，按 route 分批替换并测试 |

