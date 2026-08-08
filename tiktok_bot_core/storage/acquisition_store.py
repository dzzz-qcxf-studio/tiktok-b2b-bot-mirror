"""Persistence boundary for stage 01/02 acquisition data and review state."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Mapping, Sequence

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from tiktok_bot_core.models.entities import (
    AcquisitionCampaign,
    AcquisitionKeyword,
    CandidateAssessment,
    CandidateReviewAudit,
    DiscoveryEvidence,
    PipelineJobUser,
)
from tiktok_bot_core.models.pipeline_states import (
    DISCOVERY_STATUS_CANDIDATE,
    KEYWORD_STATUS_NEW,
    QUALIFICATION_STATUS_QUALIFIED,
    QUALIFICATION_STATUS_REJECTED,
    legacy_job_user_status,
    validate_discovery_status,
    validate_human_review_action,
    validate_keyword_status,
    validate_qualification_status,
    validate_qualification_transition,
)


def _score(value: float | int | None, field: str) -> float | None:
    if value is None:
        return None
    normalized = float(value)
    if not math.isfinite(normalized) or not 0 <= normalized <= 100:
        raise ValueError(f"{field} must be between 0 and 100")
    return normalized


def _ratio(value: float | int | None, field: str) -> float | None:
    if value is None:
        return None
    normalized = float(value)
    if not math.isfinite(normalized) or not 0 <= normalized <= 1:
        raise ValueError(f"{field} must be between 0 and 1")
    return normalized


def _clean_list(values: Sequence[str] | None) -> list[str]:
    if not values:
        return []
    return [str(value).strip() for value in values if str(value).strip()]


class AcquisitionStore:
    """Stores immutable evidence/AI output and audited human conclusions."""

    def create_campaign(
        self,
        session: Session,
        *,
        job_id: str,
        platform: str,
        countries: Sequence[str] | None = None,
        languages: Sequence[str] | None = None,
        industries: Sequence[str] | None = None,
        products: Sequence[str] | None = None,
        customer_roles: Sequence[str] | None = None,
        hard_conditions: Mapping[str, Any] | None = None,
        preference_conditions: Mapping[str, Any] | None = None,
        excluded_targets: Sequence[str] | None = None,
        search_budget: Mapping[str, Any] | None = None,
        keyword_mix: Mapping[str, Any] | None = None,
    ) -> AcquisitionCampaign:
        if session.scalar(
            select(AcquisitionCampaign.id).where(
                AcquisitionCampaign.job_id == job_id
            )
        ) is not None:
            raise ValueError(f"Acquisition campaign already exists for job {job_id}")
        normalized_platform = str(platform).strip().lower()
        if not normalized_platform:
            raise ValueError("platform must not be empty")
        campaign = AcquisitionCampaign(
            job_id=job_id,
            platform=normalized_platform,
            countries=_clean_list(countries),
            languages=_clean_list(languages),
            industries=_clean_list(industries),
            products=_clean_list(products),
            customer_roles=_clean_list(customer_roles),
            hard_conditions=dict(hard_conditions or {}),
            preference_conditions=dict(preference_conditions or {}),
            excluded_targets=_clean_list(excluded_targets),
            search_budget=dict(search_budget or {}),
            keyword_mix=dict(keyword_mix or {}),
        )
        session.add(campaign)
        session.flush()
        return campaign

    def get_campaign(
        self, session: Session, job_id: str
    ) -> AcquisitionCampaign | None:
        return session.scalar(
            select(AcquisitionCampaign).where(
                AcquisitionCampaign.job_id == job_id
            )
        )

    def create_keyword(
        self,
        session: Session,
        *,
        job_id: str,
        platform: str,
        text: str,
        language: str = "",
        keyword_type: str = "industry",
        source: str = "manual",
        status: str = KEYWORD_STATUS_NEW,
    ) -> AcquisitionKeyword:
        validate_keyword_status(status)
        normalized_text = str(text).strip()
        if not normalized_text:
            raise ValueError("keyword text must not be empty")
        keyword = AcquisitionKeyword(
            job_id=job_id,
            platform=str(platform).strip().lower(),
            text=normalized_text,
            language=str(language).strip().lower(),
            keyword_type=str(keyword_type).strip() or "industry",
            source=str(source).strip() or "manual",
            status=status,
        )
        session.add(keyword)
        session.flush()
        return keyword

    def list_keywords(
        self, session: Session, job_id: str
    ) -> list[AcquisitionKeyword]:
        return list(
            session.scalars(
                select(AcquisitionKeyword)
                .where(AcquisitionKeyword.job_id == job_id)
                .order_by(AcquisitionKeyword.id.asc())
            )
        )

    def update_keyword_stats(
        self,
        session: Session,
        keyword_id: int,
        *,
        status: str | None = None,
        usage_count: int | None = None,
        video_count: int | None = None,
        relevant_video_count: int | None = None,
        candidate_count: int | None = None,
        qualified_count: int | None = None,
        reply_count: int | None = None,
        business_lead_count: int | None = None,
        last_used_at: datetime | None = None,
    ) -> AcquisitionKeyword:
        keyword = session.get(AcquisitionKeyword, keyword_id)
        if keyword is None:
            raise ValueError(f"Acquisition keyword not found: {keyword_id}")
        if status is not None:
            validate_keyword_status(status)
            keyword.status = status
        counters = {
            "usage_count": usage_count,
            "video_count": video_count,
            "relevant_video_count": relevant_video_count,
            "candidate_count": candidate_count,
            "qualified_count": qualified_count,
            "reply_count": reply_count,
            "business_lead_count": business_lead_count,
        }
        for field, value in counters.items():
            if value is None:
                continue
            normalized = int(value)
            if normalized < 0:
                raise ValueError(f"{field} must not be negative")
            setattr(keyword, field, normalized)
        if last_used_at is not None:
            keyword.last_used_at = last_used_at
        session.flush()
        return keyword

    def add_evidence(
        self,
        session: Session,
        *,
        job_id: str,
        user_id: int,
        source_type: str,
        keyword_id: int | None = None,
        keyword_text: str = "",
        video_id: str = "",
        video_url: str = "",
        comment_id: str = "",
        comment_url: str = "",
        author_id: str = "",
        author_url: str = "",
        raw_text: str = "",
        translated_text: str = "",
        relevance_score: float | None = None,
        completeness_score: float | None = None,
        evidence_metadata: Mapping[str, Any] | None = None,
        collected_at: datetime | None = None,
    ) -> DiscoveryEvidence:
        if session.get(PipelineJobUser, (job_id, user_id)) is None:
            raise ValueError("Pipeline job candidate not found")
        if keyword_id is not None:
            keyword = session.get(AcquisitionKeyword, keyword_id)
            if keyword is None:
                raise ValueError(f"Acquisition keyword not found: {keyword_id}")
            campaign_exists = session.scalar(
                select(AcquisitionCampaign.id).where(
                    AcquisitionCampaign.job_id == job_id
                )
            )
            if keyword.job_id != job_id or campaign_exists is None:
                raise ValueError(
                    "Acquisition keyword does not belong to this job campaign"
                )
        normalized_source = str(source_type).strip()
        if not normalized_source:
            raise ValueError("source_type must not be empty")
        evidence = DiscoveryEvidence(
            job_id=job_id,
            user_id=user_id,
            keyword_id=keyword_id,
            source_type=normalized_source,
            keyword_text=str(keyword_text),
            video_id=str(video_id),
            video_url=str(video_url),
            comment_id=str(comment_id),
            comment_url=str(comment_url),
            author_id=str(author_id),
            author_url=str(author_url),
            raw_text=str(raw_text),
            translated_text=str(translated_text),
            relevance_score=_ratio(relevance_score, "relevance_score"),
            completeness_score=_ratio(completeness_score, "completeness_score"),
            evidence_metadata_json=dict(evidence_metadata or {}),
            collected_at=collected_at or datetime.utcnow(),
        )
        session.add(evidence)
        session.flush()
        return evidence

    def list_evidence(
        self, session: Session, job_id: str, user_id: int
    ) -> list[DiscoveryEvidence]:
        return list(
            session.scalars(
                select(DiscoveryEvidence)
                .where(
                    DiscoveryEvidence.job_id == job_id,
                    DiscoveryEvidence.user_id == user_id,
                )
                .order_by(DiscoveryEvidence.id.asc())
            )
        )

    def set_discovery_status(
        self,
        session: Session,
        *,
        job_id: str,
        user_id: int,
        status: str,
    ) -> PipelineJobUser:
        validate_discovery_status(status)
        link = session.get(PipelineJobUser, (job_id, user_id))
        if link is None:
            raise ValueError("Pipeline job candidate not found")
        link.discovery_status = status
        session.flush()
        return link

    def create_assessment(
        self,
        session: Session,
        *,
        job_id: str,
        user_id: int,
        labels: Sequence[str],
        match_score: float,
        confidence_score: float,
        positive_evidence: Sequence[str] | None = None,
        negative_evidence: Sequence[str] | None = None,
        missing_fields: Sequence[str] | None = None,
        reasoning: str = "",
        suggested_status: str,
        model_provider: str = "",
        model_name: str = "",
        schema_version: str = "1.0",
        model_metadata: Mapping[str, Any] | None = None,
    ) -> CandidateAssessment:
        validate_qualification_status(suggested_status)
        normalized_match_score = _score(match_score, "match_score")
        normalized_confidence_score = _score(
            confidence_score, "confidence_score"
        )
        normalized_labels = _clean_list(labels)
        link = session.get(PipelineJobUser, (job_id, user_id))
        if link is None:
            raise ValueError("Pipeline job candidate not found")
        assessment = CandidateAssessment(
            job_id=job_id,
            user_id=user_id,
            labels_json=normalized_labels,
            match_score=normalized_match_score,
            confidence_score=normalized_confidence_score,
            positive_evidence_json=_clean_list(positive_evidence),
            negative_evidence_json=_clean_list(negative_evidence),
            missing_fields_json=_clean_list(missing_fields),
            reasoning=str(reasoning),
            suggested_status=suggested_status,
            model_provider=str(model_provider),
            model_name=str(model_name),
            schema_version=str(schema_version).strip() or "1.0",
            model_metadata_json=dict(model_metadata or {}),
        )
        session.add(assessment)
        link.match_score = normalized_match_score
        link.confidence_score = normalized_confidence_score
        session.flush()
        return assessment

    def latest_assessment(
        self, session: Session, job_id: str, user_id: int
    ) -> CandidateAssessment | None:
        return session.scalar(
            select(CandidateAssessment)
            .where(
                CandidateAssessment.job_id == job_id,
                CandidateAssessment.user_id == user_id,
            )
            .order_by(CandidateAssessment.id.desc())
            .limit(1)
        )

    def transition_candidate(
        self,
        session: Session,
        *,
        job_id: str,
        user_id: int,
        target_status: str,
        action: str,
        operator: str,
        reason: str = "",
        labels: Sequence[str] | None = None,
        priority: int | None = None,
        expected_version: int | None = None,
    ) -> CandidateReviewAudit:
        link = session.get(PipelineJobUser, (job_id, user_id))
        if link is None:
            raise ValueError("Pipeline job candidate not found")
        before_status = link.qualification_status
        current_version = link.review_version
        normalized_expected_version = (
            current_version
            if expected_version is None
            else int(expected_version)
        )
        if normalized_expected_version != current_version:
            raise RuntimeError("Candidate state changed concurrently")
        validate_qualification_transition(before_status, target_status)
        validate_human_review_action(action, target_status)
        normalized_operator = str(operator).strip()
        normalized_action = str(action).strip()
        if not normalized_operator:
            raise ValueError("operator must not be empty")
        if not normalized_action:
            raise ValueError("action must not be empty")

        labels_before = list(link.labels_json or [])
        labels_after = (
            _clean_list(labels) if labels is not None else labels_before
        )
        priority_before = link.priority
        priority_after = priority_before if priority is None else int(priority)
        if not 1 <= priority_after <= 5:
            raise ValueError("priority must be between 1 and 5")
        now = datetime.utcnow()
        values: dict[str, Any] = {
            "qualification_status": target_status,
            "status": legacy_job_user_status(target_status),
            "updated_at": now,
            "review_version": current_version + 1,
        }
        if labels is not None:
            values["labels_json"] = labels_after
        if priority is not None:
            values["priority"] = priority_after
        if target_status in {
            QUALIFICATION_STATUS_QUALIFIED,
            QUALIFICATION_STATUS_REJECTED,
        }:
            values["manually_confirmed_at"] = now

        result = session.execute(
            update(PipelineJobUser)
            .where(
                PipelineJobUser.job_id == job_id,
                PipelineJobUser.user_id == user_id,
                PipelineJobUser.qualification_status == before_status,
                PipelineJobUser.review_version == normalized_expected_version,
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise RuntimeError("Candidate state changed concurrently")
        audit = CandidateReviewAudit(
            job_id=job_id,
            user_id=user_id,
            action=normalized_action,
            before_status=before_status,
            after_status=target_status,
            labels_before_json=labels_before,
            labels_after_json=labels_after,
            priority_before=priority_before,
            priority_after=priority_after,
            reason=str(reason),
            operator=normalized_operator,
        )
        session.add(audit)
        session.flush()
        session.expire_all()
        return audit

    def update_candidate_labels(
        self,
        session: Session,
        *,
        job_id: str,
        user_id: int,
        labels: Sequence[str],
        operator: str,
        reason: str = "",
        expected_version: int | None = None,
    ) -> CandidateReviewAudit:
        """Audit a human label correction without inventing a state change."""
        link = session.get(PipelineJobUser, (job_id, user_id))
        if link is None:
            raise ValueError("Pipeline job candidate not found")
        normalized_operator = str(operator).strip()
        if not normalized_operator:
            raise ValueError("operator must not be empty")
        labels_before = list(link.labels_json or [])
        labels_after = _clean_list(labels)
        current_status = link.qualification_status
        current_priority = link.priority
        current_version = link.review_version
        normalized_expected_version = (
            current_version
            if expected_version is None
            else int(expected_version)
        )
        if normalized_expected_version != current_version:
            raise RuntimeError("Candidate state changed concurrently")
        result = session.execute(
            update(PipelineJobUser)
            .where(
                PipelineJobUser.job_id == job_id,
                PipelineJobUser.user_id == user_id,
                PipelineJobUser.review_version == normalized_expected_version,
            )
            .values(
                labels_json=labels_after,
                updated_at=datetime.utcnow(),
                review_version=current_version + 1,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise RuntimeError("Candidate state changed concurrently")
        audit = CandidateReviewAudit(
            job_id=job_id,
            user_id=user_id,
            action="update_labels",
            before_status=current_status,
            after_status=current_status,
            labels_before_json=labels_before,
            labels_after_json=labels_after,
            priority_before=current_priority,
            priority_after=current_priority,
            reason=str(reason),
            operator=normalized_operator,
        )
        session.add(audit)
        session.flush()
        return audit

    def list_review_audits(
        self, session: Session, job_id: str, user_id: int
    ) -> list[CandidateReviewAudit]:
        return list(
            session.scalars(
                select(CandidateReviewAudit)
                .where(
                    CandidateReviewAudit.job_id == job_id,
                    CandidateReviewAudit.user_id == user_id,
                )
                .order_by(CandidateReviewAudit.id.asc())
            )
        )
