"""数据实体 — SQLAlchemy ORM 模型"""

import uuid
from datetime import datetime, date
from typing import Optional
from sqlalchemy import (
    String, Integer, Float, Boolean, DateTime, Date, ForeignKey, Text, JSON,
    CheckConstraint, Index, UniqueConstraint, text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from tiktok_bot_core.models.pipeline_states import (
    DISCOVERY_STATUS_CANDIDATE,
    JOB_STATUS_QUEUED,
    JOB_USER_STATUS_PENDING,
    KEYWORD_STATUS_NEW,
    QUALIFICATION_STATUS_MANUAL_REVIEW,
    STAGE_STATUS_PENDING,
)


class Base(DeclarativeBase):
    """所有实体的基类"""
    pass


class User(Base):
    """社交媒体用户表（兼容 TikTok + 抖音）"""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(20), default="tiktok", index=True)  # tiktok / douyin
    tiktok_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    username: Mapped[str] = mapped_column(String(100), index=True)
    nickname: Mapped[str] = mapped_column(String(200), default="")
    bio: Mapped[str] = mapped_column(Text, default="")
    follower_count: Mapped[int] = mapped_column(Integer, default=0)
    following_count: Mapped[int] = mapped_column(Integer, default=0)
    like_count: Mapped[int] = mapped_column(Integer, default=0)
    video_count: Mapped[int] = mapped_column(Integer, default=0)
    country: Mapped[str] = mapped_column(String(50), default="")
    category: Mapped[str] = mapped_column(String(50), default="unknown", index=True)
    # status: pending → qualified/rejected → contacted → replied
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    source: Mapped[str] = mapped_column(String(50), default="")
    source_keyword: Mapped[str] = mapped_column(String(200), default="")
    # 平台主页链接：前端表格直接渲染可点外链；为空时由 service 层根据
    # platform + username 兜底拼出 https://www.tiktok.com/@<u> / 抖音对应链接。
    profile_url: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    strategies: Mapped[list["Strategy"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    messages: Mapped[list["Message"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Strategy(Base):
    """策略表"""
    __tablename__ = "strategies"
    __table_args__ = (
        UniqueConstraint("job_id", "user_id", name="uq_strategy_job_user"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    job_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("pipeline_jobs.id"), nullable=True, index=True
    )
    persona: Mapped[str] = mapped_column(String(50), default="")
    strategy_type: Mapped[str] = mapped_column(String(50), default="soft_sell")
    comment_template: Mapped[str] = mapped_column(Text, default="")
    dm_template: Mapped[str] = mapped_column(Text, default="")
    action_plan: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[int] = mapped_column(Integer, default=3)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="strategies")
    job: Mapped[Optional["PipelineJob"]] = relationship(back_populates="strategies")


class Message(Base):
    """消息记录表（评论/私信）"""
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "user_id",
            "message_type",
            name="uq_message_job_user_type",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    job_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("pipeline_jobs.id"), nullable=True, index=True
    )
    message_type: Mapped[str] = mapped_column(String(20), index=True)  # comment/dm
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    error_msg: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="messages")
    job: Mapped[Optional["PipelineJob"]] = relationship(back_populates="messages")
    replies: Mapped[list["Reply"]] = relationship(back_populates="message", cascade="all, delete-orphan")


class Reply(Base):
    """回复记录表"""
    __tablename__ = "replies"

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    reply_content: Mapped[str] = mapped_column(Text, default="")
    sentiment: Mapped[str] = mapped_column(String(20), default="neutral")
    is_business_intent: Mapped[bool] = mapped_column(Boolean, default=False)
    reply_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    message: Mapped["Message"] = relationship(back_populates="replies")


class DailyReport(Base):
    """日报表"""
    __tablename__ = "daily_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    report_date: Mapped[date] = mapped_column(Date, unique=True, index=True)
    new_users_found: Mapped[int] = mapped_column(Integer, default=0)
    users_qualified: Mapped[int] = mapped_column(Integer, default=0)
    users_rejected: Mapped[int] = mapped_column(Integer, default=0)
    comments_sent: Mapped[int] = mapped_column(Integer, default=0)
    dms_sent: Mapped[int] = mapped_column(Integer, default=0)
    replies_received: Mapped[int] = mapped_column(Integer, default=0)
    reply_rate: Mapped[float] = mapped_column(Float, default=0.0)
    positive_replies: Mapped[int] = mapped_column(Integer, default=0)
    business_leads: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ExperienceRule(Base):
    """经验规则表（环节 6 沉淀）"""
    __tablename__ = "experience_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(
        String(20), default="tiktok", index=True
    )
    job_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("pipeline_jobs.id"), nullable=True, index=True
    )
    rule_type: Mapped[str] = mapped_column(String(50), index=True)
    rule_content: Mapped[str] = mapped_column(Text)
    effectiveness: Mapped[float] = mapped_column(Float, default=0.0)
    sample_size: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class Account(Base):
    """用户账号表（认证用）"""
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200), default="")
    api_key_hash: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TikTokAccount(Base):
    """社交平台账号表（兼容 TikTok + 抖音，Cookie 持久化）

    唯一约束：(platform, username) — 同名账号在不同平台可独立存在
    """
    __tablename__ = "tiktok_accounts"
    __table_args__ = (
        UniqueConstraint("platform", "username", name="uq_account_platform_username"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(20), default="tiktok", index=True)
    username: Mapped[str] = mapped_column(String(100), index=True)
    display_name: Mapped[str] = mapped_column(
        String(100), default="", server_default=""
    )
    nickname: Mapped[str] = mapped_column(String(200), default="")
    avatar_url: Mapped[str] = mapped_column(
        String(1000), default="", server_default=""
    )
    cookies_json: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    login_method: Mapped[str] = mapped_column(String(20), default="")
    qrcode_token: Mapped[str] = mapped_column(String(100), default="")
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    follower_count: Mapped[int] = mapped_column(Integer, default=0)
    browser_provider: Mapped[str] = mapped_column(
        String(50), default="", server_default=""
    )
    browser_profile_id: Mapped[str] = mapped_column(
        String(200), default="", server_default=""
    )
    storage_state_path: Mapped[str] = mapped_column(
        String(500), default="", server_default=""
    )
    profile_path: Mapped[str] = mapped_column(
        String(500), default="", server_default=""
    )
    auth_verified_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )
    auth_version: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    pipeline_jobs: Mapped[list["PipelineJob"]] = relationship(back_populates="account")


# 别名：SocialAccount（语义更准确）
SocialAccount = TikTokAccount


class ConfigRecord(Base):
    """系统配置表（可视化后台修改）"""
    __tablename__ = "config_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    value: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


LLM_ROUTE_KEYS = (
    "collection",
    "qualification",
    "strategy",
    "iteration",
    "default",
)
_LLM_ROUTE_KEYS_SQL = ", ".join(f"'{key}'" for key in LLM_ROUTE_KEYS)


class LLMProvider(Base):
    """持久化的 OpenAI-compatible 上游配置，不保存密钥值。"""

    __tablename__ = "llm_providers"
    __table_args__ = (
        CheckConstraint(
            "protocol = 'openai_chat'",
            name="ck_llm_provider_protocol",
        ),
        CheckConstraint(
            "timeout_seconds > 0 AND timeout_seconds <= 86400",
            name="ck_llm_provider_timeout",
        ),
        CheckConstraint(
            "enabled IN (0, 1)",
            name="ck_llm_provider_enabled",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(160))
    protocol: Mapped[str] = mapped_column(
        String(32),
        default="openai_chat",
        server_default="openai_chat",
    )
    base_url: Mapped[str] = mapped_column(String(500))
    default_model: Mapped[str] = mapped_column(String(200))
    api_key_env: Mapped[str] = mapped_column(String(160))
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default="1",
        index=True,
    )
    timeout_seconds: Mapped[float] = mapped_column(
        Float,
        default=30.0,
        server_default="30",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    routes: Mapped[list["LLMRoute"]] = relationship(
        back_populates="provider",
    )
    request_logs: Mapped[list["LLMRequestLog"]] = relationship(
        back_populates="provider",
        passive_deletes=True,
    )


class LLMRoute(Base):
    """一个业务 Route 中按优先级排列的 Provider 条目。"""

    __tablename__ = "llm_routes"
    __table_args__ = (
        UniqueConstraint(
            "route_key",
            "provider_id",
            name="uq_llm_route_provider",
        ),
        CheckConstraint(
            f"route_key IN ({_LLM_ROUTE_KEYS_SQL})",
            name="ck_llm_route_key",
        ),
        CheckConstraint(
            "priority >= 0",
            name="ck_llm_route_priority",
        ),
        CheckConstraint(
            "enabled IN (0, 1)",
            name="ck_llm_route_enabled",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    route_key: Mapped[str] = mapped_column(String(32), index=True)
    provider_id: Mapped[str] = mapped_column(
        ForeignKey("llm_providers.id", ondelete="RESTRICT"),
        index=True,
    )
    priority: Mapped[int] = mapped_column(Integer, default=100)
    model_override: Mapped[Optional[str]] = mapped_column(
        String(200),
        nullable=True,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default="1",
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    provider: Mapped["LLMProvider"] = relationship(back_populates="routes")


class LLMRequestLog(Base):
    """仅记录路由元数据和用量，不记录 prompt/response/密钥。"""

    __tablename__ = "llm_request_logs"
    __table_args__ = (
        CheckConstraint(
            f"route_key IN ({_LLM_ROUTE_KEYS_SQL})",
            name="ck_llm_request_route_key",
        ),
        CheckConstraint(
            "status IN ('success', 'failed')",
            name="ck_llm_request_status",
        ),
        CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 "
            "AND total_tokens >= 0",
            name="ck_llm_request_tokens",
        ),
        CheckConstraint(
            "latency_ms >= 0 AND latency_ms <= 86400000",
            name="ck_llm_request_latency",
        ),
        CheckConstraint(
            "fallback_used IN (0, 1)",
            name="ck_llm_request_fallback",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    route_key: Mapped[str] = mapped_column(String(32), index=True)
    provider_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("llm_providers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    provider_name: Mapped[str] = mapped_column(String(100), default="")
    model: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), index=True)
    error_category: Mapped[str] = mapped_column(String(80), default="")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        index=True,
    )

    provider: Mapped[Optional["LLMProvider"]] = relationship(
        back_populates="request_logs",
    )


class PipelineJob(Base):
    """统一管线任务。"""

    __tablename__ = "pipeline_jobs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    trigger_type: Mapped[str] = mapped_column(String(20), default="manual")
    schedule_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("pipeline_schedules.id"), nullable=True, index=True
    )
    platform: Mapped[str] = mapped_column(String(20), index=True)
    account_mode: Mapped[str] = mapped_column(String(20), default="auto")
    account_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("tiktok_accounts.id"), nullable=True, index=True
    )
    stages_json: Mapped[list] = mapped_column(JSON, default=list)
    config_snapshot_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(
        String(24), default=JOB_STATUS_QUEUED, index=True
    )
    current_stage: Mapped[str] = mapped_column(String(20), default="")
    priority: Mapped[int] = mapped_column(Integer, default=100, index=True)
    retry_of_job_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("pipeline_jobs.id"), nullable=True, index=True
    )
    error_summary: Mapped[str] = mapped_column(Text, default="")
    queued_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    schedule: Mapped[Optional["PipelineSchedule"]] = relationship(
        back_populates="jobs"
    )
    account: Mapped[Optional["TikTokAccount"]] = relationship(
        back_populates="pipeline_jobs"
    )
    retry_of: Mapped[Optional["PipelineJob"]] = relationship(
        remote_side=[id], foreign_keys=[retry_of_job_id]
    )
    stages: Mapped[list["PipelineJobStage"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="PipelineJobStage.stage_order",
    )
    users: Mapped[list["PipelineJobUser"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    strategies: Mapped[list["Strategy"]] = relationship(back_populates="job")
    messages: Mapped[list["Message"]] = relationship(back_populates="job")


class PipelineJobEvent(Base):
    """A durable, UI-safe event emitted by one Pipeline Job."""

    __tablename__ = "pipeline_job_events"
    __table_args__ = (
        Index(
            "ix_pipeline_job_events_job_type",
            "job_id",
            "event_type",
        ),
        {"sqlite_autoincrement": True},
    )

    sequence: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    job_id: Mapped[str] = mapped_column(
        ForeignKey("pipeline_jobs.id", ondelete="CASCADE"),
        index=True,
    )
    stage: Mapped[str] = mapped_column(String(20), default="", index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    level: Mapped[str] = mapped_column(String(16), default="info", index=True)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        index=True,
    )


class PipelineDecisionCheckpoint(Base):
    """One durable decision checkpoint associated with a Pipeline Job."""

    __tablename__ = "pipeline_decision_checkpoints"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'resolved', 'expired', 'cancelled')",
            name="ck_pipeline_checkpoint_status",
        ),
        CheckConstraint(
            "resolution_source IS NULL OR resolution_source IN "
            "('human', 'timeout', 'system')",
            name="ck_pipeline_checkpoint_resolution_source",
        ),
        CheckConstraint(
            "version >= 1",
            name="ck_pipeline_checkpoint_version",
        ),
        Index(
            "uq_pipeline_checkpoint_job_pending",
            "job_id",
            unique=True,
            sqlite_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    job_id: Mapped[str] = mapped_column(
        ForeignKey("pipeline_jobs.id", ondelete="CASCADE"),
        index=True,
    )
    stage: Mapped[str] = mapped_column(String(20), default="", index=True)
    kind: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    option_keys_json: Mapped[list] = mapped_column(JSON, default=list)
    default_option_key: Mapped[str] = mapped_column(String(80))
    context_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(
        String(20),
        default="pending",
        server_default="pending",
        index=True,
    )
    deadline_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        nullable=True,
    )
    resolution_key: Mapped[Optional[str]] = mapped_column(
        String(80),
        nullable=True,
    )
    resolution_source: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
    )
    operator: Mapped[str] = mapped_column(String(200), default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )


class PipelineJobStage(Base):
    """统一管线任务的单阶段执行记录。"""

    __tablename__ = "pipeline_job_stages"
    __table_args__ = (
        UniqueConstraint("job_id", "stage", name="uq_pipeline_job_stage"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("pipeline_jobs.id"), index=True
    )
    stage: Mapped[str] = mapped_column(String(20))
    stage_order: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        String(20), default=STAGE_STATUS_PENDING, index=True
    )
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    result_json: Mapped[dict] = mapped_column(JSON, default=dict)
    error_message: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    job: Mapped["PipelineJob"] = relationship(back_populates="stages")


class PipelineSchedule(Base):
    """统一管线定时计划。"""

    __tablename__ = "pipeline_schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    platform: Mapped[str] = mapped_column(String(20), index=True)
    account_mode: Mapped[str] = mapped_column(String(20), default="auto")
    account_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("tiktok_accounts.id"), nullable=True, index=True
    )
    stages_json: Mapped[list] = mapped_column(JSON, default=list)
    cron_expression: Mapped[str] = mapped_column(String(100))
    timezone: Mapped[str] = mapped_column(String(50), default="Asia/Shanghai")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    config_json: Mapped[dict] = mapped_column(JSON, default=dict)
    next_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    account: Mapped[Optional["TikTokAccount"]] = relationship()
    jobs: Mapped[list["PipelineJob"]] = relationship(back_populates="schedule")


class PipelineJobUser(Base):
    """管线任务的用户快照关联。"""

    __tablename__ = "pipeline_job_users"

    job_id: Mapped[str] = mapped_column(
        ForeignKey("pipeline_jobs.id"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    source_stage: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(
        String(20), default=JOB_USER_STATUS_PENDING, index=True
    )
    category: Mapped[str] = mapped_column(String(50), default="unknown", index=True)
    discovery_status: Mapped[str] = mapped_column(
        String(32),
        default=DISCOVERY_STATUS_CANDIDATE,
        server_default=DISCOVERY_STATUS_CANDIDATE,
        index=True,
    )
    qualification_status: Mapped[str] = mapped_column(
        String(32),
        default=QUALIFICATION_STATUS_MANUAL_REVIEW,
        server_default=QUALIFICATION_STATUS_MANUAL_REVIEW,
        index=True,
    )
    match_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    confidence_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    labels_json: Mapped[list] = mapped_column(
        JSON, default=list, server_default=text("'[]'")
    )
    priority: Mapped[int] = mapped_column(
        Integer, default=3, server_default="3", index=True
    )
    manually_confirmed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )
    review_version: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=True,
    )

    job: Mapped["PipelineJob"] = relationship(back_populates="users")
    user: Mapped["User"] = relationship()


class AcquisitionCampaign(Base):
    """A Pipeline job's immutable stage 01/02 targeting snapshot."""

    __tablename__ = "acquisition_campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("pipeline_jobs.id"), unique=True, index=True
    )
    platform: Mapped[str] = mapped_column(String(20), index=True)
    countries: Mapped[list] = mapped_column(JSON, default=list)
    languages: Mapped[list] = mapped_column(JSON, default=list)
    industries: Mapped[list] = mapped_column(JSON, default=list)
    products: Mapped[list] = mapped_column(JSON, default=list)
    customer_roles: Mapped[list] = mapped_column(JSON, default=list)
    hard_conditions: Mapped[dict] = mapped_column(JSON, default=dict)
    preference_conditions: Mapped[dict] = mapped_column(JSON, default=dict)
    excluded_targets: Mapped[list] = mapped_column(JSON, default=list)
    search_budget: Mapped[dict] = mapped_column(JSON, default=dict)
    keyword_mix: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AcquisitionKeyword(Base):
    """A keyword and its effectiveness counters within one campaign job."""

    __tablename__ = "acquisition_keywords"
    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "platform",
            "text",
            "language",
            name="uq_acquisition_keyword_job_text",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("pipeline_jobs.id"), index=True)
    platform: Mapped[str] = mapped_column(String(20), index=True)
    text: Mapped[str] = mapped_column(String(300), index=True)
    language: Mapped[str] = mapped_column(String(20), default="")
    keyword_type: Mapped[str] = mapped_column(String(50), default="industry")
    source: Mapped[str] = mapped_column(String(50), default="manual")
    status: Mapped[str] = mapped_column(
        String(20), default=KEYWORD_STATUS_NEW, index=True
    )
    usage_count: Mapped[int] = mapped_column(Integer, default=0)
    video_count: Mapped[int] = mapped_column(Integer, default=0)
    relevant_video_count: Mapped[int] = mapped_column(Integer, default=0)
    candidate_count: Mapped[int] = mapped_column(Integer, default=0)
    qualified_count: Mapped[int] = mapped_column(Integer, default=0)
    reply_count: Mapped[int] = mapped_column(Integer, default=0)
    business_lead_count: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class DiscoveryEvidence(Base):
    """One traceable path by which a candidate was discovered."""

    __tablename__ = "discovery_evidence"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("pipeline_jobs.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    keyword_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("acquisition_keywords.id"), nullable=True, index=True
    )
    source_type: Mapped[str] = mapped_column(String(50), index=True)
    keyword_text: Mapped[str] = mapped_column(String(300), default="")
    video_id: Mapped[str] = mapped_column(String(200), default="")
    video_url: Mapped[str] = mapped_column(String(1000), default="")
    comment_id: Mapped[str] = mapped_column(String(200), default="")
    comment_url: Mapped[str] = mapped_column(String(1000), default="")
    author_id: Mapped[str] = mapped_column(String(200), default="")
    author_url: Mapped[str] = mapped_column(String(1000), default="")
    raw_text: Mapped[str] = mapped_column(Text, default="")
    translated_text: Mapped[str] = mapped_column(Text, default="")
    relevance_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    completeness_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    evidence_metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    collected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CandidateAssessment(Base):
    """Versioned AI recommendation; never stores the human conclusion."""

    __tablename__ = "candidate_assessments"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("pipeline_jobs.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    labels_json: Mapped[list] = mapped_column(JSON, default=list)
    match_score: Mapped[float] = mapped_column(Float)
    confidence_score: Mapped[float] = mapped_column(Float)
    positive_evidence_json: Mapped[list] = mapped_column(JSON, default=list)
    negative_evidence_json: Mapped[list] = mapped_column(JSON, default=list)
    missing_fields_json: Mapped[list] = mapped_column(JSON, default=list)
    reasoning: Mapped[str] = mapped_column(Text, default="")
    suggested_status: Mapped[str] = mapped_column(String(32), index=True)
    model_provider: Mapped[str] = mapped_column(String(100), default="")
    model_name: Mapped[str] = mapped_column(String(200), default="")
    schema_version: Mapped[str] = mapped_column(String(30), default="1.0")
    model_metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CandidateReviewAudit(Base):
    """Append-only human review audit record."""

    __tablename__ = "candidate_review_audits"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("pipeline_jobs.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(50), index=True)
    before_status: Mapped[str] = mapped_column(String(32))
    after_status: Mapped[str] = mapped_column(String(32))
    labels_before_json: Mapped[list] = mapped_column(JSON, default=list)
    labels_after_json: Mapped[list] = mapped_column(JSON, default=list)
    priority_before: Mapped[int] = mapped_column(Integer, default=3)
    priority_after: Mapped[int] = mapped_column(Integer, default=3)
    reason: Mapped[str] = mapped_column(Text, default="")
    operator: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
