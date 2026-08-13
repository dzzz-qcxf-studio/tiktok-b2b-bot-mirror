"""Short-transaction persistence primitives for the Stage 04 queue."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from tiktok_bot_core.models.entities import OutreachItem, PipelineJob


@dataclass(frozen=True)
class OutreachItemSnapshot:
    id: int
    job_id: str
    strategy_id: int
    user_id: int
    strategy_review_version: int
    platform: str
    channel: str
    target_username: str
    content: str
    status: str
    message_id: int | None
    authorized_at: datetime | None
    authorized_by: str | None
    claimed_at: datetime | None
    finished_at: datetime | None
    error_code: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class OutreachItemPage:
    items: tuple[OutreachItemSnapshot, ...]
    total: int


@dataclass(frozen=True)
class OutreachMutation:
    applied: bool
    current: OutreachItemSnapshot | None


def _snapshot(item: OutreachItem) -> OutreachItemSnapshot:
    return OutreachItemSnapshot(
        id=item.id,
        job_id=item.job_id,
        strategy_id=item.strategy_id,
        user_id=item.user_id,
        strategy_review_version=item.strategy_review_version,
        platform=item.platform,
        channel=item.channel,
        target_username=item.target_username,
        content=item.content,
        status=item.status,
        message_id=item.message_id,
        authorized_at=item.authorized_at,
        authorized_by=item.authorized_by,
        claimed_at=item.claimed_at,
        finished_at=item.finished_at,
        error_code=item.error_code,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


class OutreachQueueStore:
    """Persist queue items without performing any external platform action."""

    @staticmethod
    def _scope(*, job_id: str, platform: str):
        return (
            OutreachItem.job_id == job_id,
            OutreachItem.platform == platform,
            select(PipelineJob.id)
            .where(PipelineJob.id == job_id, PipelineJob.platform == platform)
            .exists(),
        )

    def insert_pending(
        self,
        session: Session,
        *,
        job_id: str,
        strategy_id: int,
        user_id: int,
        strategy_review_version: int,
        platform: str,
        channel: str,
        target_username: str,
        content: str,
    ) -> bool:
        existing = session.scalar(
            select(OutreachItem).where(
                OutreachItem.job_id == job_id,
                OutreachItem.strategy_id == strategy_id,
                OutreachItem.channel == channel,
            )
        )
        if existing is not None:
            snapshot_changed = (
                existing.strategy_review_version != strategy_review_version
                or existing.user_id != user_id
                or existing.platform != platform
                or existing.target_username != target_username
                or existing.content != content
            )
            if snapshot_changed and existing.status in {
                "pending_approval",
                "ready",
                "skipped",
            } and existing.message_id is None:
                session.execute(
                    update(OutreachItem)
                    .where(
                        OutreachItem.id == existing.id,
                        OutreachItem.status.in_(
                            ("pending_approval", "ready", "skipped")
                        ),
                        OutreachItem.message_id.is_(None),
                    )
                    .values(
                        user_id=user_id,
                        strategy_review_version=strategy_review_version,
                        platform=platform,
                        target_username=target_username,
                        content=content,
                        status="pending_approval",
                        authorized_at=None,
                        authorized_by=None,
                        claimed_at=None,
                        finished_at=None,
                        error_code=None,
                        updated_at=datetime.utcnow(),
                    )
                    .execution_options(synchronize_session=False)
                )
            return False
        result = session.execute(
            sqlite_insert(OutreachItem)
            .values(
                job_id=job_id,
                strategy_id=strategy_id,
                user_id=user_id,
                strategy_review_version=strategy_review_version,
                platform=platform,
                channel=channel,
                target_username=target_username,
                content=content,
                status="pending_approval",
            )
            .on_conflict_do_nothing(
                index_elements=["job_id", "strategy_id", "channel"]
            )
        )
        return bool(result.rowcount)

    def list_items(
        self,
        session: Session,
        *,
        job_id: str,
        platform: str,
        status: str | None = None,
        channel: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> OutreachItemPage:
        predicates = list(self._scope(job_id=job_id, platform=platform))
        if status is not None:
            predicates.append(OutreachItem.status == status)
        if channel is not None:
            predicates.append(OutreachItem.channel == channel)
        total = session.scalar(select(func.count(OutreachItem.id)).where(*predicates))
        rows = session.scalars(
            select(OutreachItem)
            .where(*predicates)
            .order_by(OutreachItem.id)
            .limit(limit)
            .offset(offset)
        ).all()
        return OutreachItemPage(
            items=tuple(_snapshot(row) for row in rows), total=int(total or 0)
        )

    def current(self, session: Session, *, item_id: int) -> OutreachItemSnapshot | None:
        row = session.scalar(
            select(OutreachItem)
            .where(OutreachItem.id == item_id)
            .execution_options(populate_existing=True)
        )
        return _snapshot(row) if row is not None else None

    def transition(
        self,
        session: Session,
        *,
        item_id: int,
        from_statuses: tuple[str, ...],
        to_status: str,
        values: dict | None = None,
    ) -> OutreachMutation:
        changed = {"status": to_status, "updated_at": datetime.utcnow()}
        changed.update(values or {})
        result = session.execute(
            update(OutreachItem)
            .where(
                OutreachItem.id == item_id,
                OutreachItem.status.in_(from_statuses),
            )
            .values(**changed)
            .execution_options(synchronize_session=False)
        )
        return OutreachMutation(
            applied=bool(result.rowcount), current=self.current(session, item_id=item_id)
        )

    def claim_next(
        self, session: Session, *, job_id: str, platform: str
    ) -> OutreachItemSnapshot | None:
        candidate_id = session.scalar(
            select(OutreachItem.id)
            .where(
                *self._scope(job_id=job_id, platform=platform),
                OutreachItem.status == "ready",
            )
            .order_by(OutreachItem.id)
            .limit(1)
        )
        if candidate_id is None:
            return None
        mutation = self.transition(
            session,
            item_id=candidate_id,
            from_statuses=("ready",),
            to_status="sending",
            values={"claimed_at": datetime.utcnow(), "error_code": None},
        )
        return mutation.current if mutation.applied else None


__all__ = [
    "OutreachItemPage",
    "OutreachItemSnapshot",
    "OutreachMutation",
    "OutreachQueueStore",
]
