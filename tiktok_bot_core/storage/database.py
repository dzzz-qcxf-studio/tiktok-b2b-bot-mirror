"""数据库初始化与 Session 管理"""

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session

from tiktok_bot_core.models.entities import (
    Base,
    ExperienceRule,
    Message,
    PipelineJobUser,
    Strategy,
    TikTokAccount,
    User,
)


MIGRATABLE_MODELS = (
    User,
    Strategy,
    Message,
    TikTokAccount,
    PipelineJobUser,
    ExperienceRule,
)
logger = logging.getLogger(__name__)


class MigrationDataConflictError(RuntimeError):
    """Existing rows violate a new invariant and require human resolution."""


def _configure_sqlite_connection(
    dbapi_connection,
    _connection_record,
) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        # The legacy/new PipelineJobUser compatibility triggers intentionally
        # perform one normalization UPDATE. Recursive execution would treat
        # that internal UPDATE as a fresh legacy write and could collapse
        # ``need_enrichment`` back to ``manual_review``.
        cursor.execute("PRAGMA recursive_triggers=OFF")
    finally:
        cursor.close()


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
        sqlite_connect_args = (
            {
                "check_same_thread": False,
                "timeout": 30,
            }
            if db_url.startswith("sqlite")
            else {}
        )
        self.engine = create_engine(
            db_url,
            echo=False,
            future=True,
            connect_args=sqlite_connect_args,
        )
        if db_url.startswith("sqlite"):
            event.listen(
                self.engine,
                "connect",
                _configure_sqlite_connection,
            )
        self.SessionLocal = sessionmaker(
            bind=self.engine,
            autoflush=False,
            autocommit=False,
            future=True,
        )

    def init(self) -> None:
        """创建所有表（首次启动）并对老库执行幂等迁移"""
        Base.metadata.create_all(self.engine)
        self._migrate()
        from tiktok_bot_core.settings import get_settings
        from tiktok_bot_core.storage.llm_store import (
            seed_legacy_llm_config,
        )

        seed_legacy_llm_config(self, get_settings())

    def _migrate(self) -> None:
        """轻量级 SQLite 迁移：对已存在的旧库追加新列。

        仅处理已经在 Base.metadata 里声明的列；不在 metadata 里的字段不会
        被自动添加。SQLite 的 ``ALTER TABLE ADD COLUMN`` 无法给已有表补充
        外键约束，因此旧表只补列、默认值、非空约束和索引；新建表仍由
        ``Base.metadata.create_all`` 创建完整外键。生产环境推荐改用 Alembic，
        这里只为开发期补缺，避免冒险重建并损坏已有数据。
        """
        try:
            from sqlalchemy import inspect, literal, text

            inspector = inspect(self.engine)
            existing_tables = set(inspector.get_table_names())
            additions: list[tuple[str, str, str, bool]] = []
            indexes_to_create: set[tuple[str, str, str]] = set()
            available_columns: dict[str, set[str]] = {}
            for model in MIGRATABLE_MODELS:
                table = model.__table__
                if table.name not in existing_tables:
                    continue
                existing_columns = {
                    column["name"] for column in inspector.get_columns(table.name)
                }
                available_columns[table.name] = set(existing_columns)
                existing_indexes = {
                    tuple(index["column_names"])
                    for index in inspector.get_indexes(table.name)
                }
                for column in table.columns:
                    column_available = column.name in existing_columns
                    if not column_available:
                        sql_type = column.type.compile(dialect=self.engine.dialect)
                        if table.name == "strategies" and column.name == "updated_at":
                            # SQLite 不允许为已有表追加带
                            # CURRENT_TIMESTAMP 的非空列；先追加可空列，
                            # 再于同一迁移事务内回填历史行。
                            additions.append(
                                (table.name, column.name, sql_type, False)
                            )
                            column_available = True
                            available_columns[table.name].add(column.name)
                            continue
                        default = getattr(column, "default", None)
                        default_sql = None
                        if default is not None and default.is_scalar:
                            default_sql = str(
                                literal(default.arg, type_=column.type).compile(
                                    dialect=self.engine.dialect,
                                    compile_kwargs={"literal_binds": True},
                                )
                            )
                        elif column.server_default is not None:
                            server_default = column.server_default.arg
                            default_sql = str(server_default)

                        if default_sql is not None:
                            nullability = "" if column.nullable else " NOT NULL"
                            definition = (
                                f"{sql_type}{nullability} DEFAULT {default_sql}"
                            )
                        elif column.nullable:
                            definition = sql_type
                        else:
                            logger.warning(
                                "跳过无法安全追加的非空列 %s.%s：缺少标量默认值",
                                table.name,
                                column.name,
                            )
                            continue

                        additions.append(
                            (
                                table.name,
                                column.name,
                                definition,
                                bool(column.foreign_keys),
                            )
                        )
                        column_available = True
                        available_columns[table.name].add(column.name)

                    if (
                        column_available
                        and column.index
                        and (column.name,) not in existing_indexes
                    ):
                        indexes_to_create.add(
                            (
                                table.name,
                                column.name,
                                f"ix_{table.name}_{column.name}",
                            )
                        )
        except Exception as exc:
            logger.exception("数据库迁移检查失败")
            raise RuntimeError("数据库迁移检查失败") from exc

        try:
            with self.engine.begin() as conn:
                added_columns = {
                    (table_name, column_name)
                    for table_name, column_name, _definition, _fk in additions
                }
                for table_name, column_name, definition, has_foreign_key in additions:
                    conn.execute(
                        text(
                            f'ALTER TABLE "{table_name}" '
                            f'ADD COLUMN "{column_name}" {definition}'
                        )
                    )
                    if has_foreign_key:
                        logger.warning(
                            "SQLite 无法通过 ALTER TABLE 补充外键约束：%s.%s；"
                            "已保留列定义，新建数据库会包含完整外键",
                            table_name,
                            column_name,
                        )
                if (
                    "pipeline_job_users",
                    "qualification_status",
                ) in added_columns:
                    conn.execute(
                        text(
                            "UPDATE pipeline_job_users SET qualification_status = "
                            "CASE "
                            "WHEN status IN ('qualified', 'contacted', 'replied') "
                            "THEN 'qualified' "
                            "WHEN status = 'rejected' THEN 'rejected' "
                            "ELSE 'manual_review' END"
                        )
                    )
                strategy_columns = available_columns.get("strategies", set())
                if {"review_status", "review_version"} <= strategy_columns:
                    conn.execute(
                        text(
                            "UPDATE strategies SET "
                            "review_status = COALESCE(review_status, 'draft'), "
                            "review_version = COALESCE(review_version, 0)"
                        )
                    )
                if "updated_at" in strategy_columns:
                    created_at_expression = (
                        "created_at" if "created_at" in strategy_columns else "NULL"
                    )
                    conn.execute(
                        text(
                            "UPDATE strategies SET updated_at = "
                            f"COALESCE(updated_at, {created_at_expression}, "
                            "CURRENT_TIMESTAMP)"
                        )
                    )
                job_user_columns = available_columns.get(
                    "pipeline_job_users", set()
                )
                if {"status", "qualification_status"} <= job_user_columns:
                    # Legacy PipelineJobStore writes only ``status`` through
                    # Core SQL. AcquisitionStore writes the new review state.
                    # These triggers keep both paths compatible without
                    # rebuilding the old table or changing every caller.
                    conn.execute(
                        text(
                            "DROP TRIGGER IF EXISTS "
                            "trg_pipeline_job_users_sync_insert"
                        )
                    )
                    conn.execute(
                        text(
                            "DROP TRIGGER IF EXISTS "
                            "trg_pipeline_job_users_sync_update"
                        )
                    )
                    conn.execute(
                        text(
                            "CREATE TRIGGER "
                            "trg_pipeline_job_users_sync_insert "
                            "AFTER INSERT ON pipeline_job_users "
                            "WHEN NEW.qualification_status IS NOT "
                            "CASE "
                            "WHEN NEW.status IN "
                            "('qualified', 'contacted', 'replied') "
                            "THEN 'qualified' "
                            "WHEN NEW.status = 'rejected' THEN 'rejected' "
                            "ELSE 'manual_review' END "
                            "BEGIN "
                            "UPDATE pipeline_job_users "
                            "SET qualification_status = CASE "
                            "WHEN NEW.status IN "
                            "('qualified', 'contacted', 'replied') "
                            "THEN 'qualified' "
                            "WHEN NEW.status = 'rejected' THEN 'rejected' "
                            "ELSE 'manual_review' END "
                            "WHERE job_id = NEW.job_id "
                            "AND user_id = NEW.user_id; "
                            "END"
                        )
                    )
                    conn.execute(
                        text(
                            "CREATE TRIGGER "
                            "trg_pipeline_job_users_sync_update "
                            "AFTER UPDATE ON pipeline_job_users "
                            "WHEN ("
                            "OLD.qualification_status IS NOT "
                            "NEW.qualification_status AND "
                            "NEW.status IS NOT CASE "
                            "WHEN NEW.qualification_status = 'qualified' "
                            "THEN CASE WHEN NEW.status IN "
                            "('contacted', 'replied') THEN NEW.status "
                            "ELSE 'qualified' END "
                            "WHEN NEW.qualification_status = 'rejected' "
                            "THEN 'rejected' ELSE 'pending' END"
                            ") OR ("
                            "OLD.qualification_status IS "
                            "NEW.qualification_status AND "
                            "OLD.status IS NOT NEW.status AND "
                            "NEW.qualification_status IS NOT CASE "
                            "WHEN NEW.status IN "
                            "('qualified', 'contacted', 'replied') "
                            "THEN 'qualified' "
                            "WHEN NEW.status = 'rejected' THEN 'rejected' "
                            "ELSE 'manual_review' END"
                            ") "
                            "BEGIN "
                            "UPDATE pipeline_job_users SET "
                            "status = CASE WHEN "
                            "OLD.qualification_status IS NOT "
                            "NEW.qualification_status THEN CASE "
                            "WHEN NEW.qualification_status = 'qualified' "
                            "THEN CASE WHEN NEW.status IN "
                            "('contacted', 'replied') THEN NEW.status "
                            "ELSE 'qualified' END "
                            "WHEN NEW.qualification_status = 'rejected' "
                            "THEN 'rejected' ELSE 'pending' END "
                            "ELSE NEW.status END, "
                            "qualification_status = CASE WHEN "
                            "OLD.qualification_status IS NOT "
                            "NEW.qualification_status "
                            "THEN NEW.qualification_status ELSE CASE "
                            "WHEN NEW.status IN "
                            "('qualified', 'contacted', 'replied') "
                            "THEN 'qualified' "
                            "WHEN NEW.status = 'rejected' THEN 'rejected' "
                            "ELSE 'manual_review' END END "
                            "WHERE job_id = NEW.job_id "
                            "AND user_id = NEW.user_id; "
                            "END"
                        )
                    )
                for table_name, column_name, index_name in sorted(indexes_to_create):
                    conn.execute(
                        text(
                            f'CREATE INDEX IF NOT EXISTS "{index_name}" '
                            f'ON "{table_name}" ("{column_name}")'
                        )
                    )
                message_columns = available_columns.get("messages", set())
                if {"id", "job_id", "user_id"} <= strategy_columns:
                    duplicate = conn.execute(
                        text(
                            "SELECT job_id, user_id, COUNT(*) AS row_count "
                            "FROM strategies WHERE job_id IS NOT NULL "
                            "GROUP BY job_id, user_id HAVING COUNT(*) > 1 "
                            "LIMIT 1"
                        )
                    ).mappings().first()
                    if duplicate is not None:
                        message = (
                            "检测到重复 strategies 记录 "
                            f"job_id={duplicate['job_id']} "
                            f"user_id={duplicate['user_id']}；"
                            "拒绝自动删除或合并，请人工处理后重试迁移"
                        )
                        logger.error(message)
                        raise MigrationDataConflictError(message)
                    conn.execute(
                        text(
                            "CREATE UNIQUE INDEX IF NOT EXISTS "
                            "uq_strategy_job_user_idx "
                            "ON strategies (job_id, user_id) "
                            "WHERE job_id IS NOT NULL"
                        )
                    )
                if {
                    "id",
                    "job_id",
                    "user_id",
                    "message_type",
                } <= message_columns:
                    duplicate = conn.execute(
                        text(
                            "SELECT job_id, user_id, message_type, "
                            "COUNT(*) AS row_count FROM messages "
                            "WHERE job_id IS NOT NULL "
                            "GROUP BY job_id, user_id, message_type "
                            "HAVING COUNT(*) > 1 LIMIT 1"
                        )
                    ).mappings().first()
                    if duplicate is not None:
                        message = (
                            "检测到重复 messages 记录 "
                            f"job_id={duplicate['job_id']} "
                            f"user_id={duplicate['user_id']} "
                            f"message_type={duplicate['message_type']}；"
                            "拒绝自动删除或合并，请人工处理后重试迁移"
                        )
                        logger.error(message)
                        raise MigrationDataConflictError(message)
                    conn.execute(
                        text(
                            "CREATE UNIQUE INDEX IF NOT EXISTS "
                            "uq_message_job_user_type_idx "
                            "ON messages (job_id, user_id, message_type) "
                            "WHERE job_id IS NOT NULL"
                        )
                    )
        except MigrationDataConflictError:
            logger.exception("数据库迁移检测到数据冲突")
            raise
        except Exception as exc:
            logger.exception("数据库迁移执行失败")
            raise RuntimeError("数据库迁移执行失败") from exc

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
