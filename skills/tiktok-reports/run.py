"""
Skill: tiktok-reports
查询日报和趋势
"""

import asyncio

CLI = "python -m tiktok_bot_console.cli.main"


async def run():
    proc = await asyncio.create_subprocess_exec(
        *CLI.split(), "report", "daily",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, _ = await proc.communicate()
    return f"日报:\n{stdout.decode()[:600]}"
