"""Trusted mutation boundary for Stage 03 human strategy review."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

from sqlalchemy.orm import Session

from tiktok_bot_core.models.entities import (
    PipelineJob,
    PipelineJobUser,
    Strategy,
    StrategyReviewAudit,
    User,
)
from tiktok_bot_core.services.acquisition_agents import StrategyResult
from tiktok_bot_core.storage.database import Database, get_db
from tiktok_bot_core.storage.strategy_review_store import (
    StrategyReviewBatchResult,
    StrategyReviewEdit,
    StrategyReviewMutation,
    StrategyReviewSnapshot,
    StrategyReviewStore,
)


ERROR_MESSAGES = {
    "job_not_found": "Pipeline job was not found.",
    "strategy_not_found": "Strategy was not found.",
    "strategy_scope_mismatch": "Strategy is outside this pipeline job.",
    "platform_mismatch": "Strategy platform does not match the pipeline job.",
    "candidate_not_qualified": "Candidate is not qualified for strategy review.",
    "legacy_strategy": "Legacy strategy cannot be reviewed.",
    "invalid_operator": "Review operator is invalid.",
    "invalid_reason": "Review reason is invalid.",
    "invalid_version": "Review version is invalid.",
    "strategy_conflict": "Strategy changed before this review was applied.",
    "invalid_strategy": "Strategy content failed safety validation.",
}

_OPERATOR_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]{0,63}$")
_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")
_CREDENTIAL_MARKER = re.compile(
    r"(?:\bsk-[A-Za-z0-9_-]{8,}|\b(?:api[_ -]?key|bearer|password|secret)\b)",
    re.IGNORECASE,
)


class StrategyReviewError(RuntimeError):
    """Stable public domain failure, optionally carrying current authority."""

    def __init__(
        self,
        code: str,
        *,
        current: StrategyReviewSnapshot | None = None,
    ) -> None:
        if code not in ERROR_MESSAGES:
            code = "invalid_strategy"
        self.code = code
        self.public_message = ERROR_MESSAGES[code]
        self.current = current
        super().__init__(self.public_message)


def _validated_operator(operator: str) -> str:
    value = operator.strip() if isinstance(operator, str) else ""
    folded = value.casefold()
    if (
        not _OPERATOR_PATTERN.fullmatch(value)
        or folded.startswith(("sk-", "api-key", "bearer-"))
    ):
        raise StrategyReviewError("invalid_operator")
    return value


def _validated_reason(reason: str) -> str:
    value = reason.strip() if isinstance(reason, str) else ""
    if (
        not value
        or len(value) > 500
        or _CONTROL_CHARACTER.search(value)
        or _CREDENTIAL_MARKER.search(value)
    ):
        raise StrategyReviewError("invalid_reason")
    return value


def _validated_version(version: int) -> int:
    if isinstance(version, bool) or not isinstance(version, int) or version < 0:
        raise StrategyReviewError("invalid_version")
    return version


@dataclass(frozen=True)
class _Scope:
    strategy: Strategy
    before: StrategyReviewSnapshot


class StrategyReviewService:
    """Perform review mutations with scope checks and atomic audit records."""

    def __init__(
        self,
        database: Database | None = None,
        *,
        store: StrategyReviewStore | None = None,
    ) -> None:
        self.database = database or get_db()
        self.store = store or StrategyReviewStore()

    def _scope(
        self,
        session: Session,
        *,
        job_id: str,
        platform: str,
        strategy_id: int,
    ) -> _Scope:
        job = session.get(PipelineJob, job_id)
        if job is None:
            raise StrategyReviewError("job_not_found")
        strategy = session.get(Strategy, strategy_id)
        if strategy is None:
            raise StrategyReviewError("strategy_not_found")
        if strategy.job_id is None:
            raise StrategyReviewError("legacy_strategy")
        if strategy.job_id != job_id:
            raise StrategyReviewError("strategy_scope_mismatch")
        user = session.get(User, strategy.user_id)
        if job.platform != platform or user is None or user.platform != platform:
            raise StrategyReviewError("platform_mismatch")
        membership = session.get(PipelineJobUser, (job_id, strategy.user_id))
        if membership is None or membership.qualification_status != "qualified":
            raise StrategyReviewError("candidate_not_qualified")
        before = self.store._current(
            session,
            job_id=job_id,
            platform=platform,
            strategy_id=strategy_id,
        )
        if before is None:
            raise StrategyReviewError("candidate_not_qualified")
        return _Scope(strategy=strategy, before=before)

    @staticmethod
    def _validate_current_strategy(before: StrategyReviewSnapshot) -> None:
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
        except (TypeError, ValueError):
            raise StrategyReviewError("invalid_strategy") from None

    @staticmethod
    def _audit(
        session: Session,
        *,
        before: StrategyReviewSnapshot,
        after: StrategyReviewSnapshot,
        action: str,
        operator: str,
        reason: str | None,
    ) -> None:
        session.add(
            StrategyReviewAudit(
                job_id=after.job_id,
                strategy_id=after.id,
                user_id=after.user_id,
                action=action,
                before_status=before.review_status,
                after_status=after.review_status,
                before_version=before.review_version,
                after_version=after.review_version,
                operator=operator,
                reason=reason,
            )
        )

    @staticmethod
    def _require_applied(mutation: StrategyReviewMutation) -> None:
        if not mutation.applied:
            raise StrategyReviewError(
                "strategy_conflict",
                current=mutation.current,
            )

    def edit(
        self,
        *,
        job_id: str,
        platform: str,
        strategy_id: int,
        expected_version: int,
        changes: StrategyReviewEdit,
        operator: str,
    ) -> StrategyReviewMutation:
        operator = _validated_operator(operator)
        expected_version = _validated_version(expected_version)
        try:
            changes.validated()
        except (TypeError, ValueError) as exc:
            raise StrategyReviewError("invalid_strategy") from None
        with self.database.session() as session:
            scope = self._scope(
                session,
                job_id=job_id,
                platform=platform,
                strategy_id=strategy_id,
            )
            mutation = self.store.edit(
                session,
                job_id=job_id,
                platform=platform,
                strategy_id=strategy_id,
                expected_version=expected_version,
                changes=changes,
            )
            self._require_applied(mutation)
            self._audit(
                session,
                before=scope.before,
                after=mutation.current,
                action="edit",
                operator=operator,
                reason=None,
            )
            return mutation

    def _review(
        self,
        *,
        job_id: str,
        platform: str,
        strategy_id: int,
        expected_version: int,
        operator: str,
        action: str,
        reason: str | None,
    ) -> StrategyReviewMutation:
        operator = _validated_operator(operator)
        expected_version = _validated_version(expected_version)
        if action == "reject":
            reason = _validated_reason(reason)
        with self.database.session() as session:
            scope = self._scope(
                session,
                job_id=job_id,
                platform=platform,
                strategy_id=strategy_id,
            )
            self._validate_current_strategy(scope.before)
            kwargs = dict(
                job_id=job_id,
                platform=platform,
                strategy_id=strategy_id,
                expected_version=expected_version,
                operator=operator,
            )
            mutation = (
                self.store.approve(session, **kwargs)
                if action == "approve"
                else self.store.reject(session, reason=reason, **kwargs)
            )
            self._require_applied(mutation)
            self._audit(
                session,
                before=scope.before,
                after=mutation.current,
                action=action,
                operator=operator,
                reason=reason,
            )
            return mutation

    def approve(self, **kwargs) -> StrategyReviewMutation:
        return self._review(action="approve", reason=None, **kwargs)

    def reject(self, *, reason: str, **kwargs) -> StrategyReviewMutation:
        return self._review(action="reject", reason=reason, **kwargs)

    def approve_batch(
        self,
        *,
        job_id: str,
        platform: str,
        expected_versions: Mapping[int, int],
        operator: str,
    ) -> StrategyReviewBatchResult:
        operator = _validated_operator(operator)
        approved = skipped = conflicted = 0
        for strategy_id, expected_version in expected_versions.items():
            try:
                self.approve(
                    job_id=job_id,
                    platform=platform,
                    strategy_id=strategy_id,
                    expected_version=expected_version,
                    operator=operator,
                )
                approved += 1
            except StrategyReviewError as exc:
                if exc.code == "strategy_conflict":
                    conflicted += 1
                else:
                    skipped += 1
        return StrategyReviewBatchResult(
            total=len(expected_versions),
            approved=approved,
            skipped=skipped,
            conflicted=conflicted,
        )


__all__ = [
    "ERROR_MESSAGES",
    "StrategyReviewError",
    "StrategyReviewService",
]
