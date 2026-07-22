"""数据库初始化与 Session 管理"""

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from tiktok_bot_core.models.entities import Base


class Database:
    """SQLite 数据库管理器

    采用 SQLAlchemy 2.0 风格，支持同步和异步访问。
    同一进程内只有一个 engine。
    """

    def __init__(self, db_url: str = None):
        if db_url is None:
            # 默认 SQLite 数据路径
            # database.py 在 tiktok_bot_core/storage/database.py
            # parents[0] = storage/, [1] = tiktok_bot_core/, [2] = 项目根目录
            base_dir = Path(__file__).resolve().parents[2]
            db_dir = base_dir / "data"
            db_dir.mkdir(parents=True, exist_ok=True)
            db_path = db_dir / "tiktok_bot.db"
            db_url = f"sqlite:///{db_path}"

        self.db_url = db_url
        self.engine = create_engine(
            db_url,
            echo=False,
            future=True,
            connect_args={"check_same_thread": False} if db_url.startswith("sqlite") else {},
        )
        self.SessionLocal = sessionmaker(
            bind=self.engine,
            autoflush=False,
            autocommit=False,
            future=True,
        )

    def init(self) -> None:
        """创建所有表（首次启动）"""
        Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        """上下文管理器：自动 commit / rollback / close"""
        s = self.SessionLocal()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def get_session(self) -> Session:
        """获取 session（用于 FastAPI Depends）"""
        s = self.SessionLocal()
        try:
            yield s
        finally:
            s.close()


# 全局单例
_db_instance: Database | None = None


def get_db() -> Database:
    """获取全局数据库实例"""
    global _db_instance
    if _db_instance is None:
        _db_instance = Database()
        _db_instance.init()
    return _db_instance


def init_db() -> Database:
    """初始化数据库（CLI 入口调用）"""
    db = get_db()
    db.init()
    return db
