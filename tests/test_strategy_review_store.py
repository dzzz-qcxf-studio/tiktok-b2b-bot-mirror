"""Stage 03 strategy review model and migration contracts."""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from tiktok_bot_core.models.entities import Strategy, User
from tiktok_bot_core.storage.database import Database


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
