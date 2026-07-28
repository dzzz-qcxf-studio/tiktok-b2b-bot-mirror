# 交互式登录与统一 LLM Router 实施计划

> **For implementer:** Use TDD throughout. Write failing test first. Watch it fail. Then implement.

**Goal:** 用账号级隔离浏览器实现可人工完成风控验证的持久化登录，并用项目内部唯一的 LLM Router 统一供应商、业务路由、故障转移和用量日志。

**Architecture:** `InteractiveLoginService` 通过平台 Browser Provider 打开账号级 Profile，只观察、验证并持久化登录态；`LLMRouter` 以数据库中的 Provider 与 Route 为唯一配置源，通过 OpenAI 兼容适配器执行有界故障转移。旧 QR API 和 `get_llm_client()` 只作为迁移 facade，不保留第二套执行逻辑。

**Tech Stack:** Python 3.11、FastAPI、SQLAlchemy 2、SQLite、asyncio、Playwright、OpenAI Python SDK、Vue 3、TypeScript、Vitest/pytest。

**Design:** `docs/plans/2026-07-28-interactive-login-and-llm-router-design.md`

---

## Task 1：登录态模型、路径与幂等迁移

**Files:**

- Modify: `tiktok_bot_core/models/entities.py`
- Modify: `tiktok_bot_core/storage/database.py`
- Modify: `tiktok_bot_core/storage/sqlite_store.py`
- Modify: `.gitignore`
- Test: `tests/test_platforms_auth.py`

**Step 1: Write the failing tests**

新增：

```python
def test_social_account_has_persistent_auth_fields(db):
    columns = {
        column["name"]
        for column in inspect(db.engine).get_columns("tiktok_accounts")
    }
    assert {
        "storage_state_path",
        "profile_path",
        "auth_verified_at",
        "auth_version",
    } <= columns


def test_auth_migration_is_idempotent(db):
    db.init()
    db.init()


def test_auth_paths_are_account_scoped_and_relative(tmp_path):
    paths = build_auth_paths(
        data_root=tmp_path,
        platform="douyin",
        account_key="42",
    )
    assert paths.profile_dir == tmp_path / "browser_profiles" / "douyin" / "42"
    assert paths.storage_state == tmp_path / "auth_states" / "douyin" / "42.json"
```

**Step 2: Run tests and confirm RED**

```powershell
python -m pytest tests/test_platforms_auth.py -k "persistent_auth_fields or auth_migration or auth_paths" -v
```

Expected: FAIL because fields and `build_auth_paths` do not exist.

**Step 3: Minimal implementation**

在 `TikTokAccount` 增加：

```python
storage_state_path = mapped_column(String(500), default="", server_default="")
profile_path = mapped_column(String(500), default="", server_default="")
auth_verified_at = mapped_column(DateTime, nullable=True)
auth_version = mapped_column(Integer, default=1, server_default="1")
```

在 `auth_service.py` 或新建的登录状态模块中实现：

```python
@dataclass(frozen=True)
class AuthPaths:
    profile_dir: Path
    storage_state: Path


def build_auth_paths(data_root: Path, platform: str, account_key: str) -> AuthPaths:
    safe_key = re.sub(r"[^a-zA-Z0-9_-]", "_", account_key)
    if not safe_key:
        raise ValueError("invalid account key")
    return AuthPaths(
        profile_dir=data_root / "browser_profiles" / platform / safe_key,
        storage_state=data_root / "auth_states" / platform / f"{safe_key}.json",
    )
```

路径必须由服务端生成并保持在 data root 内。更新 Store 的账号 upsert，使新字段可保存但不覆盖未提供值。

`.gitignore` 增加：

```gitignore
data/browser_profiles/
data/auth_states/
tiktok_bot_core/data/browser_profiles/
tiktok_bot_core/data/auth_states/
```

**Step 4: Verify GREEN**

```powershell
python -m pytest tests/test_platforms_auth.py -k "persistent_auth_fields or auth_migration or auth_paths" -v
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add .gitignore tiktok_bot_core/models/entities.py tiktok_bot_core/storage/database.py tiktok_bot_core/storage/sqlite_store.py tiktok_bot_core/services/auth_service.py tests/test_platforms_auth.py
git commit -m "feat: add persistent social auth state"
```

---

## Task 2：账号租约与交互式登录会话状态机

**Files:**

- Create: `tiktok_bot_core/services/account_leases.py`
- Create: `tiktok_bot_core/services/interactive_login.py`
- Test: `tests/test_interactive_login.py`

**Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_same_account_cannot_hold_two_leases():
    leases = AccountLeaseManager()
    first = await leases.acquire("douyin", 1, owner="login:a")
    with pytest.raises(AccountBusyError):
        await leases.acquire("douyin", 1, owner="pipeline:b")
    await first.release()


def test_login_session_state_transitions():
    session = LoginSession.new("douyin", "marketing_01")
    session.transition("waiting_user")
    session.transition("verifying")
    session.transition("persisted")
    session.transition("confirmed")
    assert session.status == "confirmed"


def test_login_session_rejects_invalid_transition():
    session = LoginSession.new("douyin", "marketing_01")
    with pytest.raises(InvalidLoginTransition):
        session.transition("confirmed")
```

**Step 2: Verify RED**

```powershell
python -m pytest tests/test_interactive_login.py -k "lease or transition" -v
```

Expected: import failures.

**Step 3: Minimal implementation**

实现进程内 `AccountLeaseManager`：

- key 为 `(platform, account_id)`；新账号使用规范化 alias 临时 key。
- `acquire()` 原子检查并返回 async context/lease。
- `release()` 幂等。
- 租约记录 owner，错误响应包含当前用途但不泄露内部对象。

实现 `LoginSession` 与明确的 `ALLOWED_TRANSITIONS`：

```python
ALLOWED_TRANSITIONS = {
    "launching": {"waiting_user", "failed", "cancelled"},
    "waiting_user": {"verifying", "failed", "expired", "cancelled"},
    "verifying": {"waiting_user", "persisted", "failed", "expired", "cancelled"},
    "persisted": {"confirmed", "failed"},
    "confirmed": set(),
    "failed": set(),
    "expired": set(),
    "cancelled": set(),
}
```

**Step 4: Verify GREEN**

```powershell
python -m pytest tests/test_interactive_login.py -k "lease or transition" -v
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add tiktok_bot_core/services/account_leases.py tiktok_bot_core/services/interactive_login.py tests/test_interactive_login.py
git commit -m "feat: add interactive login session state"
```

---

## Task 3：抖音持久化浏览器登录 Provider

**Files:**

- Modify: `tiktok_bot_core/browser/providers.py`
- Modify: `tiktok_bot_core/services/interactive_login.py`
- Test: `tests/test_interactive_login.py`

**Step 1: Write the failing tests**

使用 fake Playwright BrowserType，不访问真实抖音：

```python
@pytest.mark.asyncio
async def test_douyin_login_launches_persistent_headed_context(tmp_path, fake_chromium):
    provider = DouyinInteractiveLoginProvider(fake_chromium, data_root=tmp_path)
    opened = await provider.open(account_key="42")
    kwargs = fake_chromium.launch_persistent_context.await_args.kwargs
    assert kwargs["user_data_dir"] == tmp_path / "browser_profiles" / "douyin" / "42"
    assert kwargs["headless"] is False
    opened.page.goto.assert_awaited_once_with(
        "https://www.douyin.com/",
        wait_until="domcontentloaded",
    )


@pytest.mark.asyncio
async def test_douyin_provider_never_clicks_login_controls(...):
    ...
    assert opened.page.click.await_count == 0


@pytest.mark.asyncio
async def test_verify_requires_authenticated_cookie_and_protected_page(...):
    result = await provider.verify(opened)
    assert result.authenticated is False
```

**Step 2: Verify RED**

```powershell
python -m pytest tests/test_interactive_login.py -k "douyin_login or verify_requires" -v
```

Expected: FAIL because Provider is absent.

**Step 3: Minimal implementation**

实现 `DouyinInteractiveLoginProvider`：

- `launch_persistent_context`，强制 `headless=False`。
- 使用独立 `user_data_dir`。
- 只 `goto(home_url)`，不调用 click、locator click 或 QR 提取。
- `verify()` 检查可靠 Cookie，再访问轻量登录后页面确认未跳转到登录。
- `persist()` 调用：

```python
state = await context.storage_state(indexed_db=True)
atomic_write_private_json(storage_state_path, state)
cookies = state.get("cookies", [])
```

写文件使用临时文件 + 同目录原子替换；测试不得把 Cookie 值输出到日志。

**Step 4: Verify GREEN**

```powershell
python -m pytest tests/test_interactive_login.py -k "douyin_login or verify_requires" -v
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add tiktok_bot_core/browser/providers.py tiktok_bot_core/services/interactive_login.py tests/test_interactive_login.py
git commit -m "feat: open persistent browser for douyin login"
```

---

## Task 4：TikTok 指纹登录边界与统一会话服务

**Files:**

- Modify: `tiktok_bot_core/browser/providers.py`
- Modify: `tiktok_bot_core/services/interactive_login.py`
- Test: `tests/test_interactive_login.py`
- Test: `tests/test_pipeline_jobs.py`

**Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_tiktok_login_requires_registered_fingerprint_provider(service):
    with pytest.raises(LoginUnavailableError) as error:
        await service.start(platform="tiktok", account_alias="tk_01")
    assert error.value.code == "fingerprint_provider_unavailable"


@pytest.mark.asyncio
async def test_login_persists_only_after_reliable_verification(service, fake_provider):
    session = await service.start(platform="douyin", account_alias="dy_01")
    fake_provider.verify.return_value = AuthVerification(authenticated=False)
    result = await service.verify(session.token)
    assert result.status == "waiting_user"
    fake_provider.persist.assert_not_awaited()


@pytest.mark.asyncio
async def test_verified_login_is_persisted_before_confirmed(...):
    ...
    assert session.status == "confirmed"
    assert session.persisted is True
```

**Step 2: Verify RED**

```powershell
python -m pytest tests/test_interactive_login.py tests/test_pipeline_jobs.py -k "fingerprint_provider or reliable_verification or persisted_before" -v
```

Expected: FAIL.

**Step 3: Minimal implementation**

- 扩展现有 Browser Provider registry，区分 Pipeline Session 与 Interactive Login Session。
- `UnavailableFingerprintProvider.open_interactive_login()` 返回稳定错误码。
- `InteractiveLoginService.start/status/verify/cancel` 成为唯一入口。
- `verify()` 严格按 `waiting_user -> verifying -> persisted -> confirmed` 推进。
- persist 失败时不得把账号标为 `logged_in`。
- 会话终态总是关闭浏览器并释放账号租约。
- 超时清理使用服务内部任务，不依赖 FastAPI `BackgroundTasks` 长时间占用响应任务。

**Step 4: Verify GREEN**

```powershell
python -m pytest tests/test_interactive_login.py tests/test_pipeline_jobs.py -k "fingerprint_provider or reliable_verification or persisted_before" -v
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add tiktok_bot_core/browser/providers.py tiktok_bot_core/services/interactive_login.py tests/test_interactive_login.py tests/test_pipeline_jobs.py
git commit -m "feat: unify interactive social login service"
```

---

## Task 5：登录 API 与旧 QR API 兼容层

**Files:**

- Modify: `tiktok_bot_api/main.py`
- Modify: `tiktok_bot_console/ui/src/api/index.ts`
- Test: `tests/test_platforms_auth.py`

**Step 1: Write the failing API tests**

```python
def test_create_interactive_login_session(api_client, fake_login_service):
    response = api_client.post("/api/accounts/login-sessions", json={
        "platform": "douyin",
        "accountAlias": "dy_01",
    })
    assert response.status_code == 201
    assert response.json()["status"] == "launching"
    assert "qrcode_url" not in response.json()


def test_verify_interactive_login_session(api_client, fake_login_service):
    response = api_client.post("/api/accounts/login-sessions/token-1/verify")
    assert response.status_code == 200


def test_legacy_qrcode_endpoint_is_deprecated_and_has_no_image(api_client):
    response = api_client.post("/api/accounts/login-qrcode", json={
        "platform": "douyin",
        "username": "dy_01",
    })
    assert response.json()["deprecated"] is True
    assert "qrcode_url" not in response.json()
```

**Step 2: Verify RED**

```powershell
python -m pytest tests/test_platforms_auth.py -k "interactive_login_session or legacy_qrcode" -v
```

Expected: 404 or schema failure.

**Step 3: Minimal implementation**

增加四个新端点并把 service 注入方式保持可测试。HTTP 映射：

- 201：会话创建。
- 404：token 不存在。
- 409：账号租约冲突或 TikTok Provider 不可用。
- 422：平台/alias 无效。
- 500：浏览器启动或持久化失败。

旧 QR API 调用同一 Service，不再调用 `_qrcode_login_task`，不返回图片字段。删除新代码对 `qrcode_path/qrcode_payload` 的依赖。

前端 API 新增：

```ts
createLoginSession(payload)
getLoginSession(token)
verifyLoginSession(token)
cancelLoginSession(token)
```

**Step 4: Verify GREEN**

```powershell
python -m pytest tests/test_platforms_auth.py -k "interactive_login_session or legacy_qrcode" -v
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add tiktok_bot_api/main.py tiktok_bot_console/ui/src/api/index.ts tests/test_platforms_auth.py
git commit -m "feat: expose interactive login sessions"
```

---

## Task 6：前端交互式登录窗口

**Files:**

- Move: `tiktok_bot_console/ui/src/components/QRScanModal.vue` -> `tiktok_bot_console/ui/src/components/InteractiveLoginModal.vue`
- Modify: `tiktok_bot_console/ui/src/views/ConfigAccounts.vue`
- Modify: `tiktok_bot_console/ui/src/i18n/zh-CN.ts`
- Modify: `tiktok_bot_console/ui/src/i18n/en-US.ts`
- Modify: `tiktok_bot_console/ui/scripts/smoke.mjs`
- Test: `tiktok_bot_console/ui/src/components/InteractiveLoginModal.spec.ts`

**Step 1: Write the failing component tests**

```ts
it('shows manual browser instructions without QR content', async () => {
  const wrapper = mount(InteractiveLoginModal, { props: { platform: 'douyin' } })
  expect(wrapper.text()).toContain('请在已打开的浏览器中完成登录')
  expect(wrapper.find('img[alt=\"QR Code\"]').exists()).toBe(false)
  expect(wrapper.find('.qr-fake').exists()).toBe(false)
})

it('verifies and saves only after user action', async () => {
  await wrapper.get('[data-test=\"verify-login\"]').trigger('click')
  expect(api.verifyLoginSession).toHaveBeenCalledWith('token-1')
})

it('cancels backend session before closing', async () => {
  await wrapper.get('[data-test=\"cancel-login\"]').trigger('click')
  expect(api.cancelLoginSession).toHaveBeenCalledWith('token-1')
})
```

**Step 2: Verify RED**

```powershell
cd tiktok_bot_console/ui
npm test -- --run src/components/InteractiveLoginModal.spec.ts
```

Expected: FAIL because component is absent and old component renders QR.

**Step 3: Minimal implementation**

- 删除二维码 URL、伪二维码、QR cells、`seenRealQR` 和二维码状态。
- 状态改为新会话状态。
- 显示浏览器人工登录说明。
- 增加明确的验证/保存和取消按钮。
- 平台切换时先取消旧会话再创建新会话。
- 组件卸载时发送 cancel；confirmed 会话除外。
- 父组件只在 confirmed 后刷新账号列表。

**Step 4: Verify GREEN**

```powershell
npm test -- --run src/components/InteractiveLoginModal.spec.ts
npm run build
node scripts/smoke.mjs
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add tiktok_bot_console/ui/src/components tiktok_bot_console/ui/src/views/ConfigAccounts.vue tiktok_bot_console/ui/src/i18n tiktok_bot_console/ui/scripts/smoke.mjs
git commit -m "feat: replace QR modal with interactive login"
```

---

## Task 7：LLM Provider、Route 与请求日志模型

**Files:**

- Modify: `tiktok_bot_core/models/entities.py`
- Modify: `tiktok_bot_core/storage/database.py`
- Create: `tiktok_bot_core/storage/llm_store.py`
- Test: `tests/test_llm_router.py`

**Step 1: Write the failing tests**

```python
def test_llm_tables_are_created(db):
    tables = set(inspect(db.engine).get_table_names())
    assert {"llm_providers", "llm_routes", "llm_request_logs"} <= tables


def test_default_routes_are_seeded_from_legacy_settings(db, settings):
    seed_legacy_llm_config(db, settings)
    routes = LLMStore().list_routes(db.session())
    assert {r.route_key for r in routes} == {
        "collection", "qualification", "strategy", "iteration", "default",
    }


def test_llm_seed_is_idempotent(db, settings):
    seed_legacy_llm_config(db, settings)
    seed_legacy_llm_config(db, settings)
    assert LLMStore().count_providers(db.session()) == 1
```

**Step 2: Verify RED**

```powershell
python -m pytest tests/test_llm_router.py -k "tables or default_routes or seed" -v
```

Expected: import/table failures.

**Step 3: Minimal implementation**

按设计创建三张表。`LLMStore` 提供：

```python
create_provider(...)
update_provider(...)
delete_provider(...)
list_providers(...)
replace_route_chain(route_key, entries)
get_route_chain(route_key)
record_request(...)
usage_summary(...)
```

约束：

- Provider name 唯一。
- `api_key_env` 只允许 `[A-Z][A-Z0-9_]*`。
- route key 只允许注册表中的五种。
- 删除仍被 Route 引用的 Provider 返回冲突，不能级联静默删除。
- seed 不保存密钥值。

**Step 4: Verify GREEN**

```powershell
python -m pytest tests/test_llm_router.py -k "tables or default_routes or seed" -v
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add tiktok_bot_core/models/entities.py tiktok_bot_core/storage/database.py tiktok_bot_core/storage/llm_store.py tests/test_llm_router.py
git commit -m "feat: add LLM provider routing models"
```

---

## Task 8：LLM Router、失败分类与熔断

**Files:**

- Replace: `tiktok_bot_core/llm/client.py`
- Create: `tiktok_bot_core/llm/router.py`
- Create: `tiktok_bot_core/llm/providers.py`
- Modify: `tiktok_bot_core/llm/__init__.py`
- Test: `tests/test_llm_router.py`
- Modify: `tests/test_core.py`

**Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_router_uses_route_priority(router, first_provider, second_provider):
    await router.chat(route="strategy", prompt="hello")
    first_provider.chat.assert_awaited_once()
    second_provider.chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_retryable_failure_falls_back(router, first_provider, second_provider):
    first_provider.chat.side_effect = LLMProviderError("timeout", retryable=True)
    second_provider.chat.return_value = completion("ok")
    result = await router.chat(route="strategy", prompt="hello")
    assert result == "ok"


@pytest.mark.asyncio
async def test_auth_failure_does_not_fallback(router, first_provider, second_provider):
    first_provider.chat.side_effect = LLMProviderError("unauthorized", retryable=False)
    with pytest.raises(LLMRouteError):
        await router.chat(route="strategy", prompt="hello")
    second_provider.chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_circuit_opens_after_three_retryable_failures(...):
    ...
    assert router.circuits["provider-1"].state == "open"


@pytest.mark.asyncio
async def test_json_completion_records_parse_failure_without_prompt(...):
    ...
    assert log.error_category == "invalid_json"
    assert not hasattr(log, "prompt")
```

**Step 2: Verify RED**

```powershell
python -m pytest tests/test_llm_router.py tests/test_core.py -k "router or fallback or circuit or parse_failure" -v
```

Expected: FAIL because Router is absent.

**Step 3: Minimal implementation**

实现：

```python
class LLMRouter:
    async def chat(self, *, route: str = "default", prompt: str, system: str | None = None) -> str: ...
    async def json_completion(self, *, route: str = "default", prompt: str, system: str | None = None) -> dict: ...
```

`OpenAICompatibleProvider` 每次从 Provider 配置的 `api_key_env` 读取密钥，创建或缓存与配置版本绑定的 SDK Client。

错误分类：

```python
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
NON_RETRYABLE_STATUS = {400, 401, 403, 404, 422}
```

未知网络异常按 retryable；本地参数/配置异常按 non-retryable。每次请求最多三家 Provider，每家一次。

保留兼容：

```python
def get_llm_client() -> LLMRouter:
    return get_llm_router()
```

**Step 4: Verify GREEN**

```powershell
python -m pytest tests/test_llm_router.py tests/test_core.py -v
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add tiktok_bot_core/llm tests/test_llm_router.py tests/test_core.py
git commit -m "feat: route LLM calls with failover"
```

---

## Task 9：把业务调用点映射到唯一 Route

**Files:**

- Modify: `tiktok_bot_core/plugins/collectors/ai_douyin_collector.py`
- Modify: `tiktok_bot_core/plugins/filters/llm_filter.py`
- Modify: `tiktok_bot_core/services/pipeline.py`
- Modify: `tests/test_pipeline.py`
- Modify: `tests/test_plugins.py`

**Step 1: Write the failing tests**

在现有 mock 断言中增加 route：

```python
assert mock_llm.json_completion.await_args.kwargs["route"] == "collection"
assert filter_llm.json_completion.await_args.kwargs["route"] == "qualification"
assert strategy_llm.json_completion.await_args.kwargs["route"] == "strategy"
assert iteration_llm.json_completion.await_args.kwargs["route"] == "iteration"
```

**Step 2: Verify RED**

```powershell
python -m pytest tests/test_pipeline.py tests/test_plugins.py -k "llm or strategy or iterate or collector" -v
```

Expected: FAIL because current calls do not pass route.

**Step 3: Minimal implementation**

只在调用参数增加固定 route，不让插件读取 Provider：

- `ai_douyin_collector` -> `collection`
- `llm_filter` -> `qualification`
- Pipeline 策略 -> `strategy`
- Pipeline 迭代 -> `iteration`
- 未明确调用 -> `default`

更新 mocks 为兼容 keyword-only route。

**Step 4: Verify GREEN**

```powershell
python -m pytest tests/test_pipeline.py tests/test_plugins.py -v
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add tiktok_bot_core/plugins/collectors/ai_douyin_collector.py tiktok_bot_core/plugins/filters/llm_filter.py tiktok_bot_core/services/pipeline.py tests/test_pipeline.py tests/test_plugins.py
git commit -m "refactor: map business LLM calls to routes"
```

---

## Task 10：LLM 管理 API 与服务端连接测试

**Files:**

- Modify: `tiktok_bot_api/main.py`
- Modify: `tiktok_bot_console/ui/src/api/index.ts`
- Create: `tests/test_llm_api.py`

**Step 1: Write the failing API tests**

```python
def test_provider_crud_never_returns_secret(api_client):
    response = api_client.post("/api/llm/providers", json={
        "name": "deepseek-main",
        "displayName": "DeepSeek",
        "baseUrl": "https://api.deepseek.com/v1",
        "defaultModel": "deepseek-chat",
        "apiKeyEnv": "DEEPSEEK_API_KEY",
    })
    body = response.json()
    assert "apiKey" not in body
    assert body["configured"] in {True, False}


def test_replace_route_chain_is_atomic(api_client, providers):
    response = api_client.put("/api/llm/routes/strategy", json={
        "providers": [
            {"providerId": providers[0], "priority": 10},
            {"providerId": providers[1], "priority": 20},
        ]
    })
    assert response.status_code == 200


def test_connection_test_runs_server_side(api_client, fake_probe):
    response = api_client.post(f"/api/llm/providers/{fake_probe.id}/test")
    fake_probe.test.assert_awaited_once()
    assert response.json()["reachable"] is True
```

**Step 2: Verify RED**

```powershell
python -m pytest tests/test_llm_api.py -v
```

Expected: 404.

**Step 3: Minimal implementation**

端点：

```text
GET    /api/llm/providers
POST   /api/llm/providers
PUT    /api/llm/providers/{id}
DELETE /api/llm/providers/{id}
POST   /api/llm/providers/{id}/test
PUT    /api/llm/providers/{id}/secret
GET    /api/llm/routes
PUT    /api/llm/routes/{route_key}
GET    /api/llm/usage
```

删除旧的虚构 providers payload。Secret 端点只写 Provider 指定的环境变量/被忽略的 `.env`，响应不回显。

连接测试统一由 Provider adapter 执行，并设置短超时。

**Step 4: Verify GREEN**

```powershell
python -m pytest tests/test_llm_api.py -v
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add tiktok_bot_api/main.py tiktok_bot_console/ui/src/api/index.ts tests/test_llm_api.py
git commit -m "feat: expose LLM routing management API"
```

---

## Task 11：真实 LLM 配置 UI

**Files:**

- Modify: `tiktok_bot_console/ui/src/views/ConfigLlm.vue`
- Modify: `tiktok_bot_console/ui/src/i18n/zh-CN.ts`
- Modify: `tiktok_bot_console/ui/src/i18n/en-US.ts`
- Modify: `tiktok_bot_console/ui/src/api/mock.ts`
- Modify: `tiktok_bot_console/ui/scripts/smoke.mjs`
- Test: `tiktok_bot_console/ui/src/views/ConfigLlm.spec.ts`

**Step 1: Write the failing tests**

```ts
it('persists a provider through backend API', async () => {
  await wrapper.get('[data-test=\"save-provider\"]').trigger('click')
  expect(api.createLlmProvider).toHaveBeenCalled()
})

it('never probes an upstream URL from the browser', async () => {
  await wrapper.get('[data-test=\"test-provider\"]').trigger('click')
  expect(api.testLlmProvider).toHaveBeenCalledWith('provider-1')
  expect(global.fetch).not.toHaveBeenCalledWith(
    expect.stringContaining('api.deepseek.com'),
    expect.anything(),
  )
})

it('persists ordered route providers', async () => {
  await saveStrategyRoute()
  expect(api.updateLlmRoute).toHaveBeenCalledWith('strategy', expect.anything())
})
```

**Step 2: Verify RED**

```powershell
cd tiktok_bot_console/ui
npm test -- --run src/views/ConfigLlm.spec.ts
```

Expected: FAIL because current UI mutates local arrays and direct-fetches upstream.

**Step 3: Minimal implementation**

- Provider CRUD 完全走 API。
- 页面刷新重新读取数据库。
- API Key 输入只用于更新，不回填。
- 连接测试调用后端端点。
- 五个 Route 用有序 Provider 列表配置；不另设“前端主供应商”状态。
- 用量卡片读取真实 `/api/llm/usage`。
- 删除 Provider 前展示引用它的 Route；后端 409 时保留 UI 数据。

**Step 4: Verify GREEN**

```powershell
npm test -- --run src/views/ConfigLlm.spec.ts
npm run build
node scripts/smoke.mjs
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add tiktok_bot_console/ui/src/views/ConfigLlm.vue tiktok_bot_console/ui/src/i18n tiktok_bot_console/ui/src/api/mock.ts tiktok_bot_console/ui/scripts/smoke.mjs tiktok_bot_console/ui/src/views/ConfigLlm.spec.ts
git commit -m "feat: manage LLM routes in console"
```

---

## Task 12：Pipeline 登录态复用与统一回归

**Files:**

- Modify: `tiktok_bot_core/browser/providers.py`
- Modify: `tiktok_bot_core/services/pipeline_jobs.py`
- Modify: `tests/test_pipeline_runtime.py`
- Modify: `tests/test_pipeline_jobs.py`
- Modify: `tests/test_platforms_auth.py`

**Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_douyin_pipeline_loads_saved_storage_state(...):
    session = await provider.acquire(account)
    assert browser.new_context.await_args.kwargs["storage_state"] == account.storage_state_path


@pytest.mark.asyncio
async def test_pipeline_waits_while_account_is_in_interactive_login(...):
    lease = await leases.acquire("douyin", account.id, owner="login:token")
    assert await dispatcher.can_run(job) is False
    await lease.release()


def test_pipeline_rejects_expired_auth_state(...):
    ...
    assert error.code == "account_auth_expired"
```

**Step 2: Verify RED**

```powershell
python -m pytest tests/test_pipeline_runtime.py tests/test_pipeline_jobs.py tests/test_platforms_auth.py -k "storage_state or interactive_login or auth_expired" -v
```

Expected: FAIL.

**Step 3: Minimal implementation**

- 抖音 Pipeline Context 优先加载 storage state，旧账号回退 cookies。
- TikTok 继续由指纹 Profile 提供会话。
- Dispatcher 与登录服务共享 `AccountLeaseManager`。
- 登录态过期错误更新账号状态，但不删除 Profile 和快照。
- 账号检测也必须先申请租约。

**Step 4: Verify GREEN**

```powershell
python -m pytest tests/test_pipeline_runtime.py tests/test_pipeline_jobs.py tests/test_platforms_auth.py -v
```

Expected: PASS.

**Step 5: Commit**

```powershell
git add tiktok_bot_core/browser/providers.py tiktok_bot_core/services/pipeline_jobs.py tests/test_pipeline_runtime.py tests/test_pipeline_jobs.py tests/test_platforms_auth.py
git commit -m "feat: reuse persisted social login state"
```

---

## Task 13：文档、全量验证与服务验收

**Files:**

- Modify: `docs/wiki/00-索引.md`
- Modify: `docs/wiki/02-架构设计.md`
- Modify: `docs/wiki/03-Core层.md`
- Modify: `docs/wiki/06-CLI-API-UI.md`
- Modify: `docs/wiki/07-数据库.md`
- Modify: `docs/wiki/10-账号管理.md`
- Modify: `docs/wiki/11-双平台支持.md`
- Modify: `docs/wiki/13-外部资料与策略参考.md`
- Modify: `README.md`
- Modify: `tiktok_bot_console/ui/README.md`

**Step 1: Update documentation**

所有文档顶部日期更新为 2026-07-28，并同步：

- 交互式登录流程与状态机；
- Profile/storage state 安全边界；
- 新登录 API 与旧 QR API 弃用；
- LLM 三张表、五个 Route、失败分类和熔断；
- ConfigLlm 真实行为；
- MediaCrawler、Playwright 与 cc-switch 的参考结论和许可证边界。

**Step 2: Run complete backend tests**

```powershell
python -m pytest -q
```

Expected: all PASS.

**Step 3: Run complete frontend verification**

```powershell
cd tiktok_bot_console/ui
npm test -- --run
npm run build
node scripts/smoke.mjs
```

Expected: all PASS.

**Step 4: Restart services and check health**

按 `docs/wiki/06-CLI-API-UI.md` 的当前启动命令重启 API 和 UI，然后验证：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-WebRequest http://127.0.0.1:5173/
```

Expected: API health is OK; UI returns HTTP 200.

**Step 5: Manual staged acceptance**

1. 抖音账号登录：浏览器打开、人工完成验证、保存成功。
2. 服务重启：同一账号状态仍可复用。
3. TikTok 无指纹 Provider：明确提示且不启动普通浏览器。
4. LLM：主 Provider 正常请求。
5. 模拟主 Provider 5xx：备用 Provider 接管。
6. 刷新 ConfigLlm：Provider 和 Route 顺序不丢失。

**Step 6: Commit**

```powershell
git add README.md docs tiktok_bot_console/ui/README.md
git commit -m "docs: document interactive login and LLM routing"
```

