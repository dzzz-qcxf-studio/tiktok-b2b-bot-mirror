# Hermes H2 获客任务创建器实施计划

> 状态：待执行  
> 日期：2026-08-08  
> 对应设计：`docs/plans/2026-08-08-hermes-h2-acquisition-creator-design.md`  
> 目标：让现有 Pipeline 页面能用目标画像、初始关键词和探索预算，原子创建一条可由 Hermes 执行的获客任务

## 实施原则

- 严格 RED→GREEN→REFACTOR，每个任务完成后独立提交。
- 不修改 H1 后端契约，不增加第二个任务系统或新路由。
- 前端只调用原子 `POST /api/acquisition/jobs`，不先创建普通 Job 再补 Campaign/Keyword。
- 新的创建器抽为独立组件，避免已超过 1000 行的 `Pipeline.vue` 继续膨胀。
- 最初失败测试必须是预期的行为缺失，不得因测试环境或语法错误而失败。

## Task 1：锁定前端原子建单契约

**Files**

- Modify: `tiktok_bot_console/ui/src/types/pipeline.ts`
- Modify: `tiktok_bot_console/ui/src/api/index.ts`
- Modify: `tiktok_bot_console/ui/src/api/mock.ts`
- Create: `tiktok_bot_console/ui/src/api/acquisitionJob.spec.ts`

### 1.1 RED：先写 API 契约测试

测试要求：

- `createAcquisitionJob()` 向 `/api/acquisition/jobs` 发送一次 POST；
- 请求类型要求 `campaign` 和 1—100 个 `keywords`；
- 响应类型同时包含 `job/campaign/keywords`；
- Mock 模式返回相同结构，并保留请求中的 Campaign 与 Keyword 值；
- Real/Auto 模式写请求不在后端失败时退化成伪造 Mock 成功。

Run:

```powershell
cd tiktok_bot_console/ui
npm exec vitest -- --run src/api/acquisitionJob.spec.ts
```

Expected: FAIL，因为原子请求/响应类型和 `createAcquisitionJob` 尚不存在。

### 1.2 GREEN：实现最小契约

- 新增 `CreateAcquisitionJobPayload` 和 `AcquisitionJobResponse`。
- 在 `realApi`、`wrapped` 和 named exports 中各增加一个原子建单函数。
- 在 `mock.ts` 中从同一 payload 创建 Job、Campaign 和 Keywords，不伪造业务统计。
- 对原子写入使用显式 Mock/Real 选择，Real 失败时直接透传失败。

Run 同上，Expected: PASS。

### 1.3 REFACTOR 与提交

```powershell
npm run type-check
git diff --check
git add tiktok_bot_console/ui/src/types/pipeline.ts tiktok_bot_console/ui/src/api/index.ts tiktok_bot_console/ui/src/api/mock.ts tiktok_bot_console/ui/src/api/acquisitionJob.spec.ts
git commit -m "feat: add atomic acquisition job client"
```

## Task 2：建立可测试的画像草稿与校验器

**Files**

- Create: `tiktok_bot_console/ui/src/components/acquisitionCreator.ts`
- Create: `tiktok_bot_console/ui/src/components/acquisitionCreator.spec.ts`

### 2.1 RED：写纯状态/校验测试

覆盖：

- 抖音默认 `countries=['CN']`、`languages=['zh-CN']`；
- TikTok 国家为空时目标画像校验失败；
- 切换回抖音后国家恢复为 `CN`；
- `collect` 始终存在且阶段被定义顺序规范化；
- 标签和关键词去空格、拒绝空白/重复；
- 预算的七个整数边界、成立年限范围和关键词比例合计；
- 最终 payload 将硬性条件、阶段 02 偏好、预算、关键词和无凭据 `configSnapshot` 完整分层。

Run:

```powershell
npm exec vitest -- --run src/components/acquisitionCreator.spec.ts
```

Expected: FAIL，因为草稿工厂、校验器和 payload 构建器尚不存在。

### 2.2 GREEN：最小领域函数

实现：

- `createAcquisitionDraft(platform)`；
- `applyPlatformDefaults(draft, platform)`；
- `normalizeSelectedStages(stages)`；
- `addUniqueListItem()` 和关键词联合去重；
- `validateExecutionScope/validateTargetProfile/validateExplorationStrategy`；
- `buildAcquisitionJobPayload(draft)`。

校验返回稳定的错误 code，界面再通过 i18n 翻译，不在领域函数中硬编码中文。

Run 同上，Expected: PASS。

### 2.3 REFACTOR 与提交

```powershell
npm run type-check
git diff --check
git add tiktok_bot_console/ui/src/components/acquisitionCreator.ts tiktok_bot_console/ui/src/components/acquisitionCreator.spec.ts
git commit -m "feat: define acquisition creator validation"
```

## Task 3：实现分步创建器的执行范围与目标画像

**Files**

- Create: `tiktok_bot_console/ui/src/components/AcquisitionJobCreator.vue`
- Create: `tiktok_bot_console/ui/src/views/PipelineAcquisition.spec.ts`
- Modify: `tiktok_bot_console/ui/src/i18n/zh-CN.ts`
- Modify: `tiktok_bot_console/ui/src/i18n/en-US.ts`

### 3.1 RED：写前两步行为测试

使用 Vue Test Utils 和真实 i18n，Mock：

- `getAccounts`；
- `getPipelineCapabilities`；
- `createAcquisitionJob`。

断言：

- 渲染四步导航，默认在执行范围；
- 保留 TikTok/抖音键盘单选、自动/指定账号和 Provider/账号预检；
- `collect` 不可取消，其他阶段可选，无可用账号/预检失败时不能继续；
- 抖音目标国家显示中国且不可编辑；
- TikTok 可增删国家，无国家、行业或客户角色时不能继续；
- 标签输入支持 Enter/按钮添加与独立删除，局部错误可见。

Run:

```powershell
npm exec vitest -- --run src/views/PipelineAcquisition.spec.ts
```

Expected: FAIL，因为组件尚不存在。

### 3.2 GREEN：实现前两步

- 使用现有 Pipeline 设计 token，实现四步导航和前两步内容。
- 账号列表和能力预检保留请求代次，忽略迟到响应。
- 切换平台时清除失效指定账号并应用国家/语言默认。
- 表单控件提供 label、test id、可见焦点和加载/错误/禁用状态。

### 3.3 验证与提交

```powershell
npm exec vitest -- --run src/views/PipelineAcquisition.spec.ts src/components/acquisitionCreator.spec.ts
npm run type-check
git diff --check
git add tiktok_bot_console/ui/src/components/AcquisitionJobCreator.vue tiktok_bot_console/ui/src/views/PipelineAcquisition.spec.ts tiktok_bot_console/ui/src/i18n/zh-CN.ts tiktok_bot_console/ui/src/i18n/en-US.ts
git commit -m "feat: add acquisition target profile steps"
```

## Task 4：实现探索策略、原子提交与锁定摘要

**Files**

- Modify: `tiktok_bot_console/ui/src/components/AcquisitionJobCreator.vue`
- Modify: `tiktok_bot_console/ui/src/views/PipelineAcquisition.spec.ts`
- Modify: `tiktok_bot_console/ui/src/i18n/zh-CN.ts`
- Modify: `tiktok_bot_console/ui/src/i18n/en-US.ts`

### 4.1 RED：扩展行为测试

断言：

- 硬性条件和“阶段 02 按需核验”为两个有语义的分区；
- 员工数、注册资本和上市状态的说明明确不用于阶段 01 强制淘汰；
- 关键词可增删并选择语言/类型，重复和 0 个关键词被阻止；
- 七项预算与关键词混合比例都有边界错误；
- 确认步显示完整业务摘要，且未通过全量校验时不能提交；
- 双击/并发提交只发送一次请求；
- 请求完整含 `platform/accountMode/accountId/stages/configSnapshot/campaign/keywords`；
- 成功后显示来自 API 响应的“已锁定”摘要，表单继续编辑不会改变摘要；
- 失败时保留输入、显示可重试错误且不产生锁定摘要。

Run 专项测试，Expected: FAIL，因为第 3/4 步和提交尚未实现。

### 4.2 GREEN：实现第 3/4 步

- 实现条件分层、关键词编辑器、七项预算和 70/30 默认混合比例。
- 确认步使用同一 draft 的规范化视图，不手工组装第二份 payload。
- 提交时快照 payload，设置 loading 锁，调用 `createAcquisitionJob()`。
- 锁定摘要深拷贝 API 返回值，显示 Job/Campaign/Keywords 业务字段。
- 提交成功后 emit `created`，不在子组件中复制 Pipeline 历史逻辑。

### 4.3 验证与提交

```powershell
npm exec vitest -- --run src/views/PipelineAcquisition.spec.ts src/components/acquisitionCreator.spec.ts src/api/acquisitionJob.spec.ts
npm run type-check
git diff --check
git add tiktok_bot_console/ui/src/components/AcquisitionJobCreator.vue tiktok_bot_console/ui/src/views/PipelineAcquisition.spec.ts tiktok_bot_console/ui/src/i18n/zh-CN.ts tiktok_bot_console/ui/src/i18n/en-US.ts
git commit -m "feat: submit acquisition profile atomically"
```

## Task 5：嵌入 Pipeline 并保留旧任务能力

**Files**

- Modify: `tiktok_bot_console/ui/src/views/Pipeline.vue`
- Modify: `tiktok_bot_console/ui/src/views/PipelineAcquisition.spec.ts`

### 5.1 RED：写页面集成测试

断言：

- Pipeline 渲染 `AcquisitionJobCreator`，不再渲染旧普通 Job 创建表单；
- 创建成功事件会将历史 offset 重置为 0、刷新历史并选中新 Job；
- 任务历史、详情轮询、取消、重试和阶段时间线继续工作；
- 新组件回传的账号元数据继续用于任务详情的账号标签。

Run 专项测试，Expected: FAIL，因为 Pipeline 尚未嵌入组件。

### 5.2 GREEN：替换旧创建区

- 用 `AcquisitionJobCreator` 替换旧 creator card 及其专用状态/函数/样式。
- 父组件只处理创建成功后的历史刷新与选中，不再组装获客 payload。
- 保留已有轮询请求代次与 onUnmounted 清理。

### 5.3 验证与提交

```powershell
npm exec vitest -- --run src/views/PipelineAcquisition.spec.ts
npm run type-check
npm run build
git diff --check
git add tiktok_bot_console/ui/src/views/Pipeline.vue tiktok_bot_console/ui/src/views/PipelineAcquisition.spec.ts
git commit -m "feat: embed acquisition creator in pipeline"
```

## Task 6：响应式、可访问性和错误状态收口

**Files**

- Modify: `tiktok_bot_console/ui/src/components/AcquisitionJobCreator.vue`
- Modify: `tiktok_bot_console/ui/src/views/PipelineAcquisition.spec.ts`

### 6.1 RED：增加可访问性/状态测试

- 所有可见输入都有可定位 label；
- 步骤状态使用 `aria-current`，错误使用 `role=alert`，加载使用 `role=status`；
- 提交中禁用步骤、返回和提交；
- 关键词/标签删除按钮有包含对象名称的 `aria-label`；
- 后端 422 数组 detail 能稳定提取人类可读错误，不显示 `[object Object]`。

### 6.2 GREEN：补齐状态与布局

- 桌面使用宽卡与双列分组；`<=900px` 切换单列；390px 保证 44px 触摸目标。
- 所有动效在 `prefers-reduced-motion` 下关闭，不使用 `scrollIntoView`。
- 完整覆盖 hover/focus/active/disabled/loading/empty/error。

### 6.3 验证与提交

```powershell
npm exec vitest -- --run src/views/PipelineAcquisition.spec.ts
npm run type-check
npm run build
git diff --check
git add tiktok_bot_console/ui/src/components/AcquisitionJobCreator.vue tiktok_bot_console/ui/src/views/PipelineAcquisition.spec.ts
git commit -m "fix: harden acquisition creator interactions"
```

## Task 7：文档镜像、全量回归与 H2 备份

**Files**

- Modify: `docs/wiki/00-索引.md`
- Modify: `docs/wiki/05-Pipeline.md`
- Modify: `docs/wiki/06-CLI-API-UI.md`
- Modify: `docs/wiki/12-测试报告.md`
- Modify: `README.md`

### 7.1 更新文档

- 版本更新为 H2，顶部“最后更新”使用 2026-08-08。
- 记录四步创建器、TikTok/抖音国家规则、条件分层、关键词/预算校验、
  原子建单与锁定摘要。
- 明确 H3 工作台和租户隔离仍未交付，不宣称全链路已上线。

### 7.2 专项与回归

```powershell
cd tiktok_bot_console/ui
npm exec vitest -- --run
npm run type-check
npm run build
npm test
cd ../..
python -X utf8 -m pytest tests/test_acquisition_api.py tests/test_acquisition_job_service.py tests/test_pipeline_jobs_api.py -q
python -X utf8 -m pytest -q
git diff --check
```

### 7.3 真实页面验收

- 用真实本地后端登录后访问 `/pipeline`。
- 桌面视口和 390px 视口各走一次四步流程。
- 确认浏览器 console 无 error/warning，无溢出、遮挡和不可操作控件。
- 在不消耗真实平台额度的前提下，使用可用本地账号创建一个最小 `collect/filter/report` 任务，
  确认 Job/Campaign/Keywords 同时落库且摘要显示服务端返回值。

### 7.4 独立审查、阶段提交与备份

- 请独立审查者检查规格匹配、安全边界、竞态、可访问性与测试质量。
- 修复 Important 及以上问题并重跑全量验收。
- 扫描即将提交的文件名，确认无 API Key/Cookie/Token 正文。
- 提交文档与最终修复，创建注释标签 `backup/hermes-h2-20260808`。
- 在项目根目录外的 `backups/` 生成包含完整历史的 Git bundle，用 `git bundle verify` 验证。
- 未经用户要求不 push 到远程。

## H2 最终验收门槛

- [ ] 现有 Pipeline 创建区已替换为四步 AI 获客任务创建器。
- [ ] 平台/账号/Provider/阶段预检保留，`collect` 与阶段顺序契约不会被界面破坏。
- [ ] TikTok 国家必填，抖音固定中国。
- [ ] 硬性条件和阶段 02 偏好分层正确，员工数/注册资本/上市状态不被当作阶段 01 强制淘汰。
- [ ] 关键词、比例和七项预算有与后端一致的校验。
- [ ] 前端只调用原子获客端点，无两步建单竞态，无敏感配置进入 `configSnapshot`。
- [ ] 提交后锁定摘要来自服务端快照，后续表单编辑不改变旧任务。
- [ ] 桌面、390px、可访问性、错误/加载/空状态验收通过。
- [ ] 前端专项/全量、类型、构建、Smoke、后端专项/全量与密钥扫描通过。
- [ ] H2 提交、标签和经验证的离线 bundle 完成；未 push。
