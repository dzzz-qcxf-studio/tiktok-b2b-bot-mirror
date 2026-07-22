"""
Skill: tiktok-users
查询 TikTok B2B 用户列表（按状态、分类）
"""

import asyncio
import json

CLI = "python -m tiktok_bot_console.cli.main"


async def run():
    args = hermes.parsed_args or {}
    status = args.get("status", "qualified")
    limit = args.get("limit", 20)

    proc = await asyncio.create_subprocess_exec(
        *CLI.split(), "user", "list",
        "--status", status,
        "--limit", str(limit),
        "--format", "json",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, _ = await proc.communicate()
    data = json.loads(stdout.decode())
    count = len(data)

    lines = [f"找到 {count} 个 {status} 用户:"]
    for u in data[:10]:
        lines.append(f"- @{u['username']}: {u['bio'][:60] if u.get('bio') else 'N/A'}")
    return "\n".join(lines)
