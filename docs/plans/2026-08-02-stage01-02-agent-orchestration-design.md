# 阶段 01—02 Agent 编排设计

> 状态：已确认，进入分阶段实现
> 日期：2026-08-02
> 范围：用户搜索、候选证据、用户筛选、人工复核；不包含阶段 03—06 的功能扩展

## 1. 目标

把当前“六个阶段依次运行并显示汇总 JSON”的技术流程，升级为可运营、可追溯、可人工把关的
获客工作流：

- 阶段 01 以视频和评论发现为主，直接搜索用户为辅；
- 每条候选记录保留关键词、视频、评论、作者和发现路径；
- 阶段 02 分离客户匹配度与判断可信度；
- 使用 `qualified / manual_review / need_enrichment / rejected` 四个核心状态；
- `manual_review` 可直接人工通过或淘汰，不强制先补充资料；
- 未满足进入条件的候选不会自动进入阶段 03；
- Pipeline 页面展示业务结果和人工待办，不再以原始 JSON 作为主要结果。

## 2. 为什么使用 Hermes，而不直接引入 OpenClaw

项目已经有持久化 `PipelineJobService`、账号租约、浏览器 Provider、LLM Router 和 Hermes
`BrowseAgent`。因此本阶段采用：

```text
PipelineJobService（唯一任务状态）
        │
        ├─ DiscoveryPlannerAgent    选择关键词和搜索路径
        ├─ HermesEvidenceAgent      受限浏览视频、评论、作者主页
        ├─ CandidateAgent           形成候选建议，不直接改业务终态
        ├─ EnrichmentAgent          按需补全主页/公开企业资料
        └─ QualificationAgent       输出匹配度、可信度、标签和建议状态
                         │
                         ▼
                AcquisitionService（事务校验与落库）
                         │
                         ▼
                  Human Review Gate
```

暂不增加 OpenClaw 常驻运行时，原因是它会形成第二套任务队列、重试和状态来源。以后如果需要
跨系统 Agent，可把 OpenClaw 作为调用方接入现有 REST API，而不能绕过当前数据库状态机。

## 3. Agent 安全边界

Agent 只允许返回受版本控制的结构化结果：

- Hermes 动作仍限制为 `navigate/click/scroll/wait/extract/done`；
- 导航仅允许 TikTok、抖音及明确批准的公开企业信息域名；
- 单关键词、单视频、单评论区、作者主页和总运行时间都有预算；
- Agent 不直接写 `qualified`、不发送评论/私信、不删除用户；
- Agent 输出先经过 schema 校验，再由 Service 在事务中保存；
- AI 状态更新使用人工版本 CAS；模型运行期间发生的人工通过/淘汰具有最终优先权；
- 原始证据与 AI 建议不可被人工修改覆盖，人工结论单独保存并留审计记录；
- 密钥、Cookie、浏览器 Profile 路径和完整页面正文不进入 Agent 结果或 API 响应。

## 4. 核心数据模型

### 4.1 获客画像 `acquisition_campaigns`

每个 Pipeline Job 关联一份不可变任务快照，包括平台、国家/地区、语言、行业、产品、客户
角色、硬性条件、偏好条件、排除对象和搜索预算。员工数、注册资本、上市状态只作为阶段 02
的按需核验偏好，不作为阶段 01 强制淘汰条件。

### 4.2 关键词 `acquisition_keywords`

记录文本、语言、类型、来源、状态、使用次数、视频数、相关视频数、候选数、阶段 02 通过数、
回复数、商机数和最近使用时间。状态为：

`new / testing / effective / cooling / low_yield / disabled`

Planner 默认按 70% 历史有效词、30% 新词组合，比例保存在任务快照中，可由用户调整。

### 4.3 发现证据 `discovery_evidence`

一条证据表示一条发现路径，包含来源类型、关键词、视频/评论/作者标识和 URL、原文、翻译、
采集时间、相关性和证据完整度。同一平台用户按平台 ID 去重，但允许保存多条证据。

### 4.4 资格评估 `candidate_assessments`

保存 AI 原始建议：多身份标签、匹配度、可信度、正负证据、缺失字段、理由、模型元数据和建议
状态。当前人工状态与 AI 建议分开，不能相互覆盖。

### 4.5 人工审计 `candidate_review_audits`

记录操作前后状态、标签、优先级、原因、操作人和时间。人工可执行：通过、淘汰、补充资料、
修改标签、调整优先级和加入黑名单；第一阶段先交付前四项。

## 5. 状态机与阶段闸门

阶段 01 状态：

`candidate / needs_more_evidence / obvious_irrelevant / duplicate / blocked`

阶段 02 核心状态：

`qualified / manual_review / need_enrichment / rejected`

迁移规则：

- AI 可根据阈值建议任一状态，但所有结果必须保留证据；`qualified` 与 `rejected` 两个终态
  都只能由人工动作写入；
- `manual_review` 可直接转为 `qualified` 或 `rejected`；
- `manual_review` 也可转为 `need_enrichment`，但不是必经路径；
- 补充资料完成后回到 `manual_review` 或转为其他终态；
- 只有当前任务中状态为 `qualified` 的用户能进入阶段 03；
- 阶段 03 生成的 Campaign 策略必须通过严格 Schema、长度和模板安全校验；阶段 04 发送前
  再次校验当前 Job、平台和 `qualified` 状态；
- 后续实现可增加 `deferred / blacklisted`，但不阻塞本阶段四状态交付。

## 6. Pipeline 业务界面

Pipeline 保留任务历史和六阶段进度，但阶段结果改为三层：

1. 任务总览：目标画像、预算、进度、异常、人工待办数；
2. 阶段看板：关键词表现、视频/评论覆盖、候选状态漏斗、筛选状态漏斗；
3. 记录工作台：账号资料、来源链、原始证据、双评分、多标签、缺失信息和人工动作。

原始 JSON 只保留在“技术诊断”折叠区，不再作为默认业务结果。

## 7. 分阶段验收

### Gate A：数据与状态基础

- 画像、关键词、证据、评估和审计可持久化；
- 旧数据库自动迁移且不丢数据；
- 四状态和人工直接通过/淘汰通过 API 测试；
- 阶段 03 查询严格只接受当前 Job 的 `qualified` 用户。

### Gate B：阶段 01 Agent

- Hermes 按预算执行视频优先、用户搜索辅助的发现策略；
- 证据链可追溯并按平台用户 ID 去重；
- 无证据或预算耗尽时进入待核验，不直接定义为客户。

### Gate C：阶段 02 与人工工作台

- 主页与代表内容按需补全；
- 匹配度、可信度、多标签和理由全部落库；
- 人工可直接通过、淘汰、补充资料和改标签；
- 操作审计完整，未确认用户不会进入后续阶段；
- 超过 200 个候选、100 个合格用户仍可分页完成，不会永久饥饿；
- 过期 AI 结果不能覆盖人工状态或当前双评分；累计提示超过 24,000 UTF-8 字节时失败关闭；
- 模型自由话术不直接落库或触达，Campaign 只发送项目内确定性中性模板。

### Gate D：业务化结果页

- 默认不显示原始 JSON；
- 每个指标能下钻到用户与证据；
- 空状态、失败、预算耗尽和人工待办均有明确展示；
- 桌面与移动端专项测试、后端回归和真实 API 页面检查通过。
