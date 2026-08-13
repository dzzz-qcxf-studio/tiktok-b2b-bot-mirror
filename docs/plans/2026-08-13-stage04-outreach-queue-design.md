# 阶段 04 待触达队列与双重授权设计

> 状态：已确认，进入实现
> 日期：2026-08-13
> 范围：待触达计划、显式执行授权、逐条结果与界面；真实平台发送只在用户明确点击后发生

## 1. 目标

阶段 03 的 `approved` 只表示策略话术审核通过，不等于允许立即发送。阶段 04 必须先把当前
Job 中仍满足平台、人工资格和安全模板条件的策略投影成持久化待触达队列，供用户查看目标、
渠道、内容和风险状态；只有用户再次选择“执行已批准触达”后，Runner 才调用评论/私信插件。

无人选择、页面关闭或连接中断时，服务端 10 秒默认固定跳过触达并继续报告，绝不发送。

## 2. 业务状态

每条待触达项按“策略 × 渠道”记录：

- `pending_approval`：已生成，等待阶段 04 明确执行授权；
- `ready`：本次 Job 已获得显式执行授权，等待执行器领取；
- `sending`：执行器已原子领取；
- `sent`：平台动作确认成功；
- `failed`：平台明确失败，可人工查看稳定错误；
- `uncertain`：浏览器或网络异常，无法确认平台是否已发送，不自动重试；
- `skipped`：超时、资格变化、模板变化、限额或人工跳过；
- `cancelled`：Job 取消且尚未发送。

队列行冻结目标用户名、渠道和待发内容的安全副本，同时记录 `strategy_id + strategy_review_version`。
执行前仍重新读取 Strategy、PipelineJobUser、User 和账号状态；任何变化只会缩小执行范围。

## 3. 双重授权流程

1. 阶段 03 完成后生成/更新当前 Job 的待触达队列，保持 `pending_approval`。
2. Hermes 普通 `outreach_execution` 关卡展示 `execute_approved_outreach / skip_outreach /
   cancel_job`，默认 `skip_outreach`，deadline 为服务端 10 秒。
3. 选择执行时，服务端把仍合法的队列项 CAS 为 `ready`，Runner 才逐条领取并调用 Channel。
4. 跳过或超时把未执行项置为 `skipped`，继续 report/iterate。
5. 取消把未执行项置为 `cancelled`，不调用 Channel。

策略审核工作台仍可在执行授权前打开；但策略批准与发送授权是两个独立动作，不能互相替代。

## 4. API 与界面

新增 Job-scoped 受认证接口：

```text
GET  /api/acquisition/jobs/{jobId}/stage-04
GET  /api/acquisition/jobs/{jobId}/outreach-items?status=&channel=&limit=&offset=
POST /api/acquisition/jobs/{jobId}/outreach-items/prepare
POST /api/acquisition/jobs/{jobId}/outreach-items/execute
POST /api/acquisition/jobs/{jobId}/outreach-items/skip
```

Pipeline 阶段 04 卡展示待授权、待执行、发送中、成功、失败/不确定、跳过数量；“查看待触达队列”
打开当前 Job 唯一抽屉。抽屉显示客户公开资料、渠道、内容、策略版本和结果，不展示 Cookie、
Profile、Prompt、Response 或原始异常。

“执行已批准触达”是高风险主操作，界面必须显示本次评论/私信数量并二次确认；它调用服务端
授权接口/关卡，不在浏览器前端直接调用插件。

## 5. 执行与幂等

- 队列生成使用 `(job_id, strategy_id, channel)` 唯一约束，重复刷新不重复建项；
- `ready -> sending` 使用 CAS，只有一个执行者；
- `sent/uncertain` 均为本轮终态，不自动重试，避免重复触达；
- 既有 `messages` 继续作为实际发送记录，新增队列项与 Message 一一关联；
- 每日限额按数据库中已发送数与本次已领取数执行，不能只依赖内存计数；
- 日限额、账号/平台、资格、策略版本和安全模板在每次发送前重新校验。

## 6. 失败与安全

- Channel 返回 false 为 `failed`；抛出或浏览器中断为 `uncertain`，不保存原始异常正文；
- 账号退出、平台不匹配、策略被退回或候选不再 qualified 时置 `skipped`；
- 取消在每次 Channel 调用前检查；已经进入 `sending` 的动作按结果收口，不伪造 cancelled；
- UI/API 只返回固定错误码和公开状态，不回显凭据或自动化内部对象；
- 本阶段真实验收默认使用伪 Channel。任何真实平台发送都需要用户当次明确点击授权。

## 7. 交付关卡

- Gate 04-A：队列表、迁移、Store/Service、幂等准备与安全状态机；
- Gate 04-B：Runner 双重授权、逐条执行、限额与稳定结果；
- Gate 04-C：API、阶段 04 卡、队列抽屉与 Pipeline 集成；
- Gate 04-D：伪 Channel 端到端验收、文档、备份与推送。

