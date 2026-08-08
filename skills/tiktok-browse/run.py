"""
Skill: tiktok-browse
让 Hermes 调用 LLM 驱动账号浏览器（截图→LLM→动作 闭环）。
仅薄包装，不重复实现业务逻辑。
"""

import asyncio
import json

CLI = "python -m tiktok_bot_console.cli.main"


async def run():
    args = hermes.parsed_args or {}
    platform = args.get("platform", "douyin")
    account_id = int(args.get("account_id", 1))
    goal = args.get("goal") or hermes.user_prompt or ""
    if not goal:
        return "请提供目标，例如 goal='找一个批发商账号'"
    max_steps = int(args.get("max_steps", 10))

    proc = await asyncio.create_subprocess_exec(
        *CLI.split(),
        "browse", "run",
        "--platform", platform,
        "--account-id", str(account_id),
        "--goal", goal,
        "--max-steps", str(max_steps),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        return f"browse 失败 (exit={proc.returncode}): {stderr.decode()[-400:]}"
    payload = json.loads(stdout.decode())
    lines = [
        f"status={payload['status']}  steps={payload['steps']}",
        f"summary: {payload.get('summary') or '(empty)'}",
    ]
    for item in payload.get("trace", [])[:10]:
        lines.append(
            f"- step {item['step']} {item['action']}: {item['rationale'][:80]}"
        )
    return "\n".join(lines)