# 阶段 04 待触达队列实施计划

> **For implementer:** 功能优先、严格 TDD；每个 Gate 最多 3 个合并核心测试，Gate 结束后提交。

**Goal:** 实现“策略批准不等于发送”的持久化双重授权闭环，并在 Pipeline 中可视化逐条触达结果。

**Architecture:** 新增 Job-scoped OutreachItem 状态机作为计划层；实际发送仍复用 Message 和
Channel。Runner 只消费显式授权后 CAS 为 ready 的项目；API/UI 只调用领域服务。

## Gate 04-A：持久队列与领域服务

1. 新增 `OutreachItem`、约束、旧库幂等迁移和唯一 `(job,strategy,channel)`。
2. 实现 prepare/list/summary/authorize/skip/cancel/claim/finalize；固定错误注册表，不保存异常正文。
3. 3 个合并测试：幂等准备与四重隔离；授权/跳过/取消状态机；双 Session claim 单胜者。
4. 更新数据库与 Pipeline 文档，提交并推送 Gate 备份。

## Gate 04-B：Runner 与真实执行边界

1. 策略阶段后 prepare 队列；outreach 前创建 `outreach_execution`，10 秒默认 skip。
2. execute 只将合法项授权为 ready；Runner 逐条 claim，发送前重验并写 Message。
3. 数据库权威执行每日限额；false=failed、异常=uncertain；取消边界不新增发送。
4. 3 个合并测试：无选择零调用；显式执行只发合法/批准项；并发/限额/异常安全收口。

## Gate 04-C：API 与界面

1. 实现 stage-04、队列、prepare/execute/skip 认证 API 和严格安全 DTO。
2. 实现 `StageOutreachResult` 与 `OutreachQueueDrawer`，展示渠道、内容、状态和稳定结果。
3. 执行按钮显示评论/私信数量并二次确认；切 Job/关闭 abort，旧响应不能串任务。
4. Pipeline 只挂一个队列抽屉，不新增 SSE；最多 5 个前端核心用例并通过 type-check/build。

## Gate 04-D：验收与交付

1. 使用伪 Channel 端到端验证 prepare -> execute -> sent/failed/uncertain，禁止真实平台发送。
2. 浏览器验证桌面/移动入口、确认框、状态刷新和无控制台错误。
3. 更新索引、Plugin、Pipeline、API、数据库和测试报告。
4. 阶段提交，创建 `backup/stage04-outreach-queue-20260813` 并推送 mirror。

