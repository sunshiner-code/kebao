from __future__ import annotations

import json
import logging
import math
import os
import threading
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from sqlalchemy import JSON, Text, create_engine, event, text
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import ensure_dirs, settings

def _json_ready(value):
    """MySQL 的 JSON 不接受 NaN 和 Infinity。写库前改成 null。"""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value


def _dump_json(value) -> str:
    return json.dumps(_json_ready(value), ensure_ascii=False)


ensure_dirs()

_is_sqlite = settings.db_url.startswith("sqlite")
engine = create_engine(
    settings.db_url,
    connect_args={"check_same_thread": False, "timeout": 120} if _is_sqlite else {"charset": "utf8mb4"},
    pool_pre_ping=True,
    pool_recycle=3600,
    pool_size=20 if not _is_sqlite else 5,
    max_overflow=40,
    json_serializer=_dump_json,
)


@compiles(Text, "mysql")
def _mysql_mediumtext(_type, _compiler, **_kw) -> str:
    # SQLite 的 TEXT 不限长度。业务文档有超过 64KB 的正文，MySQL 的 TEXT 装不下。
    return "MEDIUMTEXT"

if _is_sqlite:

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - driver hook
        dbapi_conn.isolation_level = None
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=120000")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _sqlite_begin(conn):  # pragma: no cover - driver hook
        # 事务开始即取得写锁，由 SQLite 的忙等待统一串行化，避免读事务升级为写事务时的锁冲突
        conn.exec_driver_sql("BEGIN IMMEDIATE")


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


class Base(DeclarativeBase):
    type_annotation_map = {dict: JSON, list: JSON}


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


_local = threading.local()
_STRICT = os.environ.get("TECHINS_STRICT_SESSIONS") == "1"


@contextmanager
def session_scope() -> Iterator[Session]:
    # SQLite 下每个事务都以 BEGIN IMMEDIATE 取得写锁，同一线程内嵌套事务会自我死锁；严格模式下直接报错以便定位
    depth = getattr(_local, "depth", 0)
    if depth and _is_sqlite:
        if _STRICT:
            raise RuntimeError("同一线程内嵌套打开数据库事务：\n" + "".join(traceback.format_stack(limit=8)))
        logging.getLogger("techins.db").warning("nested session detected:\n%s", "".join(traceback.format_stack(limit=8)))
    _local.depth = depth + 1
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
        _local.depth = depth


def _column_default_sql(column) -> str | None:
    default = column.default.arg if column.default is not None else None
    if callable(default):
        try:
            default = default(None)
        except TypeError:
            default = default()
    if isinstance(default, bool):
        return "1" if default else "0"
    if isinstance(default, (int, float)):
        return str(default)
    if isinstance(default, str):
        return "'" + default.replace("'", "''") + "'"
    if isinstance(default, dict):
        return "(JSON_OBJECT())" if engine.dialect.name == "mysql" else "'{}'"
    if isinstance(default, list):
        return "(JSON_ARRAY())" if engine.dialect.name == "mysql" else "'[]'"
    return None


def _existing_tables(conn) -> set[str]:
    if engine.dialect.name == "sqlite":
        rows = conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    else:
        rows = conn.exec_driver_sql("SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE()").fetchall()
    return {row[0] for row in rows}


def _existing_columns(conn, table: str) -> set[str]:
    if engine.dialect.name == "sqlite":
        rows = conn.exec_driver_sql(f'PRAGMA table_info("{table}")').fetchall()
        return {row[1] for row in rows}
    rows = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = :name"), {"name": table}).fetchall()
    return {row[0] for row in rows}


def auto_migrate() -> list[str]:
    """增量迁移：为已存在的表补齐模型新增的列（只增不删，不改已有列）。

    全程使用同一个写事务。另开连接做表结构查询会在 SQLite 上与本连接的写锁互相等待。
    """
    applied: list[str] = []
    preparer = engine.dialect.identifier_preparer
    with engine.begin() as conn:
        existing = _existing_tables(conn)
        for table in Base.metadata.sorted_tables:
            if table.name not in existing:
                continue
            present = _existing_columns(conn, table.name)
            for column in table.columns:
                if column.name in present:
                    continue
                ddl_type = column.type.compile(dialect=engine.dialect)
                default = _column_default_sql(column)
                stmt = f"ALTER TABLE {preparer.quote(table.name)} ADD COLUMN {preparer.quote(column.name)} {ddl_type}" + (f" DEFAULT {default}" if default is not None else "")
                conn.exec_driver_sql(stmt)
                applied.append(f"{table.name}.{column.name}")
    if applied:
        logging.getLogger("techins.db").info("auto migrated columns: %s", ", ".join(applied))
    return applied


def ensure_enterprise_credit_code_index() -> bool:
    """信用代码无重复时建立唯一索引。已有重复则跳过并记日志，不改历史数据。"""
    log = logging.getLogger("techins.db")
    with engine.begin() as conn:
        if "enterprises" not in _existing_tables(conn):
            return False
        dupes = conn.exec_driver_sql("SELECT credit_code FROM enterprises GROUP BY credit_code HAVING COUNT(*) > 1").fetchall()
        if dupes:
            log.warning("skip unique index on enterprises.credit_code; duplicate codes: %s", [row[0] for row in dupes])
            return False
        if engine.dialect.name == "sqlite":
            conn.exec_driver_sql("CREATE UNIQUE INDEX IF NOT EXISTS uq_enterprises_credit_code ON enterprises (credit_code)")
            return True
        exists = conn.execute(text("SELECT 1 FROM information_schema.statistics WHERE table_schema = DATABASE() AND table_name = 'enterprises' AND index_name = 'uq_enterprises_credit_code' LIMIT 1")).first()
        if exists is None:
            conn.exec_driver_sql("CREATE UNIQUE INDEX uq_enterprises_credit_code ON enterprises (credit_code)")
    return True


def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
