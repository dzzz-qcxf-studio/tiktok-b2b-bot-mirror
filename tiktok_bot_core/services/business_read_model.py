"""Read-only business projections shared by user-facing APIs.

``User`` remains the canonical platform identity/profile record.  Acquisition
classification and qualification are projected from the newest campaign job
without copying those conclusions back to the global user row.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from typing import Any

from sqlalchemy import (
    Integer,
    Select,
    and_,
    case,
    cast,
    exists,
    func,
    or_,
    select,
)
from sqlalchemy.orm import Session

from tiktok_bot_core.models.entities import (
    AcquisitionCampaign,
    AcquisitionKeyword,
    DiscoveryEvidence,
    Message,
    PipelineJob,
    PipelineJobUser,
    Reply,
    User,
)


@dataclass(frozen=True)
class BusinessUserProjection:
    """Immutable compatibility view of one platform user and business state."""

    id: int
    platform: str
    tiktok_id: str
    username: str
    nickname: str
    bio: str
    follower_count: int
    following_count: int
    like_count: int
    video_count: int
    country: str
    category: str
    status: str
    source: str
    source_keyword: str
    profile_url: str
    created_at: datetime
    updated_at: datetime
    business_source: str
    source_job_id: str | None
    qualification_status: str | None
    match_score: float | None
    confidence_score: float | None
    labels: tuple[str, ...]


class BusinessReadModel:
    """Build a single SQL projection used by list and aggregate operations."""

    _PERSONAS = ("distributor", "buyer", "peer")
    _STATUSES = (
        "pending",
        "qualified",
        "contacted",
        "replied",
        "rejected",
    )
    _LEGACY_CONVERTED_STATUSES = ("qualified", "contacted", "replied")
    _COUNTRY_FLAGS = {
        "BR": "🇧🇷",
        "CN": "🇨🇳",
        "DE": "🇩🇪",
        "FR": "🇫🇷",
        "GB": "🇬🇧",
        "IN": "🇮🇳",
        "JP": "🇯🇵",
        "KR": "🇰🇷",
        "PH": "🇵🇭",
        "UK": "🇬🇧",
        "US": "🇺🇸",
        "VN": "🇻🇳",
    }

    def _projection_subquery(self):
        campaign_links = (
            select(
                PipelineJobUser.user_id.label("user_id"),
                PipelineJobUser.job_id.label("source_job_id"),
                PipelineJobUser.qualification_status.label(
                    "qualification_status"
                ),
                PipelineJobUser.category.label("acquisition_category"),
                PipelineJobUser.match_score.label("match_score"),
                PipelineJobUser.confidence_score.label("confidence_score"),
                PipelineJobUser.labels_json.label("labels"),
                func.row_number()
                .over(
                    partition_by=PipelineJobUser.user_id,
                    order_by=(
                        PipelineJob.created_at.desc(),
                        PipelineJob.id.desc(),
                    ),
                )
                .label("campaign_rank"),
            )
            .join(PipelineJob, PipelineJob.id == PipelineJobUser.job_id)
            .join(
                AcquisitionCampaign,
                AcquisitionCampaign.job_id == PipelineJob.id,
            )
            .subquery("ranked_campaign_users")
        )
        latest = (
            select(
                campaign_links.c.user_id,
                campaign_links.c.source_job_id,
                campaign_links.c.qualification_status,
                campaign_links.c.acquisition_category,
                campaign_links.c.match_score,
                campaign_links.c.confidence_score,
                campaign_links.c.labels,
            )
            .where(campaign_links.c.campaign_rank == 1)
            .subquery("latest_campaign_user")
        )

        has_reply = exists().where(Reply.user_id == User.id)
        has_sent_message = exists().where(
            Message.user_id == User.id,
            Message.status == "sent",
        )
        latest_keyword = (
            select(DiscoveryEvidence.keyword_text)
            .where(
                DiscoveryEvidence.job_id == latest.c.source_job_id,
                DiscoveryEvidence.user_id == User.id,
                func.trim(DiscoveryEvidence.keyword_text) != "",
            )
            .order_by(
                DiscoveryEvidence.created_at.desc(),
                DiscoveryEvidence.id.desc(),
            )
            .limit(1)
            .correlate(User, latest)
            .scalar_subquery()
        )

        projected_status = case(
            (has_reply, "replied"),
            (has_sent_message, "contacted"),
            (latest.c.qualification_status == "qualified", "qualified"),
            (latest.c.qualification_status == "rejected", "rejected"),
            (latest.c.user_id.is_not(None), "pending"),
            else_=User.status,
        )
        projected_category = func.coalesce(
            func.nullif(func.trim(latest.c.acquisition_category), ""),
            func.nullif(func.trim(User.category), ""),
            "unknown",
        )

        return (
            select(
                User.id.label("id"),
                User.platform.label("platform"),
                User.tiktok_id.label("tiktok_id"),
                User.username.label("username"),
                User.nickname.label("nickname"),
                User.bio.label("bio"),
                User.follower_count.label("follower_count"),
                User.following_count.label("following_count"),
                User.like_count.label("like_count"),
                User.video_count.label("video_count"),
                User.country.label("country"),
                projected_category.label("category"),
                projected_status.label("status"),
                User.source.label("source"),
                func.coalesce(
                    latest_keyword,
                    User.source_keyword,
                    "",
                ).label("source_keyword"),
                User.profile_url.label("profile_url"),
                User.created_at.label("created_at"),
                User.updated_at.label("updated_at"),
                case(
                    (latest.c.user_id.is_not(None), "ai_acquisition"),
                    else_="legacy",
                ).label("business_source"),
                latest.c.source_job_id.label("source_job_id"),
                latest.c.qualification_status.label("qualification_status"),
                latest.c.match_score.label("match_score"),
                latest.c.confidence_score.label("confidence_score"),
                latest.c.labels.label("labels"),
            )
            .select_from(User)
            .outerjoin(latest, latest.c.user_id == User.id)
            .subquery("business_users")
        )

    @staticmethod
    def _apply_filters(
        statement: Select,
        projection,
        *,
        status: str | None,
        category: str | None,
    ) -> Select:
        if status:
            statement = statement.where(projection.c.status == status)
        if category:
            statement = statement.where(projection.c.category == category)
        return statement

    @staticmethod
    def _to_projection(row: Any) -> BusinessUserProjection:
        values = dict(row)
        values["labels"] = tuple(values.get("labels") or ())
        return BusinessUserProjection(**values)

    def list_users(
        self,
        session: Session,
        status: str | None = None,
        category: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[BusinessUserProjection]:
        projection = self._projection_subquery()
        statement = self._apply_filters(
            select(projection),
            projection,
            status=status,
            category=category,
        )
        statement = statement.order_by(
            projection.c.created_at.desc(), projection.c.id.desc()
        ).limit(limit).offset(offset)
        rows = session.execute(statement).mappings().all()
        return [self._to_projection(row) for row in rows]

    def count_users(
        self,
        session: Session,
        status: str | None = None,
        category: str | None = None,
    ) -> int:
        projection = self._projection_subquery()
        statement = self._apply_filters(
            select(func.count()).select_from(projection),
            projection,
            status=status,
            category=category,
        )
        return int(session.scalar(statement) or 0)

    def status_counts(
        self,
        session: Session,
        *,
        now: datetime | None = None,
    ) -> dict[str, int]:
        projection = self._projection_subquery()
        utc_now = now or datetime.utcnow()
        today_start = utc_now.replace(hour=0, minute=0, second=0, microsecond=0)
        statement = select(
            func.count().label("total"),
            *(
                func.sum(
                    case((projection.c.status == status, 1), else_=0)
                ).label(status)
                for status in self._STATUSES
            ),
            func.sum(
                case((projection.c.created_at >= today_start, 1), else_=0)
            ).label("new_today"),
        ).select_from(projection)
        row = session.execute(statement).mappings().one()
        return {
            "total": int(row["total"] or 0),
            **{status: int(row[status] or 0) for status in self._STATUSES},
            "new_today": int(row["new_today"] or 0),
        }

    def persona_counts(self, session: Session) -> dict[str, int]:
        projection = self._projection_subquery()
        persona = case(
            (
                projection.c.category.in_(self._PERSONAS),
                projection.c.category,
            ),
            else_="unknown",
        ).label("persona")
        rows = session.execute(
            select(persona, func.count())
            .select_from(projection)
            .group_by(persona)
        ).all()
        result = {
            "distributor": 0,
            "buyer": 0,
            "peer": 0,
            "unknown": 0,
        }
        for key, count in rows:
            result[str(key)] = int(count or 0)
        return result

    @staticmethod
    def _keyword_key(value: str) -> str:
        return " ".join(str(value or "").strip().split()).casefold()

    def keyword_effectiveness(self, session: Session) -> list[dict[str, Any]]:
        """Return live AI + legacy keyword effectiveness by distinct user.

        Stored acquisition counters are deliberately ignored: a partial or
        retried run may leave them stale.  Evidence and the same-job candidate
        conclusion are the only AI inputs to this global read projection.
        """

        buckets: dict[str, dict[str, Any]] = {}

        def add(
            raw_keyword: str,
            user_id: int,
            *,
            converted: bool,
        ) -> None:
            display = " ".join(str(raw_keyword or "").strip().split())
            key = self._keyword_key(display)
            if not key:
                return
            bucket = buckets.setdefault(
                key,
                {
                    "display_names": set(),
                    "users": set(),
                    "converted_users": set(),
                },
            )
            bucket["display_names"].add(display)
            bucket["users"].add(int(user_id))
            if converted:
                bucket["converted_users"].add(int(user_id))

        acquisition_rows = session.execute(
            select(
                AcquisitionKeyword.text,
                DiscoveryEvidence.user_id,
                PipelineJobUser.qualification_status,
            )
            .join(
                DiscoveryEvidence,
                and_(
                    DiscoveryEvidence.keyword_id == AcquisitionKeyword.id,
                    DiscoveryEvidence.job_id == AcquisitionKeyword.job_id,
                ),
            )
            .join(
                PipelineJobUser,
                and_(
                    PipelineJobUser.job_id == AcquisitionKeyword.job_id,
                    PipelineJobUser.user_id == DiscoveryEvidence.user_id,
                ),
            )
            .join(
                AcquisitionCampaign,
                AcquisitionCampaign.job_id == AcquisitionKeyword.job_id,
            )
            .where(func.trim(AcquisitionKeyword.text) != "")
            .distinct()
            .order_by(
                AcquisitionKeyword.text.asc(),
                DiscoveryEvidence.user_id.asc(),
            )
        ).all()
        for keyword, user_id, qualification_status in acquisition_rows:
            add(
                str(keyword),
                int(user_id),
                converted=qualification_status == "qualified",
            )

        legacy_rows = session.execute(
            select(
                User.source_keyword,
                User.id,
                User.status,
            )
            .where(
                User.source == "keyword_search",
                func.trim(User.source_keyword) != "",
            )
            .order_by(User.source_keyword.asc(), User.id.asc())
        ).all()
        for keyword, user_id, status in legacy_rows:
            add(
                str(keyword),
                int(user_id),
                converted=status in self._LEGACY_CONVERTED_STATUSES,
            )

        result: list[dict[str, Any]] = []
        for bucket in buckets.values():
            users = bucket["users"]
            converted_users = bucket["converted_users"] & users
            total = len(users)
            converted = len(converted_users)
            display = min(
                bucket["display_names"],
                key=lambda value: (value.casefold(), value),
            )
            result.append(
                {
                    "name": display,
                    "keyword": display,
                    "total": total,
                    "converted": converted,
                    "rate": converted / total if total else 0,
                }
            )
        return sorted(
            result,
            key=lambda item: (
                -float(item["rate"]),
                -int(item["converted"]),
                -int(item["total"]),
                str(item["keyword"]).casefold(),
                str(item["keyword"]),
            ),
        )

    @staticmethod
    def _literal_ilike_pattern(value: str) -> str:
        escaped = (
            value.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        return f"%{escaped}%"

    @staticmethod
    def _lead_score(match_score: float | None, user: Any) -> int:
        if match_score is not None:
            try:
                score = float(match_score)
            except (TypeError, ValueError):
                score = 0.0
            if not math.isfinite(score):
                score = 0.0
            clamped = max(0.0, min(100.0, score))
            return int(math.floor(clamped + 0.5))
        legacy = (
            50
            + len(str(user.bio or "")) // 5
            + int(user.follower_count or 0) // 10000
        )
        return int(max(0, min(99, legacy)))

    def search_leads(
        self,
        session: Session,
        *,
        keyword: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Search public profile and latest acquisition evidence fields."""

        projection = self._projection_subquery()
        pattern = self._literal_ilike_pattern(keyword.strip())
        current_evidence_match = exists().where(
            DiscoveryEvidence.job_id == projection.c.source_job_id,
            DiscoveryEvidence.user_id == projection.c.id,
            DiscoveryEvidence.keyword_text.ilike(pattern, escape="\\"),
        )
        matched_evidence_keyword = (
            select(DiscoveryEvidence.keyword_text)
            .where(
                DiscoveryEvidence.job_id == projection.c.source_job_id,
                DiscoveryEvidence.user_id == projection.c.id,
                DiscoveryEvidence.keyword_text.ilike(pattern, escape="\\"),
            )
            .order_by(
                DiscoveryEvidence.created_at.desc(),
                DiscoveryEvidence.id.desc(),
            )
            .limit(1)
            .correlate(projection)
            .scalar_subquery()
        )
        legacy_score = (
            50
            + cast(
                func.length(func.coalesce(projection.c.bio, "")) / 5,
                Integer,
            )
            + cast(
                func.coalesce(projection.c.follower_count, 0) / 10000,
                Integer,
            )
        )
        ai_score = case(
            (projection.c.match_score < 0, 0),
            (projection.c.match_score > 100, 100),
            else_=func.round(projection.c.match_score),
        )
        relevance_sort = case(
            (projection.c.match_score.is_not(None), ai_score),
            (legacy_score > 99, 99),
            else_=legacy_score,
        ).label("relevance_sort")
        rows = session.execute(
            select(
                projection,
                User.source_keyword.label("legacy_source_keyword"),
                matched_evidence_keyword.label("matched_evidence_keyword"),
                relevance_sort,
            )
            .join(User, User.id == projection.c.id)
            .where(
                or_(
                    projection.c.username.ilike(pattern, escape="\\"),
                    projection.c.nickname.ilike(pattern, escape="\\"),
                    projection.c.bio.ilike(pattern, escape="\\"),
                    projection.c.country.ilike(pattern, escape="\\"),
                    projection.c.category.ilike(pattern, escape="\\"),
                    User.source_keyword.ilike(pattern, escape="\\"),
                    current_evidence_match,
                )
            )
            .order_by(
                relevance_sort.desc(),
                projection.c.follower_count.desc(),
                projection.c.id.asc(),
            )
            .limit(limit)
        ).mappings().all()

        results: list[dict[str, Any]] = []
        for row in rows:
            score = self._lead_score(row["match_score"], row)
            evidence_keyword = str(row["matched_evidence_keyword"] or "").strip()
            legacy_keyword = str(row["legacy_source_keyword"] or "").strip()
            if evidence_keyword:
                matched_keyword = evidence_keyword
            elif keyword.casefold() in legacy_keyword.casefold():
                matched_keyword = legacy_keyword
            else:
                matched_keyword = keyword
            username = str(row["username"] or "")
            profile_url = str(row["profile_url"] or "").strip()
            if not profile_url and row["platform"] == "tiktok":
                profile_url = f"https://www.tiktok.com/@{username.lstrip('@')}"
            elif not profile_url and row["platform"] == "douyin":
                profile_url = f"https://www.douyin.com/user/{username.lstrip('@')}"
            results.append(
                {
                    "id": int(row["id"]),
                    "username": username,
                    "nickname": str(row["nickname"] or f"@{username}"),
                    "bio": str(row["bio"] or ""),
                    "avatar_initials": username[:2].upper(),
                    "follower_count": int(row["follower_count"] or 0),
                    "video_count": int(row["video_count"] or 0),
                    "country": str(row["country"] or ""),
                    "relevance_score": score,
                    "matched_keyword": matched_keyword,
                    "url": profile_url,
                    "source_job_id": row["source_job_id"],
                    "qualification_status": row["qualification_status"],
                    "confidence_score": row["confidence_score"],
                }
            )
        results.sort(
            key=lambda item: (
                -int(item["relevance_score"]),
                -int(item["follower_count"]),
                int(item["id"]),
            )
        )
        return results[:limit]

    def funnel_counts(self, session: Session) -> dict[str, int]:
        statuses = self.status_counts(session)
        replied = statuses["replied"]
        contacted = statuses["contacted"] + replied
        qualified = statuses["qualified"] + contacted
        business_intent = int(
            session.scalar(
                select(func.count(func.distinct(Reply.user_id)))
                .select_from(Reply)
                .join(Message, Message.id == Reply.message_id)
                .where(
                    Reply.is_business_intent.is_(True),
                    Message.status == "sent",
                )
            )
            or 0
        )
        return {
            "imported": statuses["total"],
            "qualified": qualified,
            "contacted": contacted,
            "replied": replied,
            "businessIntent": business_intent,
        }

    @staticmethod
    def _region_expression():
        return func.coalesce(
            func.nullif(func.trim(User.country), ""),
            "未知",
        )

    def region_metrics(self, session: Session) -> list[dict[str, Any]]:
        region = self._region_expression().label("region")
        message_rows = session.execute(
            select(region, func.count(Message.id).label("sent"))
            .select_from(Message)
            .join(User, User.id == Message.user_id)
            .where(Message.status == "sent")
            .group_by(region)
        ).all()
        reply_rows = session.execute(
            select(
                region,
                func.count(Reply.id).label("replies"),
                func.count(func.distinct(Reply.message_id)).label(
                    "replied_messages"
                ),
                func.sum(
                    case((Reply.is_business_intent.is_(True), 1), else_=0)
                ).label("intent"),
            )
            .select_from(Reply)
            .join(Message, Message.id == Reply.message_id)
            .join(User, User.id == Reply.user_id)
            .where(Message.status == "sent")
            .group_by(region)
        ).all()
        combined: dict[str, dict[str, int]] = {}
        for country, sent in message_rows:
            combined.setdefault(
                str(country),
                {"sent": 0, "replies": 0, "replied_messages": 0, "intent": 0},
            )["sent"] = int(sent or 0)
        for country, replies, replied_messages, intent in reply_rows:
            values = combined.setdefault(
                str(country),
                {"sent": 0, "replies": 0, "replied_messages": 0, "intent": 0},
            )
            values["replies"] = int(replies or 0)
            values["replied_messages"] = int(replied_messages or 0)
            values["intent"] = int(intent or 0)
        total_replies = sum(values["replies"] for values in combined.values())
        result = []
        for country, values in combined.items():
            sent = values["sent"]
            reply_rate = (
                values["replied_messages"] / sent * 100 if sent else 0.0
            )
            share = values["replies"] / total_replies * 100 if total_replies else 0.0
            flag = self._COUNTRY_FLAGS.get(country.upper(), "🌐")
            result.append(
                {
                    "name": country,
                    "flag": flag,
                    "replies": values["replies"],
                    "rate": f"{reply_rate:.1f}%",
                    "intent": values["intent"],
                    "sharePct": round(share, 1),
                }
            )
        return sorted(
            result,
            key=lambda item: (
                -int(item["replies"]),
                str(item["name"]).casefold(),
                str(item["name"]),
            ),
        )

    def sentiment_metrics(self, session: Session) -> dict[str, Any]:
        normalized = func.lower(func.trim(Reply.sentiment)).label("sentiment")
        rows = session.execute(
            select(normalized, func.count(Reply.id)).group_by(normalized)
        ).all()
        counts = {"positive": 0, "neutral": 0, "negative": 0}
        for sentiment, count in rows:
            if sentiment in counts:
                counts[str(sentiment)] = int(count or 0)
        total = sum(counts.values())
        percentages = {
            key: round(value / total * 100) if total else 0
            for key, value in counts.items()
        }
        circumference = 390
        positive_length = round(percentages["positive"] / 100 * circumference)
        neutral_length = round(percentages["neutral"] / 100 * circumference)
        negative_length = round(percentages["negative"] / 100 * circumference)
        avg_score = (
            round((counts["positive"] - counts["negative"]) / total, 2)
            if total
            else 0
        )
        return {
            "positive": {
                "pct": percentages["positive"],
                "count": counts["positive"],
                "color": "oklch(62% 0.16 150)",
                "dasharray": f"{positive_length} {circumference}",
            },
            "neutral": {
                "pct": percentages["neutral"],
                "count": counts["neutral"],
                "color": "oklch(60% 0.08 280)",
                "dasharray": f"{neutral_length} {circumference}",
                "dashoffset": -positive_length,
            },
            "negative": {
                "pct": percentages["negative"],
                "count": counts["negative"],
                "color": "oklch(60% 0.22 25)",
                "dasharray": f"{negative_length} {circumference}",
                "dashoffset": -(positive_length + neutral_length),
            },
            "avgScore": avg_score,
        }
