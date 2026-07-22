"""TikTok B2B Bot — CLI 入口

用法:
  tiktok-bot user list --status qualified
  tiktok-bot pipeline run --once
  tiktok-bot report daily
  tiktok-bot config set keywords=wholesale,importer
"""

import asyncio
import json
import sys

import click
from rich.console import Console
from rich.table import Table

from tiktok_bot_core.settings import get_settings, reload_settings
from tiktok_bot_core.storage.database import get_db, init_db
from tiktok_bot_core.storage.sqlite_store import SqliteStore
from tiktok_bot_core.storage.vector_store import VectorStore

console = Console()


def format_date(d) -> str:
    return d.strftime("%Y-%m-%d") if d else "N/A"


# ===== CLI Group =====

@click.group()
@click.version_option("0.1.0", prog_name="tiktok-bot")
@click.pass_context
def cli(ctx):
    """TikTok B2B 业务拓展机器人 CLI"""
    ctx.ensure_object(dict)
    ctx.obj["db"] = get_db()
    ctx.obj["store"] = SqliteStore()
    ctx.obj["settings"] = get_settings()


# ===== User Commands =====

@cli.group()
def user():
    """用户管理"""
    pass


@user.command("list")
@click.option("--status", default=None, help="用户状态: pending/qualified/rejected/contacted/replied")
@click.option("--category", default=None, help="用户分类: buyer/distributor/manufacturer")
@click.option("--limit", default=50, help="返回数量上限")
@click.option("--format", "fmt", default="table", type=click.Choice(["table", "json"]))
@click.pass_context
def user_list(ctx, status, category, limit, fmt):
    """列出用户"""
    store = ctx.obj["store"]
    db = ctx.obj["db"]
    with db.session() as s:
        users = list(store.get_users(s, status=status, category=category, limit=limit))

    if fmt == "json":
        console.print(json.dumps(
            [{"id": u.id, "username": u.username, "status": u.status, "category": u.category, "bio": u.bio} for u in users],
            ensure_ascii=False, indent=2, default=str
        ))
        return

    table = Table(title=f"用户列表 (status={status or 'all'}, limit={limit})")
    table.add_column("ID", style="dim")
    table.add_column("Username")
    table.add_column("Status")
    table.add_column("Category")
    table.add_column("Bio")
    table.add_column("Followers")

    for u in users:
        table.add_row(str(u.id), f"@{u.username}", u.status, u.category, (u.bio or "")[:50], str(u.follower_count))
    console.print(table)
    console.print(f"[green]共 {len(users)} 条[/green]")


@user.command("show")
@click.argument("username")
@click.pass_context
def user_show(ctx, username):
    """查看单个用户"""
    store = ctx.obj["store"]
    db = ctx.obj["db"]
    with db.session() as s:
        users = list(store.get_users(s, limit=500))
        user = next((u for u in users if u.username == username.lstrip("@")), None)

    if not user:
        console.print(f"[red]未找到用户 @{username}[/red]")
        return

    console.print(f"[bold]@{user.username}[/bold]")
    console.print(f"  状态: {user.status}  分类: {user.category}")
    console.print(f"  粉丝: {user.follower_count}  关注: {user.following_count}")
    console.print(f"  Bio: {user.bio}")
    console.print(f"  来源: {user.source} → {user.source_keyword}")


@user.command("count")
@click.option("--status", default=None, help="用户状态")
@click.pass_context
def user_count(ctx, status):
    """统计用户数量"""
    store = ctx.obj["store"]
    db = ctx.obj["db"]
    with db.session() as s:
        total = store.count_users(s, status=status)
    console.print(f"[green]{status or 'total'}: {total} 个用户[/green]")


# ===== Pipeline Commands =====

@cli.group()
def pipeline():
    """Pipeline 管理"""
    pass


@pipeline.command("run")
@click.option("--stages", default=None, help="要运行的阶段，逗号分隔。例: collect,filter")
@click.option("--once", is_flag=True, help="运行全部阶段")
@click.pass_context
def pipeline_run(ctx, stages, once):
    """运行 Pipeline"""
    async def _run():
        from tiktok_bot_core.services.pipeline import PipelineService
        from tiktok_bot_core.extensions.registry import register as get_registry
        from tiktok_bot_core.plugins import register_default_plugins

        # 确保插件注册
        reg = get_registry()
        if not reg.list_plugins()["collectors"]:
            register_default_plugins(reg)

        stage_list = stages.split(",") if stages else []
        if once:
            stage_list = ["collect", "filter", "strategy", "outreach", "report"]

        if not stage_list:
            console.print("[yellow]请指定 --stages 或 --once[/yellow]")
            return

        service = PipelineService()
        console.print(f"[bold]Pipeline 启动: {stage_list}[/bold]")

        async for result in service.run(stages=stage_list):
            status_icon = "✅" if result["status"] == "ok" else "❌"
            console.print(f"  {status_icon} [{result['stage']}] {result['status']} — {result['result']}")

    asyncio.run(_run())


@pipeline.command("status")
@click.pass_context
def pipeline_status(ctx):
    """查看 Pipeline 状态（最近事件）"""
    from tiktok_bot_core.events.bus import get_event_bus
    bus = get_event_bus()
    events = bus.history(limit=10)
    if not events:
        console.print("[yellow]暂无 Pipeline 事件[/yellow]")
        return
    for e in events:
        console.print(f"  [{e.type.value}] {e.payload} — {e.timestamp.strftime('%H:%M:%S')}")


# ===== Strategy Commands =====

@cli.group()
def strategy():
    """策略管理"""
    pass


@strategy.command("list")
@click.option("--user-id", default=None, type=int, help="用户 ID")
@click.option("--limit", default=50)
@click.pass_context
def strategy_list(ctx, user_id, limit):
    """列出策略"""
    store = ctx.obj["store"]
    db = ctx.obj["db"]
    with db.session() as s:
        strategies = list(store.get_strategies(s, user_id=user_id))[:limit]

    table = Table(title="策略列表")
    table.add_column("ID")
    table.add_column("User ID")
    table.add_column("Persona")
    table.add_column("Type")
    table.add_column("Comment Preview")
    for s in strategies:
        table.add_row(str(s.id), str(s.user_id), s.persona, s.strategy_type, (s.comment_template or "")[:40])
    console.print(table)


# ===== Report Commands =====

@cli.group()
def report():
    """数据报告"""
    pass


@report.command("daily")
@click.pass_context
def report_daily(ctx):
    """今日日报"""
    store = ctx.obj["store"]
    db = ctx.obj["db"]
    from datetime import date
    with db.session() as s:
        reports = store.list_daily_reports(s, days=1)
    if not reports:
        console.print("[yellow]今日暂无日报[/yellow]")
        return
    r = reports[0]
    console.print(f"[bold]日报 — {r.report_date}[/bold]")
    console.print(f"  新增: {r.new_users_found}  合格: {r.users_qualified}  淘汰: {r.users_rejected}")
    console.print(f"  评论: {r.comments_sent}  私信: {r.dms_sent}")
    console.print(f"  回复: {r.replies_received}  回复率: {r.reply_rate:.1%}")
    console.print(f"  商业线索: {r.business_leads}")


# ===== Config Commands =====

@cli.group()
def config():
    """配置管理"""
    pass


@config.command("list")
@click.pass_context
def config_list(ctx):
    """列出配置"""
    store = ctx.obj["store"]
    db = ctx.obj["db"]
    with db.session() as s:
        cfgs = store.list_configs(s)
    if not cfgs:
        settings = get_settings()
        console.print(f"llm_provider = {settings.llm_provider}")
        console.print(f"llm_model = {settings.llm_model}")
        console.print(f"tiktok_keywords = {settings.tiktok_keywords}")
        console.print(f"daily_comment_limit = {settings.daily_comment_limit}")
        console.print(f"daily_dm_limit = {settings.daily_dm_limit}")
    else:
        for c in cfgs:
            console.print(f"  {c.key} = {c.value}")


@config.command("set")
@click.argument("key_value")
@click.pass_context
def config_set(ctx, key_value):
    """设置配置: key=value"""
    if "=" not in key_value:
        console.print("[red]格式错误，请使用 key=value[/red]")
        return
    key, value = key_value.split("=", 1)
    store = ctx.obj["store"]
    db = ctx.obj["db"]
    with db.session() as s:
        store.set_config(s, key, value)
    console.print(f"[green]已设置 {key} = {value}[/green]")


# ===== System Commands =====

@cli.command()
@click.pass_context
def status(ctx):
    """系统状态"""
    store = ctx.obj["store"]
    db = ctx.obj["db"]
    with db.session() as s:
        total = store.count_users(s)
        pending = store.count_users(s, "pending")
        qualified = store.count_users(s, "qualified")
        contacted = store.count_users(s, "contacted")
    try:
        vec = VectorStore()
        vstats = vec.stats()
    except Exception:
        vstats = {"error": "ChromaDB 未就绪"}

    console.print(f"[bold]System Status[/bold]")
    console.print(f"  Total Users: {total} (pending={pending} qualified={qualified} contacted={contacted})")
    console.print(f"  ChromaDB: {vstats}")


@cli.command()
def init():
    """初始化数据库"""
    db = init_db()
    console.print("[green]✅ 数据库初始化完成[/green]")


if __name__ == "__main__":
    cli()
