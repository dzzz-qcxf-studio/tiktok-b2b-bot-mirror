"""
Skill: tiktok-pipeline
教导 Hermes 如何使用 tiktok-bot CLI 的 pipeline 命令。

原则: 不直接 import core，只通过 CLI 调用。
"""

import asyncio
import json
import subprocess

CLI = "python -m tiktok_bot_console.cli.main"


async def run_pipeline(stages: list[str] | None = None) -> dict:
    """启动 Pipeline"""
    args = [*CLI.split(), "pipeline", "run"]
    if stages:
        args += ["--stages", ",".join(stages)]
    else:
        args.append("--once")

    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    return {"returncode": proc.returncode, "stdout": stdout.decode(), "stderr": stderr.decode()}


async def check_status() -> dict:
    """查看 Pipeline 最近事件"""
    proc = await asyncio.create_subprocess_exec(
        *CLI.split(), "pipeline", "status",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, _ = await proc.communicate()
    return {"events": stdout.decode()}


async def run():
    """Skill 入口 — Hermes 调用"""
    # Hermes 通过自然语言解析出意图
    intent = hermes.parsed_intent or "status"

    if "run" in intent or "start" in intent or "exec" in intent:
        args = hermes.parsed_args or {}
        stages = args.get("stages")
        result = await run_pipeline(stages)
        return f"Pipeline 已启动:\n{result['stdout'][:500]}"
    else:
        result = await check_status()
        return f"Pipeline 最近事件:\n{result['events'][:500]}"
