"""统一 Pipeline 任务持久化模型测试。"""

import gc
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy
from sqlalchemy import event, func, inspect, select, text

from tiktok_bot_core.storage.database import Database


@pytest.fixture
def db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as file:
        path = Path(file.name)
    database = Database(f"sqlite:///{path}")
    database.init()
    yield database
    database.engine.dispose()
    gc.collect()
    try:
        path.unlink()
    except PermissionError:
        pass


def test_pipeline_job_tables_created(db):
    names = set(inspect(db.engine).get_table_names())
    assert {
        "pipeline_jobs",
        "pipeline_job_stages",
        "pipeline_schedules",
        "pipeline_job_users",
    } <= names


def test_pipeline_job_tables_have_designed_columns(db):
    inspector = inspect(db.engine)
    expected = {
        "pipeline_jobs": {
            "id",
            "trigger_type",
            "schedule_id",
            "platform",
            "account_mode",
            "account_id",
            "stages_json",
            "config_snapshot_json",
            "status",
            "current_stage",
            "priority",
            "retry_of_job_id",
            "error_summary",
            "queued_at",
            "started_at",
            "finished_at",
            "created_at",
            "updated_at",
        },
        "pipeline_job_stages": {
            "id",
            "job_id",
            "stage",
            "stage_order",
            "status",
            "attempt",
            "result_json",
            "error_message",
            "started_at",
            "finished_at",
        },
        "pipeline_schedules": {
            "id",
            "name",
            "platform",
            "account_mode",
            "account_id",
            "stages_json",
            "cron_expression",
            "timezone",
            "enabled",
            "config_json",
            "next_run_at",
            "last_run_at",
            "created_at",
            "updated_at",
        },
        "pipeline_job_users": {
            "job_id",
            "user_id",
            "source_stage",
            "status",
            "category",
            "created_at",
            "updated_at",
        },
    }
    for table, column_names in expected.items():
        actual = {column["name"] for column in inspector.get_columns(table)}
        assert column_names <= actual


def test_pipeline_job_uses_uuid_string_default(db):
    from tiktok_bot_core.models.entities import PipelineJob

    with db.session() as session:
        job = PipelineJob(platform="douyin")
        session.add(job)
        session.flush()
        assert isinstance(job.id, str)
        assert len(job.id) == 36
        assert str(uuid.UUID(job.id)) == job.id


def test_pipeline_stage_pair_is_unique_without_redundant_job_user_constraint(db):
    inspector = inspect(db.engine)
    stage_constraints = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("pipeline_job_stages")
    }
    user_constraints = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("pipeline_job_users")
    }
    assert ("job_id", "stage") in stage_constraints
    assert ("job_id", "user_id") not in user_constraints
    job_user_primary_key = inspector.get_pk_constraint("pipeline_job_users")
    assert tuple(job_user_primary_key["constrained_columns"]) == ("job_id", "user_id")


def test_existing_tables_receive_job_columns(db):
    inspector = inspect(db.engine)
    columns = {
        table: {column["name"]: column for column in inspector.get_columns(table)}
        for table in (
            "strategies",
            "messages",
            "tiktok_accounts",
            "experience_rules",
        )
    }
    assert "job_id" in columns["strategies"]
    assert "job_id" in columns["messages"]
    assert {
        "browser_provider",
        "browser_profile_id",
        "display_name",
        "avatar_url",
    } <= set(columns["tiktok_accounts"])
    assert {"platform", "job_id"} <= set(columns["experience_rules"])
    for name in (
        "browser_provider",
        "browser_profile_id",
        "display_name",
        "avatar_url",
    ):
        assert columns["tiktok_accounts"][name]["nullable"] is False
        assert columns["tiktok_accounts"][name]["default"] is not None


def test_new_database_has_pipeline_foreign_keys(db):
    inspector = inspect(db.engine)
    expected = {
        "pipeline_jobs": {
            ("schedule_id", "pipeline_schedules"),
            ("account_id", "tiktok_accounts"),
            ("retry_of_job_id", "pipeline_jobs"),
        },
        "pipeline_job_stages": {("job_id", "pipeline_jobs")},
        "pipeline_schedules": {("account_id", "tiktok_accounts")},
        "pipeline_job_users": {
            ("job_id", "pipeline_jobs"),
            ("user_id", "users"),
        },
        "strategies": {("job_id", "pipeline_jobs")},
        "messages": {("job_id", "pipeline_jobs")},
        "experience_rules": {("job_id", "pipeline_jobs")},
    }
    for table, expected_foreign_keys in expected.items():
        actual = {
            (foreign_key["constrained_columns"][0], foreign_key["referred_table"])
            for foreign_key in inspector.get_foreign_keys(table)
        }
        assert expected_foreign_keys <= actual


def test_existing_database_receives_safe_columns_and_preserves_data(caplog):
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as file:
        path = Path(file.name)
    database = Database(f"sqlite:///{path}")
    try:
        with database.engine.begin() as connection:
            connection.execute(text("CREATE TABLE strategies (id INTEGER PRIMARY KEY)"))
            connection.execute(text("CREATE TABLE messages (id INTEGER PRIMARY KEY)"))
            connection.execute(text("CREATE TABLE tiktok_accounts (id INTEGER PRIMARY KEY)"))
            connection.execute(text("INSERT INTO strategies (id) VALUES (11)"))
            connection.execute(text("INSERT INTO messages (id) VALUES (12)"))
            connection.execute(text("INSERT INTO tiktok_accounts (id) VALUES (13)"))

        with caplog.at_level("WARNING"):
            database.init()

        inspector = inspect(database.engine)
        columns = {
            table: {
                column["name"]: column
                for column in inspector.get_columns(table)
            }
            for table in ("strategies", "messages", "tiktok_accounts")
        }
        assert columns["strategies"]["job_id"]["nullable"] is True
        assert columns["messages"]["job_id"]["nullable"] is True
        assert {
            "browser_provider",
            "browser_profile_id",
            "display_name",
            "avatar_url",
        } <= set(columns["tiktok_accounts"])
        for name in (
            "browser_provider",
            "browser_profile_id",
            "display_name",
            "avatar_url",
        ):
            assert columns["tiktok_accounts"][name]["nullable"] is False
            assert columns["tiktok_accounts"][name]["default"] is not None

        indexes = {
            table: {
                tuple(index["column_names"])
                for index in inspector.get_indexes(table)
            }
            for table in ("strategies", "messages")
        }
        assert ("job_id",) in indexes["strategies"]
        assert ("job_id",) in indexes["messages"]

        with database.engine.connect() as connection:
            assert connection.execute(
                text("SELECT id FROM strategies")
            ).scalar_one() == 11
            assert connection.execute(
                text("SELECT id FROM messages")
            ).scalar_one() == 12
            account = connection.execute(
                text(
                    "SELECT id, browser_provider, browser_profile_id, "
                    "display_name, avatar_url "
                    "FROM tiktok_accounts"
                )
            ).one()
            assert tuple(account) == (13, "", "", "", "")

        assert "无法通过 ALTER TABLE 补充外键约束" in caplog.text
    finally:
        database.engine.dispose()
        gc.collect()
        try:
            path.unlink()
        except PermissionError:
            pass


def _schema_signature(database):
    inspector = inspect(database.engine)
    return {
        table: {
            "columns": tuple(
                (
                    column["name"],
                    str(column["type"]),
                    column["nullable"],
                    column["default"],
                    column["primary_key"],
                )
                for column in inspector.get_columns(table)
            ),
            "indexes": tuple(
                sorted(
                    (
                        index["name"],
                        tuple(index["column_names"]),
                        index["unique"],
                    )
                    for index in inspector.get_indexes(table)
                )
            ),
        }
        for table in inspector.get_table_names()
    }


def test_database_migration_is_idempotent(db):
    before = _schema_signature(db)
    db.init()
    db.init()
    assert _schema_signature(db) == before


def test_database_migration_inspection_failure_is_fatal(db, monkeypatch, caplog):
    def fail_inspection(_engine):
        raise sqlalchemy.exc.SQLAlchemyError("inspection failed")

    monkeypatch.setattr(sqlalchemy, "inspect", fail_inspection)
    with caplog.at_level("ERROR"):
        with pytest.raises(RuntimeError, match="数据库迁移检查失败"):
            db._migrate()
    assert "数据库迁移检查失败" in caplog.text


def test_create_job_creates_ordered_stage_rows(db):
    from tiktok_bot_core.storage.pipeline_job_store import (
        JOB_STATUS_QUEUED,
        STAGE_STATUS_PENDING,
        PipelineJobStore,
    )

    store = PipelineJobStore()
    with db.session() as session:
        job = store.create_job(
            session,
            platform="douyin",
            account_mode="auto",
            account_id=None,
            stages=["collect", "filter", "outreach"],
            config_snapshot={"limit": 20},
        )

        assert job.status == JOB_STATUS_QUEUED
        assert job.stages_json == ["collect", "filter", "outreach"]
        assert [
            (row.stage, row.stage_order, row.status)
            for row in job.stages
        ] == [
            ("collect", 0, STAGE_STATUS_PENDING),
            ("filter", 1, STAGE_STATUS_PENDING),
            ("outreach", 2, STAGE_STATUS_PENDING),
        ]


def test_list_jobs_filters_platform_and_status(db):
    from tiktok_bot_core.storage.pipeline_job_store import (
        JOB_STATUS_FAILED,
        PipelineJobStore,
    )

    store = PipelineJobStore()
    with db.session() as session:
        base_time = datetime(2026, 7, 26, 10, 0, 0)
        oldest = store.create_job(
            session,
            platform="douyin",
            account_mode="auto",
            account_id=None,
            stages=["collect"],
        )
        tied_a = store.create_job(
            session,
            platform="douyin",
            account_mode="auto",
            account_id=None,
            stages=["filter"],
        )
        tied_b = store.create_job(
            session,
            platform="douyin",
            account_mode="auto",
            account_id=None,
            stages=["outreach"],
        )
        store.create_job(
            session,
            platform="tiktok",
            account_mode="auto",
            account_id=None,
            stages=["collect"],
        )
        oldest.status = JOB_STATUS_FAILED
        oldest.created_at = base_time
        tied_a.status = JOB_STATUS_FAILED
        tied_a.created_at = base_time + timedelta(minutes=1)
        tied_b.status = JOB_STATUS_FAILED
        tied_b.created_at = base_time + timedelta(minutes=1)
        session.flush()

        first_page = store.list_jobs(
            session,
            platform="douyin",
            status=JOB_STATUS_FAILED,
            limit=2,
            offset=0,
        )
        second_page = store.list_jobs(
            session,
            platform="douyin",
            status=JOB_STATUS_FAILED,
            limit=1,
            offset=2,
        )

        expected_tied_ids = sorted([tied_a.id, tied_b.id], reverse=True)
        assert [job.id for job in first_page] == expected_tied_ids
        assert [job.id for job in second_page] == [oldest.id]


def test_claim_queued_job_changes_status_atomically(db):
    from tiktok_bot_core.storage.pipeline_job_store import (
        JOB_STATUS_QUEUED,
        JOB_STATUS_RUNNING,
        PipelineJobStore,
    )

    store = PipelineJobStore()
    with db.session() as session:
        selected = store.create_job(
            session,
            platform="douyin",
            account_mode="auto",
            account_id=None,
            stages=["collect"],
            priority=10,
        )
        untouched = store.create_job(
            session,
            platform="tiktok",
            account_mode="auto",
            account_id=None,
            stages=["collect"],
            priority=1,
        )
        selected_id = selected.id
        untouched_id = untouched.id

    barrier = threading.Barrier(3)

    def claim_from_independent_session():
        session = db.SessionLocal()
        try:
            barrier.wait(timeout=5)
            claimed = store.claim_next_job(
                session,
                platforms={"douyin"},
            )
            claimed_id = claimed.id if claimed is not None else None
            session.commit()
            return claimed_id
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(claim_from_independent_session)
        second = executor.submit(claim_from_independent_session)
        barrier.wait(timeout=5)
        claimed_ids = [first.result(timeout=10), second.result(timeout=10)]

    with db.session() as session:
        selected = store.get_job(session, selected_id)
        untouched = store.get_job(session, untouched_id)
        assert claimed_ids.count(selected_id) == 1
        assert claimed_ids.count(None) == 1
        assert selected is not None
        assert selected.status == JOB_STATUS_RUNNING
        assert selected.started_at is not None
        assert untouched is not None
        assert untouched.status == JOB_STATUS_QUEUED


def test_link_job_user_is_idempotent(db):
    from tiktok_bot_core.models.entities import PipelineJobUser, User
    from tiktok_bot_core.storage.pipeline_job_store import PipelineJobStore

    store = PipelineJobStore()
    with db.session() as session:
        job = store.create_job(
            session,
            platform="douyin",
            account_mode="auto",
            account_id=None,
            stages=["collect"],
        )
        user = User(
            platform="douyin",
            tiktok_id="douyin-user-1",
            username="douyin-user-1",
        )
        session.add(user)
        session.flush()
        job_id = job.id
        user_id = user.id

    first_session = db.SessionLocal()
    insert_started = threading.Event()

    def signal_competing_insert(
        _connection,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ):
        if (
            threading.current_thread() is not threading.main_thread()
            and statement.lstrip().upper().startswith("INSERT")
            and "pipeline_job_users" in statement
        ):
            insert_started.set()

    def link_from_independent_session():
        second_session = db.SessionLocal()
        try:
            link = store.link_user(
                second_session,
                job_id,
                user_id,
                "filter",
            )
            second_session.commit()
            return link.job_id, link.user_id, link.source_stage
        finally:
            second_session.close()

    event.listen(db.engine, "before_cursor_execute", signal_competing_insert)
    try:
        first = store.link_user(
            first_session,
            job_id,
            user_id,
            "collect",
        )
        first_values = (first.job_id, first.user_id, first.source_stage)
        with ThreadPoolExecutor(max_workers=1) as executor:
            competing = executor.submit(link_from_independent_session)
            assert insert_started.wait(timeout=2)
            first_session.commit()
            second_values = competing.result(timeout=5)
    finally:
        event.remove(
            db.engine,
            "before_cursor_execute",
            signal_competing_insert,
        )
        first_session.close()

    with db.session() as session:
        count = session.scalar(
            select(func.count())
            .select_from(PipelineJobUser)
            .where(PipelineJobUser.job_id == job_id)
        )

        assert first_values == (job_id, user_id, "collect")
        assert second_values == (job_id, user_id, "collect")
        assert count == 1


def test_cancel_queued_job_finishes_immediately(db):
    from tiktok_bot_core.storage.pipeline_job_store import (
        JOB_STATUS_CANCELLED,
        STAGE_STATUS_CANCELLED,
        PipelineJobStore,
    )

    store = PipelineJobStore()
    with db.session() as session:
        job = store.create_job(
            session,
            platform="douyin",
            account_mode="auto",
            account_id=None,
            stages=["collect", "filter"],
        )

        cancelled = store.request_cancel(session, job.id)

        assert cancelled is not None
        assert cancelled.status == JOB_STATUS_CANCELLED
        assert cancelled.finished_at is not None
        assert {stage.status for stage in cancelled.stages} == {
            STAGE_STATUS_CANCELLED
        }


def test_recover_running_jobs_marks_interrupted(db):
    from tiktok_bot_core.storage.pipeline_job_store import (
        JOB_STATUS_CANCELLING,
        JOB_STATUS_INTERRUPTED,
        JOB_STATUS_QUEUED,
        JOB_STATUS_RUNNING,
        STAGE_STATUS_FAILED,
        PipelineJobStore,
    )

    store = PipelineJobStore()
    with db.session() as session:
        running = store.create_job(
            session,
            platform="douyin",
            account_mode="auto",
            account_id=None,
            stages=["collect"],
        )
        cancelling = store.create_job(
            session,
            platform="douyin",
            account_mode="auto",
            account_id=None,
            stages=["collect"],
        )
        queued = store.create_job(
            session,
            platform="douyin",
            account_mode="auto",
            account_id=None,
            stages=["collect"],
        )
        assert store.set_job_status(
            session,
            running.id,
            JOB_STATUS_RUNNING,
            expected_statuses={JOB_STATUS_QUEUED},
        )
        assert store.set_job_status(
            session,
            cancelling.id,
            JOB_STATUS_RUNNING,
            expected_statuses={JOB_STATUS_QUEUED},
        )
        store.start_stage(session, running.id, "collect")
        store.start_stage(session, cancelling.id, "collect")
        store.request_cancel(session, cancelling.id)
        assert cancelling.status == JOB_STATUS_CANCELLING

        recovered = store.recover_interrupted(session)

        assert recovered == 2
        assert running.status == JOB_STATUS_INTERRUPTED
        assert cancelling.status == JOB_STATUS_INTERRUPTED
        assert running.finished_at is not None
        assert cancelling.finished_at is not None
        assert queued.status == JOB_STATUS_QUEUED
        assert running.stages[0].status == STAGE_STATUS_FAILED
        assert cancelling.stages[0].status == STAGE_STATUS_FAILED
        assert running.stages[0].error_message == "service interrupted"
        assert cancelling.stages[0].error_message == "service interrupted"
        assert running.stages[0].finished_at is not None
        assert cancelling.stages[0].finished_at is not None


def test_waiting_decision_state_transitions_are_strict():
    from tiktok_bot_core.models.pipeline_states import (
        JOB_STATUS_CANCELLED,
        JOB_STATUS_RUNNING,
        JOB_STATUS_SUCCEEDED,
        JOB_STATUS_WAITING_DECISION,
        STAGE_STATUS_CANCELLED,
        STAGE_STATUS_FAILED,
        STAGE_STATUS_RUNNING,
        STAGE_STATUS_SUCCEEDED,
        STAGE_STATUS_WAITING_DECISION,
        validate_job_transition,
        validate_stage_transition,
    )

    assert validate_job_transition(
        JOB_STATUS_RUNNING,
        JOB_STATUS_WAITING_DECISION,
    )
    assert validate_job_transition(
        JOB_STATUS_WAITING_DECISION,
        JOB_STATUS_RUNNING,
    )
    assert validate_job_transition(
        JOB_STATUS_WAITING_DECISION,
        JOB_STATUS_CANCELLED,
    )
    assert validate_stage_transition(
        STAGE_STATUS_RUNNING,
        STAGE_STATUS_WAITING_DECISION,
    )
    assert validate_stage_transition(
        STAGE_STATUS_WAITING_DECISION,
        STAGE_STATUS_RUNNING,
    )
    assert validate_stage_transition(
        STAGE_STATUS_WAITING_DECISION,
        STAGE_STATUS_CANCELLED,
    )
    assert validate_stage_transition(
        STAGE_STATUS_WAITING_DECISION,
        STAGE_STATUS_FAILED,
    )
    with pytest.raises(ValueError, match="Invalid pipeline job transition"):
        validate_job_transition(
            JOB_STATUS_SUCCEEDED,
            JOB_STATUS_WAITING_DECISION,
        )
    with pytest.raises(ValueError, match="Invalid pipeline stage transition"):
        validate_stage_transition(
            STAGE_STATUS_WAITING_DECISION,
            STAGE_STATUS_SUCCEEDED,
        )


def test_store_pauses_and_resumes_job_and_stage_as_one_cas(db):
    from tiktok_bot_core.models.pipeline_states import (
        JOB_STATUS_RUNNING,
        JOB_STATUS_WAITING_DECISION,
        STAGE_STATUS_RUNNING,
        STAGE_STATUS_SUCCEEDED,
        STAGE_STATUS_WAITING_DECISION,
    )
    from tiktok_bot_core.storage.pipeline_job_store import PipelineJobStore

    store = PipelineJobStore()
    with db.session() as session:
        job = store.create_job(
            session,
            platform="douyin",
            account_mode="auto",
            account_id=None,
            stages=["collect"],
        )
        claimed = store.claim_next_job(session, platforms={"douyin"})
        assert claimed is not None and claimed.id == job.id
        assert store.start_stage(session, job.id, "collect") is not None

        assert store.pause_for_decision(session, job.id, "collect") is True
        assert store.pause_for_decision(session, job.id, "collect") is False
        session.refresh(job)
        session.refresh(job.stages[0])
        assert job.status == JOB_STATUS_WAITING_DECISION
        assert job.stages[0].status == STAGE_STATUS_WAITING_DECISION
        assert store.finish_stage(
            session,
            job.id,
            "collect",
            STAGE_STATUS_SUCCEEDED,
        ) is None

        assert store.resume_from_decision(session, job.id, "collect") is True
        assert store.resume_from_decision(session, job.id, "collect") is False
        session.refresh(job)
        session.refresh(job.stages[0])
        assert job.status == JOB_STATUS_RUNNING
        assert job.stages[0].status == STAGE_STATUS_RUNNING


def test_generic_status_setter_cannot_enter_or_resume_decision_waiting(db):
    from tiktok_bot_core.models.pipeline_states import (
        JOB_STATUS_QUEUED,
        JOB_STATUS_RUNNING,
        JOB_STATUS_WAITING_DECISION,
    )
    from tiktok_bot_core.storage.pipeline_job_store import PipelineJobStore

    store = PipelineJobStore()
    with db.session() as session:
        job = store.create_job(
            session,
            platform="douyin",
            account_mode="auto",
            account_id=None,
            stages=["collect"],
        )
        assert store.set_job_status(
            session,
            job.id,
            JOB_STATUS_RUNNING,
            expected_statuses={JOB_STATUS_QUEUED},
        )
        assert store.start_stage(session, job.id, "collect") is not None

        with pytest.raises(ValueError, match="pause_for_decision"):
            store.set_job_status(
                session,
                job.id,
                JOB_STATUS_WAITING_DECISION,
                expected_statuses={JOB_STATUS_RUNNING},
            )
        assert store.pause_for_decision(session, job.id, "collect")
        with pytest.raises(ValueError, match="resume_from_decision"):
            store.set_job_status(
                session,
                job.id,
                JOB_STATUS_RUNNING,
                expected_statuses={JOB_STATUS_WAITING_DECISION},
            )
        with pytest.raises(ValueError, match="resume_from_decision"):
            store.set_job_status(
                session,
                job.id,
                JOB_STATUS_RUNNING,
            )


def test_pause_savepoint_rolls_back_job_when_stage_cas_misses(db):
    from tiktok_bot_core.models.pipeline_states import (
        JOB_STATUS_RUNNING,
        STAGE_STATUS_FAILED,
    )
    from tiktok_bot_core.storage.pipeline_job_store import PipelineJobStore

    store = PipelineJobStore()
    with db.session() as session:
        job = store.create_job(
            session,
            platform="douyin",
            account_mode="auto",
            account_id=None,
            stages=["collect"],
        )
        claimed = store.claim_next_job(session, platforms={"douyin"})
        assert claimed is not None and claimed.id == job.id
        assert store.start_stage(session, job.id, "collect") is not None
        assert store.finish_stage(
            session,
            job.id,
            "collect",
            STAGE_STATUS_FAILED,
            error="injected stage mismatch",
        ) is not None

        assert store.pause_for_decision(session, job.id, "collect") is False

        session.refresh(job)
        session.refresh(job.stages[0])
        assert job.status == JOB_STATUS_RUNNING
        assert job.stages[0].status == STAGE_STATUS_FAILED


def test_pause_resume_sync_only_loaded_target_identities(db):
    from tiktok_bot_core.models.entities import PipelineJobEvent
    from tiktok_bot_core.models.pipeline_states import (
        JOB_STATUS_RUNNING,
        JOB_STATUS_WAITING_DECISION,
        STAGE_STATUS_RUNNING,
        STAGE_STATUS_WAITING_DECISION,
    )
    from tiktok_bot_core.storage.pipeline_job_store import PipelineJobStore

    store = PipelineJobStore()
    session = db.SessionLocal()
    try:
        target = store.create_job(
            session,
            platform="douyin",
            account_mode="auto",
            account_id=None,
            stages=["collect"],
        )
        session.commit()
        claimed = store.claim_next_job(session, platforms={"douyin"})
        assert claimed is not None and claimed.id == target.id
        assert store.start_stage(session, target.id, "collect") is not None
        session.commit()

        target = store.get_job(session, target.id)
        assert target is not None
        target_stage = target.stages[0]
        unrelated = store.create_job(
            session,
            platform="douyin",
            account_mode="auto",
            account_id=None,
            stages=["filter"],
        )
        session.commit()
        target.status
        target_stage.status
        session.refresh(unrelated)
        unrelated.error_summary = "unrelated dirty value"
        invalid_unflushed = PipelineJobEvent(
            job_id=target.id,
            stage="collect",
            event_type=None,
            level="info",
            payload_json={},
        )
        session.add(invalid_unflushed)

        assert store.pause_for_decision(
            session,
            target.id,
            "collect",
        )
        assert target.status == JOB_STATUS_WAITING_DECISION
        assert target_stage.status == STAGE_STATUS_WAITING_DECISION
        assert invalid_unflushed in session.new
        assert unrelated in session.dirty
        assert inspect(unrelated).expired is False
        assert unrelated.error_summary == "unrelated dirty value"

        assert store.resume_from_decision(
            session,
            target.id,
            "collect",
        )
        assert target.status == JOB_STATUS_RUNNING
        assert target_stage.status == STAGE_STATUS_RUNNING
        assert invalid_unflushed in session.new
        assert unrelated in session.dirty
        assert inspect(unrelated).expired is False
    finally:
        session.rollback()
        session.close()


def test_cancelling_waiting_job_closes_checkpoint_and_finishes_states(db):
    from tiktok_bot_core.models.pipeline_states import (
        JOB_STATUS_CANCELLED,
        STAGE_STATUS_CANCELLED,
    )
    from tiktok_bot_core.storage.pipeline_job_store import PipelineJobStore
    from tiktok_bot_core.storage.pipeline_live_store import PipelineLiveStore

    store = PipelineJobStore()
    live_store = PipelineLiveStore()
    with db.session() as session:
        job = store.create_job(
            session,
            platform="douyin",
            account_mode="auto",
            account_id=None,
            stages=["collect", "filter"],
        )
        claimed = store.claim_next_job(session, platforms={"douyin"})
        assert claimed is not None and claimed.id == job.id
        assert store.start_stage(session, job.id, "collect") is not None
        checkpoint = live_store.create_checkpoint(
            session,
            job_id=job.id,
            stage="collect",
            kind="insufficient_evidence",
            option_keys=["continue_with_current_evidence", "cancel_job"],
            default_option_key="continue_with_current_evidence",
            context={"schemaVersion": 1, "summary": "review evidence"},
            deadline_at=datetime.utcnow() + timedelta(seconds=10),
        )
        assert store.pause_for_decision(session, job.id, "collect")

        cancelled = store.request_cancel(session, job.id)

        assert cancelled is not None
        assert cancelled.status == JOB_STATUS_CANCELLED
        assert cancelled.finished_at is not None
        assert {stage.status for stage in cancelled.stages} == {
            STAGE_STATUS_CANCELLED
        }
        authoritative = live_store.get_checkpoint(
            session,
            job_id=job.id,
            checkpoint_id=checkpoint.id,
        )
        assert authoritative is not None
        assert authoritative.status == "cancelled"
        assert authoritative.resolution_source == "system"
        assert live_store.get_active_checkpoint(session, job_id=job.id) is None


def test_recover_waiting_decision_marks_interrupted_and_closes_checkpoint(db):
    from tiktok_bot_core.models.pipeline_states import (
        JOB_STATUS_INTERRUPTED,
        STAGE_STATUS_FAILED,
    )
    from tiktok_bot_core.storage.pipeline_job_store import PipelineJobStore
    from tiktok_bot_core.storage.pipeline_live_store import PipelineLiveStore

    store = PipelineJobStore()
    live_store = PipelineLiveStore()
    with db.session() as session:
        job = store.create_job(
            session,
            platform="douyin",
            account_mode="auto",
            account_id=None,
            stages=["collect"],
        )
        claimed = store.claim_next_job(session, platforms={"douyin"})
        assert claimed is not None and claimed.id == job.id
        assert store.start_stage(session, job.id, "collect") is not None
        checkpoint = live_store.create_checkpoint(
            session,
            job_id=job.id,
            stage="collect",
            kind="insufficient_evidence",
            option_keys=["continue_with_current_evidence"],
            default_option_key="continue_with_current_evidence",
            context={"schemaVersion": 1, "summary": "review evidence"},
            deadline_at=datetime.utcnow() + timedelta(seconds=10),
        )
        assert store.pause_for_decision(session, job.id, "collect")

        assert store.recover_interrupted(session) == 1

        session.refresh(job)
        session.refresh(job.stages[0])
        assert job.status == JOB_STATUS_INTERRUPTED
        assert job.stages[0].status == STAGE_STATUS_FAILED
        assert job.stages[0].error_message == "service interrupted"
        authoritative = live_store.get_checkpoint(
            session,
            job_id=job.id,
            checkpoint_id=checkpoint.id,
        )
        assert authoritative is not None
        assert authoritative.status == "cancelled"
        assert authoritative.resolution_source == "system"


def test_pipeline_status_defaults_come_from_shared_state_module(db):
    from tiktok_bot_core.models import pipeline_states
    from tiktok_bot_core.models.entities import PipelineJob, PipelineJobStage
    from tiktok_bot_core.storage import pipeline_job_store

    assert pipeline_job_store.JOB_STATUSES is pipeline_states.JOB_STATUSES
    assert pipeline_job_store.STAGE_STATUSES is pipeline_states.STAGE_STATUSES
    assert (
        PipelineJob.__table__.c.status.default.arg
        == pipeline_states.JOB_STATUS_QUEUED
    )
    assert (
        PipelineJobStage.__table__.c.status.default.arg
        == pipeline_states.STAGE_STATUS_PENDING
    )


def test_get_job_and_set_job_status_persist_requested_fields(db):
    from tiktok_bot_core.storage.pipeline_job_store import (
        JOB_STATUS_FAILED,
        JOB_STATUS_QUEUED,
        JOB_STATUS_RUNNING,
        PipelineJobStore,
    )

    store = PipelineJobStore()
    finished_at = datetime(2026, 7, 26, 11, 0, 0)
    with db.session() as session:
        job = store.create_job(
            session,
            platform="douyin",
            account_mode="auto",
            account_id=None,
            stages=["collect"],
        )

        fetched = store.get_job(session, job.id)
        started = store.set_job_status(
            session,
            job.id,
            JOB_STATUS_RUNNING,
            expected_statuses={JOB_STATUS_QUEUED},
            started_at=datetime(2026, 7, 26, 10, 59, 0),
        )
        updated = store.set_job_status(
            session,
            job.id,
            JOB_STATUS_FAILED,
            expected_statuses={JOB_STATUS_RUNNING},
            finished_at=finished_at,
            error_summary="collector failed",
        )

        assert fetched is job
        assert started is True
        assert updated is True
        assert job.status == JOB_STATUS_FAILED
        assert job.finished_at == finished_at
        assert job.error_summary == "collector failed"
        assert store.get_job(session, "missing-job") is None


def test_stage_lifecycle_tracks_attempt_timestamps_result_and_error(db):
    from tiktok_bot_core.storage.pipeline_job_store import (
        JOB_STATUS_QUEUED,
        JOB_STATUS_RUNNING,
        STAGE_STATUS_FAILED,
        STAGE_STATUS_PENDING,
        STAGE_STATUS_RUNNING,
        STAGE_STATUS_SUCCEEDED,
        PipelineJobStore,
    )

    store = PipelineJobStore()
    with db.session() as session:
        job = store.create_job(
            session,
            platform="douyin",
            account_mode="auto",
            account_id=None,
            stages=["collect"],
        )
        stage = job.stages[0]
        assert stage.status == STAGE_STATUS_PENDING
        assert stage.attempt == 0
        assert stage.started_at is None
        assert stage.finished_at is None

        assert store.start_stage(session, job.id, "collect") is None
        assert store.set_job_status(
            session,
            job.id,
            JOB_STATUS_RUNNING,
            expected_statuses={JOB_STATUS_QUEUED},
        )

        started = store.start_stage(session, job.id, "collect")
        assert started is stage
        assert stage.status == STAGE_STATUS_RUNNING
        assert stage.attempt == 1
        assert stage.started_at is not None
        assert stage.finished_at is None
        assert job.current_stage == "collect"

        failed = store.finish_stage(
            session,
            job.id,
            "collect",
            STAGE_STATUS_FAILED,
            result={"saved": 2},
            error="temporary failure",
        )
        assert failed is stage
        assert stage.status == STAGE_STATUS_FAILED
        assert stage.finished_at is not None
        assert stage.result_json == {"saved": 2}
        assert stage.error_message == "temporary failure"

        retried = store.start_stage(session, job.id, "collect")
        assert retried is stage
        assert stage.status == STAGE_STATUS_RUNNING
        assert stage.attempt == 2
        assert stage.finished_at is None
        assert stage.error_message == ""

        succeeded = store.finish_stage(
            session,
            job.id,
            "collect",
            STAGE_STATUS_SUCCEEDED,
            result={"saved": 3},
        )
        assert succeeded is stage
        assert stage.status == STAGE_STATUS_SUCCEEDED
        assert stage.finished_at is not None
        assert stage.result_json == {"saved": 3}
        assert stage.error_message == ""
        assert (
            store.finish_stage(
                session,
                job.id,
                "collect",
                STAGE_STATUS_FAILED,
                error="late failure",
            )
            is None
        )


def test_list_job_user_ids_filters_status_and_sorts_ids(db):
    from tiktok_bot_core.models.entities import User
    from tiktok_bot_core.storage.pipeline_job_store import PipelineJobStore

    store = PipelineJobStore()
    with db.session() as session:
        job = store.create_job(
            session,
            platform="douyin",
            account_mode="auto",
            account_id=None,
            stages=["collect"],
        )
        users = [
            User(
                platform="douyin",
                tiktok_id=f"douyin-user-{index}",
                username=f"douyin-user-{index}",
                status=status,
            )
            for index, status in enumerate(
                ["qualified", "pending", "qualified"],
                start=1,
            )
        ]
        session.add_all(users)
        session.flush()
        for user in reversed(users):
            store.link_user(
                session,
                job.id,
                user.id,
                "collect",
                status=user.status,
            )

        all_ids = store.list_job_user_ids(session, job.id)
        qualified_ids = store.list_job_user_ids(
            session,
            job.id,
            user_status="qualified",
        )

        assert all_ids == sorted(user.id for user in users)
        assert qualified_ids == sorted([users[0].id, users[2].id])


@pytest.mark.parametrize(
    "stages",
    [
        [],
        ["collect", "collect"],
        ["collect", "unknown"],
    ],
)
def test_create_job_rejects_invalid_stage_lists(db, stages):
    from tiktok_bot_core.storage.pipeline_job_store import PipelineJobStore

    store = PipelineJobStore()
    with db.session() as session:
        with pytest.raises(ValueError):
            store.create_job(
                session,
                platform="douyin",
                account_mode="auto",
                account_id=None,
                stages=stages,
            )


def test_state_machine_rejects_unknown_and_terminal_rollbacks(db):
    from tiktok_bot_core.models.pipeline_states import (
        JOB_STATUS_CANCELLED,
        JOB_STATUS_QUEUED,
        JOB_STATUS_RUNNING,
        STAGE_STATUS_RUNNING,
        validate_job_status,
        validate_job_transition,
        validate_stage_status,
        validate_stage_transition,
    )

    with pytest.raises(ValueError, match="Unknown pipeline job status"):
        validate_job_status("not-a-status")
    with pytest.raises(ValueError, match="Unknown pipeline stage status"):
        validate_stage_status("not-a-status")
    with pytest.raises(ValueError, match="Invalid pipeline job transition"):
        validate_job_transition(JOB_STATUS_CANCELLED, JOB_STATUS_RUNNING)
    with pytest.raises(ValueError, match="Invalid pipeline stage transition"):
        validate_stage_transition("succeeded", STAGE_STATUS_RUNNING)
    assert validate_job_transition(JOB_STATUS_QUEUED, JOB_STATUS_CANCELLED)


def test_terminal_job_status_cannot_be_overwritten(db):
    from tiktok_bot_core.storage.pipeline_job_store import (
        JOB_STATUS_CANCELLED,
        JOB_STATUS_QUEUED,
        JOB_STATUS_RUNNING,
        PipelineJobStore,
    )

    store = PipelineJobStore()
    with db.session() as session:
        job = store.create_job(
            session,
            platform="douyin",
            account_mode="auto",
            account_id=None,
            stages=["collect"],
        )
        assert store.set_job_status(
            session,
            job.id,
            JOB_STATUS_CANCELLED,
            expected_statuses={JOB_STATUS_QUEUED},
            finished_at=datetime.utcnow(),
        )
        with pytest.raises(ValueError, match="Invalid pipeline job transition"):
            store.set_job_status(
                session,
                job.id,
                JOB_STATUS_RUNNING,
                expected_statuses={JOB_STATUS_CANCELLED},
            )
        assert store.get_job(session, job.id).status == JOB_STATUS_CANCELLED


def test_late_completion_cannot_overwrite_cancelling_job(db):
    from tiktok_bot_core.storage.pipeline_job_store import (
        JOB_STATUS_CANCELLING,
        JOB_STATUS_QUEUED,
        JOB_STATUS_RUNNING,
        JOB_STATUS_SUCCEEDED,
        PipelineJobStore,
    )

    store = PipelineJobStore()
    with db.session() as session:
        job = store.create_job(
            session,
            platform="douyin",
            account_mode="auto",
            account_id=None,
            stages=["collect"],
        )
        assert store.set_job_status(
            session,
            job.id,
            JOB_STATUS_RUNNING,
            expected_statuses={JOB_STATUS_QUEUED},
        )
        store.request_cancel(session, job.id)
        assert job.status == JOB_STATUS_CANCELLING

        completed = store.set_job_status(
            session,
            job.id,
            JOB_STATUS_SUCCEEDED,
            expected_statuses={JOB_STATUS_RUNNING},
            finished_at=datetime.utcnow(),
        )

        assert completed is False
        assert store.get_job(session, job.id).status == JOB_STATUS_CANCELLING


def test_cancel_and_claim_race_never_leaves_job_running(db):
    from tiktok_bot_core.storage.pipeline_job_store import (
        JOB_STATUS_CANCELLED,
        JOB_STATUS_CANCELLING,
        PipelineJobStore,
    )

    store = PipelineJobStore()
    with db.session() as session:
        job = store.create_job(
            session,
            platform="douyin",
            account_mode="auto",
            account_id=None,
            stages=["collect"],
        )
        job_id = job.id

    barrier = threading.Barrier(3)

    def claim():
        session = db.SessionLocal()
        try:
            barrier.wait(timeout=5)
            claimed = store.claim_next_job(
                session,
                platforms={"douyin"},
            )
            claimed_id = claimed.id if claimed is not None else None
            session.commit()
            return claimed_id
        finally:
            session.close()

    def cancel():
        session = db.SessionLocal()
        try:
            barrier.wait(timeout=5)
            cancelled = store.request_cancel(session, job_id)
            status = cancelled.status if cancelled is not None else None
            session.commit()
            return status
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        claim_result = executor.submit(claim)
        cancel_result = executor.submit(cancel)
        barrier.wait(timeout=5)
        claimed_id = claim_result.result(timeout=10)
        cancel_status = cancel_result.result(timeout=10)

    with db.session() as session:
        final_job = store.get_job(session, job_id)
        assert final_job is not None
        assert final_job.status in {
            JOB_STATUS_CANCELLED,
            JOB_STATUS_CANCELLING,
        }
        if claimed_id is None:
            assert cancel_status == JOB_STATUS_CANCELLED
        else:
            assert claimed_id == job_id
            assert cancel_status == JOB_STATUS_CANCELLING


def test_add_user_rejects_existing_cross_platform_identity(db):
    from tiktok_bot_core.storage.sqlite_store import SqliteStore

    store = SqliteStore()
    with db.session() as session:
        store.add_user(
            session,
            tiktok_id="shared-provider-id",
            username="first",
            platform="douyin",
        )
    with pytest.raises(ValueError, match="platform"):
        with db.session() as session:
            store.add_user(
                session,
                tiktok_id="shared-provider-id",
                username="second",
                platform="tiktok",
            )


def test_job_user_state_columns_are_migrated_for_existing_table():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as file:
        path = Path(file.name)
    database = Database(f"sqlite:///{path}")
    try:
        with database.engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE pipeline_job_users ("
                    "job_id VARCHAR(36) NOT NULL, "
                    "user_id INTEGER NOT NULL, "
                    "source_stage VARCHAR(20) NOT NULL, "
                    "created_at DATETIME, "
                    "PRIMARY KEY (job_id, user_id))"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO pipeline_job_users "
                    "(job_id, user_id, source_stage) "
                    "VALUES ('legacy-job', 1, 'collect')"
                )
            )

        database.init()

        columns = {
            column["name"]
            for column in inspect(database.engine).get_columns(
                "pipeline_job_users"
            )
        }
        assert {"status", "category", "updated_at"} <= columns
        with database.engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT status, category FROM pipeline_job_users "
                    "WHERE job_id = 'legacy-job' AND user_id = 1"
                )
            ).one()
        assert row == ("pending", "unknown")
    finally:
        database.engine.dispose()
        gc.collect()
        try:
            path.unlink()
        except PermissionError:
            pass


def test_job_strategy_and_message_uniqueness_indexes_exist(db):
    indexes = {
        table: {
            tuple(index["column_names"])
            for index in inspect(db.engine).get_indexes(table)
            if index.get("unique")
        }
        for table in ("strategies", "messages")
    }
    assert ("job_id", "user_id") in indexes["strategies"]
    assert ("job_id", "user_id", "message_type") in indexes["messages"]


class _RetryAvailableProvider:
    async def check_available(self, _account):
        from tiktok_bot_core.browser.providers import BrowserAvailability

        return BrowserAvailability(available=True)


class _RetryCloneFailingStore:
    """Delegate campaign reads/writes but fail during keyword cloning."""

    def __init__(self):
        from tiktok_bot_core.storage.acquisition_store import AcquisitionStore

        self._delegate = AcquisitionStore()
        self.keyword_writes = 0

    def get_campaign(self, session, job_id):
        return self._delegate.get_campaign(session, job_id)

    def list_keywords(self, session, job_id):
        return self._delegate.list_keywords(session, job_id)

    def create_campaign(self, session, **kwargs):
        return self._delegate.create_campaign(session, **kwargs)

    def create_keyword(self, session, **kwargs):
        self.keyword_writes += 1
        if self.keyword_writes == 2:
            raise RuntimeError("injected retry keyword clone failure")
        return self._delegate.create_keyword(session, **kwargs)


def _retry_service(db, *, acquisition_store=None):
    from tiktok_bot_core.browser.providers import BrowserProviderRegistry
    from tiktok_bot_core.services.pipeline_jobs import PipelineJobService

    return PipelineJobService(
        database=db,
        providers=BrowserProviderRegistry(
            {"douyin": _RetryAvailableProvider()}
        ),
        acquisition_store=acquisition_store,
    )


def _add_retry_account(db):
    from tiktok_bot_core.models.entities import TikTokAccount

    with db.session() as session:
        account = TikTokAccount(
            platform="douyin",
            username="retry-account",
            status="logged_in",
            cookies_json="[]",
        )
        session.add(account)
        session.flush()
        return account.id


def _fail_retry_job(db, job_id, *, failed_stage="filter"):
    from tiktok_bot_core.models.pipeline_states import (
        JOB_STATUS_FAILED,
        JOB_STATUS_RUNNING,
        STAGE_STATUS_FAILED,
        STAGE_STATUS_SUCCEEDED,
    )
    from tiktok_bot_core.storage.pipeline_job_store import PipelineJobStore

    store = PipelineJobStore()
    with db.session() as session:
        assert store.claim_job(session, job_id, account_id=1)
        job = store.get_job(session, job_id)
        for stage in sorted(job.stages, key=lambda item: item.stage_order):
            store.start_stage(session, job_id, stage.stage)
            if stage.stage == failed_stage:
                store.finish_stage(
                    session,
                    job_id,
                    stage.stage,
                    STAGE_STATUS_FAILED,
                    error=f"{stage.stage} failed",
                )
                break
            store.finish_stage(
                session,
                job_id,
                stage.stage,
                STAGE_STATUS_SUCCEEDED,
            )
        store.set_job_status(
            session,
            job_id,
            JOB_STATUS_FAILED,
            expected_statuses={JOB_STATUS_RUNNING},
            error_summary=f"{failed_stage} failed",
            finished_at=datetime.utcnow(),
        )


@pytest.mark.asyncio
async def test_retry_acquisition_job_restarts_collect_and_clones_campaign_keywords_atomically(
    db,
):
    from tiktok_bot_core.models.entities import (
        AcquisitionCampaign,
        AcquisitionKeyword,
        CandidateAssessment,
        CandidateReviewAudit,
        DiscoveryEvidence,
        PipelineJob,
        User,
    )
    from tiktok_bot_core.storage.acquisition_store import AcquisitionStore
    from tiktok_bot_core.storage.pipeline_job_store import PipelineJobStore

    account_id = _add_retry_account(db)
    service = _retry_service(db)
    original = await service.create_job(
        platform="douyin",
        account_mode="specified",
        account_id=account_id,
        stages=["collect", "filter", "strategy", "report"],
        config_snapshot={"businessMode": "ai_acquisition", "keep": True},
    )
    acquisition_store = AcquisitionStore()
    with db.session() as session:
        acquisition_store.create_campaign(
            session,
            job_id=original.id,
            platform="douyin",
            countries=["CN"],
            languages=["zh-CN"],
            industries=["power infrastructure"],
            products=["transformer"],
            customer_roles=["contractor"],
            hard_conditions={"notListed": True},
            preference_conditions={"employeeCount": "10-20"},
            excluded_targets=["consumer"],
            search_budget={"maxVideos": 30},
            keyword_mix={"effectivePercent": 70, "newPercent": 30},
        )
        first = acquisition_store.create_keyword(
            session,
            job_id=original.id,
            platform="douyin",
            text="power grid contractor",
            language="zh-cn",
            keyword_type="industry",
            source="manual",
            status="effective",
        )
        acquisition_store.update_keyword_stats(
            session,
            first.id,
            usage_count=9,
            video_count=8,
            relevant_video_count=7,
            candidate_count=6,
            qualified_count=5,
            reply_count=4,
            business_lead_count=3,
            last_used_at=datetime.utcnow(),
        )
        acquisition_store.create_keyword(
            session,
            job_id=original.id,
            platform="douyin",
            text="transformer procurement",
            language="zh-cn",
            keyword_type="intent",
            source="ai_expansion",
            status="testing",
        )
        user = User(
            platform="douyin",
            tiktok_id="retry-source-candidate",
            username="retry-source-candidate",
        )
        session.add(user)
        session.flush()
        PipelineJobStore().link_user(
            session,
            original.id,
            user.id,
            "collect",
        )
        acquisition_store.add_evidence(
            session,
            job_id=original.id,
            user_id=user.id,
            keyword_id=first.id,
            source_type="video_comment",
            raw_text="Need a transformer supplier",
        )
        acquisition_store.create_assessment(
            session,
            job_id=original.id,
            user_id=user.id,
            labels=["buyer"],
            match_score=88,
            confidence_score=80,
            suggested_status="manual_review",
        )
        acquisition_store.transition_candidate(
            session,
            job_id=original.id,
            user_id=user.id,
            target_status="qualified",
            action="approve",
            operator="reviewer@example.test",
        )
    _fail_retry_job(db, original.id, failed_stage="filter")

    retried = await service.retry_job(original.id)

    # The returned object remains usable after retry_job's transaction closes.
    assert retried.retry_of_job_id == original.id
    assert retried.status == "queued"
    assert retried.config_snapshot_json == {
        "businessMode": "ai_acquisition",
        "keep": True,
    }
    assert retried.stages_json == ["collect", "filter", "strategy", "report"]
    assert [stage.stage for stage in retried.stages] == retried.stages_json
    with db.session() as session:
        campaign = session.scalar(
            select(AcquisitionCampaign).where(
                AcquisitionCampaign.job_id == retried.id
            )
        )
        assert campaign is not None
        assert {
            "platform": campaign.platform,
            "countries": campaign.countries,
            "languages": campaign.languages,
            "industries": campaign.industries,
            "products": campaign.products,
            "customer_roles": campaign.customer_roles,
            "hard_conditions": campaign.hard_conditions,
            "preference_conditions": campaign.preference_conditions,
            "excluded_targets": campaign.excluded_targets,
            "search_budget": campaign.search_budget,
            "keyword_mix": campaign.keyword_mix,
        } == {
            "platform": "douyin",
            "countries": ["CN"],
            "languages": ["zh-CN"],
            "industries": ["power infrastructure"],
            "products": ["transformer"],
            "customer_roles": ["contractor"],
            "hard_conditions": {"notListed": True},
            "preference_conditions": {"employeeCount": "10-20"},
            "excluded_targets": ["consumer"],
            "search_budget": {"maxVideos": 30},
            "keyword_mix": {"effectivePercent": 70, "newPercent": 30},
        }
        keywords = list(
            session.scalars(
                select(AcquisitionKeyword)
                .where(AcquisitionKeyword.job_id == retried.id)
                .order_by(AcquisitionKeyword.id.asc())
            )
        )
        assert [
            (
                keyword.text,
                keyword.language,
                keyword.keyword_type,
                keyword.source,
                keyword.status,
            )
            for keyword in keywords
        ] == [
            (
                "power grid contractor",
                "zh-cn",
                "industry",
                "manual",
                "effective",
            ),
            (
                "transformer procurement",
                "zh-cn",
                "intent",
                "ai_expansion",
                "testing",
            ),
        ]
        for keyword in keywords:
            assert (
                keyword.usage_count,
                keyword.video_count,
                keyword.relevant_video_count,
                keyword.candidate_count,
                keyword.qualified_count,
                keyword.reply_count,
                keyword.business_lead_count,
                keyword.last_used_at,
            ) == (0, 0, 0, 0, 0, 0, 0, None)
        assert session.scalar(
            select(func.count()).select_from(DiscoveryEvidence).where(
                DiscoveryEvidence.job_id == retried.id
            )
        ) == 0
        assert session.scalar(
            select(func.count()).select_from(CandidateAssessment).where(
                CandidateAssessment.job_id == retried.id
            )
        ) == 0
        assert session.scalar(
            select(func.count()).select_from(CandidateReviewAudit).where(
                CandidateReviewAudit.job_id == retried.id
            )
        ) == 0
        assert session.scalar(
            select(func.count()).select_from(PipelineJob).where(
                PipelineJob.retry_of_job_id == original.id
            )
        ) == 1


@pytest.mark.asyncio
async def test_retry_legacy_job_keeps_first_failed_stage_semantics(db):
    account_id = _add_retry_account(db)
    service = _retry_service(db)
    original = await service.create_job(
        platform="douyin",
        account_mode="specified",
        account_id=account_id,
        stages=["collect", "filter", "strategy"],
    )
    _fail_retry_job(db, original.id, failed_stage="filter")

    retried = await service.retry_job(original.id)

    assert retried.retry_of_job_id == original.id
    assert retried.stages_json == ["filter", "strategy"]


@pytest.mark.asyncio
async def test_retry_acquisition_clone_failure_rolls_back_retry_job(db):
    from tiktok_bot_core.models.entities import (
        AcquisitionCampaign,
        AcquisitionKeyword,
        PipelineJob,
        PipelineJobStage,
    )
    from tiktok_bot_core.storage.acquisition_store import AcquisitionStore

    account_id = _add_retry_account(db)
    setup_service = _retry_service(db)
    original = await setup_service.create_job(
        platform="douyin",
        account_mode="specified",
        account_id=account_id,
        stages=["collect", "filter"],
    )
    acquisition_store = AcquisitionStore()
    with db.session() as session:
        acquisition_store.create_campaign(
            session,
            job_id=original.id,
            platform="douyin",
            industries=["power infrastructure"],
        )
        for text_value in ("grid contractor", "transformer buyer"):
            acquisition_store.create_keyword(
                session,
                job_id=original.id,
                platform="douyin",
                text=text_value,
            )
    _fail_retry_job(db, original.id, failed_stage="filter")
    service = _retry_service(
        db,
        acquisition_store=_RetryCloneFailingStore(),
    )

    with pytest.raises(
        RuntimeError,
        match="injected retry keyword clone failure",
    ):
        await service.retry_job(original.id)

    with db.session() as session:
        assert session.scalar(
            select(func.count()).select_from(PipelineJob)
        ) == 1
        assert session.scalar(
            select(func.count()).select_from(PipelineJobStage)
        ) == 2
        assert session.scalar(
            select(func.count()).select_from(AcquisitionCampaign)
        ) == 1
        assert session.scalar(
            select(func.count()).select_from(AcquisitionKeyword)
        ) == 2


def test_experience_rule_dimensions_are_migrated_for_existing_table():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as file:
        path = Path(file.name)
    database = Database(f"sqlite:///{path}")
    try:
        with database.engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE experience_rules ("
                    "id INTEGER PRIMARY KEY, "
                    "rule_type VARCHAR(50) NOT NULL, "
                    "rule_content TEXT NOT NULL, "
                    "effectiveness FLOAT NOT NULL DEFAULT 0, "
                    "sample_size INTEGER NOT NULL DEFAULT 0)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO experience_rules "
                    "(id, rule_type, rule_content) "
                    "VALUES (1, 'legacy', '{}')"
                )
            )

        database.init()

        columns = {
            column["name"]
            for column in inspect(database.engine).get_columns(
                "experience_rules"
            )
        }
        assert {"platform", "job_id"} <= columns
        with database.engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT platform, job_id FROM experience_rules "
                    "WHERE id = 1"
                )
            ).one()
        assert row == ("tiktok", None)
    finally:
        database.engine.dispose()
        gc.collect()
        try:
            path.unlink()
        except PermissionError:
            pass
