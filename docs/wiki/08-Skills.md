# 08 — Hermes Skills 设计

> 关联: [索引](00-索引.md) | [CLI-API-UI](06-CLI-API-UI.md)

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
