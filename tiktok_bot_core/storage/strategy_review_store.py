"""Job-scoped persistence primitives for Stage 03 strategy review."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from sqlalchemy import exists, func, select, update
from sqlalchemy.orm import Session

from tiktok_bot_core.models.entities import PipelineJob, PipelineJobUser, Strategy, User
from tiktok_bot_core.services.acquisition_agents import StrategyResult


@dataclass(frozen=True)
class StrategyReviewEdit:
    persona: str
    strategy_type: str
    comment_template: str
    dm_template: str
    action_plan: str
    priority: int

    def validated(self) -> StrategyResult:
        return StrategyResult.model_validate(
            {
                "schema_version": "1.0",
                "persona": self.persona,
                "strategy_type": self.strategy_type,
                "comment_template": self.comment_template,
                "dm_template": self.dm_template,
                "action_plan": self.action_plan,
                "priority": self.priority,
            }
        )


@dataclass(frozen=True)
class StrategyReviewSnapshot:
    id: int
    job_id: str | None
    user_id: int
    review_status: str
    review_version: int
    persona: str
    strategy_type: str
    comment_template: str
    dm_template: str
    action_plan: str
    priority: int
    reviewed_at: datetime | None
    reviewed_by: str | None
    review_reason: str | None
    updated_at: datetime


@dataclass(frozen=True)
class StrategyReviewPage:
    items: tuple[StrategyReviewSnapshot, ...]
    total: int


@dataclass(frozen=True)
class StrategyReviewMutation:
    applied: bool
    current: StrategyReviewSnapshot | None


@dataclass(frozen=True)
class StrategyReviewBatchResult:
    total: int
    approved: int
    skipped: int
    conflicted: int


def _snapshot(strategy: Strategy) -> StrategyReviewSnapshot:
    return StrategyReviewSnapshot(
        id=strategy.id,
        job_id=strategy.job_id,
        user_id=strategy.user_id,
        review_status=strategy.review_status,
        review_version=strategy.review_version,
        persona=strategy.persona,
        strategy_type=strategy.strategy_type,
        comment_template=strategy.comment_template,
        dm_template=strategy.dm_template,
        action_plan=strategy.action_plan,
        priority=strategy.priority,
        reviewed_at=strategy.reviewed_at,
        reviewed_by=strategy.reviewed_by,
        review_reason=strategy.review_reason,
        updated_at=strategy.updated_at,
    )


class StrategyReviewStore:
    """Short-transaction queries and compare-and-swap mutations."""

    @staticmethod
    def _scope_predicates(*, job_id: str, platform: str):
        return (
            Strategy.job_id == job_id,
            exists(
                select(PipelineJob.id).where(
                    PipelineJob.id == job_id,
                    PipelineJob.platform == platform,
                )
            ),
            exists(
                select(User.id).where(
                    User.id == Strategy.user_id,
                    User.platform == platform,
                )
            ),
            exists(
                select(PipelineJobUser.user_id).where(
                    PipelineJobUser.job_id == job_id,
                    PipelineJobUser.user_id == Strategy.user_id,
                    PipelineJobUser.qualification_status == "qualified",
                )
            ),
        )

    def list_strategies(
        self,
        session: Session,
        *,
        job_id: str,
        platform: str,
        review_status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> StrategyReviewPage:
        predicates = list(self._scope_predicates(job_id=job_id, platform=platform))
        if review_status is not None:
            predicates.append(Strategy.review_status == review_status)
        total = session.scalar(select(func.count(Strategy.id)).where(*predicates))
        rows = session.scalars(
            select(Strategy)
            .where(*predicates)
            .order_by(Strategy.id)
            .limit(limit)
            .offset(offset)
        ).all()
        return StrategyReviewPage(
            items=tuple(_snapshot(row) for row in rows),
            total=int(total or 0),
        )

    def _current(
        self,
        session: Session,
        *,
        job_id: str,
        platform: str,
        strategy_id: int,
        qualified_only: bool = True,
    ) -> StrategyReviewSnapshot | None:
        predicates = [Strategy.id == strategy_id, Strategy.job_id == job_id]
        predicates.append(
            exists(
                select(PipelineJob.id).where(
                    PipelineJob.id == job_id,
                    PipelineJob.platform == platform,
                )
            )
        )
        predicates.append(
            exists(
                select(User.id).where(
                    User.id == Strategy.user_id,
                    User.platform == platform,
                )
            )
        )
        if qualified_only:
            predicates.append(
                exists(
                    select(PipelineJobUser.user_id).where(
                        PipelineJobUser.job_id == job_id,
                        PipelineJobUser.user_id == Strategy.user_id,
                        PipelineJobUser.qualification_status == "qualified",
                    )
                )
            )
        row = session.scalar(
            select(Strategy)
            .where(*predicates)
            .execution_options(populate_existing=True)
        )
        return _snapshot(row) if row is not None else None

    def edit(
        self,
        session: Session,
        *,
        job_id: str,
        platform: str,
        strategy_id: int,
        expected_version: int,
        changes: StrategyReviewEdit,
    ) -> StrategyReviewMutation:
        valid = changes.validated()
        result = session.execute(
            update(Strategy)
            .where(
                Strategy.id == strategy_id,
                Strategy.review_version == expected_version,
                *self._scope_predicates(job_id=job_id, platform=platform),
            )
            .values(
                persona=valid.persona,
                strategy_type=valid.strategy_type,
                comment_template=valid.comment_template,
                dm_template=valid.dm_template,
                action_plan=valid.action_plan,
                priority=valid.priority,
                review_status="draft",
                review_version=Strategy.review_version + 1,
                reviewed_at=None,
                reviewed_by=None,
                review_reason=None,
                updated_at=datetime.utcnow(),
            )
            .execution_options(synchronize_session=False)
        )
        current = self._current(
            session,
            job_id=job_id,
            platform=platform,
            strategy_id=strategy_id,
        )
        return StrategyReviewMutation(applied=bool(result.rowcount), current=current)

    def _review(
        self,
        session: Session,
        *,
        job_id: str,
        platform: str,
        strategy_id: int,
        expected_version: int,
        target_status: str,
        operator: str,
        reason: str | None,
    ) -> StrategyReviewMutation:
        result = session.execute(
            update(Strategy)
            .where(
                Strategy.id == strategy_id,
                Strategy.review_version == expected_version,
                Strategy.review_status == "draft",
                *self._scope_predicates(job_id=job_id, platform=platform),
            )
            .values(
                review_status=target_status,
                review_version=Strategy.review_version + 1,
                reviewed_at=datetime.utcnow(),
                reviewed_by=operator,
                review_reason=reason,
                updated_at=datetime.utcnow(),
            )
            .execution_options(synchronize_session=False)
        )
        current = self._current(
            session,
            job_id=job_id,
            platform=platform,
            strategy_id=strategy_id,
        )
        return StrategyReviewMutation(applied=bool(result.rowcount), current=current)

    def approve(self, session: Session, **kwargs) -> StrategyReviewMutation:
        return self._review(
            session,
            target_status="approved",
            reason=None,
            **kwargs,
        )

    def reject(
        self,
        session: Session,
        *,
        reason: str,
        **kwargs,
    ) -> StrategyReviewMutation:
        return self._review(
            session,
            target_status="rejected",
            reason=reason,
            **kwargs,
        )

    def approve_batch(
        self,
        session: Session,
        *,
        job_id: str,
        platform: str,
        expected_versions: Mapping[int, int],
        operator: str,
    ) -> StrategyReviewBatchResult:
        approved = skipped = conflicted = 0
        for strategy_id, expected_version in expected_versions.items():
            before = self._current(
                session,
                job_id=job_id,
                platform=platform,
                strategy_id=strategy_id,
            )
            if before is None:
                skipped += 1
                continue
            try:
                StrategyResult.model_validate(
                    {
                        "schema_version": "1.0",
                        "persona": before.persona,
                        "strategy_type": before.strategy_type,
                        "comment_template": before.comment_template,
                        "dm_template": before.dm_template,
                        "action_plan": before.action_plan,
                        "priority": before.priority,
                    }
                )
            except ValueError:
                skipped += 1
                continue
            mutation = self.approve(
                session,
                job_id=job_id,
                platform=platform,
                strategy_id=strategy_id,
                expected_version=expected_version,
                operator=operator,
            )
            if mutation.applied:
                approved += 1
            else:
                conflicted += 1
        return StrategyReviewBatchResult(
            total=len(expected_versions),
            approved=approved,
            skipped=skipped,
            conflicted=conflicted,
        )


__all__ = [
    "StrategyReviewBatchResult",
    "StrategyReviewEdit",
    "StrategyReviewMutation",
    "StrategyReviewPage",
    "StrategyReviewSnapshot",
    "StrategyReviewStore",
]
