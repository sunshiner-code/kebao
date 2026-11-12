"""把现有 SQLite 业务库复制到 .env 配置的 MySQL。

隐私占位符库 data/privacy_vault.db 不在这次迁移里，它和业务库分开存放。
已有业务数据时拒绝覆盖，避免把同一份数据再导入一次。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, create_engine, insert, text
from sqlalchemy.types import JSON

from techins.config import settings
from techins.db import Base, engine, ensure_enterprise_credit_code_index
from techins.knowledge.index import ensure_fulltext
import techins.models  # noqa: F401


def _parse_dt(value):
    if isinstance(value, datetime) or value is None:
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", ""))
    except ValueError:
        return None


_truncated: set[str] = set()


def _coerce(column, value):
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if isinstance(column.type, JSON):
        parsed = value
        if isinstance(value, str):
            if not value.strip():
                parsed = None
            else:
                try:
                    parsed = json.loads(value)
                except json.JSONDecodeError:
                    parsed = None
        if parsed is None and not column.nullable:
            default = column.default.arg if column.default is not None else dict
            parsed = [] if default in (list, []) else {}
        return parsed
    if isinstance(column.type, Boolean):
        if value is None:
            return None if column.nullable else False
        return bool(value)
    if isinstance(column.type, DateTime):
        parsed = _parse_dt(value)
        if parsed is None and not column.nullable:
            return datetime.utcnow()
        return parsed
    if isinstance(column.type, String) and isinstance(value, str) and column.type.length and len(value) > column.type.length:
        label = f"{column.table.name}.{column.name}"
        if label not in _truncated:
            print(f"截断 {label}：声明长度 {column.type.length}，实际 {len(value)}", flush=True)
            _truncated.add(label)
        return value[: column.type.length]
    return value


def _batches(rows: list[dict]):
    batch: list[dict] = []
    size = 0
    for row in rows:
        row_size = 0
        for value in row.values():
            if isinstance(value, str):
                row_size += len(value)
            elif isinstance(value, (dict, list)):
                row_size += len(json.dumps(value, ensure_ascii=False))
            else:
                row_size += 32
        if batch and (len(batch) >= 80 or size + row_size > 8_000_000):
            yield batch
            batch = []
            size = 0
        batch.append(row)
        size += row_size
    if batch:
        yield batch


def _sqlite_tables(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def _copy_table(src: sqlite3.Connection, dest, table) -> int:
    present = {row[1] for row in src.execute(f'PRAGMA table_info("{table.name}")')}
    columns = [column for column in table.columns if column.name in present]
    if not columns:
        return 0
    names = ", ".join(f'"{column.name}"' for column in columns)
    copied = 0
    pending: list[dict] = []
    for raw in src.execute(f'SELECT {names} FROM "{table.name}"'):
        pending.append({column.name: _coerce(column, raw[index]) for index, column in enumerate(columns)})
        if len(pending) >= 500:
            for batch in _batches(pending):
                dest.execute(insert(table).values(batch))
                copied += len(batch)
            dest.commit()
            pending = []
            print(f"  {table.name}: {copied}", flush=True)
    if pending:
        for batch in _batches(pending):
            dest.execute(insert(table).values(batch))
            copied += len(batch)
        dest.commit()
    return copied


def _reset_autoincrement(dest) -> None:
    preparer = engine.dialect.identifier_preparer
    for table in Base.metadata.sorted_tables:
        column = table.autoincrement_column
        if column is None or not isinstance(column.type, Integer):
            continue
        quoted_table = preparer.quote(table.name)
        quoted_column = preparer.quote(column.name)
        max_id = dest.execute(text(f"SELECT MAX({quoted_column}) FROM {quoted_table}")).scalar()
        if max_id:
            dest.execute(text(f"ALTER TABLE {quoted_table} AUTO_INCREMENT = {int(max_id) + 1}"))
    dest.commit()


def main() -> None:
    if not settings.db_url.startswith("mysql"):
        raise SystemExit(f"当前数据库不是 MySQL：{settings.db_url}")
    source_path = settings.data_dir / "techins.db"
    if not source_path.exists():
        raise SystemExit(f"找不到 SQLite 业务库：{source_path}")

    print(f"源库 {source_path}")
    print("目标 MySQL 已从环境配置读取")
    Base.metadata.create_all(engine)
    with engine.connect() as dest:
        existing = dest.execute(text("SELECT COUNT(*) FROM users")).scalar()
        if existing:
            raise SystemExit(f"MySQL 的 users 表已有 {existing} 行，停止导入，避免重复写入")
        dest.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        dest.execute(text("SET UNIQUE_CHECKS=0"))
        dest.commit()
        src = sqlite3.connect(source_path)
        src.execute("PRAGMA busy_timeout=120000")
        src.execute("BEGIN")
        available = _sqlite_tables(src)
        try:
            for table in Base.metadata.sorted_tables:
                if table.name not in available:
                    print(f"跳过 {table.name}：SQLite 中没有这张表")
                    continue
                count = src.execute(f'SELECT COUNT(*) FROM "{table.name}"').fetchone()[0]
                if not count:
                    continue
                print(f"导入 {table.name}（{count} 行）", flush=True)
                copied = _copy_table(src, dest, table)
                print(f"完成 {table.name}：{copied} 行", flush=True)
            _reset_autoincrement(dest)
        except Exception:
            print("导入中断，正在清空已写入的表，方便重新执行", flush=True)
            dest.execute(text("SET FOREIGN_KEY_CHECKS=0"))
            preparer = engine.dialect.identifier_preparer
            for table in reversed(list(Base.metadata.sorted_tables)):
                dest.execute(text(f"TRUNCATE TABLE {preparer.quote(table.name)}"))
            dest.commit()
            raise
        finally:
            src.close()
            dest.execute(text("SET UNIQUE_CHECKS=1"))
            dest.execute(text("SET FOREIGN_KEY_CHECKS=1"))
            dest.commit()

    ensure_enterprise_credit_code_index()
    print("创建全文索引，这一步会按切片数量花几分钟", flush=True)
    ensure_fulltext()

    with engine.connect() as dest:
        print("核对行数：")
        src = sqlite3.connect(source_path)
        mismatched = False
        for table in Base.metadata.sorted_tables:
            if table.name not in _sqlite_tables(src):
                continue
            source_count = src.execute(f'SELECT COUNT(*) FROM "{table.name}"').fetchone()[0]
            target_count = dest.execute(text(f"SELECT COUNT(*) FROM {engine.dialect.identifier_preparer.quote(table.name)}")).scalar()
            if source_count != target_count:
                mismatched = True
                print(f"  不一致 {table.name}: sqlite {source_count} / mysql {target_count}")
        src.close()
        user = dest.execute(text("SELECT username, display_name, enabled FROM users WHERE username = 'sunyalin'")).first()
        print(f"账号 sunyalin：{tuple(user) if user else '未找到'}")
        if mismatched:
            raise SystemExit("有表的行数不一致")
    print("迁移完成")


if __name__ == "__main__":
    sys.exit(main())
