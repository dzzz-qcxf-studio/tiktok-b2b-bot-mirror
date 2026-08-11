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
import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, AsyncIterator

import httpx
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from tiktok_bot_core.browser.providers import BrowserSession
from tiktok_bot_core.events.bus import Event, EventType, get_event_bus
from tiktok_bot_core.extensions.registry import register as get_registry
from tiktok_bot_core.plugins import register_default_plugins
from tiktok_bot_core.settings import get_settings
from tiktok_bot_core.services.acquisition_agents import (
    CampaignStrategyAgent,
    CandidateObservation,
    DiscoveryPlannerAgent,
    EnrichmentAgent,
    ExplorationBudget,
    HermesEvidenceAgent,
    QualificationAgent,
    render_safe_campaign_strategy,
    StrategyResult,
    Stage01CandidateAgent,
)
from tiktok_bot_core.llm.client import get_llm_client
from tiktok_bot_core.models.entities import DiscoveryEvidence, PipelineJobUser
from tiktok_bot_core.storage.acquisition_store import AcquisitionStore
from tiktok_bot_core.storage.database import get_db
from tiktok_bot_core.storage.pipeline_job_store import PipelineJobStore
from tiktok_bot_core.storage.sqlite_store import SqliteStore
from tiktok_bot_core.storage.vector_store import VectorStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PipelineRunContext:
    """由统一 Job Runner 注入、贯穿所有 Pipeline 阶段的执行上下文。"""

    job_id: str
    platform: str
    account_id: int
    account_username: str
    browser_session: Any
    event_recorder: Any | None = None

    def plugin_config(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "account_id": self.account_id,
            "account": self.account_username,
            "browser_session": self.browser_session,
            "job_id": self.job_id,
        }


def _ensure_registered():
    """确保默认插件已注册"""
    reg = get_registry()
    if not reg.list_plugins()["collectors"]:
        register_default_plugins(reg)


_STABLE_STAGE_ERROR_CODES = frozenset(
    {
        "network",
        "timeout",
        "upstream_server",
        "account_blocked",
        "authentication_required",
        "captcha_required",
        "login_required",
        "risk_control",
    }
)


def _stable_stage_error_code(error: Exception) -> str:
    """Return only an explicit, registered category; never parse error text."""

    if isinstance(
        error,
        (
            TimeoutError,
            asyncio.TimeoutError,
            httpx.TimeoutException,
            PlaywrightTimeoutError,
        ),
    ):
        return "timeout"
    if isinstance(error, (ConnectionError, httpx.NetworkError)):
        return "network"
    for attribute in ("error_category", "category", "code"):
        value = getattr(error, attribute, "")
        if isinstance(value, str) and value in _STABLE_STAGE_ERROR_CODES:
            return value
    return ""


def validate_persisted_campaign_strategy(
    *,
    persona: Any,
    strategy_type: Any,
    comment_template: Any,
    dm_template: Any,
    priority: Any,
    action_plan: Any,
) -> StrategyResult:
    """Apply the exact strict validation used immediately before outreach."""

    strategy = StrategyResult.model_validate(
        {
            "schema_version": "1.0",
            "persona": persona,
            "strategy_type": strategy_type,
            "comment_template": comment_template,
            "dm_template": dm_template,
            "priority": priority,
            "action_plan": action_plan,
        }
    )
    return render_safe_campaign_strategy(strategy)


def _collection_decision_summary(
    campaign_budget: dict[str, Any],
    collection_metrics: dict[str, Any],
) -> dict[str, Any]:
    """Project authoritative collector totals into a safe decision summary."""

    totals = dict(collection_metrics.get("totals") or {})

    def number(name: str) -> float:
        value = totals.get(name, 0)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return 0.0
        if not math.isfinite(float(value)) or value < 0:
            return 0.0
        return float(value)

    max_pages = max(0, int(campaign_budget.get("maxPages", 0) or 0))
    max_llm_calls = max(
        0, int(campaign_budget.get("maxLlmCalls", 0) or 0)
    )
    max_duration_seconds = max(
        0.0,
        float(campaign_budget.get("maxDurationMinutes", 0) or 0) * 60,
    )
    remaining = {
        "pages": max(0, max_pages - int(number("pages"))),
        "llmCalls": max(
            0,
            max_llm_calls - int(number("llm_calls")),
        ),
        "durationSeconds": max(
            0.0,
            max_duration_seconds - number("duration_seconds"),
        ),
    }
    reasons: set[str] = set()
    metrics = collection_metrics.get("keywords")
    if isinstance(metrics, dict):
        for metric in metrics.values():
            if not isinstance(metric, dict):
                continue
            values = metric.get("truncation_reasons")
            if isinstance(values, (list, tuple, set, frozenset)):
                reasons.update(
                    value.strip()
                    for value in values
                    if isinstance(value, str) and value.strip()
                )
            exhaustion = metric.get("exhaustion_reason")
            if isinstance(exhaustion, str) and exhaustion.strip():
                reasons.add(exhaustion.strip())
    return {
        "remaining_budget": remaining,
        "truncation_reasons": sorted(reasons),
    }


class PipelineService:
    """Pipeline 编排服务"""

    def __init__(self):
        _ensure_registered()
        self.bus = get_event_bus()
        self.settings = get_settings()
        self.db = get_db()
        self.store = SqliteStore()
        self.job_store = PipelineJobStore()
        self.vector = VectorStore()

    async def run(
        self,
        context: PipelineRunContext,
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
            context: 统一任务执行上下文，生产执行必填

        Yields:
            {"stage": "collect", "result": {...}, "status": "ok" | "error"}
        """
        self._validate_context(context)
        stages = stages if stages is not None else self.settings.pipeline_stages
        self._validate_plugins(stages)
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
                result = await runner(
                    collection_config,
                    strategy_config,
                    outreach_config,
                    context,
                )
                yield {"stage": stage, "status": "ok", "result": result}
                await self.bus.publish(
                    Event(getattr(EventType, f"{stage.upper()}_DONE"), result, source="pipeline")
                )
            except Exception as e:
                logger.error(f"Pipeline 阶段 [{stage}] 失败: {e}", exc_info=True)
                error_result = {"error": str(e)}
                error_code = _stable_stage_error_code(e)
                if error_code:
                    error_result["errorCode"] = error_code
                yield {"stage": stage, "status": "error", "result": error_result}
                await self.bus.publish(
                    Event(EventType.ERROR_OCCURRED, {"stage": stage, "error": str(e)}, source="pipeline")
                )

        await self.bus.publish(Event(EventType.PIPELINE_END, {}, source="pipeline"))
        logger.info("=== Pipeline 全部完成 ===")

    def _validate_context(self, context: PipelineRunContext | None) -> None:
        if context is None:
            raise ValueError("PipelineRunContext is required")
        browser_session = context.browser_session
        if not isinstance(browser_session, BrowserSession):
            raise ValueError("PipelineRunContext.browser_session is invalid")
        if browser_session._released:
            raise ValueError("PipelineRunContext.browser_session is already released")
        if browser_session.client is None:
            raise ValueError("PipelineRunContext.browser_session client is missing")
        if browser_session.platform != context.platform:
            raise ValueError("PipelineRunContext browser platform mismatch")
        if browser_session.account_id != context.account_id:
            raise ValueError("PipelineRunContext browser account mismatch")
        with self.db.session() as session:
            job = self.job_store.get_job(session, context.job_id)
            if job is None:
                raise ValueError("PipelineRunContext job does not exist")
            if job.platform != context.platform:
                raise ValueError("PipelineRunContext job platform mismatch")
            if job.account_id is not None and job.account_id != context.account_id:
                raise ValueError("PipelineRunContext job account mismatch")

    @staticmethod
    def _validate_plugins(stages: list[str]) -> None:
        registry = get_registry()
        if "collect" in stages and registry.get_collector("keyword") is None:
            raise RuntimeError("keyword collector is required")
        if "outreach" in stages:
            if registry.get_channel("comment") is None:
                raise RuntimeError("comment channel is required")
            if registry.get_channel("dm") is None:
                raise RuntimeError("dm channel is required")

    async def _execute_reserved_message(
        self,
        channel,
        *,
        reserved_id: int,
        target: str,
        content: str,
        config: dict,
    ) -> tuple[bool, bool]:
        try:
            success = await channel.execute(
                target=target,
                content=content,
                config=config,
            )
        except Exception as exc:
            logger.error("触达渠道执行状态不确定: %s", exc, exc_info=True)
            with self.db.session() as session:
                self.store.finish_message(
                    session,
                    reserved_id,
                    success=False,
                    status="uncertain",
                    error_msg=str(exc),
                )
            return False, True

        with self.db.session() as session:
            self.store.finish_message(
                session,
                reserved_id,
                success=success,
                error_msg="" if success else "channel returned false",
            )
        return success, not success

    # ===== 阶段 1: 用户搜集 =====

    async def _run_collect(self, cfg, _, __, context: PipelineRunContext) -> dict:
        """用户搜集（双平台）"""
        reg = get_registry()
        keyword_collector = reg.get_collector("keyword")

        if not keyword_collector:
            return {"error": "未注册 keyword collector"}

        acquisition_store = AcquisitionStore()
        with self.db.session() as session:
            campaign = acquisition_store.get_campaign(session, context.job_id)
        if campaign is not None:
            return await self._run_acquisition_collect(
                cfg,
                context=context,
                keyword_collector=keyword_collector,
                acquisition_store=acquisition_store,
            )

        # 确保 cfg 带 platform（默认 tiktok）
        platform_name = context.platform
        cfg = {**cfg, "platform": platform_name}
        cfg.update(context.plugin_config())

        raw_users = await keyword_collector.collect(cfg)

        # Provider 结果只能属于当前任务平台；平台信息冲突时整阶段关闭。
        for u in raw_users:
            returned_platform = u.get("platform")
            raw_id = str(u.get("tiktok_id", ""))
            id_prefix = raw_id.split(":", 1)[0] if ":" in raw_id else ""
            if returned_platform and returned_platform != platform_name:
                raise ValueError("collector result platform mismatch")
            if id_prefix in {"tiktok", "douyin"} and id_prefix != platform_name:
                raise ValueError("collector result tiktok_id platform mismatch")
            if not raw_id:
                raise ValueError("collector result tiktok_id is required")
            if id_prefix not in {"tiktok", "douyin"}:
                u["tiktok_id"] = f"{platform_name}:{raw_id}"
            u["platform"] = platform_name

        # 入库
        saved = 0
        with self.db.session() as s:
            for u in raw_users:
                try:
                    user = self.store.add_user(s, **u)
                    self.job_store.link_user(
                        s,
                        context.job_id,
                        user.id,
                        "collect",
                    )
                    saved += 1
                except ValueError:
                    raise
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

    async def _run_acquisition_collect(
        self,
        cfg: dict,
        *,
        context: PipelineRunContext,
        keyword_collector,
        acquisition_store: AcquisitionStore,
    ) -> dict:
        """Run schema-validated stage 01 without qualification or outreach."""
        with self.db.session() as session:
            campaign = acquisition_store.get_campaign(session, context.job_id)
            if campaign is None:
                raise ValueError("Acquisition campaign does not exist")
            if campaign.platform != context.platform:
                raise ValueError("Acquisition campaign platform mismatch")
            keyword_rows = acquisition_store.list_keywords(session, context.job_id)
            campaign_budget = dict(campaign.search_budget or {})
            keyword_mix = dict(campaign.keyword_mix or {})
            keyword_values = [
                {
                    "id": item.id,
                    "text": item.text,
                    "status": item.status,
                    "qualified_count": item.qualified_count,
                    "candidate_count": item.candidate_count,
                    "relevant_video_count": item.relevant_video_count,
                }
                for item in keyword_rows
            ]

        max_keywords = int(campaign_budget.get("maxKeywords", 10))
        max_videos = int(campaign_budget.get("maxVideosPerKeyword", 10))
        max_comments = int(campaign_budget.get("maxCommentsPerVideo", 50))
        max_author_videos = int(campaign_budget.get("maxAuthorVideos", 5))
        max_pages = int(campaign_budget.get("maxPages", 10))
        max_duration_minutes = int(
            campaign_budget.get("maxDurationMinutes", 60)
        )
        max_llm_calls = int(campaign_budget.get("maxLlmCalls", 100))
        estimated_profiles = max_keywords * max_videos * (max_comments + 1)
        budget = ExplorationBudget(
            max_keywords=max_keywords,
            max_videos_per_keyword=max_videos,
            max_comments_per_video=max_comments,
            max_profiles=min(2000, max(1, estimated_profiles)),
            max_total_observations=min(10000, max(1, estimated_profiles * 2)),
            max_author_videos=max_author_videos,
            max_pages=max_pages,
            max_duration_minutes=max_duration_minutes,
            max_llm_calls=max_llm_calls,
        )
        effective_percent = float(keyword_mix.get("effectivePercent", 70))
        effective_ratio = effective_percent / 100
        plan = DiscoveryPlannerAgent().plan(
            platform=context.platform,
            keywords=keyword_values,
            budget=budget,
            total_keywords=budget.max_keywords,
            effective_ratio=effective_ratio,
        )
        planned_keywords = [item.text for item in plan.keywords]
        keyword_ids = {
            item.text: item.keyword_id
            for item in plan.keywords
            if item.keyword_id is not None
        }
        collection_metrics: dict[str, Any] = {"keywords": {}}
        collect_cfg = {
            **cfg,
            **context.plugin_config(),
            "platform": context.platform,
            "keywords": planned_keywords,
            "keyword_ids": keyword_ids,
            "acquisition_mode": True,
            "search_modes": list(plan.search_modes),
            "budget": budget.model_dump(exclude={"schema_version"}),
            "evidence_agent": HermesEvidenceAgent(
                router=get_llm_client(),
                bus=self.bus,
                event_recorder=context.event_recorder,
                job_id=context.job_id,
                stage="collect",
            ),
            "collection_metrics": collection_metrics,
        }
        raw_candidates = await keyword_collector.collect(collect_cfg)

        # Validate the complete batch before opening the persistence transaction.
        # One malformed observation therefore writes neither a candidate nor evidence.
        candidates = [
            CandidateObservation.model_validate(item) for item in raw_candidates
        ]
        for candidate in candidates:
            if candidate.platform != context.platform:
                raise ValueError("collector candidate platform mismatch")
        self._validate_acquisition_batch(
            candidates,
            budget=budget,
            planned_keywords=plan.keywords,
            collection_metrics=collection_metrics,
        )

        with self.db.session() as session:
            summary = Stage01CandidateAgent().persist(
                session,
                job_id=context.job_id,
                candidates=candidates,
                acquisition_store=acquisition_store,
                pipeline_store=self.job_store,
                user_store=self.store,
            )
            persisted_evidence = session.query(DiscoveryEvidence).filter(
                DiscoveryEvidence.job_id == context.job_id
            ).all()
            keyword_stats = []
            for planned in plan.keywords:
                if planned.keyword_id is None:
                    continue
                matched = [
                    observation
                    for observation in persisted_evidence
                    if observation.keyword_id == planned.keyword_id
                    or (
                        observation.keyword_id is None
                        and observation.keyword_text == planned.text
                    )
                ]
                video_ids = {
                    observation.video_id
                    for observation in matched
                    if observation.video_id
                }
                relevant_video_ids = {
                    observation.video_id
                    for observation in matched
                    if observation.video_id
                    and observation.relevance_score is not None
                    and observation.relevance_score >= 0.5
                }
                candidate_ids = {
                    observation.user_id for observation in matched
                }
                metric = self._keyword_metric(
                    collection_metrics,
                    keyword_id=planned.keyword_id,
                    keyword=planned.text,
                )
                explored_video_ids = {
                    str(video_id)
                    for video_id in metric["explored_video_ids"]
                    if str(video_id)
                }
                updated = acquisition_store.update_keyword_stats(
                    session,
                    planned.keyword_id,
                    # A Pipeline job is the execution unit. Retrying the same
                    # job refreshes its aggregate but does not add usage.
                    usage_count=1,
                    video_count=len(video_ids | explored_video_ids),
                    relevant_video_count=max(
                        len(relevant_video_ids),
                        int(metric.get("relevant_videos", 0)),
                    ),
                    candidate_count=max(
                        len(candidate_ids),
                        int(metric.get("candidate_count", 0)),
                    ),
                    last_used_at=datetime.utcnow(),
                )
                keyword_stats.append({
                    "keyword_id": updated.id,
                    "keyword": updated.text,
                    "usage_count": updated.usage_count,
                    "video_count": updated.video_count,
                    "relevant_video_count": updated.relevant_video_count,
                    "candidate_count": updated.candidate_count,
                })

        candidate_count = sum(
            candidate.discovery_state == "candidate" for candidate in candidates
        )
        needs_more_evidence = sum(
            candidate.discovery_state == "needs_more_evidence"
            for candidate in candidates
        )
        return {
            "mode": "acquisition",
            "keywords_planned": len(plan.keywords),
            "candidates": summary.candidates,
            "evidence": summary.evidence,
            "candidate": candidate_count,
            "needs_more_evidence": needs_more_evidence,
            "keyword_stats": keyword_stats,
            **_collection_decision_summary(
                {
                    "maxPages": budget.max_pages,
                    "maxLlmCalls": budget.max_llm_calls,
                    "maxDurationMinutes": budget.max_duration_minutes,
                },
                collection_metrics,
            ),
        }

    @staticmethod
    def _validate_acquisition_batch(
        candidates: list[CandidateObservation],
        *,
        budget: ExplorationBudget,
        planned_keywords,
        collection_metrics: dict[str, Any],
    ) -> None:
        """Fail the whole untrusted collector batch before any DB write."""
        if len(candidates) > budget.max_profiles:
            raise ValueError("collector batch exceeds profile budget")
        observations = [
            observation
            for candidate in candidates
            for observation in candidate.evidence
        ]
        if len(observations) > budget.max_total_observations:
            raise ValueError("collector batch exceeds total observation budget")

        planned_ids = {
            item.keyword_id for item in planned_keywords if item.keyword_id is not None
        }
        planned_texts = {item.text for item in planned_keywords}
        videos_by_keyword: dict[tuple[str, object], set[str]] = {}
        comments_by_video: dict[str, set[str]] = {}
        author_videos: dict[str, set[str]] = {}
        for candidate in candidates:
            for observation in candidate.evidence:
                if observation.keyword_id is not None:
                    if observation.keyword_id not in planned_ids:
                        raise ValueError("collector batch exceeds keyword budget")
                    keyword_key = ("id", observation.keyword_id)
                else:
                    if observation.keyword_text not in planned_texts:
                        raise ValueError("collector batch exceeds keyword budget")
                    keyword_key = ("text", observation.keyword_text)
                if observation.video_id:
                    videos_by_keyword.setdefault(keyword_key, set()).add(
                        observation.video_id
                    )
                if observation.comment_id:
                    comments_by_video.setdefault(observation.video_id, set()).add(
                        observation.comment_id
                    )
                if observation.source_type == "profile" and observation.video_id:
                    author_videos.setdefault(candidate.platform_user_id, set()).add(
                        observation.video_id
                    )

        if any(
            len(items) > budget.max_videos_per_keyword
            for items in videos_by_keyword.values()
        ):
            raise ValueError("collector batch exceeds video budget")
        if any(
            len(items) > budget.max_comments_per_video
            for items in comments_by_video.values()
        ):
            raise ValueError("collector batch exceeds comment budget")
        if any(
            len(items) > budget.max_author_videos
            for items in author_videos.values()
        ):
            raise ValueError("collector batch exceeds author video budget")
        metrics = collection_metrics.get("keywords", {})
        if not isinstance(metrics, dict):
            raise ValueError("collector metrics must be a mapping")
        allowed_metric_keys = {
            str(item.keyword_id) if item.keyword_id is not None else item.text
            for item in planned_keywords
        }
        if any(str(key) not in allowed_metric_keys for key in metrics):
            raise ValueError("collector metrics exceed keyword budget")
        if set(map(str, metrics)) != allowed_metric_keys:
            raise ValueError("collector keyword metrics are missing")
        pages = llm_calls = total_observations = 0
        duration_minutes = 0.0
        required_metric_fields = {
            "videos_explored",
            "explored_video_ids",
            "relevant_videos",
            "candidate_count",
            "pages",
            "llm_calls",
            "duration_minutes",
            "author_videos_explored",
            "total_observations",
            "truncation_reasons",
        }
        for value in metrics.values():
            if not isinstance(value, dict):
                raise ValueError("collector keyword metrics must be a mapping")
            if not required_metric_fields.issubset(value):
                raise ValueError("collector keyword metrics are incomplete")
            explored_video_ids = value["explored_video_ids"]
            if (
                not isinstance(explored_video_ids, list)
                or any(not isinstance(item, str) or not item for item in explored_video_ids)
                or len(set(explored_video_ids)) != len(explored_video_ids)
            ):
                raise ValueError("collector explored video metrics are invalid")
            numeric = {
                name: float(value.get(name, 0))
                for name in (
                    "videos_explored",
                    "relevant_videos",
                    "candidate_count",
                    "pages",
                    "llm_calls",
                    "duration_minutes",
                    "author_videos_explored",
                    "total_observations",
                )
            }
            if any(not math.isfinite(item) or item < 0 for item in numeric.values()):
                raise ValueError("collector metrics must not be negative")
            if any(
                not numeric[name].is_integer()
                for name in (
                    "videos_explored",
                    "relevant_videos",
                    "candidate_count",
                    "pages",
                    "llm_calls",
                    "author_videos_explored",
                    "total_observations",
                )
            ):
                raise ValueError("collector count metrics must be integers")
            if numeric["videos_explored"] > budget.max_videos_per_keyword:
                raise ValueError("collector metrics exceed video budget")
            if numeric["videos_explored"] != len(explored_video_ids):
                raise ValueError("collector explored video metrics are inconsistent")
            if numeric["candidate_count"] > budget.max_profiles:
                raise ValueError("collector metrics exceed profile budget")
            if numeric["author_videos_explored"] > budget.max_author_videos:
                raise ValueError("collector metrics exceed author video budget")
            if numeric["relevant_videos"] > numeric["videos_explored"]:
                raise ValueError("collector relevant videos exceed explored videos")
            pages += int(numeric["pages"])
            llm_calls += int(numeric["llm_calls"])
            total_observations += int(numeric["total_observations"])
            duration_minutes += numeric["duration_minutes"]
        if pages > budget.max_pages:
            raise ValueError("collector metrics exceed page budget")
        if llm_calls > budget.max_llm_calls:
            raise ValueError("collector metrics exceed LLM call budget")
        if duration_minutes > budget.max_duration_minutes:
            raise ValueError("collector metrics exceed duration budget")
        if total_observations > budget.max_total_observations:
            raise ValueError("collector metrics exceed total observation budget")
        totals = collection_metrics.get("totals")
        if not isinstance(totals, dict):
            raise ValueError("collector total metrics are missing")
        if not {"pages", "llm_calls", "duration_seconds"}.issubset(totals):
            raise ValueError("collector total metrics are incomplete")
        raw_total_pages = float(totals["pages"])
        raw_total_llm_calls = float(totals["llm_calls"])
        total_duration_seconds = float(totals["duration_seconds"])
        if (
            not all(math.isfinite(value) for value in (
                raw_total_pages, raw_total_llm_calls, total_duration_seconds
            ))
            or min(raw_total_pages, raw_total_llm_calls, total_duration_seconds) < 0
            or not raw_total_pages.is_integer()
            or not raw_total_llm_calls.is_integer()
        ):
            raise ValueError("collector total metrics must not be negative")
        total_pages = int(raw_total_pages)
        total_llm_calls = int(raw_total_llm_calls)
        if total_pages > budget.max_pages:
            raise ValueError("collector total metrics exceed page budget")
        if total_llm_calls > budget.max_llm_calls:
            raise ValueError("collector total metrics exceed LLM call budget")
        if total_duration_seconds > budget.max_duration_minutes * 60:
            raise ValueError("collector total metrics exceed duration budget")
        if total_pages != pages or total_llm_calls != llm_calls:
            raise ValueError("collector total metrics are inconsistent")
        if abs(total_duration_seconds - duration_minutes * 60) > 1e-6:
            raise ValueError("collector total duration metrics are inconsistent")

    @staticmethod
    def _keyword_metric(
        collection_metrics: dict[str, Any],
        *,
        keyword_id: int,
        keyword: str,
    ) -> dict[str, Any]:
        metrics = collection_metrics.get("keywords", {})
        return dict(metrics.get(str(keyword_id)) or metrics.get(keyword) or {})


    # ===== 阶段 2: 用户筛选 =====

    async def _run_filter(self, _, __, ___, context: PipelineRunContext) -> dict:
        """用户筛选：先关键词预筛，再 LLM 精筛"""
        with self.db.session() as session:
            campaign = AcquisitionStore().get_campaign(
                session, context.job_id
            )
            campaign_snapshot = (
                self._campaign_qualification_snapshot(campaign)
                if campaign is not None
                else None
            )
        if campaign_snapshot is not None:
            return await self._run_acquisition_filter(
                context, campaign_snapshot
            )

        with self.db.session() as s:
            pending_users = [
                dict(
                    uid=user.id,
                    username=user.username,
                    nickname=user.nickname,
                    bio=user.bio,
                    follower_count=user.follower_count,
                    category=link.category,
                    platform=user.platform,
                )
                for link, user in self.job_store.list_job_users(
                    s,
                    context.job_id,
                    status="pending",
                    platform=context.platform,
                    limit=200,
                )
            ]

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
                    self.job_store.update_job_user(
                        s,
                        context.job_id,
                        u["uid"],
                        status="rejected",
                        category="irrelevant",
                    )
                rejected += 1
                continue

            # 2. LLM 精筛
            llm_result = await llm.evaluate(user, {})
            category = llm_result.get("category", "unknown")
            if llm_result["is_potential"]:
                with self.db.session() as s:
                    self.job_store.update_job_user(
                        s,
                        context.job_id,
                        u["uid"],
                        status="qualified",
                        category=category,
                    )
                qualified += 1
            else:
                with self.db.session() as s:
                    self.job_store.update_job_user(
                        s,
                        context.job_id,
                        u["uid"],
                        status="rejected",
                        category=category,
                    )
                rejected += 1

        return {"total": len(pending_users), "qualified": qualified, "rejected": rejected}

    @staticmethod
    def _campaign_qualification_snapshot(campaign: Any) -> dict[str, Any]:
        return {
            "platform": campaign.platform,
            "countries": list(campaign.countries or []),
            "languages": list(campaign.languages or []),
            "industries": list(campaign.industries or []),
            "products": list(campaign.products or []),
            "customer_roles": list(campaign.customer_roles or []),
            "hard_conditions": dict(campaign.hard_conditions or {}),
            "preference_conditions": dict(
                campaign.preference_conditions or {}
            ),
            "excluded_targets": list(campaign.excluded_targets or []),
        }

    @staticmethod
    def _evidence_payload(evidence: Any) -> dict[str, Any]:
        return {
            "source_type": evidence.source_type,
            "keyword_text": evidence.keyword_text,
            "video_url": evidence.video_url,
            "comment_url": evidence.comment_url,
            "author_url": evidence.author_url,
            "raw_text": evidence.raw_text,
            "translated_text": evidence.translated_text,
            "relevance_score": evidence.relevance_score,
            "completeness_score": evidence.completeness_score,
        }

    @staticmethod
    def _confirmed_hard_exclusion(
        campaign: dict[str, Any], result: Any
    ) -> bool:
        if not result.hard_exclusion or not result.hard_exclusion_reasons:
            return False
        configured: list[str] = [
            str(value).strip().casefold()
            for value in campaign.get("excluded_targets", [])
            if str(value).strip()
        ]
        exclusion_key_markers = (
            "exclude",
            "excluded",
            "forbid",
            "blocked",
            "blacklist",
            "disallow",
            "deny",
        )
        for key, value in campaign.get("hard_conditions", {}).items():
            normalized_key = str(key).strip().casefold().replace("_", "")
            if not any(
                marker in normalized_key for marker in exclusion_key_markers
            ):
                continue
            items = value if isinstance(value, (list, tuple, set)) else [value]
            configured.extend(
                str(item).strip().casefold()
                for item in items
                if str(item).strip()
            )
        configured_set = set(configured)
        for reason in result.hard_exclusion_reasons:
            normalized = str(reason).strip().casefold()
            if normalized in configured_set:
                return True
        return False

    async def _run_acquisition_filter(
        self,
        context: PipelineRunContext,
        campaign: dict[str, Any],
    ) -> dict[str, int]:
        """Run stage 02 while preserving AI/human authority separation."""

        acquisition = AcquisitionStore()
        counters = {
            "total": 0,
            "qualified": 0,
            "manual_review": 0,
            "need_enrichment": 0,
            "rejected": 0,
            "stale_skipped": 0,
        }
        router = get_llm_client()
        enrichment_agent = EnrichmentAgent(router=router)
        qualification_agent = QualificationAgent(router=router)
        after_user_id = 0
        page_size = 100
        while True:
            with self.db.session() as session:
                rows = self.job_store.list_job_users(
                    session,
                    context.job_id,
                    platform=context.platform,
                    qualification_statuses=(
                        "manual_review",
                        "need_enrichment",
                    ),
                    after_user_id=after_user_id,
                    limit=page_size,
                )
                if not rows:
                    break
                after_user_id = rows[-1][0].user_id
                user_ids = [link.user_id for link, _ in rows]
                from sqlalchemy import select

                evidence_by_user: dict[int, list[dict[str, Any]]] = {
                    user_id: [] for user_id in user_ids
                }
                for item in session.scalars(
                    select(DiscoveryEvidence)
                    .where(
                        DiscoveryEvidence.job_id == context.job_id,
                        DiscoveryEvidence.user_id.in_(user_ids),
                    )
                    .order_by(
                        DiscoveryEvidence.user_id.asc(),
                        DiscoveryEvidence.id.asc(),
                    )
                ):
                    evidence_by_user[item.user_id].append(
                        self._evidence_payload(item)
                    )
                candidates = [
                    (
                        link.user_id,
                        link.review_version,
                        link.qualification_status,
                        link.match_score,
                        link.confidence_score,
                        {
                            "username": user.username,
                            "nickname": user.nickname or "",
                            "bio": user.bio or "",
                            "follower_count": user.follower_count,
                            "profile_url": user.profile_url or "",
                            "platform": user.platform,
                        },
                        evidence_by_user[link.user_id],
                    )
                    for link, user in rows
                ]
            counters["total"] += len(candidates)

            for (
                user_id,
                review_version,
                starting_status,
                starting_match_score,
                starting_confidence_score,
                public_profile,
                evidence,
            ) in candidates:
                try:
                    enrichment = await enrichment_agent.run(
                        public_profile=public_profile,
                        public_content=[],
                        evidence=evidence,
                    )
                except Exception as exc:
                    logger.warning(
                        "Stage 02 enrichment failed safely for user_id=%s: %s",
                        user_id,
                        type(exc).__name__,
                    )
                    with self.db.session() as session:
                        updated = self.job_store.update_ai_qualification(
                            session,
                            context.job_id,
                            user_id,
                            qualification_status="need_enrichment",
                            expected_review_version=review_version,
                            expected_qualification_status=starting_status,
                        )
                        actual_status = (
                            "need_enrichment"
                            if updated
                            else session.get(
                                PipelineJobUser, (context.job_id, user_id)
                            ).qualification_status
                        )
                    if not updated:
                        counters["stale_skipped"] += 1
                    counters[actual_status] += 1
                    continue

                try:
                    assessment = await qualification_agent.run(
                        campaign=campaign,
                        public_profile=public_profile,
                        enrichment=enrichment,
                        evidence=evidence,
                    )
                except Exception as exc:
                    logger.warning(
                        "Stage 02 qualification failed safely for user_id=%s: %s",
                        user_id,
                        type(exc).__name__,
                    )
                    with self.db.session() as session:
                        updated = self.job_store.update_ai_qualification(
                            session,
                            context.job_id,
                            user_id,
                            qualification_status="manual_review",
                            expected_review_version=review_version,
                            expected_qualification_status=starting_status,
                        )
                        actual_status = (
                            "manual_review"
                            if updated
                            else session.get(
                                PipelineJobUser, (context.job_id, user_id)
                            ).qualification_status
                        )
                    if not updated:
                        counters["stale_skipped"] += 1
                    counters[actual_status] += 1
                    continue

                target_status = assessment.suggested_status
                if target_status == "qualified":
                    target_status = "manual_review"
                elif target_status == "rejected":
                    target_status = "manual_review"
                with self.db.session() as session:
                    missing_fields = tuple(
                        dict.fromkeys(
                            (
                                *enrichment.missing_fields,
                                *assessment.missing_fields,
                            )
                        )
                    )
                    acquisition.create_assessment(
                        session,
                        job_id=context.job_id,
                        user_id=user_id,
                        labels=assessment.labels,
                        match_score=assessment.match_score,
                        confidence_score=assessment.confidence_score,
                        positive_evidence=assessment.positive_evidence,
                        negative_evidence=assessment.negative_evidence,
                        missing_fields=missing_fields,
                        reasoning=assessment.reasoning,
                        suggested_status=assessment.suggested_status,
                        schema_version=assessment.schema_version,
                        model_metadata={
                            "hardExclusion": assessment.hard_exclusion,
                            "hardExclusionReasons": list(
                                assessment.hard_exclusion_reasons
                            ),
                        },
                    )
                    updated = self.job_store.update_ai_qualification(
                        session,
                        context.job_id,
                        user_id,
                        qualification_status=target_status,
                        match_score=assessment.match_score,
                        confidence_score=assessment.confidence_score,
                        category=(
                            assessment.labels[0]
                            if assessment.labels
                            else "unknown"
                        ),
                        expected_review_version=review_version,
                        expected_qualification_status=starting_status,
                    )
                    actual_status = (
                        target_status
                        if updated
                        else session.get(
                            PipelineJobUser, (context.job_id, user_id)
                        ).qualification_status
                    )
                    if not updated:
                        current_link = session.get(
                            PipelineJobUser, (context.job_id, user_id)
                        )
                        current_link.match_score = starting_match_score
                        current_link.confidence_score = (
                            starting_confidence_score
                        )
                        session.flush()
                if not updated:
                    counters["stale_skipped"] += 1
                counters[actual_status] += 1
        return counters

    # ===== 阶段 3: 策略制定 =====

    async def _run_strategy(self, _, strategy_cfg, ___, context: PipelineRunContext) -> dict:
        """为每个 qualified 用户生成触达策略"""
        strategy_cfg = strategy_cfg or {}

        with self.db.session() as s:
            has_campaign = AcquisitionStore().get_campaign(
                s, context.job_id
            ) is not None
        if has_campaign:
            return await self._run_campaign_strategy(context)

        with self.db.session() as s:
            qualified_users = [
                {
                    "id": user.id,
                    "username": user.username,
                    "bio": user.bio,
                    "category": link.category,
                }
                for link, user in self.job_store.list_job_users(
                    s,
                    context.job_id,
                    status="qualified",
                    platform=context.platform,
                    limit=100,
                )
            ]

        if not qualified_users:
            return {"total": 0, "strategies": 0}

        llm = get_llm_client()

        strategy_count = 0
        for user in qualified_users:
            try:
                prompt = f"""为以下 {self._platform_label(context.platform)} B2B 用户生成个性化触达策略。
用户：@{user['username']}, bio={user['bio'] or 'N/A'}, category={user['category'] or 'unknown'}

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
                        user_id=user["id"],
                        job_id=context.job_id,
                        persona=result.get("persona", user["category"] or "unknown"),
                        strategy_type=result.get("strategy_type", "soft_sell"),
                        comment_template=result.get("comment_template", ""),
                        dm_template=result.get("dm_template", ""),
                        action_plan=result.get("action_plan", ""),
                        priority=result.get("priority", 3),
                    )
                    strategy_count += 1
            except Exception as e:
                logger.warning(f"@{user['username']} 策略生成失败: {e}")

        return {"total": len(qualified_users), "strategies": strategy_count}

    async def _run_campaign_strategy(
        self, context: PipelineRunContext
    ) -> dict[str, int]:
        """Generate only schema-validated strategies in bounded keyset pages."""

        agent = CampaignStrategyAgent(router=get_llm_client())
        total = 0
        strategy_count = 0
        after_user_id = 0
        while True:
            with self.db.session() as session:
                rows = self.job_store.list_job_users(
                    session,
                    context.job_id,
                    platform=context.platform,
                    qualification_status="qualified",
                    after_user_id=after_user_id,
                    limit=100,
                )
                users = [
                    {
                        "id": user.id,
                        "username": user.username,
                        "nickname": user.nickname or "",
                        "bio": user.bio or "",
                        "follower_count": user.follower_count,
                        "profile_url": user.profile_url or "",
                        "platform": user.platform,
                        "category": link.category or "unknown",
                    }
                    for link, user in rows
                ]
                if rows:
                    after_user_id = rows[-1][0].user_id
            if not users:
                break
            total += len(users)
            for user in users:
                try:
                    suggestion = await agent.run(
                        public_profile=user,
                        category=user["category"],
                    )
                    result = render_safe_campaign_strategy(suggestion)
                    with self.db.session() as session:
                        self.store.add_strategy(
                            session,
                            user_id=user["id"],
                            job_id=context.job_id,
                            persona=result.persona,
                            strategy_type=result.strategy_type,
                            comment_template=result.comment_template,
                            dm_template=result.dm_template,
                            action_plan=result.action_plan,
                            priority=result.priority,
                        )
                    strategy_count += 1
                except Exception as exc:
                    logger.warning(
                        "Campaign strategy failed safely for user_id=%s: %s",
                        user["id"],
                        type(exc).__name__,
                    )
        return {"total": total, "strategies": strategy_count}

    # ===== 阶段 4: 执行触达 =====

    async def _run_outreach(self, _, __, outreach_cfg, context: PipelineRunContext) -> dict:
        """执行评论/私信"""
        outreach_cfg = outreach_cfg or {}
        comment_limit = outreach_cfg.get("comment_limit", self.settings.daily_comment_limit)
        dm_limit = outreach_cfg.get("dm_limit", self.settings.daily_dm_limit)

        reg = get_registry()
        comment_ch = reg.get_channel("comment")
        dm_ch = reg.get_channel("dm")

        with self.db.session() as session:
            has_campaign = AcquisitionStore().get_campaign(
                session, context.job_id
            ) is not None
        if has_campaign:
            return await self._run_campaign_outreach(
                context=context,
                outreach_cfg=outreach_cfg,
                comment_ch=comment_ch,
                dm_ch=dm_ch,
                comment_limit=comment_limit,
                dm_limit=dm_limit,
            )

        with self.db.session() as s:
            from sqlalchemy import select
            from tiktok_bot_core.models.entities import (
                PipelineJobUser,
                User as UserModel,
                Strategy as StrategyModel,
            )

            stmt = (
                select(
                    StrategyModel.comment_template,
                    StrategyModel.dm_template,
                    UserModel.id,
                    UserModel.username,
                    PipelineJobUser.status,
                )
                .join(UserModel, StrategyModel.user_id == UserModel.id)
                .order_by(StrategyModel.priority.asc())
            )
            stmt = (
                stmt.join(
                    PipelineJobUser,
                    PipelineJobUser.user_id == UserModel.id,
                )
                .where(
                    PipelineJobUser.job_id == context.job_id,
                    UserModel.platform == context.platform,
                    StrategyModel.job_id == context.job_id,
                )
            )
            stmt = stmt.where(
                PipelineJobUser.status.in_(["qualified", "contacted"])
            )
            rows = [tuple(row) for row in s.execute(stmt).all()]

        comment_sent = 0
        dm_sent = 0
        errors = 0
        channel_config = dict(outreach_cfg)
        channel_config.update(context.plugin_config())

        for (
            comment_template,
            dm_template,
            user_id,
            username,
            user_status,
        ) in rows:
            # 评论
            if comment_sent < comment_limit:
                with self.db.session() as s:
                    reserved = self.store.reserve_message(
                        s,
                        job_id=context.job_id,
                        user_id=user_id,
                        message_type="comment",
                        content=comment_template,
                    )
                    reserved_id = reserved.id if reserved else None
                if reserved_id is not None:
                    success, had_error = await self._execute_reserved_message(
                        comment_ch,
                        reserved_id=reserved_id,
                        target=username,
                        content=comment_template,
                        config=channel_config,
                    )
                    errors += int(had_error)
                    with self.db.session() as s:
                        if success:
                            self.job_store.update_job_user(
                                s,
                                context.job_id,
                                user_id,
                                status="contacted",
                            )
                    if success:
                        comment_sent += 1
                        user_status = "contacted"
                        await self.bus.publish(
                            Event(
                                EventType.USER_CONTACTED,
                                {"user_id": user_id, "via": "comment"},
                            )
                        )

            # 私信
            if user_status == "contacted" and dm_sent < dm_limit:
                with self.db.session() as s:
                    reserved = self.store.reserve_message(
                        s,
                        job_id=context.job_id,
                        user_id=user_id,
                        message_type="dm",
                        content=dm_template,
                    )
                    reserved_id = reserved.id if reserved else None
                if reserved_id is not None:
                    success, had_error = await self._execute_reserved_message(
                        dm_ch,
                        reserved_id=reserved_id,
                        target=username,
                        content=dm_template,
                        config=channel_config,
                    )
                    errors += int(had_error)
                    if success:
                        dm_sent += 1

        return {
            "comments_sent": comment_sent,
            "dms_sent": dm_sent,
            "errors": errors,
        }

    # ===== 阶段 5: 数据汇总 =====

    async def _run_campaign_outreach(
        self,
        *,
        context: PipelineRunContext,
        outreach_cfg: dict[str, Any],
        comment_ch: Any,
        dm_ch: Any,
        comment_limit: int,
        dm_limit: int,
    ) -> dict[str, int]:
        """Send only revalidated campaign strategies in bounded pages."""

        from sqlalchemy import select
        from tiktok_bot_core.models.entities import (
            Strategy as StrategyModel,
            User as UserModel,
        )

        comment_sent = 0
        dm_sent = 0
        errors = 0
        after_strategy_id = 0
        channel_config = dict(outreach_cfg)
        channel_config.update(context.plugin_config())
        while True:
            if comment_sent >= comment_limit and dm_sent >= dm_limit:
                break
            with self.db.session() as session:
                rows = list(
                    session.execute(
                        select(
                            StrategyModel.id,
                            StrategyModel.persona,
                            StrategyModel.strategy_type,
                            StrategyModel.comment_template,
                            StrategyModel.dm_template,
                            StrategyModel.priority,
                            StrategyModel.action_plan,
                            UserModel.id,
                            UserModel.username,
                            PipelineJobUser.status,
                        )
                        .join(UserModel, StrategyModel.user_id == UserModel.id)
                        .join(
                            PipelineJobUser,
                            PipelineJobUser.user_id == UserModel.id,
                        )
                        .where(
                            StrategyModel.id > after_strategy_id,
                            StrategyModel.job_id == context.job_id,
                            PipelineJobUser.job_id == context.job_id,
                            PipelineJobUser.qualification_status == "qualified",
                            UserModel.platform == context.platform,
                        )
                        .order_by(StrategyModel.id.asc())
                        .limit(100)
                    ).all()
                )
            if not rows:
                break
            after_strategy_id = int(rows[-1][0])
            for row in rows:
                (
                    _,
                    persona,
                    strategy_type,
                    comment_template,
                    dm_template,
                    priority,
                    action_plan,
                    user_id,
                    username,
                    user_status,
                ) = tuple(row)
                try:
                    safe_strategy = validate_persisted_campaign_strategy(
                        persona=persona,
                        strategy_type=strategy_type,
                        comment_template=comment_template,
                        dm_template=dm_template,
                        priority=priority,
                        action_plan=action_plan,
                    )
                except Exception:
                    continue
                if (
                    safe_strategy.comment_template
                    and comment_sent < comment_limit
                ):
                    with self.db.session() as session:
                        reserved = self.store.reserve_message(
                            session,
                            job_id=context.job_id,
                            user_id=user_id,
                            message_type="comment",
                            content=safe_strategy.comment_template,
                        )
                        reserved_id = reserved.id if reserved else None
                    if reserved_id is not None:
                        success, had_error = await self._execute_reserved_message(
                            comment_ch,
                            reserved_id=reserved_id,
                            target=username,
                            content=safe_strategy.comment_template,
                            config=channel_config,
                        )
                        errors += int(had_error)
                        if success:
                            with self.db.session() as session:
                                self.job_store.update_job_user(
                                    session,
                                    context.job_id,
                                    user_id,
                                    status="contacted",
                                )
                            comment_sent += 1
                            user_status = "contacted"
                            await self.bus.publish(
                                Event(
                                    EventType.USER_CONTACTED,
                                    {"user_id": user_id, "via": "comment"},
                                )
                            )
                if (
                    safe_strategy.dm_template
                    and user_status == "contacted"
                    and dm_sent < dm_limit
                ):
                    with self.db.session() as session:
                        reserved = self.store.reserve_message(
                            session,
                            job_id=context.job_id,
                            user_id=user_id,
                            message_type="dm",
                            content=safe_strategy.dm_template,
                        )
                        reserved_id = reserved.id if reserved else None
                    if reserved_id is not None:
                        success, had_error = await self._execute_reserved_message(
                            dm_ch,
                            reserved_id=reserved_id,
                            target=username,
                            content=safe_strategy.dm_template,
                            config=channel_config,
                        )
                        errors += int(had_error)
                        if success:
                            dm_sent += 1
        return {
            "comments_sent": comment_sent,
            "dms_sent": dm_sent,
            "errors": errors,
        }

    async def _run_report(self, _, __, ___, context: PipelineRunContext):
        """生成日报"""
        # Persisted timestamps use UTC. Keep the report day on that same
        # boundary so local midnight cannot temporarily hide UTC-created rows.
        today = datetime.utcnow().date()

        with self.db.session() as s:
            new_users = self.store.count_job_users(
                s, context.job_id, context.platform
            )
            qualified = self.store.count_job_users(
                s, context.job_id, context.platform, "qualified"
            )
            rejected = self.store.count_job_users(
                s, context.job_id, context.platform, "rejected"
            )
            comments = self.store.count_job_messages(
                s, context.job_id, context.platform, "comment", "sent"
            )
            dms = self.store.count_job_messages(
                s, context.job_id, context.platform, "dm", "sent"
            )
            replies = self.store.count_job_replies(
                s, context.job_id, context.platform
            )
            positive = self.store.count_job_replies(
                s,
                context.job_id,
                context.platform,
                sentiment="positive",
            )
            leads = self.store.count_job_replies(
                s,
                context.job_id,
                context.platform,
                business_intent=True,
            )
            sent_total = comments + dms
            reply_rate = replies / sent_total if sent_total > 0 else 0.0

            global_new_users = self.store.count_users(s, since_date=today)
            (
                global_qualified,
                global_rejected,
            ) = self.store.count_global_job_user_outcomes(
                s,
                today,
            )
            global_comments = self.store.count_messages(
                s, "comment", today, status="sent"
            )
            global_dms = self.store.count_messages(
                s, "dm", today, status="sent"
            )
            global_replies = self.store.count_replies(s, since_date=today)
            global_positive = self.store.count_replies(
                s, "positive", today
            )
            global_leads = self.store.count_business_leads(s, today)
            global_sent_total = global_comments + global_dms
            global_reply_rate = (
                global_replies / global_sent_total
                if global_sent_total > 0
                else 0.0
            )
            self.store.upsert_daily_report(
                s,
                report_date=today,
                new_users_found=global_new_users,
                users_qualified=global_qualified,
                users_rejected=global_rejected,
                comments_sent=global_comments,
                dms_sent=global_dms,
                replies_received=global_replies,
                reply_rate=global_reply_rate,
                positive_replies=global_positive,
                business_leads=global_leads,
            )

        # 推送到 Telegram（如果有配置）
        if self.settings.telegram_bot_token:
            try:
                await self._send_telegram_report(
                    today,
                    new_users,
                    qualified,
                    comments,
                    dms,
                    replies,
                    reply_rate,
                    context.platform,
                )
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

    async def _send_telegram_report(
        self,
        today,
        new,
        qualified,
        comments,
        dms,
        replies,
        rate,
        platform,
    ):
        """推送到 Telegram"""
        from telegram import Bot
        bot = Bot(token=self.settings.telegram_bot_token)
        text = (
            f"📊 *{self._platform_label(platform)} Bot 日报* — {today}\n\n"
            f"👥 新增用户: {new}\n"
            f"✅ 合格: {qualified}\n"
            f"💬 评论发送: {comments}\n"
            f"📩 私信发送: {dms}\n"
            f"📥 收到回复: {replies}\n"
            f"📈 回复率: {rate:.1%}"
        )
        await bot.send_message(chat_id=self.settings.telegram_chat_id, text=text, parse_mode="Markdown")

    # ===== 阶段 6: 闭环迭代 =====

    async def _run_iterate(self, _, __, ___, context: PipelineRunContext):
        """经验沉淀 + 规则更新"""
        with self.db.session() as s:
            keyword_stats = self.store.get_keyword_effectiveness(
                s, context.job_id, context.platform
            )
            category_stats = self.store.get_category_distribution(
                s, context.job_id, context.platform
            )

        from tiktok_bot_core.llm.client import get_llm_client
        from datetime import date as date_cls

        llm = get_llm_client()
        import json
        prompt = f"""根据 {self._platform_label(context.platform)} B2B 营销数据，提取经验并给出优化建议。

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
            raise RuntimeError(f"分析失败: {e}") from e

        # 沉淀到 ChromaDB
        exp_text = analysis.get("summary", "") + "\n" + "\n".join(analysis.get("strategy_suggestions", []))
        try:
            self.vector.add_experience(
                exp_id=f"exp_{context.job_id}_{date_cls.today()}",
                document=exp_text,
                metadata={
                    "date": str(date_cls.today()),
                    "type": "weekly",
                    "platform": context.platform,
                    "job_id": context.job_id,
                },
            )
        except Exception as e:
            logger.warning(f"经验入库失败: {e}")

        # 保存到 SQLite
        with self.db.session() as s:
            self.store.add_rule(
                s,
                platform=context.platform,
                job_id=context.job_id,
                rule_type="weekly_optimization",
                rule_content=json.dumps(analysis, ensure_ascii=False),
                effectiveness=0.0,
                sample_size=0,
            )

        return analysis

    @staticmethod
    def _platform_label(platform: str) -> str:
        return "抖音" if platform == "douyin" else "TikTok"
