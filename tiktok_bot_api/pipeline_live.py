"""Safe read models and strict DTOs for Pipeline live monitoring."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from tiktok_bot_core.models.entities import (
    PipelineDecisionCheckpoint,
    PipelineJob,
    PipelineJobEvent,
)
from tiktok_bot_core.models.pipeline_states import TERMINAL_JOB_STATUSES
from tiktok_bot_core.storage.database import Database
from tiktok_bot_core.storage.pipeline_live_store import PipelineLiveStore


MAX_SEQUENCE = 2**63 - 1
DEFAULT_RECENT_EVENT_LIMIT = 100


class _StrictResponse(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        strict=True,
    )


class PipelineLiveEventResponse(_StrictResponse):
    sequence: int
    job_id: str = Field(alias="jobId")
    stage: str
    event_type: str = Field(alias="eventType")
    level: str
    payload: dict[str, Any]
    created_at: datetime = Field(alias="createdAt")


class PipelineLiveCheckpointResponse(_StrictResponse):
    id: str
    job_id: str = Field(alias="jobId")
    stage: str
    kind: str
    version: int
    option_keys: list[str] = Field(alias="optionKeys")
    default_option_key: str = Field(alias="defaultOptionKey")
    context: dict[str, Any]
    status: str
    deadline_at: datetime | None = Field(alias="deadlineAt")
    resolved_at: datetime | None = Field(alias="resolvedAt")
    resolution_key: str | None = Field(alias="resolutionKey")
    resolution_source: str | None = Field(alias="resolutionSource")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class PipelineLiveJobResponse(_StrictResponse):
    id: str
    platform: str
    status: str
    current_stage: str = Field(alias="currentStage")
    requested_stages: list[str] = Field(alias="requestedStages")
    started_at: datetime | None = Field(alias="startedAt")
    finished_at: datetime | None = Field(alias="finishedAt")
    updated_at: datetime = Field(alias="updatedAt")


class PipelineLiveStageResponse(_StrictResponse):
    stage: str
    order: int
    status: str
    attempt: int
    started_at: datetime | None = Field(alias="startedAt")
    finished_at: datetime | None = Field(alias="finishedAt")


class PipelineLiveMetricsResponse(_StrictResponse):
    total_events: int = Field(alias="totalEvents")
    browser_actions: int = Field(alias="browserActions")
    videos: int | float
    comments: int | float
    candidates: int | float
    evidence: int | float
    llm_calls: int | float = Field(alias="llmCalls")
    remaining_budget: dict[str, int | float] = Field(alias="remainingBudget")


class PipelineLiveResponse(_StrictResponse):
    job: PipelineLiveJobResponse
    stage: PipelineLiveStageResponse | None
    metrics: PipelineLiveMetricsResponse
    recent_events: list[PipelineLiveEventResponse] = Field(alias="recentEvents")
    active_checkpoint: PipelineLiveCheckpointResponse | None = Field(
        alias="activeCheckpoint"
    )
    last_sequence: int = Field(alias="lastSequence")


class PipelineEventHistoryResponse(_StrictResponse):
    items: list[PipelineLiveEventResponse]
    last_sequence: int = Field(alias="lastSequence")


class PipelineActiveCheckpointResponse(_StrictResponse):
    checkpoint: PipelineLiveCheckpointResponse | None


class PipelineDecisionResolutionResponse(_StrictResponse):
    checkpoint_id: str = Field(alias="checkpointId")
    job_id: str = Field(alias="jobId")
    stage: str
    kind: str
    option_key: str | None = Field(alias="optionKey")
    source: str
    status: str
    resolved_at: datetime | None = Field(alias="resolvedAt")
    deadline_at: datetime | None = Field(alias="deadlineAt")


class PipelineResolveResponse(_StrictResponse):
    resolution: PipelineDecisionResolutionResponse


@dataclass(frozen=True, slots=True)
class PipelineLiveHistory:
    items: list[dict[str, Any]]
    last_sequence: int
    terminal: bool


def serialize_event(event: PipelineJobEvent) -> dict[str, Any]:
    return {
        "sequence": event.sequence,
        "jobId": event.job_id,
        "stage": event.stage or "",
        "eventType": event.event_type,
        "level": event.level,
        "payload": dict(event.payload_json or {}),
        "createdAt": event.created_at,
    }


def serialize_checkpoint(
    checkpoint: PipelineDecisionCheckpoint | None,
) -> dict[str, Any] | None:
    if checkpoint is None:
        return None
    return {
        "id": checkpoint.id,
        "jobId": checkpoint.job_id,
        "stage": checkpoint.stage,
        "kind": checkpoint.kind,
        "version": checkpoint.version,
        "optionKeys": list(checkpoint.option_keys_json or []),
        "defaultOptionKey": checkpoint.default_option_key,
        "context": dict(checkpoint.context_json or {}),
        "status": checkpoint.status,
        "deadlineAt": (
            None
            if checkpoint.kind == "manual_review_session"
            else checkpoint.deadline_at
        ),
        "resolvedAt": checkpoint.resolved_at,
        "resolutionKey": checkpoint.resolution_key,
        "resolutionSource": checkpoint.resolution_source,
        "createdAt": checkpoint.created_at,
        "updatedAt": checkpoint.updated_at,
    }


def _numeric_max(current: int | float, value: Any) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return current
    return max(current, value)


def _metrics(
    events: list[PipelineJobEvent],
    checkpoint: PipelineDecisionCheckpoint | None,
) -> dict[str, Any]:
    browser_actions = 0
    candidates: int | float = 0
    videos: int | float = 0
    comments: int | float = 0
    evidence: int | float = 0
    llm_calls: int | float = 0
    candidate_ids: set[int] = set()
    latest_budget: dict[str, Any] = {}
    for event in events:
        payload = event.payload_json or {}
        if event.event_type.startswith("browse."):
            browser_actions += 1
        evidence = _numeric_max(evidence, payload.get("evidenceCount"))
        if event.event_type == "candidate.lifecycle":
            user_id = payload.get("userId")
            if isinstance(user_id, int) and not isinstance(user_id, bool):
                candidate_ids.add(user_id)
        budget = payload.get("budget")
        if isinstance(budget, dict):
            latest_budget = budget
            videos = _numeric_max(videos, budget.get("videosUsed"))
            comments = _numeric_max(comments, budget.get("commentsUsed"))
            candidates = _numeric_max(candidates, budget.get("candidatesUsed"))
            evidence = _numeric_max(evidence, budget.get("evidenceUsed"))
            llm_calls = _numeric_max(llm_calls, budget.get("llmCallsUsed"))
    candidates = max(candidates, len(candidate_ids))

    context = checkpoint.context_json if checkpoint is not None else {}
    context_remaining = (
        context.get("remainingBudget") if isinstance(context, dict) else None
    )
    if isinstance(context_remaining, dict):
        remaining = dict(context_remaining)
    else:
        remaining = {}
        for prefix in (
            "pages",
            "videos",
            "comments",
            "candidates",
            "evidence",
            "llmCalls",
        ):
            used = latest_budget.get(f"{prefix}Used")
            limit = latest_budget.get(f"{prefix}Limit")
            if (
                not isinstance(used, bool)
                and isinstance(used, (int, float))
                and not isinstance(limit, bool)
                and isinstance(limit, (int, float))
            ):
                remaining[prefix] = max(0, limit - used)
    return {
        "totalEvents": len(events),
        "browserActions": browser_actions,
        "videos": videos,
        "comments": comments,
        "candidates": candidates,
        "evidence": evidence,
        "llmCalls": llm_calls,
        "remainingBudget": remaining,
    }


class PipelineLiveReadService:
    """Read and detach one Job's authoritative live state."""

    def __init__(
        self,
        database: Database,
        *,
        store: PipelineLiveStore | None = None,
    ) -> None:
        self._database = database
        self._store = store or PipelineLiveStore()

    def job_exists(self, job_id: str) -> bool:
        with self._database.session() as session:
            return session.get(PipelineJob, str(job_id).strip()) is not None

    def snapshot(
        self,
        job_id: str,
        *,
        recent_limit: int = DEFAULT_RECENT_EVENT_LIMIT,
    ) -> dict[str, Any] | None:
        """Serialize the initial screen inside exactly one DB Session."""

        with self._database.session() as session:
            job = session.get(PipelineJob, str(job_id).strip())
            if job is None:
                return None
            all_events = list(
                session.scalars(
                    select(PipelineJobEvent)
                    .where(PipelineJobEvent.job_id == job.id)
                    .order_by(PipelineJobEvent.sequence.asc())
                )
            )
            recent_events = all_events[-recent_limit:]
            checkpoint = self._store.get_active_checkpoint(
                session,
                job_id=job.id,
            )
            current_stage = next(
                (
                    stage
                    for stage in job.stages
                    if stage.stage == (job.current_stage or "")
                ),
                None,
            )
            return {
                "job": {
                    "id": job.id,
                    "platform": job.platform,
                    "status": job.status,
                    "currentStage": job.current_stage or "",
                    "requestedStages": list(job.stages_json or []),
                    "startedAt": job.started_at,
                    "finishedAt": job.finished_at,
                    "updatedAt": job.updated_at,
                },
                "stage": (
                    {
                        "stage": current_stage.stage,
                        "order": current_stage.stage_order,
                        "status": current_stage.status,
                        "attempt": current_stage.attempt,
                        "startedAt": current_stage.started_at,
                        "finishedAt": current_stage.finished_at,
                    }
                    if current_stage is not None
                    else None
                ),
                "metrics": _metrics(all_events, checkpoint),
                "recentEvents": [serialize_event(event) for event in recent_events],
                "activeCheckpoint": serialize_checkpoint(checkpoint),
                "lastSequence": all_events[-1].sequence if all_events else 0,
            }

    def history(
        self,
        job_id: str,
        *,
        after_sequence: int,
        limit: int,
    ) -> PipelineLiveHistory | None:
        with self._database.session() as session:
            job = session.get(PipelineJob, str(job_id).strip())
            if job is None:
                return None
            events = self._store.list_events(
                session,
                job_id=job.id,
                after_sequence=after_sequence,
                limit=limit,
            )
            items = [serialize_event(event) for event in events]
            return PipelineLiveHistory(
                items=items,
                last_sequence=(
                    events[-1].sequence if events else after_sequence
                ),
                terminal=job.status in TERMINAL_JOB_STATUSES,
            )

    def active_checkpoint(self, job_id: str) -> tuple[bool, dict[str, Any] | None]:
        with self._database.session() as session:
            job = session.get(PipelineJob, str(job_id).strip())
            if job is None:
                return False, None
            checkpoint = self._store.get_active_checkpoint(
                session,
                job_id=job.id,
            )
            return True, serialize_checkpoint(checkpoint)

    def checkpoint(
        self,
        job_id: str,
        checkpoint_id: str,
    ) -> dict[str, Any] | None:
        with self._database.session() as session:
            checkpoint = self._store.get_checkpoint(
                session,
                job_id=str(job_id).strip(),
                checkpoint_id=str(checkpoint_id).strip(),
            )
            return serialize_checkpoint(checkpoint)


__all__ = [
    "DEFAULT_RECENT_EVENT_LIMIT",
    "MAX_SEQUENCE",
    "PipelineActiveCheckpointResponse",
    "PipelineDecisionResolutionResponse",
    "PipelineEventHistoryResponse",
    "PipelineLiveCheckpointResponse",
    "PipelineLiveEventResponse",
    "PipelineLiveReadService",
    "PipelineLiveResponse",
    "PipelineResolveResponse",
    "serialize_checkpoint",
    "serialize_event",
]
