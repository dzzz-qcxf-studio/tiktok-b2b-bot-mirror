"""
Skill: tiktok-config
读取系统配置
"""

import asyncio

CLI = "python -m tiktok_bot_console.cli.main"


async def run():
    args = hermes.parsed_args or {}
    if "set" in (hermes.parsed_intent or "") and args.get("key"):
        proc = await asyncio.create_subprocess_exec(
            *CLI.split(), "config", "set", f"{args['key']}={args['value']}",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        return f"配置已更新:\n{stdout.decode()}"
    else:
        proc = await asyncio.create_subprocess_exec(
            *CLI.split(), "config", "list",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        return f"当前配置:\n{stdout.decode()[:600]}"
