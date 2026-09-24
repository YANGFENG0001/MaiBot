"""M01-M10: v46 -> v47 MemoryTransfer migration gates."""

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError
from sqlmodel import SQLModel

from src.common.database import database_model as _database_model  # noqa: F401
from src.common.database.migrations.builtin import LATEST_SCHEMA_VERSION, V47_SCHEMA_VERSION
from src.common.database.migrations.models import MigrationExecutionContext
from src.common.database.migrations.v46_to_v47 import migrate_v46_to_v47


def _context(connection: Connection) -> MigrationExecutionContext:
    return MigrationExecutionContext(
        connection=connection,
        current_version=46,
        target_version=47,
        step_index=1,
        step_name="v46_to_v47",
        total_steps=1,
    )


def _legacy_schema(connection: Connection) -> None:
    connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    connection.exec_driver_sql("CREATE TABLE memory_spaces (id VARCHAR(64) PRIMARY KEY)")
    connection.exec_driver_sql(
        "CREATE TABLE memory_partitions (id VARCHAR(96) PRIMARY KEY, memory_space_id VARCHAR(64) NOT NULL)"
    )
    connection.exec_driver_sql(
        """CREATE TABLE memory_transfer_jobs (
            id VARCHAR(64) PRIMARY KEY, source_space_id VARCHAR(64) NOT NULL,
            target_space_id VARCHAR(64) NOT NULL, mode VARCHAR(16) NOT NULL,
            filters_json TEXT NOT NULL DEFAULT '{}', approval_policy VARCHAR(16) NOT NULL DEFAULT 'manual',
            conflict_policy VARCHAR(16) NOT NULL DEFAULT 'skip', status VARCHAR(24) NOT NULL DEFAULT 'pending',
            created_by VARCHAR(64) NOT NULL DEFAULT 'webui', created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL, CHECK(source_space_id <> target_space_id)
        )"""
    )
    connection.exec_driver_sql("INSERT INTO memory_spaces VALUES ('source'), ('target')")
    connection.exec_driver_sql("INSERT INTO memory_partitions VALUES ('p-source','source'), ('p-target','target')")
    connection.exec_driver_sql(
        "INSERT INTO memory_transfer_jobs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            "legacy-job", "source", "target", "copy", '{"ids":["paragraph-1"]}',
            "manual", "skip", "pending", "legacy", datetime.now(), datetime.now(),
        ),
    )


def test_m01_m02_m03_m04_schema_version_and_lossless_rerun() -> None:
    assert V47_SCHEMA_VERSION == 47
    assert LATEST_SCHEMA_VERSION == 47
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        _legacy_schema(connection)
        migrate_v46_to_v47(_context(connection))
        migrate_v46_to_v47(_context(connection))
        row = connection.exec_driver_sql(
            "SELECT id, created_by, filters_json, selection_json FROM memory_transfer_jobs"
        ).one()
        assert row == ("legacy-job", "legacy", '{"ids":["paragraph-1"]}', '{}')
        tables = {row[0] for row in connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {
            "memory_transfer_items", "memory_transfer_lineage", "memory_transfer_approvals",
            "memory_transfer_attempts", "memory_transfer_events",
        } <= tables


def test_m05_m06_m07_foreign_keys_and_unique_constraints() -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        _legacy_schema(connection)
        migrate_v46_to_v47(_context(connection))
        now = datetime.now()
        item = (
            "item-1", "legacy-job", "copy", "paragraph", "source-object", None, "source", "p-source",
            "target", "p-target", "paragraph", "source-object", "fp", "operation-1", "planned", "", "", "", 0, "hash", now, now,
        )
        connection.exec_driver_sql(
            """INSERT INTO memory_transfer_items (id,job_id,mode,object_type,source_object_id,target_object_id,source_space_id,source_partition_id,
             target_space_id,target_partition_id,root_object_type,root_object_id,content_fingerprint,operation_key,
             status,conflict_code,error_code,error_detail,attempt_count,ancestry_hash,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            item,
        )
        with pytest.raises(IntegrityError):
            connection.exec_driver_sql(
                """INSERT INTO memory_transfer_items (id,job_id,mode,object_type,source_object_id,target_object_id,source_space_id,source_partition_id,
             target_space_id,target_partition_id,root_object_type,root_object_id,content_fingerprint,operation_key,
             status,conflict_code,error_code,error_detail,attempt_count,ancestry_hash,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("item-2", *item[1:13], "operation-1", *item[14:]),
            )


def test_m08_m09_m10_latest_orm_and_partial_index() -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with engine.begin() as connection:
        migrate_v46_to_v47(_context(connection))
        migrate_v46_to_v47(_context(connection))
        indexes = {row[1] for row in connection.exec_driver_sql("PRAGMA index_list(memory_transfer_jobs)")}
        assert "uq_memory_transfer_jobs_idempotency" in indexes
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
        columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(memory_transfer_lineage)")}
        assert {"object_type", "object_id", "root_object_type", "root_object_id", "ancestry_hash"} <= columns
