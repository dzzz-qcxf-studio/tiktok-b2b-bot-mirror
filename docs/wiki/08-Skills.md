# 08 — Hermes Skills 设计

> 关联: [索引](00-索引.md) | [CLI-API-UI](06-CLI-API-UI.md)
> 最后更新: 2026-08-02

## 核心理念

**Skills 不重复实现业务逻辑 — 只调 CLI。**

```text
Hermes (AI)                      软件本体
   │                                │
   ├─ Skill "tiktok-users" ──CLI──▶│ tiktok-bot user list
   ├─ Skill "tiktok-strategy"──CLI─▶│ tiktok-bot strategy list
   └─ Skill "tiktok-report" ──CLI─▶│ tiktok-bot report daily
```

## 为什么 Skill → CLI 而不是 Skill → Core？

| 维度 | Skill → Core | Skill → CLI |
| --- | --- | --- |
| 权限 | 需要数据库密码 | 走 CLI 安全校验 |
| 日志 | 分散在 Skill | 统一在软件层 |
| 重用 | AI 和人各自实现 | CLI = 单一事实源 |
| 审计 | 难追踪 | CLI 调用即审计 |
| 升级 | Skill 需同步更新 | 软件升级自动惠及 |

## Skill 文件结构

```text
skills/
├── tiktok-pipeline/
│   ├── manifest.yaml         # "启动/停止/查看 Pipeline"
│   └── run.py                # hermes.cli.run("tiktok-bot", "pipeline", "run")
│
├── tiktok-users/
│   ├── manifest.yaml         # "查询/筛选/导出用户"
│   └── run.py                # hermes.cli.run("tiktok-bot", "user", "list")
│
├── tiktok-reports/
│   ├── manifest.yaml         # "查看日报/趋势"
│   └── run.py                # hermes.cli.run("tiktok-bot", "report", "daily")
│
├── tiktok-browse/
│   ├── manifest.yaml         # "驱动账号浏览器让 LLM 决定每一步动作"
│   └── run.py                # hermes.cli.run("tiktok-bot", "browse", "run", ...)
│
└── tiktok-config/
    ├── manifest.yaml         # "读取/修改配置"
    └── run.py                # hermes.cli.run("tiktok-bot", "config", "list")
```

## Skill 薄包装示例

```python
# skills/tiktok-users/run.py
import json

async def run():
    """Skill 入口 — Hermes 调用此函数"""
    args = hermes.parse_args("status", "category", "limit")

    result = await hermes.cli.run(
        "tiktok-bot", "user", "list",
        "--status", args.status or "qualified",
        "--limit", str(args.limit or 20),
        "--format", "json"
    )

    users = json.loads(result.stdout)
    count = len(users)
    summary = "\n".join(
        f"- @{u['username']}: {u['bio'][:50]}" for u in users[:10]
    )
    return f"找到 {count} 个用户:\n{summary}"
```

## manifest.yaml 示例

```yaml
name: tiktok-users
version: "1.0.0"
description: "查询/筛选/导出 TikTok B2B 用户"
triggers:
  - "查询用户"
  - "用户列表"
  - "list users"
tools_required:
  - cli
```

## Skill → CLI → Core 的扩展能力：`tiktok-browse`

标准 Skills 是只读薄包装（user list / strategy list / report daily）。`tiktok-browse`
是**第一个允许 AI 改变系统状态**的 Skill：它驱动账号浏览器让 LLM 决定每一步
点击、滚动、提取。仍然走 Skill → CLI → Core 三层，只是 Core 这次不再是
只读查询，而是「截图哈希 + 受限结构化 DOM → LLM → 动作」的闭环：

```
Hermes → tiktok-browse Skill
   └─ CLI: tiktok-bot browse run --platform douyin --account-id 1 --goal "..."
        └─ Core: BrowseAgent.run()
              ├─ 截图哈希 + 最多 100 个交互/链接节点的安全 DOM snapshot → iteration LLM
              ├─ LLM 输出受限动作集合（navigate / click / scroll / wait / extract / done）
              ├─ _validate_action 拒绝不安全 URL 与 file:// / javascript:
              └─ 每步 BROWSE_STEP 事件进 history；BROWSE_DONE 触发订阅者
```

安全契约：
- 走 `iteration` 路由，不污染 `default / collection / qualification`，熔断与 fallback 由 Router 统一保障
- 动作名是固定 frozenset，LLM 不可扩展
- `navigate` 只允许 http(s)，host 必须落在 `douyin.com / tiktok.com / iesdouyin.com`
- `click` 必须给非空 CSS selector；不允许执行任意 JS
- 初始 URL、动作前后 URL 和 DOM href 都按当前平台校验；外域跳转 fail closed
- wait 限 50..10000 ms、scroll 限 1..3000 px；click 在页预算已满时不会先执行
- 页数、精确秒级 deadline、LLM 次数和阶段 01 多层证据预算都是硬上限；异步
  Browser factory、浏览器动作、LLM、关闭和完成事件均有超时边界
- `extract.payload.observation` 必须通过 `EvidenceObservation` Schema；逐用户记录截断原因
- 单步不安全决策记为 `invalid step` 继续循环，不让 LLM 一次坏决策毁掉整次运行
- 浏览器在 done / timeout / 错误时都被 `_safe_close` 关闭
