"""TikTok B2B Bot — FastAPI REST 服务器

启动: uvicorn tiktok_bot_api.main:app --reload
"""

import asyncio
import logging
import os
from datetime import date, datetime
from typing import Optional

from fastapi import FastAPI, Query, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel

from tiktok_bot_core.storage.database import get_db
from tiktok_bot_core.storage.sqlite_store import SqliteStore
from tiktok_bot_core.settings import get_settings, reload_settings
from tiktok_bot_core.events.bus import get_event_bus
from tiktok_bot_api.auth import (
    LoginRequest, RegisterRequest, TokenResponse,
    authenticate, authenticate_apikey, create_token, decode_token,
    get_current_user, require_user,
)
# 集中导入到顶部，避免每个 endpoint 都重复 inline import
from tiktok_bot_core.services.auth_service import get_auth_service

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="TikTok B2B Bot API",
    version="0.1.0",
    description="TikTok B2B 业务拓展机器人 REST API",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

db = get_db()
store = SqliteStore()
bus = get_event_bus()
settings = get_settings()


# ===== Pydantic Models =====

class PipelineRunRequest(BaseModel):
    stages: list[str] = []

class ConfigUpdateRequest(BaseModel):
    value: str
    description: str = ""

class UserResponse(BaseModel):
    id: int
    username: str
    status: str
    category: str
    bio: str = ""
    follower_count: int = 0
    created_at: Optional[datetime] = None


# ===== Health =====

@app.get("/")
async def root():
    return {"service": "TikTok B2B Bot API", "version": "0.1.0"}


@app.get("/api/health")
async def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


# ===== Auth =====

@app.post("/api/auth/login", response_model=TokenResponse)
async def login(req: LoginRequest):
    if req.method == "apikey":
        username = authenticate_apikey(req.password)
        if username:
            return {"access_token": create_token(username), "username": username}
        raise HTTPException(401, "API Key 无效")
    if authenticate(req.username, req.password):
        return {"access_token": create_token(req.username), "username": req.username}
    raise HTTPException(401, "用户名或密码错误")


@app.post("/api/auth/register")
async def register(req: RegisterRequest):
    db = get_db()
    from tiktok_bot_api.auth import hash_password
    with db.session() as s:
        existing = store.get_account(s, req.username)
        if existing:
            raise HTTPException(400, "用户名已存在")
        api_key = os.urandom(24).hex()
        store.create_account(s, req.username, hash_password(req.password), hash_password(api_key))
    return {"username": req.username, "api_key": api_key}


@app.get("/api/auth/me")
async def me(current_user: str = Depends(get_current_user)):
    if current_user == "guest":
        return {"username": "guest", "authenticated": False}
    return {"username": current_user, "authenticated": True}


# ===== Users =====

@app.get("/api/users")
async def list_users(
    status: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = Query(default=50, le=500),
    offset: int = 0,
):
    with db.session() as s:
        users = list(store.get_users(s, status=status, category=category, limit=limit, offset=offset))
        # 在 session 内取值，避免 DetachedInstanceError
        items = [
            {
                "id": u.id, "tiktok_id": u.tiktok_id, "username": u.username,
                "nickname": u.nickname, "bio": u.bio, "follower_count": u.follower_count,
                "country": u.country, "category": u.category, "status": u.status,
                "source": u.source, "source_keyword": u.source_keyword,
                "created_at": u.created_at.isoformat() if u.created_at else None,
            }
            for u in users
        ]
    return {"total": len(items), "items": items}


@app.get("/api/users/stats")
async def user_stats():
    with db.session() as s:
        return {
            "total": store.count_users(s),
            "pending": store.count_users(s, "pending"),
            "qualified": store.count_users(s, "qualified"),
            "contacted": store.count_users(s, "contacted"),
            "replied": store.count_users(s, "replied"),
        }


class AddUserRequest(BaseModel):
    username: str
    platform: str = "tiktok"
    bio: str = ""
    follower_count: int = 0
    country: str = ""
    category: str = "unknown"
    source: str = "manual"


@app.post("/api/users")
async def add_user(req: AddUserRequest):
    """手动添加用户到数据库"""
    with db.session() as s:
        user = store.add_user(
            s,
            platform=req.platform,
            tiktok_id=f"{req.platform}:{req.username}",
            username=req.username,
            bio=req.bio,
            follower_count=req.follower_count,
            country=req.country,
            category=req.category,
            status="pending",
            source=req.source,
            source_keyword="",
        )
        return {
            "id": user.id,
            "username": user.username,
            "platform": user.platform,
            "status": user.status,
        }


@app.get("/api/users/{user_id}")
async def get_user(user_id: int):
    with db.session() as s:
        user = store.get_user(s, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    return {
        "id": user.id, "username": user.username, "status": user.status,
        "category": user.category, "bio": user.bio,
        "follower_count": user.follower_count,
        "source": user.source, "created_at": user.created_at.isoformat() if user.created_at else None,
    }


@app.get("/api/users/{username}/detail")
async def get_user_detail(username: str):
    """用户详情页数据（供 UserDetail.vue 渲染画像/策略/时间线）"""
    with db.session() as s:
        from sqlalchemy import select as sa_select
        from tiktok_bot_core.models.entities import User, Strategy, Message, Reply
        user = s.execute(sa_select(User).where(User.username == username)).scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")
        # 取策略
        strategies = list(s.execute(sa_select(Strategy).where(Strategy.user_id == user.id)).scalars().all())
        # 取消息和回复
        messages = list(s.execute(sa_select(Message).where(Message.user_id == user.id)).scalars().all())
        replies = list(s.execute(sa_select(Reply).where(Reply.user_id == user.id)).scalars().all())

    strategy = strategies[0] if strategies else None
    reply_count = len(replies)
    reply_rate = reply_count / len(messages) if messages else 0

    return {
        "username": username,
        "profile": {
            "bio_zh": user.bio or f"@{username} — 暂无详细画像",
            "meta_zh": f"@{username} · {user.category} · {user.country or '未知区域'} · 粉丝 {user.follower_count:,}",
            "stats": {
                "followers": user.follower_count,
                "videos": user.video_count,
                "likes": user.like_count,
                "engagement_pct": round(reply_rate * 100, 1),
            },
        },
        "breakdown": [
            {"name": "Bio 商业关键词", "v": 72, "cls": "brand"},
            {"name": "视频内容相关", "v": 68, "cls": "brand"},
            {"name": "粉丝量级健康", "v": 76, "cls": "cyan"},
            {"name": "互动率真实", "v": 70, "cls": "cyan"},
            {"name": "地区匹配", "v": 80, "cls": "ok"},
            {"name": "更新频率", "v": 64, "cls": ""},
        ],
        "videos": [],
        "timeline": [
            {
                "time": m.created_at.strftime("%m-%d %H:%M") if m.created_at else "",
                "cls": "ok" if any(r.message_id == m.id and r.sentiment == "positive" for r in replies) else "",
                "who": "私信已发送" if m.message_type == "dm" else "评论已发送",
                "desc": m.content[:80] if m.content else "",
            }
            for m in messages[:10]
        ],
        "strategy": {
            "body": strategy.action_plan if strategy else "暂无策略",
            "window": "触达窗口 9:00 – 21:00",
            "gap": "评论→私信间隔 24h",
            "expected": f"期望回复率 {reply_rate:.0%}",
            "historical": "历史同画像 —",
        },
    }


# ===== Pipeline =====

@app.post("/api/pipeline/run")
async def run_pipeline(req: PipelineRunRequest):
    if not req.stages:
        return {"status": "error", "message": "需要指定 stages"}
    try:
        from tiktok_bot_core.services.pipeline import PipelineService
        service = PipelineService()
        results = []
        async for r in service.run(stages=req.stages):
            results.append(r)
        return {"status": "ok", "results": results}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/pipeline/events")
async def pipeline_events(limit: int = 50):
    events = bus.history(limit=limit)
    return [_format_event(e) for e in events]


def _format_event(e) -> dict:
    """将内部 Event 转换为前端 Pipeline.vue 期望的格式"""
    type_val = e.type.value if hasattr(e.type, "value") else str(e.type)
    payload = e.payload if isinstance(e.payload, dict) else {}
    level, message = _event_to_message(type_val, payload)
    return {
        "type": type_val,
        "level": level,
        "message": message,
        "payload": payload,
        "timestamp": e.timestamp.isoformat(),
    }


# 事件类型 → 中文动作映射
_ACTION_MAP = {"discovered": "发现", "qualified": "合格", "rejected": "淘汰", "contacted": "触达", "replied": "已回复"}


def _event_to_message(type_val: str, payload: dict) -> tuple[str, str]:
    """根据事件类型推断 level 和生成 human-readable message"""
    if "error" in type_val:
        stage = payload.get("stage", "")
        err = payload.get("error", "")
        msg = f"Pipeline 阶段 {stage} 失败: {err}" if stage else f"错误: {err}"
        return "err", msg

    if ".done" in type_val:
        stage = type_val.split(".")[0]
        parts = [f"环节 {stage} 完成"]
        for key, label in [("total", "项"), ("qualified", "合格"), ("saved", "保存")]:
            if key in payload:
                parts.append(f"{payload[key]} {label}")
        return "ok", " · ".join(parts)

    if "user." in type_val:
        uid = payload.get("user_id", "")
        via = payload.get("via", "")
        action = type_val.split(".")[-1]
        msg = f"用户 {uid} {_ACTION_MAP.get(action, action)}"
        if via:
            msg += f" ({via})"
        return "", msg

    if type_val == "pipeline.start":
        return "", f"Pipeline 启动 · {len(payload.get('stages', []))} 个环节"

    if type_val == "pipeline.end":
        return "ok", "Pipeline 全部完成"

    msg = f"{type_val} {json.dumps(payload, ensure_ascii=False)[:80]}" if payload else type_val
    return "", msg


@app.get("/api/pipeline/overview")
async def pipeline_overview():
    """Pipeline 总览（供 Pipeline 页面渲染 6 阶段卡片 + 最近 7 天运行 + 摘要）

    当无真实 Pipeline 运行数据时返回合理的默认值。
    """
    with db.session() as s:
        # 从数据库获取基础统计
        total_users = store.count_users(s)
        qualified = store.count_users(s, "qualified")
        contacted = store.count_users(s, "contacted")
        today = date.today()
        today_comments = store.count_messages(s, "comment", today)
        today_dms = store.count_messages(s, "dm", today)

    # 最近 7 天运行记录（从 event bus 推断，无事件则返回空）
    jobs = []
    events = bus.history(limit=200)
    if events:
        # 按日期分组事件，生成 job 行
        from collections import defaultdict
        day_events: dict[str, list] = defaultdict(list)
        for e in events:
            day = e.timestamp.strftime("%m-%d")
            day_events[day].append(e)
        for day in sorted(day_events.keys(), reverse=True)[:7]:
            evts = day_events[day]
            job_id = f"2026{day.replace('-', '')}-01"
            jobs.append({
                "date": day,
                "status": "完成",
                "statusCls": "ok",
                "detail": f"{len(evts)} 事件",
                "duration": "—",
                "jobId": job_id,
            })

    # 6 阶段卡片（默认 pending，无真实运行数据）
    stages = [
        {"index": 1, "key": "collect", "nameI18n": "pipeline.collect", "descI18n": "pipeline.collectDs", "ix": "01 / COLLECT", "status": "pending", "metric": str(total_users), "metricLabelI18n": "pipeline.usersStored", "extra": None, "time": "—"},
        {"index": 2, "key": "filter", "nameI18n": "pipeline.filter", "descI18n": "pipeline.filterDs", "ix": "02 / FILTER", "status": "pending", "metric": str(qualified), "metricLabelI18n": "pipeline.qualifiedCount", "extra": None, "time": "—"},
        {"index": 3, "key": "strategy", "nameI18n": "pipeline.strategy", "descI18n": "pipeline.strategyDs", "ix": "03 / STRATEGY", "status": "pending", "metric": str(qualified), "metricLabelI18n": "pipeline.strategyGenerated", "extra": None, "time": "—"},
        {"index": 4, "key": "outreach", "nameI18n": "pipeline.outreach", "descI18n": "pipeline.outreachDs", "ix": "04 / OUTREACH", "status": "pending", "metric": str(contacted), "metricLabelI18n": "pipeline.reached", "extra": None, "time": "—"},
        {"index": 5, "key": "report", "nameI18n": "pipeline.report", "descI18n": "pipeline.reportDs", "ix": "05 / REPORT", "status": "pending", "metric": "—", "metricLabelI18n": "pipeline.triggerAt", "extra": None, "time": "—"},
        {"index": 6, "key": "iterate", "nameI18n": "pipeline.iterate", "descI18n": "pipeline.iterateDs", "ix": "06 / ITERATE", "status": "pending", "metric": "—", "metricLabelI18n": "pipeline.weeklySun", "extra": None, "time": "—"},
    ]

    results = [
        {"stage": 1, "cls": "pending", "msg": "待运行"},
        {"stage": 2, "cls": "pending", "msg": "待运行"},
        {"stage": 3, "cls": "pending", "msg": "待运行"},
        {"stage": 4, "cls": "pending", "msg": "待运行"},
        {"stage": 5, "cls": "pending", "msg": "待运行"},
        {"stage": 6, "cls": "pending", "msg": "待运行"},
    ]

    summary = {
        "totalDuration": "—",
        "llmCalls": "0",
        "llmCost": "¥0",
        "browserOps": "0",
        "browserErrors": "0",
        "accountSwitches": "0",
        "commentsSent": str(today_comments),
        "dmsSent": str(today_dms),
    }

    return {"jobs": jobs, "results": results, "stages": stages, "summary": summary}


@app.get("/api/pipeline/events/stream")
async def pipeline_events_stream():
    """SSE 实时事件流"""
    async def event_stream():
        last_count = len(bus.history())
        while True:
            await asyncio.sleep(2)
            hist = bus.history()
            if len(hist) > last_count:
                for e in hist[last_count:]:
                    yield f"data: {e.type.value} {e.payload}\n\n"
                last_count = len(hist)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ===== Reports =====

@app.get("/api/reports/daily")
async def daily_report(d: Optional[str] = None):
    report_date = date.fromisoformat(d) if d else date.today()
    with db.session() as s:
        reports = store.list_daily_reports(s, days=180)
    r = next((r for r in reports if r.report_date == report_date), None)
    if not r:
        return {"date": str(report_date), "message": "暂无数据"}
    return {
        "date": str(r.report_date),
        "new_users_found": r.new_users_found,
        "users_qualified": r.users_qualified,
        "users_rejected": r.users_rejected,
        "comments_sent": r.comments_sent,
        "dms_sent": r.dms_sent,
        "replies_received": r.replies_received,
        "reply_rate": r.reply_rate,
        "positive_replies": r.positive_replies,
        "business_leads": r.business_leads,
    }


@app.get("/api/reports/trend")
async def trend_report(days: int = 30):
    with db.session() as s:
        reports = list(store.list_daily_reports(s, days=days))
    return [
        {
            "date": str(r.report_date),
            "new_users": r.new_users_found,
            "reply_rate": r.reply_rate,
            "comments": r.comments_sent,
            "dms": r.dms_sent,
            "replies": r.replies_received,
            "leads": r.business_leads,
        }
        for r in reports
    ]


@app.get("/api/reports/overview")
async def reports_overview():
    """报告页子面板（转化漏斗 + 地区分布 + 情感分布）"""
    with db.session() as s:
        total = store.count_users(s)
        qualified = store.count_users(s, "qualified")
        contacted = store.count_users(s, "contacted")
        replied = store.count_users(s, "replied")
        # 取最近 30 天报告做汇总
        reports = list(store.list_daily_reports(s, days=30))
        total_leads = sum(r.business_leads for r in reports)

    contacted_pct = round(contacted / total * 100) if total else 0
    replied_pct = round(replied / total * 100) if total else 0
    leads_pct = round(total_leads / total * 100) if total else 0

    return {
        "funnel": [
            {"label": "imported", "count": total, "pct": 100, "color": "oklch(14% 0.012 280)"},
            {"label": "qualified", "count": qualified, "pct": round(qualified / total * 100) if total else 0, "color": "oklch(70% 0.12 200)"},
            {"label": "contacted", "count": contacted, "pct": contacted_pct, "color": "oklch(58% 0.22 350)"},
            {"label": "replied", "count": replied, "pct": replied_pct, "color": "oklch(72% 0.16 75)"},
            {"label": "businessIntent", "count": total_leads, "pct": leads_pct, "color": "oklch(62% 0.16 150)"},
        ],
        "regions": [],
        "sentiment": {
            "positive": {"pct": 60, "count": 0, "color": "oklch(62% 0.16 150)", "dasharray": "234 390"},
            "neutral": {"pct": 28, "count": 0, "color": "oklch(60% 0.08 280)", "dasharray": "109 390", "dashoffset": -234},
            "negative": {"pct": 12, "count": 0, "color": "oklch(60% 0.22 25)", "dasharray": "47 390", "dashoffset": -343},
            "avgScore": 0.62,
        },
    }


# ===== LLM Config =====

@app.get("/api/llm/providers")
async def llm_providers():
    """LLM 提供商列表 + 使用统计 + 技能调用统计"""
    return {
        "providers": [
            {
                "name": "deepseek",
                "displayName": "DeepSeek",
                "initials": "DS",
                "model": settings.llm_model,
                "baseUrl": settings.llm_base_url,
                "url": "api.deepseek.com",
                "color": "linear-gradient(135deg, oklch(58% 0.22 350), oklch(70% 0.14 200))",
                "role": "main",
                "status": "active" if settings.llm_api_key else "unconfigured",
            },
        ],
        "usage": {
            "todayCalls": 0,
            "todayCost": 0,
            "monthCalls": 0,
            "monthCost": 0,
            "monthBudget": 500,
            "avgLatency": 0,
            "p95": "—",
            "tokenMillions": 0,
            "tokenIn": 0,
            "tokenOut": 0,
            "latency": "—",
            "successRate": "—",
            "apiKeyMasked": "***" + (settings.llm_api_key[-4:] if len(settings.llm_api_key) >= 4 else ""),
            "dayOverDay": 0,
        },
        "skills": [],
    }


# ===== Stats =====

@app.get("/api/stats/dashboard")
async def dashboard_stats():
    with db.session() as s:
        today = date.today()
        total = store.count_users(s)
        qualified = store.count_users(s, "qualified")
        today_new = store.count_users(s, since_date=today)
        today_comments = store.count_messages(s, "comment", today)
        today_dms = store.count_messages(s, "dm", today)
        today_replies = store.count_replies(s, since_date=today)
        today_leads = store.count_business_leads(s, today)
        sent = today_comments + today_dms
        reply_rate = today_replies / sent if sent else 0

        keyword_stats = store.get_keyword_effectiveness(s)[:10]
        category_stats = store.get_category_distribution(s)

    return {
        "overview": {
            "total_users": total,
            "qualified_users": qualified,
            "today_new": today_new,
            "today_comments": today_comments,
            "today_dms": today_dms,
            "today_replies": today_replies,
            "today_reply_rate": reply_rate,
            "today_leads": today_leads,
        },
        "keywords": keyword_stats,
        "categories": category_stats,
    }


# ===== Config =====

@app.get("/api/config")
async def list_config():
    with db.session() as s:
        cfgs = {c.key: c.value for c in store.list_configs(s)}
    result = {
        "llm_provider": cfgs.get("llm_provider", settings.llm_provider),
        "llm_model": cfgs.get("llm_model", settings.llm_model),
        "llm_api_key": "***" + (settings.llm_api_key[-4:] if len(settings.llm_api_key) >= 4 else ""),
        "has_api_key": bool(settings.llm_api_key),
        "tiktok_keywords": settings.tiktok_keywords,
        "daily_comment_limit": int(cfgs.get("daily_comment_limit", settings.daily_comment_limit)),
        "daily_dm_limit": int(cfgs.get("daily_dm_limit", settings.daily_dm_limit)),
    }
    return result


@app.put("/api/config/{key}")
async def update_config(key: str, req: ConfigUpdateRequest):
    with db.session() as s:
        store.set_config(s, key, req.value, req.description)
    return {"status": "ok", "key": key, "value": req.value}


class ApiKeyRequest(BaseModel):
    api_key: str

@app.post("/api/config/apikey")
async def update_api_key(req: ApiKeyRequest):
    import os
    env_path = __import__('pathlib').Path(__file__).resolve().parents[2] / ".env"
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if line.startswith("DEEPSEEK_API_KEY="):
                lines[i] = f"DEEPSEEK_API_KEY={req.api_key}"
                break
        else:
            lines.append(f"DEEPSEEK_API_KEY={req.api_key}")
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ["DEEPSEEK_API_KEY"] = req.api_key
    global _client; _client = None
    from tiktok_bot_core.settings import reload_settings; reload_settings()
    return {"status": "ok", "key": "llm_api_key"}


# ===== Social Accounts (TikTok + 抖音) =====

class SocialAccountRequest(BaseModel):
    """添加社交账号请求（支持双平台）"""
    platform: str = "tiktok"  # "tiktok" / "douyin"
    username: str
    cookies_json: str = ""


class LoginQRCodeRequest(BaseModel):
    """启动 QR 扫码登录请求"""
    platform: str = "tiktok"
    username: str  # 自定义账号标识


@app.get("/api/accounts")
async def list_accounts(platform: Optional[str] = None):
    """列出账号（可按平台过滤）"""
    from tiktok_bot_core.services.auth_service import get_auth_service
    # list_accounts 已返回 dict 列表，直接返回
    return get_auth_service().list_accounts(platform=platform)


@app.post("/api/accounts")
async def add_account(req: SocialAccountRequest):
    """仅添加账号元信息（不实际登录）"""
    try:
        return get_auth_service().add_account(req.platform, req.username)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.put("/api/accounts/{aid}/cookies")
async def update_account_cookies(aid: int, req: SocialAccountRequest):
    """手动更新账号 cookies"""
    with db.session() as s:
        store.update_tiktok_cookies(s, aid, req.cookies_json)
    return {"status": "ok"}


@app.delete("/api/accounts/{aid}")
async def remove_account(aid: int):
    """删除账号"""
    from tiktok_bot_core.services.auth_service import get_auth_service
    get_auth_service().delete_account(aid)
    return {"status": "ok"}


@app.post("/api/accounts/login-qrcode")
async def start_login_qrcode(req: LoginQRCodeRequest):
    """启动 QR 码登录（双平台）

    返回 session token，前端轮询 `/api/accounts/login-status?token=xxx` 获取二维码截图路径与登录状态。
    """
    from tiktok_bot_core.services.auth_service import get_auth_service
    from tiktok_bot_core.platforms import PlatformType

    try:
        PlatformType.parse(req.platform)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    svc = get_auth_service()
    token = await svc.start_qrcode_login(req.platform, req.username)
    return {
        "session_token": token,
        "platform": req.platform,
        "username": req.username,
        "message": "请通过 /api/accounts/login-status?token=xxx 轮询状态并获取二维码",
    }


@app.get("/api/accounts/login-status")
async def login_status(token: str = Query(...)):
    """查询 QR 登录状态"""
    from tiktok_bot_core.services.auth_service import get_auth_service
    svc = get_auth_service()
    status = svc.check_login(token)
    qrcode_path = svc.get_qrcode_path(token)

    return {
        **status,
        "qrcode_url": f"/api/accounts/qrcode/{token}" if qrcode_path else None,
        "qrcode_path": qrcode_path,
    }


@app.get("/api/accounts/qrcode/{token}")
async def get_qrcode_image(token: str):
    """返回二维码截图（PNG）"""
    from fastapi.responses import FileResponse
    from tiktok_bot_core.services.auth_service import get_auth_service

    qrcode_path = get_auth_service().get_qrcode_path(token)
    if not qrcode_path or not os.path.exists(qrcode_path):
        raise HTTPException(status_code=404, detail="二维码未生成或已过期")
    return FileResponse(qrcode_path, media_type="image/png")


@app.post("/api/accounts/{aid}/check-session")
async def check_account_session(aid: int):
    """检测账号 cookie 是否还有效"""
    from tiktok_bot_core.services.auth_service import get_auth_service
    with db.session() as s:
        acc = store.get_tiktok_account(s, aid)
        if not acc:
            raise HTTPException(status_code=404, detail="账号不存在")
        valid = await get_auth_service().check_session_valid(acc.platform, acc.username)
        # 更新状态
        new_status = "logged_in" if valid else "expired"
        store.update_account_status(s, aid, new_status)
    return {"id": aid, "status": new_status, "valid": valid}


# ===== Stats/Charts =====

@app.get("/api/stats/wordcloud")
async def wordcloud_data():
    with db.session() as s:
        kw_stats = store.get_keyword_effectiveness(s)
    return [{"name": r["keyword"], "value": r["total"]} for r in kw_stats]


# ===== Lead Discovery =====

@app.get("/api/leads/search")
async def search_leads(keyword: str = Query(..., min_length=1), limit: int = Query(default=20, le=100)):
    """公开搜索潜在 B2B 客户（不需登录）

    先从数据库中模糊匹配 bio/username 包含关键词的用户，
    按粉丝数降序返回。若数据库无匹配，返回空列表。
    """
    with db.session() as s:
        from sqlalchemy import or_, select as sa_select
        from tiktok_bot_core.models.entities import User
        users = list(
            s.execute(
                sa_select(User).where(
                    or_(
                        User.bio.ilike(f"%{keyword}%"),
                        User.username.ilike(f"%{keyword}%"),
                        User.nickname.ilike(f"%{keyword}%"),
                        User.source_keyword.ilike(f"%{keyword}%"),
                    )
                )
                .order_by(User.follower_count.desc())
                .limit(limit)
            ).scalars().all()
        )

        # 在 session 内取值，避免 DetachedInstanceError
        results = []
        for i, u in enumerate(users, 1):
            score = min(99, 50 + len(u.bio or "") // 5 + (u.follower_count // 10000))
            results.append({
                "id": u.id,
                "username": u.username,
                "nickname": u.nickname or f"@{u.username}",
                "bio": u.bio or "",
                "avatar_initials": u.username[:2].upper(),
                "follower_count": u.follower_count,
                "video_count": u.video_count,
                "country": u.country or "",
                "relevance_score": score,
                "matched_keyword": keyword,
                "url": f"https://www.tiktok.com/@{u.username}",
            })

    return results
