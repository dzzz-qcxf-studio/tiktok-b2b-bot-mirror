"""Pipeline 服务 — 编排 6 个环节

被 CLI/API/UI 三层共享调用。

借鉴 ChopperBot 的编排（chopperbot-section-work 组合多个插件），
但加入：
- 事件总线（阶段完成发布事件，可被 UI 实时拉取）
- 错误隔离（单阶段失败不影响其他）
- 进度回报（yield 阶段结果，UI 可流式展示）
"""

import asyncio
import logging
from datetime import date, datetime
from typing import AsyncIterator

from tiktok_bot_core.events.bus import Event, EventType, get_event_bus
from tiktok_bot_core.extensions.registry import register as get_registry
from tiktok_bot_core.models.entities import (
    User, Strategy, Message, Reply, DailyReport,
)
from tiktok_bot_core.plugins import register_default_plugins
from tiktok_bot_core.settings import get_settings
from tiktok_bot_core.storage.database import get_db
from tiktok_bot_core.storage.sqlite_store import SqliteStore
from tiktok_bot_core.storage.vector_store import VectorStore

logger = logging.getLogger(__name__)


def _ensure_registered():
    """确保默认插件已注册"""
    reg = get_registry()
    if not reg.list_plugins()["collectors"]:
        register_default_plugins(reg)


class PipelineService:
    """Pipeline 编排服务"""

    def __init__(self):
        _ensure_registered()
        self.bus = get_event_bus()
        self.settings = get_settings()
        self.db = get_db()
        self.store = SqliteStore()
        self.vector = VectorStore()

    async def run(
        self,
        stages: list[str] | None = None,
        collection_config: dict | None = None,
        strategy_config: dict | None = None,
        outreach_config: dict | None = None,
    ) -> AsyncIterator[dict]:
        """运行 Pipeline，yield 每个阶段的结果

        Args:
            stages: 要运行的阶段列表（默认全部）
            collection_config: 搜集阶段参数
            strategy_config: 策略阶段参数
            outreach_config: 触达阶段参数

        Yields:
            {"stage": "collect", "result": {...}, "status": "ok" | "error"}
        """
        stages = stages if stages is not None else self.settings.pipeline_stages
        collection_config = collection_config or {"keywords": self.settings.tiktok_keywords, "max_per_keyword": 20}

        await self.bus.publish(Event(EventType.PIPELINE_START, {"stages": stages}, source="pipeline"))

        stage_runners = {
            "collect": self._run_collect,
            "filter": self._run_filter,
            "strategy": self._run_strategy,
            "outreach": self._run_outreach,
            "report": self._run_report,
            "iterate": self._run_iterate,
        }

        for stage in stages:
            runner = stage_runners.get(stage)
            if not runner:
                yield {"stage": stage, "status": "error", "result": {"error": f"未知阶段: {stage}"}}
                continue

            try:
                logger.info(f"=== Pipeline 阶段 [{stage}] 开始 ===")
                result = await runner(collection_config, strategy_config, outreach_config)
                yield {"stage": stage, "status": "ok", "result": result}
                await self.bus.publish(
                    Event(getattr(EventType, f"{stage.upper()}_DONE"), result, source="pipeline")
                )
            except Exception as e:
                logger.error(f"Pipeline 阶段 [{stage}] 失败: {e}", exc_info=True)
                yield {"stage": stage, "status": "error", "result": {"error": str(e)}}
                await self.bus.publish(
                    Event(EventType.ERROR_OCCURRED, {"stage": stage, "error": str(e)}, source="pipeline")
                )

        await self.bus.publish(Event(EventType.PIPELINE_END, {}, source="pipeline"))
        logger.info("=== Pipeline 全部完成 ===")

    # ===== 阶段 1: 用户搜集 =====

    async def _run_collect(self, cfg, _, __) -> dict:
        """用户搜集（双平台）"""
        reg = get_registry()
        keyword_collector = reg.get_collector("keyword")

        if not keyword_collector:
            return {"error": "未注册 keyword collector"}

        # 确保 cfg 带 platform（默认 tiktok）
        platform_name = cfg.get("platform", "tiktok")
        cfg = {**cfg, "platform": platform_name}

        raw_users = await keyword_collector.collect(cfg)

        # 强制给每条记录加 platform（兜底）
        for u in raw_users:
            u.setdefault("platform", platform_name)

        # 入库
        saved = 0
        with self.db.session() as s:
            for u in raw_users:
                try:
                    self.store.add_user(s, **u)
                    saved += 1
                except Exception as e:
                    logger.warning(f"保存用户 {u.get('tiktok_id')} 失败: {e}")

        # 向量化入库
        for u in raw_users[:50]:
            try:
                self.vector.add_user_profile(
                    user_id=f"user_{u['tiktok_id']}",
                    document=f"{u.get('bio', '')} {u['username']}",
                    metadata={"username": u["username"], "source": u.get("source", "")},
                )
            except Exception:
                pass

        return {"total": len(raw_users), "saved": saved}

    # ===== 阶段 2: 用户筛选 =====

    async def _run_filter(self, _, __, ___) -> dict:
        """用户筛选：先关键词预筛，再 LLM 精筛"""
        with self.db.session() as s:
            pending_users = [dict(uid=u.id, username=u.username, nickname=u.nickname, bio=u.bio,
                                  follower_count=u.follower_count, category=u.category)
                             for u in self.store.get_users(s, status="pending", limit=200)]

        if not pending_users:
            return {"total": 0, "qualified": 0, "rejected": 0}

        from tiktok_bot_core.plugins.filters.composite_filter import CompositeFilter, KeywordPreFilter
        from tiktok_bot_core.plugins.filters.llm_filter import LLMFilter
        from tiktok_bot_core.models.entities import User as UserEntity

        pre = KeywordPreFilter()
        llm = LLMFilter()

        qualified = 0
        rejected = 0

        for u in pending_users:
            user = UserEntity(**{k: v for k, v in u.items() if k in UserEntity.__table__.columns.keys()})
            # 1. 预筛
            pre_result = await pre.evaluate(user, {})
            if not pre_result["is_potential"]:
                with self.db.session() as s:
                    self.store.update_user_status(s, u["uid"], "rejected", "irrelevant")
                rejected += 1
                continue

            # 2. LLM 精筛
            llm_result = await llm.evaluate(user, {})
            category = llm_result.get("category", "unknown")
            if llm_result["is_potential"]:
                with self.db.session() as s:
                    self.store.update_user_status(s, u["uid"], "qualified", category)
                qualified += 1
            else:
                with self.db.session() as s:
                    self.store.update_user_status(s, u["uid"], "rejected", category)
                rejected += 1

        return {"total": len(pending_users), "qualified": qualified, "rejected": rejected}

    # ===== 阶段 3: 策略制定 =====

    async def _run_strategy(self, _, strategy_cfg, ___) -> dict:
        """为每个 qualified 用户生成触达策略"""
        strategy_cfg = strategy_cfg or {}

        with self.db.session() as s:
            qualified_users = list(self.store.get_users(s, status="qualified", limit=100))

        if not qualified_users:
            return {"total": 0, "strategies": 0}

        from tiktok_bot_core.llm.client import get_llm_client
        llm = get_llm_client()

        strategy_count = 0
        for user in qualified_users:
            try:
                prompt = f"""为以下 TikTok B2B 用户生成个性化触达策略。
用户：@{user.username}, bio={user.bio or 'N/A'}, category={user.category or 'unknown'}

返回 JSON：
{{
    "persona": "buyer/distributor/manufacturer/competitor",
    "strategy_type": "soft_sell/hard_sell/partnership",
    "comment_template": "评论话术（英文，50字内）",
    "dm_template": "私信话术（英文，100字内）",
    "priority": 1-5,
    "action_plan": "执行计划"
}}"""
                result = await llm.json_completion(prompt)
                with self.db.session() as s:
                    self.store.add_strategy(
                        s,
                        user_id=user.id,
                        persona=result.get("persona", user.category or "unknown"),
                        strategy_type=result.get("strategy_type", "soft_sell"),
                        comment_template=result.get("comment_template", ""),
                        dm_template=result.get("dm_template", ""),
                        action_plan=result.get("action_plan", ""),
                        priority=result.get("priority", 3),
                    )
                    strategy_count += 1
            except Exception as e:
                logger.warning(f"@{user.username} 策略生成失败: {e}")

        return {"total": len(qualified_users), "strategies": strategy_count}

    # ===== 阶段 4: 执行触达 =====

    async def _run_outreach(self, _, __, outreach_cfg) -> dict:
        """执行评论/私信"""
        outreach_cfg = outreach_cfg or {}
        comment_limit = outreach_cfg.get("comment_limit", self.settings.daily_comment_limit)
        dm_limit = outreach_cfg.get("dm_limit", self.settings.daily_dm_limit)

        reg = get_registry()
        comment_ch = reg.get_channel("comment")
        dm_ch = reg.get_channel("dm")

        with self.db.session() as s:
            from sqlalchemy import select
            from tiktok_bot_core.models.entities import User as UserModel, Strategy as StrategyModel

            stmt = (
                select(StrategyModel, UserModel)
                .join(UserModel, StrategyModel.user_id == UserModel.id)
                .where(UserModel.status.in_(["qualified", "contacted"]))
                .order_by(StrategyModel.priority.asc())
                .limit(comment_limit + dm_limit)
            )
            rows = s.execute(stmt).all()

        comment_sent = 0
        dm_sent = 0

        for strategy, user in rows:
            # 评论
            if comment_sent < comment_limit:
                success = await comment_ch.execute(
                    target=user.username,
                    content=strategy.comment_template,
                    config={},
                )
                with self.db.session() as s:
                    self.store.add_message(
                        s,
                        user_id=user.id,
                        message_type="comment",
                        content=strategy.comment_template,
                        status="sent" if success else "failed",
                        sent_at=datetime.utcnow() if success else None,
                    )
                    if success:
                        self.store.update_user_status(s, user.id, "contacted")
                if success:
                    comment_sent += 1
                    await self.bus.publish(
                        Event(EventType.USER_CONTACTED, {"user_id": user.id, "via": "comment"})
                    )

            # 私信
            if user.status == "contacted" and dm_sent < dm_limit:
                success = await dm_ch.execute(
                    target=user.username,
                    content=strategy.dm_template,
                    config={},
                )
                with self.db.session() as s:
                    self.store.add_message(
                        s,
                        user_id=user.id,
                        message_type="dm",
                        content=strategy.dm_template,
                        status="sent" if success else "failed",
                        sent_at=datetime.utcnow() if success else None,
                    )
                if success:
                    dm_sent += 1

        return {"comments_sent": comment_sent, "dms_sent": dm_sent}

    # ===== 阶段 5: 数据汇总 =====

    async def _run_report(self, *_):
        """生成日报"""
        today = date.today()

        with self.db.session() as s:
            new_users = self.store.count_users(s, since_date=today)
            qualified = self.store.count_users(s, status="qualified", since_date=today)
            rejected = new_users - qualified
            comments = self.store.count_messages(s, "comment", today)
            dms = self.store.count_messages(s, "dm", today)
            replies = self.store.count_replies(s, since_date=today)
            positive = self.store.count_replies(s, "positive", today)
            leads = self.store.count_business_leads(s, today)
            sent_total = comments + dms
            reply_rate = replies / sent_total if sent_total > 0 else 0.0

            self.store.upsert_daily_report(
                s,
                report_date=today,
                new_users_found=new_users,
                users_qualified=qualified,
                users_rejected=rejected,
                comments_sent=comments,
                dms_sent=dms,
                replies_received=replies,
                reply_rate=reply_rate,
                positive_replies=positive,
                business_leads=leads,
            )

        # 推送到 Telegram（如果有配置）
        if self.settings.telegram_bot_token:
            try:
                await self._send_telegram_report(today, new_users, qualified, comments, dms, replies, reply_rate)
            except Exception as e:
                logger.warning(f"Telegram 推送失败: {e}")

        return {
            "date": str(today),
            "new_users": new_users,
            "qualified": qualified,
            "comments": comments,
            "dms": dms,
            "replies": replies,
            "reply_rate": reply_rate,
        }

    async def _send_telegram_report(self, today, new, qualified, comments, dms, replies, rate):
        """推送到 Telegram"""
        from telegram import Bot
        bot = Bot(token=self.settings.telegram_bot_token)
        text = (
            f"📊 *TikTok Bot 日报* — {today}\n\n"
            f"👥 新增用户: {new}\n"
            f"✅ 合格: {qualified}\n"
            f"💬 评论发送: {comments}\n"
            f"📩 私信发送: {dms}\n"
            f"📥 收到回复: {replies}\n"
            f"📈 回复率: {rate:.1%}"
        )
        await bot.send_message(chat_id=self.settings.telegram_chat_id, text=text, parse_mode="Markdown")

    # ===== 阶段 6: 闭环迭代 =====

    async def _run_iterate(self, *_):
        """经验沉淀 + 规则更新"""
        with self.db.session() as s:
            keyword_stats = self.store.get_keyword_effectiveness(s)
            category_stats = self.store.get_category_distribution(s)

        from tiktok_bot_core.llm.client import get_llm_client
        from datetime import date as date_cls

        llm = get_llm_client()
        import json
        prompt = f"""根据 TikTok B2B 营销数据，提取经验并给出优化建议。

关键词效果：
{json.dumps(keyword_stats, ensure_ascii=False, indent=2)}

用户分类分布：
{json.dumps(category_stats, ensure_ascii=False, indent=2)}

请返回 JSON：
{{
    "top_keywords": ["效果最好的3个关键词"],
    "drop_keywords": ["建议放弃的"],
    "strategy_suggestions": ["3条优化建议"],
    "summary": "本周总结"
}}"""
        try:
            analysis = await llm.json_completion(prompt)
        except Exception as e:
            return {"error": f"分析失败: {e}", "keyword_stats": keyword_stats}

        # 沉淀到 ChromaDB
        exp_text = analysis.get("summary", "") + "\n" + "\n".join(analysis.get("strategy_suggestions", []))
        try:
            self.vector.add_experience(
                exp_id=f"exp_{date_cls.today()}",
                document=exp_text,
                metadata={"date": str(date_cls.today()), "type": "weekly"},
            )
        except Exception as e:
            logger.warning(f"经验入库失败: {e}")

        # 保存到 SQLite
        with self.db.session() as s:
            self.store.add_rule(
                s,
                rule_type="weekly_optimization",
                rule_content=json.dumps(analysis, ensure_ascii=False),
                effectiveness=0.0,
                sample_size=0,
            )

        return analysis
