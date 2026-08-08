"""Persistent pipeline schedules and a small standard five-field cron parser."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy import select, update

from tiktok_bot_core.models.entities import PipelineSchedule
from tiktok_bot_core.storage.database import Database, get_db

logger = logging.getLogger(__name__)

UTC = timezone.utc


@dataclass(frozen=True)
class _CronField:
    values: frozenset[int]
    wildcard: bool


@dataclass(frozen=True)
class CronExpression:
    minute: _CronField
    hour: _CronField
    day: _CronField
    month: _CronField
    weekday: _CronField

    @classmethod
    def parse(cls, expression: str) -> "CronExpression":
        parts = expression.split()
        if len(parts) != 5:
            raise ValueError("Cron expression must contain five fields")
        return cls(
            minute=_parse_field(parts[0], 0, 59),
            hour=_parse_field(parts[1], 0, 23),
            day=_parse_field(parts[2], 1, 31),
            month=_parse_field(parts[3], 1, 12),
            weekday=_parse_field(parts[4], 0, 7, normalize_weekday=True),
        )

    def matches(self, local_time: datetime) -> bool:
        if (
            local_time.minute not in self.minute.values
            or local_time.hour not in self.hour.values
            or local_time.month not in self.month.values
        ):
            return False

        day_matches = local_time.day in self.day.values
        cron_weekday = (local_time.weekday() + 1) % 7
        weekday_matches = cron_weekday in self.weekday.values
        if self.day.wildcard or self.weekday.wildcard:
            return day_matches and weekday_matches
        return day_matches or weekday_matches


def next_cron_run(
    expression: str,
    timezone_name: str,
    after: datetime,
) -> datetime:
    """Return the first UTC minute strictly after ``after``."""

    cron = CronExpression.parse(expression)
    zone = ZoneInfo(timezone_name)
    cursor = _as_utc(after).replace(second=0, microsecond=0) + timedelta(
        minutes=1
    )
    for _ in range(60 * 24 * 366 * 5):
        if cron.matches(cursor.astimezone(zone)):
            return cursor.replace(tzinfo=None)
        cursor += timedelta(minutes=1)
    raise ValueError("Cron expression has no run within five years")


def previous_cron_run(
    expression: str,
    timezone_name: str,
    at_or_before: datetime,
) -> datetime:
    """Return the latest UTC minute at or before ``at_or_before``."""

    cron = CronExpression.parse(expression)
    zone = ZoneInfo(timezone_name)
    cursor = _as_utc(at_or_before).replace(second=0, microsecond=0)
    for _ in range(60 * 24 * 366 * 5):
        if cron.matches(cursor.astimezone(zone)):
            return cursor.replace(tzinfo=None)
        cursor -= timedelta(minutes=1)
    raise ValueError("Cron expression has no run within five years")


class PipelineScheduler:
    """Generate scheduled jobs through the same ``PipelineJobService``."""

    def __init__(
        self,
        *,
        database: Database | None = None,
        job_service,
        poll_interval: float = 1.0,
    ) -> None:
        self.database = database or get_db()
        self.job_service = job_service
        self.poll_interval = poll_interval

    async def tick(self, *, now: datetime | None = None) -> int:
        now = _naive_utc(now or datetime.utcnow())
        schedules = self._due_schedule_snapshots(now)
        created = 0
        for schedule in schedules:
            try:
                latest_run = previous_cron_run(
                    schedule.cron_expression,
                    schedule.timezone,
                    now,
                )
                following_run = next_cron_run(
                    schedule.cron_expression,
                    schedule.timezone,
                    now,
                )
                await self.job_service.preflight_job(
                    platform=schedule.platform,
                    account_mode=schedule.account_mode,
                    account_id=schedule.account_id,
                )
                with self.database.session() as session:
                    claimed = session.execute(
                        update(PipelineSchedule)
                        .where(
                            PipelineSchedule.id == schedule.id,
                            PipelineSchedule.enabled.is_(True),
                            PipelineSchedule.next_run_at
                            == schedule.observed_next_run_at,
                        )
                        .values(
                            last_run_at=latest_run,
                            next_run_at=following_run,
                            updated_at=datetime.utcnow(),
                        )
                        .execution_options(synchronize_session=False)
                    )
                    if claimed.rowcount != 1:
                        continue
                    await self.job_service.create_job(
                        platform=schedule.platform,
                        account_mode=schedule.account_mode,
                        account_id=schedule.account_id,
                        stages=schedule.stages,
                        trigger_type="schedule",
                        schedule_id=schedule.id,
                        config_snapshot=schedule.config,
                        _session=session,
                        _preflighted=True,
                    )
            except Exception:
                logger.exception(
                    "Could not create job for pipeline schedule %s",
                    schedule.id,
                )
                continue
            created += 1
        return created

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Pipeline scheduler poll failed")
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=self.poll_interval,
                )
            except asyncio.TimeoutError:
                pass

    def _due_schedule_snapshots(
        self,
        now: datetime,
    ) -> list[SimpleNamespace]:
        snapshots: list[SimpleNamespace] = []
        with self.database.session() as session:
            schedules = list(
                session.scalars(
                    select(PipelineSchedule)
                    .where(PipelineSchedule.enabled.is_(True))
                    .order_by(PipelineSchedule.id.asc())
                )
            )
            for schedule in schedules:
                if schedule.next_run_at is None:
                    try:
                        schedule.next_run_at = next_cron_run(
                            schedule.cron_expression,
                            schedule.timezone,
                            now,
                        )
                    except Exception:
                        logger.exception(
                            "Invalid pipeline schedule %s",
                            schedule.id,
                        )
                    continue
                if schedule.next_run_at > now:
                    continue
                snapshots.append(
                    SimpleNamespace(
                        id=schedule.id,
                        platform=schedule.platform,
                        account_mode=schedule.account_mode,
                        account_id=schedule.account_id,
                        stages=list(schedule.stages_json or []),
                        cron_expression=schedule.cron_expression,
                        timezone=schedule.timezone,
                        config=dict(schedule.config_json or {}),
                        observed_next_run_at=schedule.next_run_at,
                    )
                )
        return snapshots


def _parse_field(
    text: str,
    minimum: int,
    maximum: int,
    *,
    normalize_weekday: bool = False,
) -> _CronField:
    wildcard = text == "*"
    values: set[int] = set()
    for component in text.split(","):
        if not component:
            raise ValueError(f"Invalid cron field: {text}")
        base, separator, step_text = component.partition("/")
        step = int(step_text) if separator else 1
        if step <= 0:
            raise ValueError("Cron step must be positive")

        if base == "*":
            start, end = minimum, maximum
        elif "-" in base:
            start_text, end_text = base.split("-", 1)
            start, end = int(start_text), int(end_text)
        else:
            start = end = int(base)
        if start < minimum or end > maximum or start > end:
            raise ValueError(f"Cron value out of range: {component}")
        values.update(range(start, end + 1, step))

    if normalize_weekday:
        values = {0 if value == 7 else value for value in values}
    return _CronField(frozenset(values), wildcard)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _naive_utc(value: datetime) -> datetime:
    return _as_utc(value).replace(tzinfo=None)


__all__ = [
    "CronExpression",
    "PipelineScheduler",
    "next_cron_run",
    "previous_cron_run",
]
