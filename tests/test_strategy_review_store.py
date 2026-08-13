"""Stage 03 strategy review model and migration contracts."""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from tiktok_bot_core.models.entities import PipelineJob, PipelineJobUser, Strategy, User
from tiktok_bot_core.storage.database import Database
from tiktok_bot_core.storage.sqlite_store import SqliteStore
from tiktok_bot_core.storage.strategy_review_store import (
    StrategyReviewEdit,
    StrategyReviewStore,
)


@pytest.fixture
def db(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'strategy-review.db'}")
    database.init()
    try:
        yield database
    finally:
        database.engine.dispose()


def _add_user(database: Database, *, suffix: str) -> int:
    with database.session() as session:
        user = User(
            platform="douyin",
            tiktok_id=f"douyin:strategy-review:{suffix}",
            username=f"strategy-review-{suffix}",
        )
        session.add(user)
        session.flush()
        return user.id


def _seed_review_strategy(
    database: Database,
    *,
    job_id: str,
    platform: str = "douyin",
    qualification_status: str = "qualified",
    review_status: str = "draft",
    suffix: str,
) -> int:
    with database.session() as session:
        job = session.get(PipelineJob, job_id)
        if job is None:
            session.add(PipelineJob(id=job_id, platform=platform, stages_json=[]))
        user = User(
            platform=platform,
            tiktok_id=f"{platform}:strategy-review:{suffix}",
            username=f"strategy-review-{suffix}",
        )
        session.add(user)
        session.flush()
        session.add(
            PipelineJobUser(
                job_id=job_id,
                user_id=user.id,
                source_stage="filter",
                status=qualification_status,
                qualification_status=qualification_status,
            )
        )
        strategy = Strategy(
            job_id=job_id,
            user_id=user.id,
            persona="buyer",
            strategy_type="soft_sell",
            comment_template="A safe business discussion.",
            dm_template="",
            action_plan="Start with a neutral exchange.",
            priority=3,
            review_status=review_status,
        )
        session.add(strategy)
        session.flush()
        return strategy.id


def test_new_strategy_defaults_to_unreviewed_draft(db):
    user_id = _add_user(db, suffix="defaults")

    with db.session() as session:
        strategy = Strategy(user_id=user_id)
        session.add(strategy)
        session.flush()

        assert strategy.review_status == "draft"
        assert strategy.review_version == 0
        assert strategy.reviewed_at is None
        assert strategy.reviewed_by is None
        assert strategy.review_reason is None
        assert isinstance(strategy.updated_at, datetime)


def test_new_database_rejects_unknown_strategy_review_status(db):
    user_id = _add_user(db, suffix="bad-status")

    with pytest.raises(IntegrityError):
        with db.session() as session:
            session.add(
                Strategy(
                    user_id=user_id,
                    review_status="silently_approved",
                )
            )


def test_new_database_rejects_negative_strategy_review_version(db):
    user_id = _add_user(db, suffix="bad-version")

    with pytest.raises(IntegrityError):
        with db.session() as session:
            session.add(Strategy(user_id=user_id, review_version=-1))


def test_old_strategy_table_migration_adds_review_fields_and_backfills_draft(
    tmp_path,
):
    database = Database(f"sqlite:///{tmp_path / 'legacy-strategies.db'}")
    try:
        with database.engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE strategies ("
                    "id INTEGER PRIMARY KEY, "
                    "user_id INTEGER NOT NULL, "
                    "persona VARCHAR(50) NOT NULL DEFAULT '', "
                    "strategy_type VARCHAR(50) NOT NULL DEFAULT 'soft_sell', "
                    "comment_template TEXT NOT NULL DEFAULT '', "
                    "dm_template TEXT NOT NULL DEFAULT '', "
                    "action_plan TEXT NOT NULL DEFAULT '', "
                    "priority INTEGER NOT NULL DEFAULT 3, "
                    "created_at DATETIME NOT NULL)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO strategies "
                    "(id, user_id, persona, created_at) "
                    "VALUES (1, 7, 'legacy buyer', '2026-08-01 12:00:00')"
                )
            )

        database.init()
        database.init()

        columns = {
            column["name"]: column
            for column in inspect(database.engine).get_columns("strategies")
        }
        assert {
            "review_status",
            "review_version",
            "reviewed_at",
            "reviewed_by",
            "review_reason",
            "updated_at",
        } <= columns.keys()

        with database.engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT review_status, review_version, reviewed_at, "
                    "reviewed_by, review_reason, updated_at "
                    "FROM strategies WHERE id = 1"
                )
            ).one()

        assert row.review_status == "draft"
        assert row.review_version == 0
        assert row.reviewed_at is None
        assert row.reviewed_by is None
        assert row.review_reason is None
        assert row.updated_at is not None
    finally:
        database.engine.dispose()


def test_review_list_is_job_platform_qualified_scoped_and_paginated(db):
    first = _seed_review_strategy(db, job_id="job-a", suffix="a-1")
    second = _seed_review_strategy(db, job_id="job-a", suffix="a-2")
    _seed_review_strategy(db, job_id="job-b", suffix="b-1")
    _seed_review_strategy(
        db, job_id="job-a", qualification_status="rejected", suffix="rejected"
    )
    store = StrategyReviewStore()

    with db.session() as session:
        page = store.list_strategies(
            session,
            job_id="job-a",
            platform="douyin",
            review_status="draft",
            limit=1,
            offset=1,
        )

    assert page.total == 2
    assert [item.id for item in page.items] == [second]
    assert first != second


def test_safe_edit_resets_draft_increments_version_and_ai_regeneration_does_too(db):
    strategy_id = _seed_review_strategy(
        db, job_id="job-edit", review_status="approved", suffix="edit"
    )
    review_store = StrategyReviewStore()

    with db.session() as session:
        edited = review_store.edit(
            session,
            job_id="job-edit",
            platform="douyin",
            strategy_id=strategy_id,
            expected_version=0,
            changes=StrategyReviewEdit(
                persona="buyer",
                strategy_type="partnership",
                comment_template="A neutral partnership discussion.",
                dm_template="",
                action_plan="Ask about current requirements.",
                priority=2,
            ),
        )
        assert edited.applied is True
        assert edited.current.review_status == "draft"
        assert edited.current.review_version == 1

        with pytest.raises(ValueError):
            review_store.edit(
                session,
                job_id="job-edit",
                platform="douyin",
                strategy_id=strategy_id,
                expected_version=1,
                changes=StrategyReviewEdit(
                    persona="buyer",
                    strategy_type="soft_sell",
                    comment_template="Visit https://unsafe.example",
                    dm_template="",
                    action_plan="",
                    priority=3,
                ),
            )
        assert session.get(Strategy, strategy_id).review_version == 1

        regenerated = SqliteStore().add_strategy(
            session,
            job_id="job-edit",
            user_id=edited.current.user_id,
            persona="supplier",
            strategy_type="soft_sell",
            comment_template="A regenerated safe discussion.",
            dm_template="",
            action_plan="Begin neutrally.",
            priority=3,
        )
        assert regenerated.review_status == "draft"
        assert regenerated.review_version == 2


def test_review_cas_has_one_winner_and_batch_validates_each_item(db):
    winner_id = _seed_review_strategy(db, job_id="job-cas", suffix="winner")
    batch_ok = _seed_review_strategy(db, job_id="job-cas", suffix="batch-ok")
    batch_skip = _seed_review_strategy(
        db,
        job_id="job-cas",
        qualification_status="rejected",
        suffix="batch-skip",
    )
    batch_unsafe = _seed_review_strategy(
        db, job_id="job-cas", suffix="batch-unsafe"
    )
    with db.session() as session:
        session.get(Strategy, batch_unsafe).comment_template = "Call +1 555 123 4567"
    store = StrategyReviewStore()
    first_session = db.SessionLocal()
    second_session = db.SessionLocal()
    try:
        first = store.approve(
            first_session,
            job_id="job-cas",
            platform="douyin",
            strategy_id=winner_id,
            expected_version=0,
            operator="reviewer-a",
        )
        first_session.commit()
        second = store.reject(
            second_session,
            job_id="job-cas",
            platform="douyin",
            strategy_id=winner_id,
            expected_version=0,
            operator="reviewer-b",
            reason="not a fit",
        )
        assert first.applied is True
        assert second.applied is False
        assert second.current.review_status == "approved"
        assert second.current.review_version == 1
    finally:
        first_session.close()
        second_session.close()

    with db.session() as session:
        batch = store.approve_batch(
            session,
            job_id="job-cas",
            platform="douyin",
            expected_versions={
                batch_ok: 0,
                batch_skip: 0,
                batch_unsafe: 0,
                winner_id: 0,
            },
            operator="reviewer-a",
        )
        assert batch.total == 4
        assert batch.approved == 1
        assert batch.skipped == 2
        assert batch.conflicted == 1
        assert session.get(Strategy, batch_unsafe).review_status == "draft"
