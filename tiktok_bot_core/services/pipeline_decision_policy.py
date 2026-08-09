"""Deterministic decision policy for AI acquisition Pipeline jobs.

The policy only describes finite, executable actions.  It never mutates a
candidate, Job, Stage, or checkpoint; the Runner owns action execution and the
DecisionGate owns durable waiting/resolution.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from sqlalchemy import func, select

from tiktok_bot_core.models.entities import (
    AcquisitionCampaign,
    PipelineJob,
    PipelineJobUser,
    Strategy,
    User,
)
from tiktok_bot_core.services.pipeline import (
    validate_persisted_campaign_strategy,
)
from tiktok_bot_core.storage.database import Database


_RETRYABLE_ERROR_CODES = frozenset({"network", "timeout", "upstream_server"})
_ACCOUNT_BLOCKED_ERROR_CODES = frozenset(
    {
        "account_blocked",
        "authentication_required",
        "captcha_required",
        "login_required",
        "risk_control",
    }
)


@dataclass(frozen=True, slots=True)
class DecisionPolicyCapabilities:
    """Actions backed by a real bounded executor in the current runtime."""

    deepen_collect: bool = False
    batch_enrichment: bool = False
    account_recovery: bool = False


@dataclass(frozen=True, slots=True)
class DecisionPlan:
    """A registered checkpoint subset and its server-owned default."""

    kind: str
    option_keys: tuple[str, ...]
    default_option_key: str
    context: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not self.option_keys:
            raise ValueError("decision plan must expose at least one option")
        if self.default_option_key not in self.option_keys:
            raise ValueError("decision plan default must be in option subset")


class PipelineDecisionPolicy:
    """Build Job-scoped decision plans from persisted acquisition state."""

    def __init__(
        self,
        database: Database,
        *,
        capabilities: DecisionPolicyCapabilities | None = None,
        evidence_ratio_threshold: float = 0.5,
    ) -> None:
        if (
            isinstance(evidence_ratio_threshold, bool)
            or not isinstance(evidence_ratio_threshold, (int, float))
            or not 0 < evidence_ratio_threshold <= 1
        ):
            raise ValueError("evidence_ratio_threshold must be in (0, 1]")
        self._database = database
        self._capabilities = capabilities or DecisionPolicyCapabilities()
        if any(
            (
                self._capabilities.deepen_collect,
                self._capabilities.batch_enrichment,
                self._capabilities.account_recovery,
            )
        ):
            raise ValueError(
                "decision policy capability executor is not implemented"
            )
        self._evidence_ratio_threshold = float(evidence_ratio_threshold)

    def after_stage(
        self,
        *,
        job_id: str,
        stage: str,
        result: Mapping[str, Any],
    ) -> DecisionPlan | None:
        campaign = self._campaign_snapshot(job_id)
        if campaign is None:
            return None
        if stage == "collect":
            return self._after_collect(result)
        if stage == "filter":
            return self._after_filter(job_id, campaign["platform"])
        return None

    def before_stage(self, *, job_id: str, stage: str) -> DecisionPlan | None:
        if stage != "outreach":
            return None
        campaign = self._campaign_snapshot(job_id)
        if campaign is None:
            return None
        with self._database.session() as session:
            job = session.get(PipelineJob, job_id)
            authorized = bool(
                job is not None
                and "outreach" in tuple(job.stages_json or ())
                and job.platform == campaign["platform"]
            )
            counts = self._qualification_counts(
                session,
                job_id=job_id,
                platform=campaign["platform"],
            )
            strategy_rows = session.execute(
                select(
                    Strategy.persona,
                    Strategy.strategy_type,
                    Strategy.comment_template,
                    Strategy.dm_template,
                    Strategy.priority,
                    Strategy.action_plan,
                )
                .join(
                    PipelineJobUser,
                    (PipelineJobUser.job_id == Strategy.job_id)
                    & (PipelineJobUser.user_id == Strategy.user_id),
                )
                .join(User, User.id == Strategy.user_id)
                .where(
                    Strategy.job_id == job_id,
                    PipelineJobUser.qualification_status == "qualified",
                    User.platform == campaign["platform"],
                )
            ).all()
            valid_strategies = 0
            for row in strategy_rows:
                try:
                    validate_persisted_campaign_strategy(
                        persona=row.persona,
                        strategy_type=row.strategy_type,
                        comment_template=row.comment_template,
                        dm_template=row.dm_template,
                        priority=row.priority,
                        action_plan=row.action_plan,
                    )
                except Exception:
                    continue
                valid_strategies += 1

        if not authorized:
            return self._plan(
                kind="outreach_confirmation",
                options=("skip_outreach",),
                default="skip_outreach",
                context={
                    "summary": "Outreach authorization is unavailable",
                    "candidateCounts": {
                        "qualified": counts["qualified"],
                        "validStrategies": valid_strategies,
                    },
                    "defaultReason": "outreach_not_authorized",
                },
            )

        options = ["execute_approved_outreach"]
        if counts["total"] > 0:
            options.append("open_review_workbench")
        options.append("skip_outreach")
        return self._plan(
            kind="outreach_confirmation",
            options=tuple(options),
            default="execute_approved_outreach",
            context={
                "summary": "Confirm outreach to the current approved set",
                "candidateCounts": {
                    "qualified": counts["qualified"],
                    "validStrategies": valid_strategies,
                },
                "defaultReason": "job_includes_outreach",
            },
        )

    def for_stage_error(
        self,
        *,
        job_id: str,
        stage: str,
        error_code: str,
        retry_count: int,
    ) -> DecisionPlan | None:
        if self._campaign_snapshot(job_id) is None:
            return None
        normalized_code = (
            error_code.strip() if isinstance(error_code, str) else ""
        )
        if (
            isinstance(retry_count, bool)
            or not isinstance(retry_count, int)
            or retry_count < 0
        ):
            raise ValueError("retry_count must be a non-negative integer")
        if normalized_code in _RETRYABLE_ERROR_CODES:
            options = (
                ("retry_once", "skip_stage", "stop_job")
                if retry_count == 0
                else ("skip_stage", "stop_job")
            )
            return self._plan(
                kind="retryable_failure",
                options=options,
                default="retry_once" if retry_count == 0 else "skip_stage",
                context={
                    "summary": "A retryable stage failure occurred",
                    "blockingReason": normalized_code,
                    "defaultReason": (
                        "bounded_retry_available"
                        if retry_count == 0
                        else "bounded_retry_consumed"
                    ),
                },
            )
        if normalized_code in _ACCOUNT_BLOCKED_ERROR_CODES:
            options = ["skip_stage", "stop_job"]
            return self._plan(
                kind="account_blocked",
                options=tuple(options),
                default="skip_stage",
                context={
                    "summary": "The account cannot continue this stage",
                    "blockingReason": normalized_code,
                    "defaultReason": "safe_skip_blocked_account",
                },
            )
        return None

    def _after_collect(self, result: Mapping[str, Any]) -> DecisionPlan | None:
        if not isinstance(result, Mapping):
            return None
        candidate_count = self._non_negative_count(
            result.get("candidate", result.get("candidates", 0))
        )
        needs_more = self._non_negative_count(
            result.get("needs_more_evidence", 0)
        )
        raw_reasons = result.get("truncation_reasons", ())
        reasons = (
            tuple(
                sorted(
                    {
                        value.strip()
                        for value in raw_reasons
                        if isinstance(value, str) and value.strip()
                    }
                )
            )
            if isinstance(raw_reasons, (list, tuple, set, frozenset))
            else ()
        )
        total = candidate_count + needs_more
        high_needs_more = bool(
            needs_more
            and total
            and needs_more / total >= self._evidence_ratio_threshold
        )
        if total != 0 and not high_needs_more and not reasons:
            return None

        remaining = self._remaining_budget(result.get("remaining_budget"))
        options: list[str] = []
        options.extend(
            (
                "continue_with_current_evidence",
                "skip_remaining_pipeline",
                "cancel_job",
            )
        )
        return self._plan(
            kind="insufficient_evidence",
            options=tuple(options),
            default="continue_with_current_evidence",
            context={
                "summary": "Candidate evidence may be insufficient",
                "candidateCounts": {
                    "candidate": candidate_count,
                    "needsMoreEvidence": needs_more,
                },
                "remainingBudget": remaining or {},
                "warnings": list(reasons),
                "defaultReason": "continue_with_available_evidence",
            },
        )

    def _after_filter(self, job_id: str, platform: str) -> DecisionPlan | None:
        with self._database.session() as session:
            counts = self._qualification_counts(
                session,
                job_id=job_id,
                platform=platform,
            )
        if counts["manualReview"] + counts["needEnrichment"] == 0:
            return None
        options = ["open_review_workbench"]
        options.append("continue_with_qualified_only")
        return self._plan(
            kind="qualification_review",
            options=tuple(options),
            default="continue_with_qualified_only",
            context={
                "summary": "Candidates are awaiting human review",
                "candidateCounts": {
                    "manualReview": counts["manualReview"],
                    "needEnrichment": counts["needEnrichment"],
                    "qualified": counts["qualified"],
                },
                "defaultReason": "continue_with_human_qualified_only",
            },
        )

    def _campaign_snapshot(self, job_id: str) -> dict[str, Any] | None:
        with self._database.session() as session:
            campaign = session.scalar(
                select(AcquisitionCampaign).where(
                    AcquisitionCampaign.job_id == job_id
                )
            )
            if campaign is None:
                return None
            return {
                "platform": campaign.platform,
                "searchBudget": dict(campaign.search_budget or {}),
            }

    @staticmethod
    def _qualification_counts(session, *, job_id: str, platform: str) -> dict[str, int]:
        rows = session.execute(
            select(
                PipelineJobUser.qualification_status,
                func.count(PipelineJobUser.user_id),
            )
            .join(User, User.id == PipelineJobUser.user_id)
            .where(
                PipelineJobUser.job_id == job_id,
                User.platform == platform,
            )
            .group_by(PipelineJobUser.qualification_status)
        ).all()
        counts = {str(status): int(count) for status, count in rows}
        return {
            "manualReview": counts.get("manual_review", 0),
            "needEnrichment": counts.get("need_enrichment", 0),
            "qualified": counts.get("qualified", 0),
            "total": sum(counts.values()),
        }

    @staticmethod
    def _non_negative_count(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return 0
        return value

    @staticmethod
    def _remaining_budget(value: Any) -> dict[str, float | int] | None:
        if not isinstance(value, Mapping):
            return None
        required = ("pages", "llmCalls", "durationSeconds")
        normalized: dict[str, float | int] = {}
        for key in required:
            item = value.get(key)
            if (
                isinstance(item, bool)
                or not isinstance(item, (int, float))
                or item <= 0
            ):
                return None
            normalized[key] = item
        return normalized

    @staticmethod
    def _plan(
        *,
        kind: str,
        options: tuple[str, ...],
        default: str,
        context: Mapping[str, Any],
    ) -> DecisionPlan:
        return DecisionPlan(
            kind=kind,
            option_keys=options,
            default_option_key=default,
            context=MappingProxyType({"schemaVersion": 1, **dict(context)}),
        )


__all__ = [
    "DecisionPlan",
    "DecisionPolicyCapabilities",
    "PipelineDecisionPolicy",
]
