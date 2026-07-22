"""SQLite 存储层 — CRUD 操作"""

from datetime import datetime, date
from typing import Optional, Sequence
from sqlalchemy import select, func, and_
from sqlalchemy.orm import Session

from tiktok_bot_core.models.entities import (
    User, Strategy, Message, Reply,
    DailyReport, ExperienceRule, ConfigRecord, Account, TikTokAccount,
)


class SqliteStore:
    """SQLite CRUD 仓库

    所有 service 层通过此类访问数据库。
    保证 API/CLI/UI 三个入口看到的视图一致。
    """

    # ===== User =====

    def add_user(self, session: Session, **kwargs) -> User:
        """插入或忽略（同 tiktok_id 唯一）"""
        existing = session.execute(
            select(User).where(User.tiktok_id == kwargs["tiktok_id"])
        ).scalar_one_or_none()
        if existing:
            return existing
        user = User(**kwargs)
        session.add(user)
        session.flush()
        return user

    def get_user(self, session: Session, user_id: int) -> Optional[User]:
        return session.get(User, user_id)

    def get_users(
        self,
        session: Session,
        status: str | None = None,
        category: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[User]:
        stmt = select(User)
        if status:
            stmt = stmt.where(User.status == status)
        if category:
            stmt = stmt.where(User.category == category)
        stmt = stmt.order_by(User.created_at.desc()).limit(limit).offset(offset)
        return session.execute(stmt).scalars().all()

    def update_user_status(self, session: Session, user_id: int, status: str, category: str | None = None) -> None:
        user = session.get(User, user_id)
        if user:
            user.status = status
            if category is not None:
                user.category = category
            user.updated_at = datetime.utcnow()

    def count_users(self, session: Session, status: str | None = None, since_date: date | None = None) -> int:
        stmt = select(func.count(User.id))
        if status:
            stmt = stmt.where(User.status == status)
        if since_date:
            stmt = stmt.where(User.created_at >= since_date)
        return session.execute(stmt).scalar_one()

    # ===== Strategy =====

    def add_strategy(self, session: Session, **kwargs) -> Strategy:
        strategy = Strategy(**kwargs)
        session.add(strategy)
        session.flush()
        return strategy

    def get_strategies(self, session: Session, user_id: int | None = None) -> Sequence[Strategy]:
        stmt = select(Strategy)
        if user_id:
            stmt = stmt.where(Strategy.user_id == user_id)
        return session.execute(stmt).scalars().all()

    # ===== Message =====

    def add_message(self, session: Session, **kwargs) -> Message:
        msg = Message(**kwargs)
        session.add(msg)
        session.flush()
        return msg

    def count_messages(self, session: Session, message_type: str | None = None, since_date: date | None = None) -> int:
        stmt = select(func.count(Message.id))
        if message_type:
            stmt = stmt.where(Message.message_type == message_type)
        if since_date:
            stmt = stmt.where(Message.created_at >= since_date)
        return session.execute(stmt).scalar_one()

    # ===== Reply =====

    def add_reply(self, session: Session, **kwargs) -> Reply:
        reply = Reply(**kwargs)
        session.add(reply)
        session.flush()
        return reply

    def count_replies(self, session: Session, sentiment: str | None = None, since_date: date | None = None) -> int:
        stmt = select(func.count(Reply.id))
        if sentiment:
            stmt = stmt.where(Reply.sentiment == sentiment)
        if since_date:
            stmt = stmt.where(Reply.created_at >= since_date)
        return session.execute(stmt).scalar_one()

    def count_business_leads(self, session: Session, since_date: date | None = None) -> int:
        stmt = select(func.count(Reply.id)).where(Reply.is_business_intent == True)
        if since_date:
            stmt = stmt.where(Reply.created_at >= since_date)
        return session.execute(stmt).scalar_one()

    # ===== DailyReport =====

    def upsert_daily_report(self, session: Session, **kwargs) -> DailyReport:
        report_date = kwargs.pop("report_date")
        existing = session.execute(
            select(DailyReport).where(DailyReport.report_date == report_date)
        ).scalar_one_or_none()
        if existing:
            for k, v in kwargs.items():
                setattr(existing, k, v)
            return existing
        report = DailyReport(report_date=report_date, **kwargs)
        session.add(report)
        session.flush()
        return report

    def list_daily_reports(self, session: Session, days: int = 30) -> Sequence[DailyReport]:
        return session.execute(
            select(DailyReport).order_by(DailyReport.report_date.desc()).limit(days)
        ).scalars().all()

    # ===== ExperienceRule =====

    def add_rule(self, session: Session, **kwargs) -> ExperienceRule:
        rule = ExperienceRule(**kwargs)
        session.add(rule)
        session.flush()
        return rule

    def list_rules(self, session: Session, rule_type: str | None = None) -> Sequence[ExperienceRule]:
        stmt = select(ExperienceRule)
        if rule_type:
            stmt = stmt.where(ExperienceRule.rule_type == rule_type)
        return session.execute(stmt).scalars().all()

    # ===== ConfigRecord =====

    def get_config(self, session: Session, key: str) -> Optional[ConfigRecord]:
        return session.execute(
            select(ConfigRecord).where(ConfigRecord.key == key)
        ).scalar_one_or_none()

    def list_configs(self, session: Session) -> Sequence[ConfigRecord]:
        return session.execute(select(ConfigRecord)).scalars().all()

    def set_config(self, session: Session, key: str, value: str, description: str = "") -> ConfigRecord:
        existing = self.get_config(session, key)
        if existing:
            existing.value = value
            existing.description = description or existing.description
            existing.updated_at = datetime.utcnow()
            return existing
        cfg = ConfigRecord(key=key, value=value, description=description)
        session.add(cfg)
        session.flush()
        return cfg

    # ===== 统计聚合 =====

    def get_keyword_effectiveness(self, session: Session) -> list[dict]:
        """按关键词统计转化率"""
        rows = session.execute(
            select(
                User.source_keyword,
                func.count(User.id).label("total"),
                func.sum(
                    func.iif(User.status.in_(["contacted", "replied"]), 1, 0)
                ).label("converted"),
            )
            .where(User.source == "keyword_search")
            .where(User.source_keyword != "")
            .group_by(User.source_keyword)
        ).all()
        result = []
        for r in rows:
            kw = r.source_keyword
            total = r.total or 0
            converted = r.converted or 0
            rate = converted / total if total else 0
            result.append({"keyword": kw, "total": total, "converted": converted, "rate": rate})
        return sorted(result, key=lambda x: x["rate"], reverse=True)

    # ===== Account =====

    def get_account(self, session: Session, username: str) -> Optional[Account]:
        return session.execute(select(Account).where(Account.username == username)).scalar_one_or_none()

    def get_api_users(self, session: Session) -> list[dict]:
        rows = session.execute(select(Account)).scalars().all()
        return [{"username": r.username, "api_key_hash": r.api_key_hash} for r in rows]

    def create_account(self, session: Session, username: str, password_hash: str, api_key_hash: str) -> Account:
        a = Account(username=username, password_hash=password_hash, api_key_hash=api_key_hash)
        session.add(a)
        session.flush()
        return a

    # ===== TikTokAccount / SocialAccount =====

    def add_tiktok_account(
        self,
        session: Session,
        username: str,
        cookies_json: str = "",
        status: str = "pending",
        platform: str = "tiktok",
        login_method: str = "",
        qrcode_token: str = "",
    ) -> TikTokAccount:
        existing = session.execute(
            select(TikTokAccount).where(
                TikTokAccount.username == username,
                TikTokAccount.platform == platform,
            )
        ).scalar_one_or_none()
        if existing:
            # 覆盖模式
            existing.status = status
            existing.cookies_json = cookies_json
            if login_method:
                existing.login_method = login_method
            if qrcode_token:
                existing.qrcode_token = qrcode_token
            existing.updated_at = datetime.utcnow()
            return existing
        a = TikTokAccount(
            username=username,
            platform=platform,
            cookies_json=cookies_json,
            status=status,
            login_method=login_method,
            qrcode_token=qrcode_token,
        )
        session.add(a)
        session.flush()
        return a

    def get_tiktok_accounts(
        self,
        session: Session,
        platform: str | None = None,
        status: str | None = None,
    ) -> list[TikTokAccount]:
        stmt = select(TikTokAccount).order_by(TikTokAccount.created_at.desc())
        if platform:
            stmt = stmt.where(TikTokAccount.platform == platform)
        if status:
            stmt = stmt.where(TikTokAccount.status == status)
        return list(session.execute(stmt).scalars().all())

    def get_tiktok_account(self, session: Session, aid: int) -> TikTokAccount | None:
        return session.get(TikTokAccount, aid)

    def get_tiktok_account_by_username(
        self, session: Session, username: str, platform: str = "tiktok"
    ) -> TikTokAccount | None:
        return session.execute(
            select(TikTokAccount).where(
                TikTokAccount.username == username,
                TikTokAccount.platform == platform,
            )
        ).scalar_one_or_none()

    def update_tiktok_cookies(
        self,
        session: Session,
        aid: int,
        cookies_json: str,
        status: str = "logged_in",
    ):
        a = session.get(TikTokAccount, aid)
        if a:
            a.cookies_json = cookies_json
            a.status = status
            a.last_login_at = datetime.utcnow()
            a.updated_at = datetime.utcnow()

    def update_account_status(self, session: Session, aid: int, status: str):
        a = session.get(TikTokAccount, aid)
        if a:
            a.status = status
            a.updated_at = datetime.utcnow()

    def delete_tiktok_account(self, session: Session, aid: int):
        a = session.get(TikTokAccount, aid)
        if a:
            session.delete(a)

    def get_active_account(self, session: Session, platform: str = "tiktok") -> TikTokAccount | None:
        """获取该平台下一个可用的已登录账号（轮询切换）"""
        return session.execute(
            select(TikTokAccount)
            .where(TikTokAccount.platform == platform)
            .where(TikTokAccount.status == "logged_in")
            .order_by(TikTokAccount.last_login_at.desc().nullslast())
        ).scalars().first()

    def get_category_distribution(self, session: Session) -> list[dict]:
        """用户分类分布"""
        rows = session.execute(
            select(User.category, func.count(User.id).label("count"))
            .where(User.status == "qualified")
            .group_by(User.category)
        ).all()
        return [{"category": r.category, "count": r.count} for r in rows]
