"""Stage 04 queue domain service; this module never calls a Channel."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType

from sqlalchemy import select
from sqlalchemy.orm import Session

from tiktok_bot_core.models.entities import (
    Message,
    PipelineJob,
    PipelineJobUser,
    Strategy,
    User,
)
from tiktok_bot_core.services.acquisition_agents import StrategyResult
from tiktok_bot_core.storage.database import Database, get_db
from tiktok_bot_core.storage.outreach_queue_store import (
    OutreachItemPage,
    OutreachItemSnapshot,
    OutreachMutation,
    OutreachQueueStore,
)


ERROR_MESSAGES = MappingProxyType(
    {
        "job_not_found": "The pipeline job was not found.",
        "platform_mismatch": "The requested platform does not match the job.",
        "invalid_error_code": "The outreach result code is not registered.",
        "invalid_operator": "The outreach operator is invalid.",
        "item_not_found": "The outreach item was not found in this job.",
        "job_not_executable": "The pipeline job cannot execute outreach.",
        "message_scope_mismatch": "The message does not belong to this outreach item.",
        "strategy_changed": "The approved strategy changed before execution.",
        "strategy_not_approved": "The strategy is no longer approved.",
        "candidate_not_qualified": "The candidate is no longer qualified.",
        "candidate_platform_changed": "The candidate platform no longer matches.",
        "invalid_strategy": "The approved strategy is not safe to execute.",
        "manual_skip": "The outreach item was skipped by an operator.",
        "decision_timeout": "The outreach authorization timed out safely.",
        "job_cancelled": "The job was cancelled before outreach.",
        "daily_limit_reached": "The daily outreach limit was reached.",
        "account_unavailable": "The sending account is unavailable.",
        "channel_failed": "The platform rejected the outreach action.",
        "channel_uncertain": "The platform outcome could not be confirmed.",
    }
)


class OutreachQueueError(RuntimeError):
    def __init__(self, code: str) -> None:
        if code not in ERROR_MESSAGES:
            code = "invalid_error_code"
        self.code = code
        super().__init__(ERROR_MESSAGES[code])


_OPERATOR_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]{0,63}$")


def _validated_operator(operator: str) -> str:
    value = operator.strip() if isinstance(operator, str) else ""
    folded = value.casefold()
    if (
        not _OPERATOR_PATTERN.fullmatch(value)
        or folded.startswith(("sk-", "api-key", "bearer-"))
        or any(marker in folded for marker in ("password", "secret"))
    ):
        raise OutreachQueueError("invalid_operator")
    return value


@dataclass(frozen=True)
class PrepareResult:
    created: int
    total: int


@dataclass(frozen=True)
class AuthorizeResult:
    ready: int
    skipped: int


@dataclass(frozen=True)
class SkipResult:
    skipped: int


@dataclass(frozen=True)
class CancelResult:
    cancelled: int


@dataclass(frozen=True)
class OutreachSummary:
    total: int
    by_status: dict[str, int]
    by_channel: dict[str, int]


class OutreachQueueService:
    def __init__(
        self,
        database: Database | None = None,
        *,
        store: OutreachQueueStore | None = None,
    ) -> None:
        self.database = database or get_db()
        self.store = store or OutreachQueueStore()

    @staticmethod
    def _require_job(
        session: Session,
        *,
        job_id: str,
        platform: str,
        executable: bool = False,
    ) -> PipelineJob:
        job = session.get(PipelineJob, job_id)
        if job is None:
            raise OutreachQueueError("job_not_found")
        if job.platform != platform:
            raise OutreachQueueError("platform_mismatch")
        if executable and job.status not in {
            "queued",
            "running",
            "waiting_decision",
        }:
            raise OutreachQueueError("job_not_executable")
        return job

    @staticmethod
    def _validated_strategy(strategy: Strategy) -> StrategyResult | None:
        try:
            return StrategyResult.model_validate(
                {
                    "schema_version": "1.0",
                    "persona": strategy.persona,
                    "strategy_type": strategy.strategy_type,
                    "comment_template": strategy.comment_template,
                    "dm_template": strategy.dm_template,
                    "action_plan": strategy.action_plan,
                    "priority": strategy.priority,
                }
            )
        except (TypeError, ValueError):
            return None

    def _item_error(
        self, session: Session, *, item: OutreachItemSnapshot, platform: str
    ) -> str | None:
        strategy = session.get(Strategy, item.strategy_id)
        if (
            strategy is None
            or strategy.job_id != item.job_id
            or strategy.user_id != item.user_id
        ):
            return "strategy_changed"
        if strategy.review_status != "approved":
            return "strategy_not_approved"
        if strategy.review_version != item.strategy_review_version:
            return "strategy_changed"
        user = session.get(User, item.user_id)
        if (
            user is None
            or user.platform != platform
            or user.username != item.target_username
        ):
            return "candidate_platform_changed"
        member = session.get(PipelineJobUser, (item.job_id, item.user_id))
        if member is None or member.qualification_status != "qualified":
            return "candidate_not_qualified"
        valid = self._validated_strategy(strategy)
        if valid is None:
            return "invalid_strategy"
        current_content = (
            valid.comment_template if item.channel == "comment" else valid.dm_template
        )
        if not current_content.strip() or current_content != item.content:
            return "strategy_changed"
        return None

    def prepare(self, *, job_id: str, platform: str) -> PrepareResult:
        created = 0
        with self.database.session() as session:
            self._require_job(
                session, job_id=job_id, platform=platform, executable=True
            )
            rows = session.execute(
                select(Strategy, User)
                .join(User, User.id == Strategy.user_id)
                .join(
                    PipelineJobUser,
                    (PipelineJobUser.job_id == Strategy.job_id)
                    & (PipelineJobUser.user_id == Strategy.user_id),
                )
                .where(
                    Strategy.job_id == job_id,
                    Strategy.review_status == "approved",
                    User.platform == platform,
                    PipelineJobUser.qualification_status == "qualified",
                )
                .order_by(Strategy.id)
            ).all()
            for strategy, user in rows:
                valid = self._validated_strategy(strategy)
                if valid is None:
                    continue
                for channel, content in (
                    ("comment", valid.comment_template),
                    ("dm", valid.dm_template),
                ):
                    if not content.strip():
                        continue
                    created += int(
                        self.store.insert_pending(
                            session,
                            job_id=job_id,
                            strategy_id=strategy.id,
                            user_id=user.id,
                            strategy_review_version=strategy.review_version,
                            platform=platform,
                            channel=channel,
                            target_username=user.username,
                            content=content,
                        )
                    )
            page = self.store.list_items(
                session, job_id=job_id, platform=platform, limit=100000
            )
            for item in page.items:
                if item.status not in {"pending_approval", "ready"}:
                    continue
                error = self._item_error(session, item=item, platform=platform)
                if error is not None:
                    self.store.transition(
                        session,
                        item_id=item.id,
                        from_statuses=("pending_approval", "ready"),
                        to_status="skipped",
                        values={"finished_at": datetime.utcnow(), "error_code": error},
                    )
            total = page.total
        return PrepareResult(created=created, total=total)

    def list_items(self, *, job_id: str, platform: str, **kwargs) -> OutreachItemPage:
        with self.database.session() as session:
            self._require_job(session, job_id=job_id, platform=platform)
            return self.store.list_items(
                session, job_id=job_id, platform=platform, **kwargs
            )

    def summary(self, *, job_id: str, platform: str) -> OutreachSummary:
        page = self.list_items(
            job_id=job_id, platform=platform, limit=100000, offset=0
        )
        by_status: dict[str, int] = {}
        by_channel: dict[str, int] = {}
        for item in page.items:
            by_status[item.status] = by_status.get(item.status, 0) + 1
            by_channel[item.channel] = by_channel.get(item.channel, 0) + 1
        return OutreachSummary(page.total, by_status, by_channel)

    def authorize(
        self, *, job_id: str, platform: str, operator: str = "system"
    ) -> AuthorizeResult:
        operator = _validated_operator(operator)
        ready = skipped = 0
        with self.database.session() as session:
            self._require_job(
                session, job_id=job_id, platform=platform, executable=True
            )
            page = self.store.list_items(
                session,
                job_id=job_id,
                platform=platform,
                status="pending_approval",
                limit=100000,
            )
            for item in page.items:
                error = self._item_error(session, item=item, platform=platform)
                if error is None:
                    mutation = self.store.transition(
                        session,
                        item_id=item.id,
                        from_statuses=("pending_approval",),
                        to_status="ready",
                        values={
                            "authorized_at": datetime.utcnow(),
                            "authorized_by": operator,
                            "error_code": None,
                        },
                    )
                    ready += int(mutation.applied)
                else:
                    mutation = self.store.transition(
                        session,
                        item_id=item.id,
                        from_statuses=("pending_approval",),
                        to_status="skipped",
                        values={"finished_at": datetime.utcnow(), "error_code": error},
                    )
                    skipped += int(mutation.applied)
        return AuthorizeResult(ready=ready, skipped=skipped)

    @staticmethod
    def _validated_error_code(error_code: str) -> str:
        if error_code not in ERROR_MESSAGES:
            raise OutreachQueueError("invalid_error_code")
        return error_code

    def skip(
        self,
        *,
        job_id: str,
        platform: str,
        error_code: str = "manual_skip",
    ) -> SkipResult:
        error_code = self._validated_error_code(error_code)
        return SkipResult(
            skipped=self._bulk_terminal(
                job_id=job_id,
                platform=platform,
                to_status="skipped",
                error_code=error_code,
            )
        )

    def cancel(self, *, job_id: str, platform: str) -> CancelResult:
        return CancelResult(
            cancelled=self._bulk_terminal(
                job_id=job_id,
                platform=platform,
                to_status="cancelled",
                error_code="job_cancelled",
            )
        )

    def _bulk_terminal(
        self, *, job_id: str, platform: str, to_status: str, error_code: str
    ) -> int:
        changed = 0
        with self.database.session() as session:
            self._require_job(session, job_id=job_id, platform=platform)
            page = self.store.list_items(
                session, job_id=job_id, platform=platform, limit=100000
            )
            for item in page.items:
                mutation = self.store.transition(
                    session,
                    item_id=item.id,
                    from_statuses=("pending_approval", "ready"),
                    to_status=to_status,
                    values={
                        "finished_at": datetime.utcnow(),
                        "error_code": error_code,
                    },
                )
                changed += int(mutation.applied)
        return changed

    def claim_next(
        self, *, job_id: str, platform: str
    ) -> OutreachItemSnapshot | None:
        with self.database.session() as session:
            self._require_job(session, job_id=job_id, platform=platform)
            return self.store.claim_next(session, job_id=job_id, platform=platform)

    def finalize(
        self,
        *,
        job_id: str,
        platform: str,
        item_id: int,
        outcome: str,
        message_id: int | None,
        error_code: str | None = None,
    ) -> OutreachMutation:
        if outcome not in {"sent", "failed", "uncertain"}:
            raise OutreachQueueError("invalid_error_code")
        if outcome == "sent" and message_id is None:
            raise OutreachQueueError("message_scope_mismatch")
        if outcome != "sent":
            default_code = (
                "channel_uncertain" if outcome == "uncertain" else "channel_failed"
            )
            error_code = self._validated_error_code(error_code or default_code)
        elif error_code is not None:
            raise OutreachQueueError("invalid_error_code")
        with self.database.session() as session:
            self._require_job(session, job_id=job_id, platform=platform)
            current = self.store.current(session, item_id=item_id)
            if (
                current is None
                or current.job_id != job_id
                or current.platform != platform
            ):
                raise OutreachQueueError("item_not_found")
            if message_id is not None:
                message = session.get(Message, message_id)
                if (
                    message is None
                    or message.job_id != current.job_id
                    or message.user_id != current.user_id
                    or message.message_type != current.channel
                ):
                    raise OutreachQueueError("message_scope_mismatch")
            return self.store.transition(
                session,
                item_id=item_id,
                from_statuses=("sending",),
                to_status=outcome,
                values={
                    "message_id": message_id,
                    "finished_at": datetime.utcnow(),
                    "error_code": error_code,
                },
            )


__all__ = [
    "ERROR_MESSAGES",
    "AuthorizeResult",
    "CancelResult",
    "OutreachQueueError",
    "OutreachQueueService",
    "OutreachSummary",
    "PrepareResult",
    "SkipResult",
]
