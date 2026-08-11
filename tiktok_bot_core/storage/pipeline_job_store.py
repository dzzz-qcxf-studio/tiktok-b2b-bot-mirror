"""Durable storage operations for the unified pipeline job system."""

from __future__ import annotations

from datetime import datetime
import math
from typing import Any, Iterable

from sqlalchemy import exists, func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

from tiktok_bot_core.models.entities import (
    PipelineDecisionCheckpoint,
    PipelineJob,
    PipelineJobStage,
    PipelineJobUser,
    User,
)
from tiktok_bot_core.models.pipeline_states import (
    JOB_STATUSES,
    JOB_STATUS_CANCELLED,
    JOB_STATUS_CANCELLING,
    JOB_STATUS_FAILED,
    JOB_STATUS_INTERRUPTED,
    JOB_STATUS_PARTIAL_FAILED,
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    JOB_STATUS_WAITING_DECISION,
    KNOWN_PIPELINE_STAGES,
    STAGE_STATUSES,
    STAGE_STATUS_CANCELLED,
    STAGE_STATUS_FAILED,
    STAGE_STATUS_PENDING,
    STAGE_STATUS_RUNNING,
    STAGE_STATUS_SKIPPED,
    STAGE_STATUS_SUCCEEDED,
    STAGE_STATUS_WAITING_DECISION,
    TERMINAL_JOB_STATUSES,
    TERMINAL_STAGE_STATUSES,
    QUALIFICATION_STATUS_MANUAL_REVIEW,
    QUALIFICATION_STATUS_NEED_ENRICHMENT,
    QUALIFICATION_STATUS_QUALIFIED,
    QUALIFICATION_STATUS_REJECTED,
    validate_job_status,
    validate_job_user_status,
    legacy_job_user_status,
    validate_discovery_status,
    validate_qualification_status,
    validate_job_transition,
    validate_pipeline_stage,
    validate_stage_transition,
)


class PipelineJobStore:
    """Persistence boundary shared by API, scheduler, dispatcher, and runner."""

    def create_job(
        self,
        session: Session,
        *,
        platform: str,
        account_mode: str,
        account_id: int | None,
        stages: Iterable[str],
        trigger_type: str = "manual",
        schedule_id: int | None = None,
        priority: int = 100,
        config_snapshot: dict[str, Any] | None = None,
        retry_of_job_id: str | None = None,
    ) -> PipelineJob:
        ordered_stages = list(stages)
        if not ordered_stages:
            raise ValueError("Pipeline job must contain at least one stage")
        if len(set(ordered_stages)) != len(ordered_stages):
            raise ValueError("Pipeline job stages must not contain duplicates")
        unknown_stages = set(ordered_stages) - KNOWN_PIPELINE_STAGES
        if unknown_stages:
            names = ", ".join(sorted(unknown_stages))
            raise ValueError(f"Unknown pipeline stages: {names}")

        job = PipelineJob(
            platform=platform,
            account_mode=account_mode,
            account_id=account_id,
            stages_json=ordered_stages,
            trigger_type=trigger_type,
            schedule_id=schedule_id,
            priority=priority,
            config_snapshot_json=dict(config_snapshot or {}),
            retry_of_job_id=retry_of_job_id,
            status=JOB_STATUS_QUEUED,
        )
        job.stages = [
            PipelineJobStage(
                stage=stage,
                stage_order=stage_order,
                status=STAGE_STATUS_PENDING,
            )
            for stage_order, stage in enumerate(ordered_stages)
        ]
        session.add(job)
        session.flush()
        return job

    def get_job(self, session: Session, job_id: str) -> PipelineJob | None:
        return session.get(PipelineJob, job_id)

    def list_jobs(
        self,
        session: Session,
        *,
        platform: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[PipelineJob]:
        statement = select(PipelineJob)
        if platform is not None:
            statement = statement.where(PipelineJob.platform == platform)
        if status is not None:
            validate_job_status(status)
            statement = statement.where(PipelineJob.status == status)
        statement = (
            statement.order_by(
                PipelineJob.created_at.desc(),
                PipelineJob.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        return list(session.scalars(statement))

    def count_jobs(
        self,
        session: Session,
        *,
        platform: str | None = None,
        status: str | None = None,
    ) -> int:
        """Count all jobs matching list filters, without pagination."""

        statement = select(func.count(PipelineJob.id))
        if platform is not None:
            statement = statement.where(PipelineJob.platform == platform)
        if status is not None:
            validate_job_status(status)
            statement = statement.where(PipelineJob.status == status)
        return int(session.scalar(statement) or 0)

    def claim_next_job(
        self,
        session: Session,
        *,
        platforms: set[str],
    ) -> PipelineJob | None:
        if not platforms:
            return None
        validate_job_transition(JOB_STATUS_QUEUED, JOB_STATUS_RUNNING)

        now = datetime.utcnow()
        candidate_id = (
            select(PipelineJob.id)
            .where(
                PipelineJob.status == JOB_STATUS_QUEUED,
                PipelineJob.platform.in_(sorted(platforms)),
            )
            .order_by(
                PipelineJob.priority.asc(),
                PipelineJob.queued_at.asc(),
                PipelineJob.created_at.asc(),
                PipelineJob.id.asc(),
            )
            .limit(1)
            .scalar_subquery()
        )
        statement = (
            update(PipelineJob)
            .where(
                PipelineJob.id == candidate_id,
                PipelineJob.status == JOB_STATUS_QUEUED,
            )
            .values(
                status=JOB_STATUS_RUNNING,
                started_at=now,
                updated_at=now,
            )
            .returning(PipelineJob.id)
            .execution_options(synchronize_session=False)
        )
        job_id = session.execute(statement).scalar_one_or_none()
        if job_id is None:
            return None

        session.expire_all()
        return session.get(PipelineJob, job_id)

    def claim_job(
        self,
        session: Session,
        job_id: str,
        *,
        account_id: int,
    ) -> bool:
        """CAS-claim one known queued job and persist its selected account."""

        validate_job_transition(JOB_STATUS_QUEUED, JOB_STATUS_RUNNING)
        now = datetime.utcnow()
        statement = (
            update(PipelineJob)
            .where(
                PipelineJob.id == job_id,
                PipelineJob.status == JOB_STATUS_QUEUED,
            )
            .values(
                status=JOB_STATUS_RUNNING,
                account_id=account_id,
                started_at=now,
                updated_at=now,
            )
            .returning(PipelineJob.id)
            .execution_options(synchronize_session=False)
        )
        updated_id = session.execute(statement).scalar_one_or_none()
        session.flush()
        session.expire_all()
        return updated_id is not None

    def set_job_status(
        self,
        session: Session,
        job_id: str,
        status: str,
        *,
        expected_statuses: Iterable[str] | None = None,
        **timestamps: Any,
    ) -> bool:
        validate_job_status(status)
        if status == JOB_STATUS_WAITING_DECISION:
            raise ValueError(
                "Use pause_for_decision to enter decision waiting"
            )

        allowed_updates = {
            "account_id",
            "current_stage",
            "error_summary",
            "queued_at",
            "started_at",
            "finished_at",
        }
        unexpected = set(timestamps) - allowed_updates
        if unexpected:
            names = ", ".join(sorted(unexpected))
            raise ValueError(f"Unsupported pipeline job fields: {names}")

        if expected_statuses is None:
            current_status = session.scalar(
                select(PipelineJob.status).where(PipelineJob.id == job_id)
            )
            if current_status is None:
                return False
            expected = {current_status}
        else:
            expected = set(expected_statuses)
            if not expected:
                return False

        if (
            status == JOB_STATUS_RUNNING
            and JOB_STATUS_WAITING_DECISION in expected
        ):
            raise ValueError(
                "Use resume_from_decision to leave decision waiting"
            )

        for current_status in expected:
            validate_job_transition(current_status, status)

        values = {
            "status": status,
            "updated_at": datetime.utcnow(),
            **timestamps,
        }
        statement = (
            update(PipelineJob)
            .where(
                PipelineJob.id == job_id,
                PipelineJob.status.in_(expected),
            )
            .values(**values)
            .returning(PipelineJob.id)
            .execution_options(synchronize_session=False)
        )
        updated_id = session.execute(statement).scalar_one_or_none()
        session.flush()
        session.expire_all()
        return updated_id is not None

    def start_stage(
        self,
        session: Session,
        job_id: str,
        stage: str,
    ) -> PipelineJobStage | None:
        validate_pipeline_stage(stage)
        validate_stage_transition(
            STAGE_STATUS_PENDING,
            STAGE_STATUS_RUNNING,
        )
        validate_stage_transition(
            STAGE_STATUS_FAILED,
            STAGE_STATUS_RUNNING,
        )
        now = datetime.utcnow()
        running_job_exists = exists(
            select(PipelineJob.id).where(
                PipelineJob.id == job_id,
                PipelineJob.status == JOB_STATUS_RUNNING,
            )
        )
        statement = (
            update(PipelineJobStage)
            .where(
                PipelineJobStage.job_id == job_id,
                PipelineJobStage.stage == stage,
                PipelineJobStage.status.in_(
                    {STAGE_STATUS_PENDING, STAGE_STATUS_FAILED}
                ),
                running_job_exists,
            )
            .values(
                status=STAGE_STATUS_RUNNING,
                attempt=PipelineJobStage.attempt + 1,
                started_at=now,
                finished_at=None,
                error_message="",
            )
            .returning(PipelineJobStage.id)
            .execution_options(synchronize_session=False)
        )
        stage_id = session.execute(statement).scalar_one_or_none()
        if stage_id is None:
            session.expire_all()
            return None

        session.execute(
            update(PipelineJob)
            .where(
                PipelineJob.id == job_id,
                PipelineJob.status == JOB_STATUS_RUNNING,
            )
            .values(current_stage=stage, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        session.flush()
        session.expire_all()
        return session.get(PipelineJobStage, stage_id)

    def finish_stage(
        self,
        session: Session,
        job_id: str,
        stage: str,
        status: str,
        result: dict[str, Any] | None = None,
        error: str = "",
    ) -> PipelineJobStage | None:
        validate_pipeline_stage(stage)
        validate_stage_transition(STAGE_STATUS_RUNNING, status)
        if status not in TERMINAL_STAGE_STATUSES:
            raise ValueError(
                f"Pipeline stage finish status must be terminal: {status}"
            )

        statement = (
            update(PipelineJobStage)
            .where(
                PipelineJobStage.job_id == job_id,
                PipelineJobStage.stage == stage,
                PipelineJobStage.status == STAGE_STATUS_RUNNING,
            )
            .values(
                status=status,
                result_json=dict(result or {}),
                error_message=error,
                finished_at=datetime.utcnow(),
            )
            .returning(PipelineJobStage.id)
            .execution_options(synchronize_session=False)
        )
        stage_id = session.execute(statement).scalar_one_or_none()
        if stage_id is None:
            session.expire_all()
            return None

        session.flush()
        session.expire_all()
        return session.get(PipelineJobStage, stage_id)

    def pause_for_decision(
        self,
        session: Session,
        job_id: str,
        stage: str,
    ) -> bool:
        """Atomically move the current running Job and Stage into waiting."""

        validate_pipeline_stage(stage)
        validate_job_transition(
            JOB_STATUS_RUNNING,
            JOB_STATUS_WAITING_DECISION,
        )
        validate_stage_transition(
            STAGE_STATUS_RUNNING,
            STAGE_STATUS_WAITING_DECISION,
        )
        return self._transition_decision_state(
            session,
            job_id=job_id,
            stage=stage,
            current_job_status=JOB_STATUS_RUNNING,
            target_job_status=JOB_STATUS_WAITING_DECISION,
            current_stage_status=STAGE_STATUS_RUNNING,
            target_stage_status=STAGE_STATUS_WAITING_DECISION,
        )

    def resume_from_decision(
        self,
        session: Session,
        job_id: str,
        stage: str,
    ) -> bool:
        """Atomically resume the Job and Stage after a committed resolution."""

        validate_pipeline_stage(stage)
        validate_job_transition(
            JOB_STATUS_WAITING_DECISION,
            JOB_STATUS_RUNNING,
        )
        validate_stage_transition(
            STAGE_STATUS_WAITING_DECISION,
            STAGE_STATUS_RUNNING,
        )
        return self._transition_decision_state(
            session,
            job_id=job_id,
            stage=stage,
            current_job_status=JOB_STATUS_WAITING_DECISION,
            target_job_status=JOB_STATUS_RUNNING,
            current_stage_status=STAGE_STATUS_WAITING_DECISION,
            target_stage_status=STAGE_STATUS_RUNNING,
        )

    @staticmethod
    def _transition_decision_state(
        session: Session,
        *,
        job_id: str,
        stage: str,
        current_job_status: str,
        target_job_status: str,
        current_stage_status: str,
        target_stage_status: str,
    ) -> bool:
        """Apply a two-row CAS without leaving either entity half-transitioned."""

        now = datetime.utcnow()
        with session.no_autoflush:
            savepoint = session.connection().begin_nested()
            try:
                updated_job_id = session.execute(
                    update(PipelineJob)
                    .where(
                        PipelineJob.id == job_id,
                        PipelineJob.status == current_job_status,
                        PipelineJob.current_stage == stage,
                    )
                    .values(status=target_job_status, updated_at=now)
                    .returning(PipelineJob.id)
                    .execution_options(synchronize_session=False)
                ).scalar_one_or_none()
                if updated_job_id is None:
                    savepoint.rollback()
                    return False
                updated_stage_id = session.execute(
                    update(PipelineJobStage)
                    .where(
                        PipelineJobStage.job_id == job_id,
                        PipelineJobStage.stage == stage,
                        PipelineJobStage.status == current_stage_status,
                    )
                    .values(status=target_stage_status)
                    .returning(PipelineJobStage.id)
                    .execution_options(synchronize_session=False)
                ).scalar_one_or_none()
                if updated_stage_id is None:
                    savepoint.rollback()
                    return False
                savepoint.commit()
                PipelineJobStore._sync_decision_identities(
                    session,
                    job_id=job_id,
                    stage_id=updated_stage_id,
                    job_values={
                        "status": target_job_status,
                        "updated_at": now,
                    },
                    stage_values={"status": target_stage_status},
                )
                return True
            except Exception:
                if savepoint.is_active:
                    savepoint.rollback()
                raise

    def interrupt_waiting_decision(
        self,
        session: Session,
        job_id: str,
        stage: str,
    ) -> bool:
        """Fail one unrecoverable gate without touching any other Job."""

        validate_pipeline_stage(stage)
        validate_job_transition(
            JOB_STATUS_WAITING_DECISION,
            JOB_STATUS_INTERRUPTED,
        )
        validate_stage_transition(
            STAGE_STATUS_WAITING_DECISION,
            STAGE_STATUS_FAILED,
        )
        now = datetime.utcnow()
        with session.no_autoflush:
            savepoint = session.connection().begin_nested()
            try:
                updated_job_id = session.execute(
                    update(PipelineJob)
                    .where(
                        PipelineJob.id == job_id,
                        PipelineJob.status == JOB_STATUS_WAITING_DECISION,
                        PipelineJob.current_stage == stage,
                    )
                    .values(
                        status=JOB_STATUS_INTERRUPTED,
                        error_summary="decision gate interrupted",
                        finished_at=now,
                        updated_at=now,
                    )
                    .returning(PipelineJob.id)
                    .execution_options(synchronize_session=False)
                ).scalar_one_or_none()
                if updated_job_id is None:
                    savepoint.rollback()
                    return False
                updated_stage_id = session.execute(
                    update(PipelineJobStage)
                    .where(
                        PipelineJobStage.job_id == job_id,
                        PipelineJobStage.stage == stage,
                        PipelineJobStage.status
                        == STAGE_STATUS_WAITING_DECISION,
                    )
                    .values(
                        status=STAGE_STATUS_FAILED,
                        error_message="decision gate interrupted",
                        finished_at=now,
                    )
                    .returning(PipelineJobStage.id)
                    .execution_options(synchronize_session=False)
                ).scalar_one_or_none()
                if updated_stage_id is None:
                    savepoint.rollback()
                    return False
                savepoint.commit()
                self._sync_decision_identities(
                    session,
                    job_id=job_id,
                    stage_id=updated_stage_id,
                    job_values={
                        "status": JOB_STATUS_INTERRUPTED,
                        "error_summary": "decision gate interrupted",
                        "finished_at": now,
                        "updated_at": now,
                    },
                    stage_values={
                        "status": STAGE_STATUS_FAILED,
                        "error_message": "decision gate interrupted",
                        "finished_at": now,
                    },
                )
                return True
            except Exception:
                if savepoint.is_active:
                    savepoint.rollback()
                raise

    @staticmethod
    def _sync_decision_identities(
        session: Session,
        *,
        job_id: str,
        stage_id: int,
        job_values: dict[str, Any],
        stage_values: dict[str, Any],
    ) -> None:
        """Synchronize only already-loaded target identities after CAS."""

        job = session.identity_map.get(
            session.identity_key(PipelineJob, (job_id,))
        )
        if job is not None:
            for field, value in job_values.items():
                set_committed_value(job, field, value)
        stage = session.identity_map.get(
            session.identity_key(PipelineJobStage, (stage_id,))
        )
        if stage is not None:
            for field, value in stage_values.items():
                set_committed_value(stage, field, value)

    def link_user(
        self,
        session: Session,
        job_id: str,
        user_id: int,
        source_stage: str,
        status: str = "pending",
        category: str = "unknown",
    ) -> PipelineJobUser:
        validate_job_user_status(status)
        statement = (
            sqlite_insert(PipelineJobUser)
            .values(
                job_id=job_id,
                user_id=user_id,
                source_stage=source_stage,
                status=status,
                category=category,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    PipelineJobUser.job_id,
                    PipelineJobUser.user_id,
                ]
            )
        )
        session.execute(statement)
        session.flush()
        link = session.get(PipelineJobUser, (job_id, user_id))
        if link is None:
            raise RuntimeError("Failed to link user to pipeline job")
        return link

    def update_job_user(
        self,
        session: Session,
        job_id: str,
        user_id: int,
        *,
        status: str,
        category: str | None = None,
    ) -> bool:
        validate_job_user_status(status)
        values: dict[str, Any] = {
            "status": status,
            "updated_at": datetime.utcnow(),
        }
        if category is not None:
            values["category"] = category
        result = session.execute(
            update(PipelineJobUser)
            .where(
                PipelineJobUser.job_id == job_id,
                PipelineJobUser.user_id == user_id,
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        session.flush()
        session.expire_all()
        return bool(result.rowcount)

    def update_ai_qualification(
        self,
        session: Session,
        job_id: str,
        user_id: int,
        *,
        qualification_status: str,
        match_score: float | None = None,
        confidence_score: float | None = None,
        category: str | None = None,
        expected_review_version: int,
        expected_qualification_status: str,
    ) -> bool:
        """Persist AI-derived fields without touching human review data.

        This deliberately does not change ``labels_json``, ``review_version``
        or ``manually_confirmed_at`` and does not create a human audit entry.
        """

        if qualification_status in {
            QUALIFICATION_STATUS_QUALIFIED,
            QUALIFICATION_STATUS_REJECTED,
        }:
            raise ValueError("AI cannot write a terminal qualification status")
        validate_qualification_status(qualification_status)
        validate_qualification_status(expected_qualification_status)
        ai_pending_statuses = {
            QUALIFICATION_STATUS_MANUAL_REVIEW,
            QUALIFICATION_STATUS_NEED_ENRICHMENT,
        }
        if expected_qualification_status not in ai_pending_statuses:
            raise ValueError("AI update requires a pending review status")
        normalized_review_version = int(expected_review_version)
        if normalized_review_version < 0:
            raise ValueError("expected_review_version must not be negative")
        values: dict[str, Any] = {
            "qualification_status": qualification_status,
            "status": legacy_job_user_status(qualification_status),
            "updated_at": datetime.utcnow(),
        }
        for field, score in (
            ("match_score", match_score),
            ("confidence_score", confidence_score),
        ):
            if score is None:
                continue
            normalized = float(score)
            if not math.isfinite(normalized) or not 0 <= normalized <= 100:
                raise ValueError(f"{field} must be between 0 and 100")
            values[field] = normalized
        if category is not None:
            values["category"] = str(category).strip() or "unknown"
        result = session.execute(
            update(PipelineJobUser)
            .where(
                PipelineJobUser.job_id == job_id,
                PipelineJobUser.user_id == user_id,
                PipelineJobUser.review_version == normalized_review_version,
                PipelineJobUser.qualification_status
                == expected_qualification_status,
                PipelineJobUser.qualification_status.in_(ai_pending_statuses),
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        session.flush()
        session.expire_all()
        return bool(result.rowcount)

    def list_job_users(
        self,
        session: Session,
        job_id: str,
        *,
        status: str | None = None,
        platform: str | None = None,
        discovery_status: str | None = None,
        qualification_status: str | None = None,
        qualification_statuses: Iterable[str] | None = None,
        after_user_id: int | None = None,
        limit: int = 200,
    ) -> list[tuple[PipelineJobUser, User]]:
        statement = (
            select(PipelineJobUser, User)
            .join(User, User.id == PipelineJobUser.user_id)
            .where(PipelineJobUser.job_id == job_id)
        )
        if status is not None:
            validate_job_user_status(status)
            statement = statement.where(PipelineJobUser.status == status)
        if platform is not None:
            statement = statement.where(User.platform == platform)
        if discovery_status is not None:
            validate_discovery_status(discovery_status)
            statement = statement.where(
                PipelineJobUser.discovery_status == discovery_status
            )
        if qualification_status is not None:
            if qualification_statuses is not None:
                raise ValueError(
                    "qualification_status and qualification_statuses conflict"
                )
            validate_qualification_status(qualification_status)
            statement = statement.where(
                PipelineJobUser.qualification_status == qualification_status
            )
        if qualification_statuses is not None:
            normalized_statuses = tuple(qualification_statuses)
            if not normalized_statuses:
                return []
            for item in normalized_statuses:
                validate_qualification_status(item)
            statement = statement.where(
                PipelineJobUser.qualification_status.in_(normalized_statuses)
            )
        if after_user_id is not None:
            normalized_after = int(after_user_id)
            if normalized_after < 0:
                raise ValueError("after_user_id must not be negative")
            statement = statement.where(
                PipelineJobUser.user_id > normalized_after
            )
        statement = statement.order_by(PipelineJobUser.user_id).limit(limit)
        return list(session.execute(statement).all())

    def list_job_user_ids(
        self,
        session: Session,
        job_id: str,
        *,
        user_status: str | None = None,
    ) -> list[int]:
        statement = select(PipelineJobUser.user_id).where(
            PipelineJobUser.job_id == job_id
        )
        if user_status is not None:
            validate_job_user_status(user_status)
            statement = statement.where(PipelineJobUser.status == user_status)
        statement = statement.order_by(PipelineJobUser.user_id.asc())
        return list(session.scalars(statement))

    def request_cancel(
        self,
        session: Session,
        job_id: str,
    ) -> PipelineJob | None:
        now = datetime.utcnow()
        if self.set_job_status(
            session,
            job_id,
            JOB_STATUS_CANCELLED,
            expected_statuses={JOB_STATUS_QUEUED},
            finished_at=now,
        ):
            validate_stage_transition(
                STAGE_STATUS_PENDING,
                STAGE_STATUS_CANCELLED,
            )
            session.execute(
                update(PipelineJobStage)
                .where(
                    PipelineJobStage.job_id == job_id,
                    PipelineJobStage.status == STAGE_STATUS_PENDING,
                )
                .values(
                    status=STAGE_STATUS_CANCELLED,
                    finished_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            session.flush()
            session.expire_all()
            return session.get(PipelineJob, job_id)

        if self.set_job_status(
            session,
            job_id,
            JOB_STATUS_CANCELLED,
            expected_statuses={JOB_STATUS_WAITING_DECISION},
            finished_at=now,
        ):
            validate_stage_transition(
                STAGE_STATUS_WAITING_DECISION,
                STAGE_STATUS_CANCELLED,
            )
            validate_stage_transition(
                STAGE_STATUS_PENDING,
                STAGE_STATUS_CANCELLED,
            )
            session.execute(
                update(PipelineJobStage)
                .where(
                    PipelineJobStage.job_id == job_id,
                    PipelineJobStage.status.in_(
                        {
                            STAGE_STATUS_WAITING_DECISION,
                            STAGE_STATUS_PENDING,
                        }
                    ),
                )
                .values(
                    status=STAGE_STATUS_CANCELLED,
                    finished_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            from tiktok_bot_core.storage.pipeline_live_store import (
                PipelineLiveStore,
            )

            PipelineLiveStore().cancel_checkpoint(
                session,
                job_id=job_id,
                reason="job_cancelled",
                resolved_at=now,
            )
            session.flush()
            session.expire_all()
            return session.get(PipelineJob, job_id)

        if self.set_job_status(
            session,
            job_id,
            JOB_STATUS_CANCELLING,
            expected_statuses={JOB_STATUS_RUNNING},
        ):
            return session.get(PipelineJob, job_id)

        session.expire_all()
        job = session.get(PipelineJob, job_id)
        if job is not None:
            validate_job_status(job.status)
        return job

    def cancel_pending_stages(
        self,
        session: Session,
        job_id: str,
    ) -> list[str]:
        """Mark stages not yet started as cancelled at a safe boundary."""

        validate_stage_transition(
            STAGE_STATUS_PENDING,
            STAGE_STATUS_CANCELLED,
        )
        rows = session.execute(
            update(PipelineJobStage)
            .where(
                PipelineJobStage.job_id == job_id,
                PipelineJobStage.status == STAGE_STATUS_PENDING,
            )
            .values(
                status=STAGE_STATUS_CANCELLED,
                finished_at=datetime.utcnow(),
            )
            .returning(
                PipelineJobStage.stage,
                PipelineJobStage.stage_order,
            )
            .execution_options(synchronize_session=False)
        ).all()
        session.flush()
        session.expire_all()
        return [stage for stage, _order in sorted(rows, key=lambda row: row[1])]

    def skip_pending_stages(
        self,
        session: Session,
        job_id: str,
    ) -> list[str]:
        """Mark every not-yet-started stage skipped at a policy boundary."""

        validate_stage_transition(
            STAGE_STATUS_PENDING,
            STAGE_STATUS_SKIPPED,
        )
        rows = session.execute(
            update(PipelineJobStage)
            .where(
                PipelineJobStage.job_id == job_id,
                PipelineJobStage.status == STAGE_STATUS_PENDING,
            )
            .values(
                status=STAGE_STATUS_SKIPPED,
                finished_at=datetime.utcnow(),
            )
            .returning(
                PipelineJobStage.stage,
                PipelineJobStage.stage_order,
            )
            .execution_options(synchronize_session=False)
        ).all()
        session.flush()
        session.expire_all()
        return [stage for stage, _order in sorted(rows, key=lambda row: row[1])]

    def recover_interrupted(self, session: Session) -> int:
        validate_stage_transition(STAGE_STATUS_RUNNING, STAGE_STATUS_FAILED)
        validate_stage_transition(
            STAGE_STATUS_WAITING_DECISION,
            STAGE_STATUS_FAILED,
        )
        validate_job_transition(JOB_STATUS_RUNNING, JOB_STATUS_INTERRUPTED)
        validate_job_transition(JOB_STATUS_CANCELLING, JOB_STATUS_INTERRUPTED)
        validate_job_transition(
            JOB_STATUS_WAITING_DECISION,
            JOB_STATUS_INTERRUPTED,
        )
        now = datetime.utcnow()
        recoverable_job_ids = select(PipelineJob.id).where(
            PipelineJob.status.in_(
                {
                    JOB_STATUS_RUNNING,
                    JOB_STATUS_CANCELLING,
                    JOB_STATUS_WAITING_DECISION,
                }
            )
        )
        session.execute(
            update(PipelineJobStage)
            .where(
                PipelineJobStage.job_id.in_(recoverable_job_ids),
                PipelineJobStage.status.in_(
                    {
                        STAGE_STATUS_RUNNING,
                        STAGE_STATUS_WAITING_DECISION,
                    }
                ),
            )
            .values(
                status=STAGE_STATUS_FAILED,
                error_message="service interrupted",
                finished_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        session.execute(
            update(PipelineDecisionCheckpoint)
            .where(
                PipelineDecisionCheckpoint.job_id.in_(recoverable_job_ids),
                PipelineDecisionCheckpoint.status == "pending",
            )
            .values(
                status="cancelled",
                resolved_at=now,
                resolution_key=None,
                resolution_source="system",
                operator="",
                reason="service_interrupted",
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        statement = (
            update(PipelineJob)
            .where(
                PipelineJob.status.in_(
                    {
                        JOB_STATUS_RUNNING,
                        JOB_STATUS_CANCELLING,
                        JOB_STATUS_WAITING_DECISION,
                    }
                )
            )
            .values(
                status=JOB_STATUS_INTERRUPTED,
                finished_at=now,
                updated_at=now,
            )
            .execution_options(synchronize_session="fetch")
        )
        result = session.execute(statement)
        session.flush()
        session.expire_all()
        return int(result.rowcount or 0)
