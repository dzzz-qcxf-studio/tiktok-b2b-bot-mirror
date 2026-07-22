"""数据实体 — SQLAlchemy ORM 模型"""

from datetime import datetime, date
from typing import Optional
from sqlalchemy import (
    String, Integer, Float, Boolean, DateTime, Date, ForeignKey, Text, JSON,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    strategies: Mapped[list["Strategy"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    messages: Mapped[list["Message"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Strategy(Base):
    """策略表"""
    __tablename__ = "strategies"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    persona: Mapped[str] = mapped_column(String(50), default="")
    strategy_type: Mapped[str] = mapped_column(String(50), default="soft_sell")
    comment_template: Mapped[str] = mapped_column(Text, default="")
    dm_template: Mapped[str] = mapped_column(Text, default="")
    action_plan: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[int] = mapped_column(Integer, default=3)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="strategies")


class Message(Base):
    """消息记录表（评论/私信）"""
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    message_type: Mapped[str] = mapped_column(String(20), index=True)  # comment/dm
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    error_msg: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="messages")
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
    nickname: Mapped[str] = mapped_column(String(200), default="")
    cookies_json: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    login_method: Mapped[str] = mapped_column(String(20), default="")
    qrcode_token: Mapped[str] = mapped_column(String(100), default="")
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    follower_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


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
