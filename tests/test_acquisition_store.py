"""阶段 01—02 获客数据、迁移和状态机测试。"""

from __future__ import annotations

import gc
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import func, inspect, select, text

from tiktok_bot_core.models.entities import (
    AcquisitionCampaign,
    AcquisitionKeyword,
    CandidateAssessment,
    CandidateReviewAudit,
    DiscoveryEvidence,
    PipelineJob,
    PipelineJobUser,
    User,
)
from tiktok_bot_core.models.pipeline_states import (
    QUALIFICATION_STATUS_MANUAL_REVIEW,
    QUALIFICATION_STATUS_QUALIFIED,
)
from tiktok_bot_core.storage.acquisition_store import AcquisitionStore
from tiktok_bot_core.storage.database import (
    Database,
    _configure_sqlite_connection,
)
from tiktok_bot_core.storage.pipeline_job_store import PipelineJobStore


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


def _seed_candidate(
    database: Database, identity: str = "buyer-one"
) -> tuple[str, int]:
    with database.session() as session:
        job = PipelineJob(
            platform="tiktok",
            stages_json=["collect", "filter"],
        )
        user = User(
            platform="tiktok",
            tiktok_id=f"tiktok:{identity}",
            username=identity,
        )
        session.add_all([job, user])
        session.flush()
        session.add(
            PipelineJobUser(
                job_id=job.id,
                user_id=user.id,
                source_stage="collect",
            )
        )
        return job.id, user.id


def test_acquisition_records_round_trip_and_keep_ai_separate_from_human(db):
    store = AcquisitionStore()
    job_id, user_id = _seed_candidate(db)

    with db.session() as session:
        campaign = store.create_campaign(
            session,
            job_id=job_id,
            platform="tiktok",
            countries=["VN"],
            languages=["vi"],
            industries=["power infrastructure"],
            products=["transformer"],
            customer_roles=["buyer", "contractor"],
            hard_conditions={"excluded_roles": ["consumer"]},
            preference_conditions={"employee_count": "10-20"},
            excluded_targets=["competitor"],
            search_budget={"max_videos": 40, "max_comments": 500},
            keyword_mix={"effective": 0.7, "new": 0.3},
        )
        keyword = store.create_keyword(
            session,
            job_id=job_id,
            platform="tiktok",
            text="thi cong tram bien ap",
            language="vi",
            keyword_type="industry",
            source="manual",
        )
        first = store.add_evidence(
            session,
            job_id=job_id,
            user_id=user_id,
            keyword_id=keyword.id,
            source_type="video_comment",
            keyword_text=keyword.text,
            video_id="video-1",
            video_url="https://www.tiktok.com/video/1",
            comment_id="comment-1",
            comment_url="https://www.tiktok.com/video/1?comment=1",
            author_id="buyer-one",
            author_url="https://www.tiktok.com/@buyer-one",
            raw_text="Can you quote this transformer?",
            translated_text="可以为这台变压器报价吗？",
            relevance_score=0.94,
            completeness_score=0.8,
        )
        second = store.add_evidence(
            session,
            job_id=job_id,
            user_id=user_id,
            keyword_id=keyword.id,
            source_type="author_profile",
            author_id="buyer-one",
            author_url="https://www.tiktok.com/@buyer-one",
            raw_text="Electrical contractor in Hanoi",
            relevance_score=0.87,
            completeness_score=0.72,
        )
        assessment = store.create_assessment(
            session,
            job_id=job_id,
            user_id=user_id,
            labels=["buyer", "contractor"],
            match_score=88.5,
            confidence_score=76.0,
            positive_evidence=["明确询价", "主页为电力承包商"],
            negative_evidence=[],
            missing_fields=["registered_capital"],
            reasoning="需求和行业匹配，企业资料仍需按需核验。",
            suggested_status="manual_review",
            model_provider="deepseek",
            model_name="deepseek-chat",
            schema_version="1.0",
        )

        # AI 建议只保存，不覆盖当前人工状态。
        link = session.get(PipelineJobUser, (job_id, user_id))
        assert link is not None
        assert link.qualification_status == QUALIFICATION_STATUS_MANUAL_REVIEW
        assert link.match_score == 88.5
        assert link.confidence_score == 76.0
        assert link.labels_json == []

        audit = store.transition_candidate(
            session,
            job_id=job_id,
            user_id=user_id,
            target_status=QUALIFICATION_STATUS_QUALIFIED,
            action="approve",
            operator="reviewer@example.test",
            reason="证据足够，人工通过",
            labels=["buyer", "contractor"],
            priority=1,
        )

        assert campaign.id is not None
        assert first.id != second.id
        assert assessment.suggested_status == "manual_review"
        assert audit.before_status == QUALIFICATION_STATUS_MANUAL_REVIEW
        assert audit.after_status == QUALIFICATION_STATUS_QUALIFIED

    with db.session() as session:
        assert session.scalar(
            select(AcquisitionCampaign).where(
                AcquisitionCampaign.job_id == job_id
            )
        ).countries == ["VN"]
        assert len(
            session.scalars(
                select(DiscoveryEvidence).where(
                    DiscoveryEvidence.job_id == job_id,
                    DiscoveryEvidence.user_id == user_id,
                )
            ).all()
        ) == 2
        link = session.get(PipelineJobUser, (job_id, user_id))
        assert link is not None
        assert link.qualification_status == "qualified"
        assert link.status == "qualified"  # 旧字段兼容映射
        assert link.labels_json == ["buyer", "contractor"]
        assert link.priority == 1
        assert link.manually_confirmed_at is not None
        assert session.scalar(select(CandidateAssessment)).match_score == 88.5
        assert session.scalar(select(CandidateReviewAudit)).operator == (
            "reviewer@example.test"
        )


def test_illegal_candidate_state_transition_is_rejected(db):
    store = AcquisitionStore()
    job_id, user_id = _seed_candidate(db)

    with pytest.raises(ValueError, match="Invalid qualification transition"):
        with db.session() as session:
            store.transition_candidate(
                session,
                job_id=job_id,
                user_id=user_id,
                target_status="not-a-state",
                action="approve",
                operator="reviewer@example.test",
            )

    with db.session() as session:
        store.transition_candidate(
            session,
            job_id=job_id,
            user_id=user_id,
            target_status="qualified",
            action="approve",
            operator="reviewer@example.test",
        )

    with pytest.raises(ValueError, match="Invalid qualification transition"):
        with db.session() as session:
            store.transition_candidate(
                session,
                job_id=job_id,
                user_id=user_id,
                target_status="need_enrichment",
                action="request_enrichment",
                operator="reviewer@example.test",
            )


def test_label_only_review_is_audited_without_fake_state_transition(db):
    store = AcquisitionStore()
    job_id, user_id = _seed_candidate(db)

    with db.session() as session:
        audit = store.update_candidate_labels(
            session,
            job_id=job_id,
            user_id=user_id,
            labels=["buyer", "distributor"],
            operator="reviewer@example.test",
            reason="人工校正身份",
        )
        assert audit.action == "update_labels"
        assert audit.before_status == "manual_review"
        assert audit.after_status == "manual_review"

    with db.session() as session:
        link = session.get(PipelineJobUser, (job_id, user_id))
        assert link is not None
        assert link.labels_json == ["buyer", "distributor"]
        audits = store.list_review_audits(session, job_id, user_id)
        assert len(audits) == 1


@pytest.mark.parametrize(
    ("target_status", "action", "expected_legacy"),
    [
        ("rejected", "reject", "rejected"),
        ("need_enrichment", "request_enrichment", "pending"),
    ],
)
def test_manual_review_transitions_have_independent_audits(
    db, target_status, action, expected_legacy
):
    store = AcquisitionStore()
    job_id, user_id = _seed_candidate(db)

    with db.session() as session:
        audit = store.transition_candidate(
            session,
            job_id=job_id,
            user_id=user_id,
            target_status=target_status,
            action=action,
            operator="reviewer@example.test",
            reason=f"人工操作 {action}",
        )
        assert audit.before_status == "manual_review"
        assert audit.after_status == target_status

    with db.session() as session:
        link = session.get(PipelineJobUser, (job_id, user_id))
        assert link is not None
        assert link.qualification_status == target_status
        assert link.status == expected_legacy
        audits = store.list_review_audits(session, job_id, user_id)
        assert [(item.action, item.after_status) for item in audits] == [
            (action, target_status)
        ]


@pytest.mark.parametrize(
    ("legacy_status", "expected_qualification"),
    [
        ("qualified", "qualified"),
        ("rejected", "rejected"),
        ("contacted", "qualified"),
        ("replied", "qualified"),
    ],
)
def test_pipeline_job_store_link_user_maps_legacy_status(
    db, legacy_status, expected_qualification
):
    store = PipelineJobStore()
    with db.session() as session:
        job = PipelineJob(platform="tiktok", stages_json=["collect", "filter"])
        user = User(
            platform="tiktok",
            tiktok_id=f"tiktok:link-{legacy_status}",
            username=f"link-{legacy_status}",
        )
        session.add_all([job, user])
        session.flush()
        store.link_user(
            session,
            job.id,
            user.id,
            "collect",
            status=legacy_status,
        )
        job_id, user_id = job.id, user.id

    with db.session() as session:
        link = session.get(PipelineJobUser, (job_id, user_id))
        assert link is not None
        assert link.status == legacy_status
        assert link.qualification_status == expected_qualification


@pytest.mark.parametrize(
    ("legacy_status", "expected_qualification"),
    [
        ("qualified", "qualified"),
        ("rejected", "rejected"),
        ("contacted", "qualified"),
        ("replied", "qualified"),
    ],
)
def test_pipeline_job_store_update_user_maps_legacy_status(
    db, legacy_status, expected_qualification
):
    store = PipelineJobStore()
    job_id, user_id = _seed_candidate(db)

    with db.session() as session:
        assert store.update_job_user(
            session,
            job_id,
            user_id,
            status=legacy_status,
        )

    with db.session() as session:
        link = session.get(PipelineJobUser, (job_id, user_id))
        assert link is not None
        assert link.status == legacy_status
        assert link.qualification_status == expected_qualification


def test_database_maps_new_qualification_status_back_to_legacy_status(db):
    store = AcquisitionStore()
    job_id, user_id = _seed_candidate(db)
    with db.session() as session:
        store.transition_candidate(
            session,
            job_id=job_id,
            user_id=user_id,
            target_status="rejected",
            action="reject",
            operator="reviewer@example.test",
        )
    with db.session() as session:
        session.execute(
            text(
                "UPDATE pipeline_job_users "
                "SET qualification_status='need_enrichment' "
                "WHERE job_id=:job_id AND user_id=:user_id"
            ),
            {"job_id": job_id, "user_id": user_id},
        )

    with db.session() as session:
        link = session.get(PipelineJobUser, (job_id, user_id))
        assert link is not None
        assert link.qualification_status == "need_enrichment"
        assert link.status == "pending"


def test_sqlite_connection_explicitly_disables_recursive_triggers():
    class RecordingCursor:
        def __init__(self):
            self.commands: list[str] = []
            self.closed = False

        def execute(self, command: str):
            self.commands.append(command)

        def close(self):
            self.closed = True

    class RecordingConnection:
        def __init__(self):
            self.recording_cursor = RecordingCursor()

        def cursor(self):
            return self.recording_cursor

    connection = RecordingConnection()
    _configure_sqlite_connection(connection, None)

    assert "PRAGMA recursive_triggers=OFF" in (
        connection.recording_cursor.commands
    )
    assert connection.recording_cursor.closed


def test_real_sqlite_connection_keeps_need_enrichment_stable(db):
    with db.engine.connect() as connection:
        recursive_triggers = connection.execute(
            text("PRAGMA recursive_triggers")
        ).scalar_one()
    assert recursive_triggers == 0

    store = AcquisitionStore()
    job_id, user_id = _seed_candidate(db)
    with db.session() as session:
        store.transition_candidate(
            session,
            job_id=job_id,
            user_id=user_id,
            target_status="need_enrichment",
            action="request_enrichment",
            operator="reviewer@example.test",
        )

    with db.session() as session:
        link = session.get(PipelineJobUser, (job_id, user_id))
        assert link is not None
        assert link.qualification_status == "need_enrichment"
        assert link.status == "pending"


def test_duplicate_message_migration_fails_closed_and_preserves_reply(caplog):
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as file:
        path = Path(file.name)
    database = Database(f"sqlite:///{path}")
    try:
        with database.engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE messages ("
                    "id INTEGER PRIMARY KEY, job_id VARCHAR(36), "
                    "user_id INTEGER NOT NULL, message_type VARCHAR(20) NOT NULL)"
                )
            )
            connection.execute(
                text(
                    "CREATE TABLE replies ("
                    "id INTEGER PRIMARY KEY, message_id INTEGER NOT NULL, "
                    "FOREIGN KEY(message_id) REFERENCES messages(id))"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO messages (id, job_id, user_id, message_type) "
                    "VALUES (1, 'job-1', 9, 'dm'), (2, 'job-1', 9, 'dm')"
                )
            )
            connection.execute(
                text("INSERT INTO replies (id, message_id) VALUES (10, 2)")
            )

        with caplog.at_level("ERROR"):
            with pytest.raises(RuntimeError, match="重复 messages"):
                database.init()

        with database.engine.connect() as connection:
            assert connection.execute(
                text("SELECT COUNT(*) FROM messages")
            ).scalar_one() == 2
            assert connection.execute(
                text("SELECT message_id FROM replies WHERE id=10")
            ).scalar_one() == 2
        assert "拒绝自动删除或合并" in caplog.text
    finally:
        database.engine.dispose()
        gc.collect()
        try:
            path.unlink()
        except PermissionError:
            pass


def test_transition_candidate_rejects_stale_review_version_without_audit(db):
    store = AcquisitionStore()
    job_id, user_id = _seed_candidate(db)
    first_session = db.SessionLocal()
    stale_session = db.SessionLocal()
    try:
        first = first_session.get(PipelineJobUser, (job_id, user_id))
        stale = stale_session.get(PipelineJobUser, (job_id, user_id))
        assert first is not None and stale is not None
        first_version = first.review_version
        stale_version = stale.review_version
        stale_session.rollback()

        store.transition_candidate(
            first_session,
            job_id=job_id,
            user_id=user_id,
            target_status="qualified",
            action="approve",
            operator="first-reviewer@example.test",
            expected_version=first_version,
        )
        first_session.commit()

        with pytest.raises(RuntimeError, match="changed concurrently"):
            store.transition_candidate(
                stale_session,
                job_id=job_id,
                user_id=user_id,
                target_status="rejected",
                action="reject",
                operator="stale-reviewer@example.test",
                expected_version=stale_version,
            )
        stale_session.rollback()
    finally:
        first_session.close()
        stale_session.close()

    with db.session() as session:
        link = session.get(PipelineJobUser, (job_id, user_id))
        assert link is not None
        assert link.qualification_status == "qualified"
        assert link.review_version == 1
        audits = store.list_review_audits(session, job_id, user_id)
        assert [audit.operator for audit in audits] == [
            "first-reviewer@example.test"
        ]


def test_label_update_rejects_stale_review_version_without_lost_update(db):
    store = AcquisitionStore()
    job_id, user_id = _seed_candidate(db)
    first_session = db.SessionLocal()
    stale_session = db.SessionLocal()
    try:
        first = first_session.get(PipelineJobUser, (job_id, user_id))
        stale = stale_session.get(PipelineJobUser, (job_id, user_id))
        assert first is not None and stale is not None
        first_version = first.review_version
        stale_version = stale.review_version
        stale_session.rollback()

        store.update_candidate_labels(
            first_session,
            job_id=job_id,
            user_id=user_id,
            labels=["buyer"],
            operator="first-reviewer@example.test",
            expected_version=first_version,
        )
        first_session.commit()

        with pytest.raises(RuntimeError, match="changed concurrently"):
            store.update_candidate_labels(
                stale_session,
                job_id=job_id,
                user_id=user_id,
                labels=["competitor"],
                operator="stale-reviewer@example.test",
                expected_version=stale_version,
            )
        stale_session.rollback()
    finally:
        first_session.close()
        stale_session.close()

    with db.session() as session:
        link = session.get(PipelineJobUser, (job_id, user_id))
        assert link is not None
        assert link.labels_json == ["buyer"]
        assert link.review_version == 1
        assert session.scalar(
            select(func.count(CandidateReviewAudit.id))
        ) == 1


@pytest.mark.parametrize(
    ("action", "target_status"),
    [
        ("approve", "rejected"),
        ("reject", "qualified"),
        ("request_enrichment", "qualified"),
        ("update_labels", "qualified"),
        ("unknown_action", "qualified"),
    ],
)
def test_human_action_must_match_target_status_without_audit(
    db, action, target_status
):
    store = AcquisitionStore()
    job_id, user_id = _seed_candidate(db)
    with pytest.raises(ValueError, match="review action"):
        with db.session() as session:
            store.transition_candidate(
                session,
                job_id=job_id,
                user_id=user_id,
                target_status=target_status,
                action=action,
                operator="reviewer@example.test",
            )

    with db.session() as session:
        assert session.scalar(
            select(func.count(CandidateReviewAudit.id))
        ) == 0


def test_complete_enrichment_returns_to_manual_review_with_audit(db):
    store = AcquisitionStore()
    job_id, user_id = _seed_candidate(db)
    with db.session() as session:
        store.transition_candidate(
            session,
            job_id=job_id,
            user_id=user_id,
            target_status="need_enrichment",
            action="request_enrichment",
            operator="reviewer@example.test",
        )

    with db.session() as session:
        link = session.get(PipelineJobUser, (job_id, user_id))
        assert link is not None
        audit = store.transition_candidate(
            session,
            job_id=job_id,
            user_id=user_id,
            target_status="manual_review",
            action="complete_enrichment",
            operator="enricher@example.test",
            expected_version=link.review_version,
        )
        assert audit.before_status == "need_enrichment"
        assert audit.after_status == "manual_review"

    with db.session() as session:
        link = session.get(PipelineJobUser, (job_id, user_id))
        assert link is not None
        assert link.qualification_status == "manual_review"
        assert link.review_version == 2
        assert [
            (audit.action, audit.after_status)
            for audit in store.list_review_audits(session, job_id, user_id)
        ] == [
            ("request_enrichment", "need_enrichment"),
            ("complete_enrichment", "manual_review"),
        ]


def test_complete_enrichment_rejects_wrong_target_without_audit(db):
    store = AcquisitionStore()
    job_id, user_id = _seed_candidate(db)
    with pytest.raises(ValueError, match="review action"):
        with db.session() as session:
            store.transition_candidate(
                session,
                job_id=job_id,
                user_id=user_id,
                target_status="qualified",
                action="complete_enrichment",
                operator="enricher@example.test",
            )

    with db.session() as session:
        assert session.scalar(
            select(func.count(CandidateReviewAudit.id))
        ) == 0


def test_ai_assessment_never_initializes_human_labels_or_review_version(db):
    store = AcquisitionStore()
    job_id, user_id = _seed_candidate(db)
    ai_session = db.SessionLocal()
    human_session = db.SessionLocal()
    try:
        human_snapshot = human_session.get(
            PipelineJobUser, (job_id, user_id)
        )
        assert human_snapshot is not None
        expected_version = human_snapshot.review_version
        human_session.rollback()

        store.create_assessment(
            ai_session,
            job_id=job_id,
            user_id=user_id,
            labels=["ai-buyer", "ai-contractor"],
            match_score=82,
            confidence_score=74,
            suggested_status="manual_review",
        )
        ai_session.commit()

        persisted = human_session.get(PipelineJobUser, (job_id, user_id))
        assert persisted is not None
        assert persisted.labels_json == []
        assert persisted.review_version == expected_version == 0
        human_session.rollback()

        audit = store.update_candidate_labels(
            human_session,
            job_id=job_id,
            user_id=user_id,
            labels=["human-distributor"],
            operator="reviewer@example.test",
            expected_version=expected_version,
        )
        assert audit.labels_before_json == []
        assert audit.labels_after_json == ["human-distributor"]
        human_session.commit()
    finally:
        ai_session.close()
        human_session.close()

    with db.session() as session:
        assessment = session.scalar(select(CandidateAssessment))
        link = session.get(PipelineJobUser, (job_id, user_id))
        assert assessment is not None and link is not None
        assert assessment.labels_json == ["ai-buyer", "ai-contractor"]
        assert link.labels_json == ["human-distributor"]
        assert link.review_version == 1


def test_add_evidence_requires_candidate_link(db):
    store = AcquisitionStore()
    job_id, _ = _seed_candidate(db)
    with db.session() as session:
        unrelated = User(
            platform="tiktok",
            tiktok_id="tiktok:not-linked",
            username="not-linked",
        )
        session.add(unrelated)
        session.flush()
        unrelated_id = unrelated.id

    with pytest.raises(ValueError, match="candidate not found"):
        with db.session() as session:
            store.add_evidence(
                session,
                job_id=job_id,
                user_id=unrelated_id,
                source_type="video_comment",
                raw_text="quote please",
            )


def test_add_evidence_rejects_keyword_from_another_job(db):
    store = AcquisitionStore()
    first_job_id, first_user_id = _seed_candidate(db, "first-candidate")
    second_job_id, _ = _seed_candidate(db, "second-candidate")
    with db.session() as session:
        store.create_campaign(
            session,
            job_id=first_job_id,
            platform="tiktok",
        )
        store.create_campaign(
            session,
            job_id=second_job_id,
            platform="tiktok",
        )
        foreign_keyword = store.create_keyword(
            session,
            job_id=second_job_id,
            platform="tiktok",
            text="foreign keyword",
        )
        foreign_keyword_id = foreign_keyword.id

    with pytest.raises(ValueError, match="keyword does not belong"):
        with db.session() as session:
            store.add_evidence(
                session,
                job_id=first_job_id,
                user_id=first_user_id,
                keyword_id=foreign_keyword_id,
                source_type="video_comment",
                raw_text="quote please",
            )

def test_old_database_migration_preserves_rows_and_adds_acquisition_schema():
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
                    "status VARCHAR(20) NOT NULL DEFAULT 'pending', "
                    "category VARCHAR(50) NOT NULL DEFAULT 'unknown', "
                    "created_at DATETIME, updated_at DATETIME, "
                    "PRIMARY KEY (job_id, user_id))"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO pipeline_job_users "
                    "(job_id, user_id, source_stage, status, category) "
                    "VALUES ('legacy-job', 7, 'collect', 'rejected', 'buyer')"
                )
            )

        database.init()

        inspector = inspect(database.engine)
        assert {
            "acquisition_campaigns",
            "acquisition_keywords",
            "discovery_evidence",
            "candidate_assessments",
            "candidate_review_audits",
        } <= set(inspector.get_table_names())
        columns = {
            column["name"]
            for column in inspector.get_columns("pipeline_job_users")
        }
        assert {
            "discovery_status",
            "qualification_status",
            "match_score",
            "confidence_score",
            "labels_json",
            "priority",
            "manually_confirmed_at",
            "review_version",
        } <= columns
        with database.engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT status, category, discovery_status, "
                    "qualification_status, labels_json, priority, "
                    "review_version "
                    "FROM pipeline_job_users WHERE job_id='legacy-job'"
                )
            ).one()
        assert row == (
            "rejected",
            "buyer",
            "candidate",
            "rejected",
            "[]",
            3,
            0,
        )
    finally:
        database.engine.dispose()
        gc.collect()
        try:
            path.unlink()
        except PermissionError:
            pass


def test_campaign_snapshot_is_immutable_per_job(db):
    store = AcquisitionStore()
    job_id, _ = _seed_candidate(db)
    with db.session() as session:
        store.create_campaign(
            session,
            job_id=job_id,
            platform="tiktok",
            countries=["VN"],
        )

    with pytest.raises(ValueError, match="already exists"):
        with db.session() as session:
            store.create_campaign(
                session,
                job_id=job_id,
                platform="tiktok",
                countries=["CN"],
            )


def test_keyword_status_validation(db):
    store = AcquisitionStore()
    job_id, _ = _seed_candidate(db)
    with pytest.raises(ValueError, match="keyword status"):
        with db.session() as session:
            store.create_keyword(
                session,
                job_id=job_id,
                platform="tiktok",
                text="transformer",
                status="best-ever",
            )
