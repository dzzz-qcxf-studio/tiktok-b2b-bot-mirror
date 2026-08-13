# 阶段 03 策略审核闭环设计

> 状态：已确认，进入分阶段实现
> 日期：2026-08-13
> 范围：阶段 03 策略草案、人工审核、阶段 04 准入；不执行新的外部触达

## 1. 目标

把现有“模型生成策略后直接成为阶段 04 可消费记录”的技术流程，升级为可运营、可审计的策略审核闭环：

- 只有当前 Job、当前平台、人工 `qualified` 的候选才能产生策略草案；
- 模型输出仍经过严格 Schema 与项目内安全模板渲染，初始状态固定为 `draft`；
- 人工可以逐条编辑、批准或退回，也可以批量批准当前 Job 的合法草案；
- 只有 `approved` 策略才能进入阶段 04，阶段 04 发送前仍重新检查 Job、平台、人工资格和模板安全；
- 10 秒无人选择时保留草案，跳过触达并继续报告，不自动发送评论或私信。

## 2. 方案比较与结论

### 方案 A：只读展示

复用现有 `strategies` 数据，仅增加阶段 03 卡片。改动小，但没有人工把关，无法满足阶段 04 安全准入。

### 方案 B：逐条审核 + 批量批准（采用）

为策略增加审核状态和版本，提供 Job-scoped API、工作台和服务端关卡。工作量适中，但能形成完整审核、审计和并发保护。

### 方案 C：仅整批批准

界面最简单，但无法处理单个客户不合适的话术，也不利于保留退回原因。

采用方案 B。

## 3. 数据模型

`strategies` 保留现有业务字段，并增加：

- `review_status`: `draft / approved / rejected`，默认 `draft`；
- `review_version`: 从 0 开始，每次人工 mutation 原子递增；
- `reviewed_at`: 最近一次人工结论时间；
- `reviewed_by`: 当前认证操作者；
- `review_reason`: 可选、受长度限制的公开审核原因；
- `updated_at`: 草案或审核状态的最近更新时间。

旧库已有策略迁移为 `draft`，绝不因历史存在而自动批准。唯一约束继续使用 `(job_id, user_id)`；所有新接口拒绝 `job_id IS NULL` 的 legacy 策略。

## 4. 后端服务与 API

新增 Job-scoped 受认证接口：

```text
GET   /api/acquisition/jobs/{jobId}/stage-03
GET   /api/acquisition/jobs/{jobId}/strategies?reviewStatus=&limit=&offset=
GET   /api/acquisition/jobs/{jobId}/strategies/{strategyId}
PATCH /api/acquisition/jobs/{jobId}/strategies/{strategyId}
POST  /api/acquisition/jobs/{jobId}/strategies/{strategyId}/approve
POST  /api/acquisition/jobs/{jobId}/strategies/{strategyId}/reject
POST  /api/acquisition/jobs/{jobId}/strategies/approve-batch
```

所有写请求必须携带 `reviewVersion`。服务在同一事务中校验策略、Job、User、PipelineJobUser 和平台归属，且候选当前仍为人工 `qualified`。版本或状态竞争返回 409 与权威当前摘要，不回显异常正文。

编辑只允许 `persona / strategyType / commentTemplate / dmTemplate / actionPlan / priority`，并复用 `StrategyResult` 的枚举、长度、链接、联系方式、控制字符和空模板安全规则。编辑后状态回到 `draft`，不能用 PATCH 绕过批准动作。

## 5. Runner 与决策关卡

阶段 03 完成后策略均为草案。若 Job 未请求 `outreach`，任务直接继续后续阶段，不创建触达关卡。

若 Job 请求了 `outreach` 且存在草案，服务端创建普通 10 秒 `strategy_review` 关卡：

- `open_strategy_workbench`：进入无自动 timeout 的人工审核会话；
- `approve_all_safe_drafts`：批量批准当前仍合法的草案；
- `skip_outreach`：保留草案并跳过阶段 04；
- `cancel_job`：取消任务。

默认项固定为 `skip_outreach`。无人操作、页面关闭或前端断线都不会产生外部发送。

人工工作台完成后，Runner 释放旧浏览器与账号租约，再执行既有完整 preflight 并重新获取资源。阶段 04 只读取 `approved` 策略；若批准后候选资格、平台或模板发生变化，该条策略在发送前被安全跳过。

## 6. Pipeline 界面

阶段 03 默认展示业务结果卡，不展示原始 JSON：

- 合格候选数、已生成草案数、待审核数、已批准数、已退回数；
- “打开策略审核工作台”主按钮；
- 空、运行中、失败、旧任务和无合格候选状态；
- 技术结果仍只在现有“技术诊断”折叠区。

策略审核抽屉绑定单一 Job，左侧为策略队列，右侧显示候选公开资料、双评分、策略字段和审核记录。切 Job、切策略、关闭或卸载会取消旧读取；mutation 全局防双击，成功后必须权威重读后才显示成功。

移动端抽屉全屏，主要操作最小高度 44px，支持 Escape、焦点恢复和 reduced-motion。

## 7. 安全与错误处理

- 不把 Cookie、Profile、Prompt、Response、API Key 或原始异常写入策略、审计、事件或 API；
- 模型只生成建议，最终外发资格仍来自人工 `qualified` 与人工 `approved`；
- 编辑/批准使用版本 CAS，旧响应不能覆盖当前策略；
- 批量批准逐条验证，不合法条目保持草稿并返回稳定计数；
- API、Runner 和阶段 04 都独立执行 Job/platform/qualified/approved 四重闸门；
- 10 秒默认只会缩小执行范围，不扩大建单授权。

## 8. 测试与验收

1. 数据迁移：旧策略为 draft，状态/版本约束和唯一约束有效；
2. API：认证、Job 隔离、分页、严格 DTO、编辑安全、CAS、逐条/批量审核；
3. Runner：10 秒默认 skip、打开人工会话释放/重获资源、取消清理、无批准策略不触达；
4. 阶段 04：只消费 approved，资格或模板变化后二次闸门跳过；
5. UI：阶段 03 卡片、策略工作台、切换防串、权威重读、移动端与无障碍；
6. 真实验收：至少生成一条草案，人工编辑并批准，确认阶段 04 只看到该批准策略；真实外发仍需单独明确授权。

## 9. 分阶段交付

- Gate 03-A：模型、迁移、Store 与审核服务；
- Gate 03-B：受认证 API 与 Runner 安全关卡；
- Gate 03-C：阶段 03 业务卡和审核工作台；
- Gate 03-D：完整回归、真实浏览器验收、文档、提交与备份。

