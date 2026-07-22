"""
数据种子脚本 — 用与前端 mock 完全一致的真实数据填充数据库
供真实后端模式 (`VITE_USE_MOCK=false`) 使用。

Usage:
    python -m tiktok_bot_api.seed            # 插入缺失的（已存在跳过）
    python -m tiktok_bot_api.seed --reset    # 清空所有表后重建
    python -m tiktok_bot_api.seed --status   # 只查看当前数据量
"""

import argparse
import logging
from datetime import date, datetime, timedelta

from tiktok_bot_core.storage.database import get_db, init_db
from tiktok_bot_core.models.entities import (
    Base, User, Strategy, Message, Reply, DailyReport, ExperienceRule, Account, TikTokAccount, ConfigRecord,
)
from tiktok_bot_core.settings import get_settings

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("seed")

# ---- Realistic mock dataset (mirrors src/api/mock.ts) ---------------------

USERS = [
    dict(platform="tiktok", tiktok_id="aroma_house_us",       username="aroma_house_us",       nickname="Aroma House US",        bio="Wholesale essential oils · Importer based in TX · DM open",                       follower_count=128400, following_count=412, like_count=2100000, video_count=412, country="US", category="distributor", status="replied",   source="keyword_search",  source_keyword="importer 1688"),
    dict(platform="tiktok", tiktok_id="led_wholesale_uk",      username="led_wholesale_uk",      nickname="LED Wholesale UK",       bio="LED lighting distributor · bulk pricing for retailers",                            follower_count=56200,  following_count=89,  like_count=421000,  video_count=87,  country="GB", category="distributor", status="qualified", source="keyword_search",  source_keyword="wholesale LED"),
    dict(platform="tiktok", tiktok_id="korean_beauty_hub",     username="korean_beauty_hub",     nickname="K-Beauty Hub",            bio="K-beauty sourcing for US/EU · Brand collabs welcome",                                follower_count=214700, following_count=312, like_count=1860000, video_count=218, country="KR", category="buyer",       status="contacted", source="recommendation", source_keyword=""),
    dict(platform="tiktok", tiktok_id="sourcing_brothers_de",  username="sourcing_brothers_de",  nickname="Sourcing Brothers",        bio="Berlin · Agent for European brands sourcing from Asia",                              follower_count=38100,  following_count=156, like_count=198000,  video_count=64,  country="DE", category="buyer",       status="qualified", source="keyword_search",  source_keyword="sourcing agent"),
    dict(platform="tiktok", tiktok_id="factory_direct_cn",     username="factory_direct_cn",     nickname="Factory Direct CN",       bio="Factory direct · OEM/ODM · 电子产品 17年",                                            follower_count=8600,   following_count=23,  like_count=24000,   video_count=18,  country="CN", category="peer",        status="rejected",  source="recommendation", source_keyword=""),
    dict(platform="tiktok", tiktok_id="maison_zara_fr",        username="maison_zara_fr",        nickname="Maison Zara FR",          bio="Boutique mode · cherche fournisseur textile MOQ 200",                                follower_count=12300,  following_count=78,  like_count=89000,   video_count=32,  country="FR", category="buyer",       status="pending",   source="keyword_search",  source_keyword="bulk buy China"),
    dict(platform="tiktok", tiktok_id="tech_retailer_ph",      username="tech_retailer_ph",      nickname="Tech Retailer PH",        bio="Tech retail · 50 stores nationwide · open for OEM deals",                            follower_count=76800,  following_count=234, like_count=412000,  video_count=156, country="PH", category="distributor", status="qualified", source="keyword_search",  source_keyword="sourcing agent"),
    dict(platform="tiktok", tiktok_id="brazil_import_br",      username="brazil_import_br",      nickname="Brazil Import",           bio="Import electronics from China · 8 anos no mercado",                                 follower_count=42100,  following_count=189, like_count=256000,  video_count=92,  country="BR", category="distributor", status="pending",   source="keyword_search",  source_keyword="importer 1688"),
    dict(platform="tiktok", tiktok_id="japan_craft_tokyo",     username="japan_craft_tokyo",     nickname="Japan Craft",             bio="Tokyo · Specialty importer of EU/US lifestyle goods",                                follower_count=34500,  following_count=67,  like_count=178000,  video_count=45,  country="JP", category="buyer",       status="qualified", source="keyword_search",  source_keyword="retail dropship"),
    dict(platform="tiktok", tiktok_id="india_wholesale_in",    username="india_wholesale_in",    nickname="India Wholesale",         bio="Mumbai · Wholesale distributor · D2C brand sourcing",                               follower_count=67200,  following_count=201, like_count=380000,  video_count=124, country="IN", category="distributor", status="contacted", source="recommendation", source_keyword=""),
]

ACCOUNTS = [
    dict(platform="tiktok", username="delong_official_01", login_method="qr", status="expired",   last_login_at=datetime(2026, 7, 9, 14, 22)),
    dict(platform="tiktok", username="delong_official_02", login_method="qr", status="logged_in", last_login_at=datetime(2026, 7, 11, 11, 54)),
    dict(platform="douyin", username="delong_cn",           login_method="qr", status="logged_in", last_login_at=datetime(2026, 7, 10, 9, 18)),
]

KEYWORDS = ["importer 1688", "wholesale LED", "sourcing agent", "bulk buy China", "retail dropship"]


def init_db():
    """Create tables if they don't exist."""
    db = get_db()
    Base.metadata.create_all(db.engine)
    return db


def _session():
    return get_db().SessionLocal()


def seed_users(session):
    inserted = 0
    for u in USERS:
        exists = session.query(User).filter_by(tiktok_id=u["tiktok_id"]).first()
        if exists:
            continue
        session.add(User(**u))
        inserted += 1
    session.commit()
    return inserted


def seed_accounts(session):
    inserted = 0
    for a in ACCOUNTS:
        exists = session.query(TikTokAccount).filter_by(platform=a["platform"], username=a["username"]).first()
        if exists:
            continue
        session.add(TikTokAccount(**a))
        inserted += 1
    session.commit()
    return inserted


def seed_config(session):
    """Seed default config records."""
    defaults = {
        "llm_model": "deepseek-v4-pro",
        "llm_base_url": "https://api.deepseek.com/v1",
        "daily_comment_limit": "25",
        "daily_dm_limit": "12",
        "comment_interval_min": "3",
        "comment_interval_max": "10",
        "dm_interval_min": "8",
        "dm_interval_max": "20",
        "comment_dm_gap_hours": "24",
        "tiktok_keywords": ",".join(KEYWORDS),
        "has_api_key": "false",
    }
    inserted = 0
    for k, v in defaults.items():
        exists = session.query(ConfigRecord).filter_by(key=k).first()
        if exists:
            continue
        session.add(ConfigRecord(key=k, value=v, description=f"Default {k}"))
        inserted += 1
    session.commit()
    return inserted


def seed_strategies(session):
    """Generate strategies for qualified users."""
    inserted = 0
    qualified = session.query(User).filter(User.status.in_(["qualified", "contacted", "replied"])).all()
    for u in qualified:
        exists = session.query(Strategy).filter_by(user_id=u.id).first()
        if exists:
            continue
        persona = "distributor" if u.category == "distributor" else u.category
        strategy_type = "soft_sell" if u.status == "qualified" else "partnership"
        action_plan = (
            f"Step 1: Comment on 3 recent videos using template\n"
            f"Step 2: Wait 24h\n"
            f"Step 3: DM with personalized pitch for {u.country} market"
        )
        session.add(Strategy(
            user_id=u.id,
            persona=persona,
            strategy_type=strategy_type,
            comment_template=f"Hi @{u.username}! Love your content on {u.nickname}...",
            dm_template=f"Hello from a verified supplier — OEM/ODM in your category...",
            action_plan=action_plan,
            priority=4 if u.follower_count > 100000 else 3,
        ))
        inserted += 1
    session.commit()
    return inserted


def seed_messages_and_replies(session):
    """Generate sample messages + replies for the demo."""
    inserted = 0
    contacted = session.query(User).filter(User.status.in_(["contacted", "replied"])).all()
    for u in contacted[:3]:
        exists = session.query(Message).filter_by(user_id=u.id).first()
        if exists:
            continue
        msg = Message(
            user_id=u.id,
            message_type="comment",
            content=f"Hi @{u.username}, great video — we work with similar {u.category}s...",
            status="sent" if u.status != "replied" else "replied",
            sent_at=datetime.utcnow() - timedelta(hours=4),
        )
        session.add(msg)
        session.flush()
        if u.status == "replied":
            session.add(Reply(
                message_id=msg.id,
                user_id=u.id,
                reply_content=f"Hi! Yes we'd love to chat about OEM options. Do you have a catalog?",
                sentiment="positive",
                is_business_intent=True,
                reply_time=datetime.utcnow() - timedelta(hours=2),
            ))
            inserted += 1
        inserted += 1
    session.commit()
    return inserted


def seed_daily_report(session):
    """Seed today's daily report."""
    today = date.today()
    exists = session.query(DailyReport).filter_by(report_date=today).first()
    if exists:
        return 0
    session.add(DailyReport(
        report_date=today,
        new_users_found=47,
        users_qualified=13,
        users_rejected=281,
        comments_sent=52,
        dms_sent=37,
        replies_received=14,
        reply_rate=0.146,
        positive_replies=8,
        business_leads=3,
        notes="自动生成于种子脚本",
    ))
    session.commit()
    return 1


def show_status(session):
    counts = {
        "users":         session.query(User).count(),
        "strategies":    session.query(Strategy).count(),
        "messages":      session.query(Message).count(),
        "replies":       session.query(Reply).count(),
        "daily_reports": session.query(DailyReport).count(),
        "accounts":      session.query(TikTokAccount).count(),
    }
    print("\n当前数据库状态：")
    for k, v in counts.items():
        bar = "█" * min(v, 50)
        print(f"  {k:<14} {v:>5}  {bar}")
    print()


def main():
    parser = argparse.ArgumentParser(description="种子数据 — 填充后端数据库")
    parser.add_argument("--reset", action="store_true", help="清空所有表后重建")
    parser.add_argument("--status", action="store_true", help="只查看当前数据量")
    args = parser.parse_args()

    settings = get_settings()
    print(f"数据库: {settings.sqlite_url or '默认路径'}")

    init_db()
    session = _session()

    if args.reset:
        confirm = input("⚠️  将删除所有数据,确认? (y/N): ").strip().lower()
        if confirm == "y":
            log.info("🗑️  清空所有表…")
            db = get_db()
            Base.metadata.drop_all(db.engine)
            init_db()
        else:
            log.info("取消")
            return

    if args.status:
        show_status(session)
        return

    log.info("🌱  开始播种数据…")
    n_users = seed_users(session)
    n_accounts = seed_accounts(session)
    n_config = seed_config(session)
    n_strategies = seed_strategies(session)
    n_messages = seed_messages_and_replies(session)
    n_reports = seed_daily_report(session)

    log.info(f"✓ users         +{n_users}")
    log.info(f"✓ accounts      +{n_accounts}")
    log.info(f"✓ config        +{n_config}")
    log.info(f"✓ strategies    +{n_strategies}")
    log.info(f"✓ messages/rel  +{n_messages}")
    log.info(f"✓ daily_reports +{n_reports}")
    log.info("✅  完成")

    show_status(session)
    session.close()


if __name__ == "__main__":
    main()