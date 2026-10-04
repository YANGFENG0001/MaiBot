"""M11-M13: v47 -> v48 记忆访问审计决策元数据迁移。

观察页要求每次请求可见 security_domain / policy revision / decision reason。
这三列此前只存在于运行期上下文，审计表没有落库；本迁移为旧库补齐列，并保证
重复执行安全（可重入）且不破坏已有审计行。
"""

from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.engine import Connection

from src.common.database import database_model as _database_model  # noqa: F401
from src.common.database.migrations.builtin import (
    LATEST_SCHEMA_VERSION,
    V48_SCHEMA_VERSION,
)
from src.common.database.migrations.models import MigrationExecutionContext
from src.common.database.migrations.v47_to_v48 import migrate_v47_to_v48

_NEW_COLUMNS = ("security_domain", "policy_revision", "decision_reason")


def _context(connection: Connection) -> MigrationExecutionContext:
    return MigrationExecutionContext(
        connection=connection,
        current_version=47,
        target_version=48,
        step_index=1,
        step_name="v47_to_v48",
        total_steps=1,
    )


def _legacy_schema(connection: Connection) -> None:
    connection.exec_driver_sql(
        """CREATE TABLE memory_access_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trace_id VARCHAR(64) NOT NULL DEFAULT '',
            session_id VARCHAR(255) NOT NULL DEFAULT '',
            person_id VARCHAR(255) NOT NULL DEFAULT '',
            workspace_id VARCHAR(64) NOT NULL DEFAULT '',
            active_bot_profile_id VARCHAR(64) NOT NULL DEFAULT '',
            permission_group_id VARCHAR(64) NOT NULL DEFAULT '',
            access_mode VARCHAR(16) NOT NULL DEFAULT 'normal',
            query_hash VARCHAR(64) NOT NULL DEFAULT '',
            requested_scope_json TEXT NOT NULL DEFAULT '{}',
            allowed_scope_json TEXT NOT NULL DEFAULT '{}',
            denied_scope_json TEXT NOT NULL DEFAULT '{}',
            result_count INTEGER NOT NULL DEFAULT 0,
            latency_ms INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL
        )"""
    )
    connection.exec_driver_sql(
        "INSERT INTO memory_access_audit "
        "(trace_id, session_id, person_id, access_mode, query_hash, created_at) "
        "VALUES ('legacy-trace', 'legacy-session', 'legacy-person', 'normal', 'legacy-hash', ?)",
        (datetime.now(),),
    )


def test_m11_v48_is_latest_and_columns_are_added() -> None:
    assert V48_SCHEMA_VERSION == 48
    assert LATEST_SCHEMA_VERSION == 48
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        _legacy_schema(connection)
        migrate_v47_to_v48(_context(connection))
        columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(memory_access_audit)")}
        assert set(_NEW_COLUMNS) <= columns


def test_m12_existing_rows_get_safe_defaults() -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        _legacy_schema(connection)
        migrate_v47_to_v48(_context(connection))
        row = connection.exec_driver_sql(
            "SELECT security_domain, policy_revision, decision_reason, trace_id "
            "FROM memory_access_audit WHERE trace_id = 'legacy-trace'"
        ).one()
        assert row == ("normal", 0, "", "legacy-trace")


def test_m13_migration_is_idempotent() -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        _legacy_schema(connection)
        migrate_v47_to_v48(_context(connection))
        migrate_v47_to_v48(_context(connection))
        columns = [row[1] for row in connection.exec_driver_sql("PRAGMA table_info(memory_access_audit)")]
        assert columns.count("security_domain") == 1
        assert columns.count("policy_revision") == 1
        assert columns.count("decision_reason") == 1
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM memory_access_audit").scalar() == 1
