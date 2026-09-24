"""M01-M10 exact migration acceptance cases from phase_6_correction.md."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import pytest

from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError

from src.common.database import database_model as _database_model  # noqa: F401
from src.common.database.migrations.builtin import LATEST_SCHEMA_VERSION, V47_SCHEMA_VERSION
from src.common.database.migrations.models import MigrationExecutionContext
from src.common.database.migrations.v46_to_v47 import migrate_v46_to_v47


pytestmark = pytest.mark.phase6_matrix

def _context(connection):
    return MigrationExecutionContext(
        connection=connection, current_version=46, target_version=47,
        step_index=1, step_name="v46_to_v47", total_steps=1,
    )


def _v46_schema(connection, *, seed: bool) -> None:
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
        updated_at DATETIME NOT NULL, CHECK(source_space_id <> target_space_id))"""
    )
    connection.exec_driver_sql("INSERT INTO memory_spaces VALUES ('source'), ('target')")
    connection.exec_driver_sql("INSERT INTO memory_partitions VALUES ('p-source','source'), ('p-target','target')")
    if seed:
        connection.exec_driver_sql(
            "INSERT INTO memory_transfer_jobs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("legacy-job", "source", "target", "copy", '{"ids":["paragraph-1"]}',
             "manual", "skip", "pending", "legacy", datetime.now(), datetime.now()),
        )


def _migrate(*, seed: bool = False):
    engine = create_engine("sqlite://")
    connection = engine.connect()
    transaction = connection.begin()
    _v46_schema(connection, seed=seed)
    migrate_v46_to_v47(_context(connection))
    return connection, transaction


def _m01() -> None:
    connection, transaction = _migrate()
    try:
        tables = {row[0] for row in connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")}
        indexes = {row[0] for row in connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='index'")}
        assert {"memory_transfer_items", "memory_transfer_lineage", "memory_transfer_approvals", "memory_transfer_attempts", "memory_transfer_events"} <= tables
        assert {"uq_memory_transfer_jobs_idempotency", "ix_memory_transfer_items_job_status"} <= indexes
    finally:
        transaction.rollback()
        connection.close()


def _m02() -> None:
    connection, transaction = _migrate(seed=True)
    try:
        row = connection.exec_driver_sql(
            "SELECT id,created_by,filters_json,selection_json,principal_id,plan_revision FROM memory_transfer_jobs"
        ).one()
        assert row == ("legacy-job", "legacy", '{"ids":["paragraph-1"]}', '{}', '', 1)
    finally:
        transaction.rollback()
        connection.close()


def _m03() -> None:
    connection, transaction = _migrate(seed=True)
    try:
        migrate_v46_to_v47(_context(connection))
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM memory_transfer_jobs").scalar_one() == 1
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM memory_transfer_items").scalar_one() == 0
    finally:
        transaction.rollback()
        connection.close()


def _m04() -> None:
    import socket
    original = socket.socket.connect
    calls = 0
    def blocked(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("migration attempted network access")
    socket.socket.connect = blocked
    try:
        connection, transaction = _migrate()
        transaction.rollback()
        connection.close()
    finally:
        socket.socket.connect = original
    assert calls == 0


def _m05() -> None:
    connection, transaction = _migrate(seed=True)
    try:
        tables = {row[0] for row in connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "paragraphs" not in tables and "entities" not in tables and "relations" not in tables
        assert connection.exec_driver_sql("SELECT details_json FROM memory_transfer_events").fetchall() == []
    finally:
        transaction.rollback()
        connection.close()


def _m06() -> None:
    connection, transaction = _migrate()
    try:
        expected = {
            "memory_transfer_jobs": {"principal_id", "policy_snapshot_hash", "plan_revision"},
            "memory_transfer_items": {"source_version", "source_snapshot_hash", "superseded"},
            "memory_transfer_lineage": {"root_object_id", "ancestry_hash", "edge_key"},
            "memory_transfer_approvals": {"policy_snapshot_hash", "plan_revision"},
        }
        for table, names in expected.items():
            actual = {row[1] for row in connection.exec_driver_sql(f"PRAGMA table_info({table})")}
            assert names <= actual, (table, names - actual)
    finally:
        transaction.rollback()
        connection.close()


def _m07() -> None:
    connection, transaction = _migrate()
    try:
        with pytest.raises(IntegrityError):
            connection.exec_driver_sql(
                "INSERT INTO memory_transfer_items (id,job_id,mode,object_type,source_object_id,source_space_id,source_partition_id,target_space_id,target_partition_id,operation_key,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                ("orphan", "missing-job", "copy", "paragraph", "p1", "source", "p-source", "target", "p-target", "op", datetime.now(), datetime.now()),
            )
    finally:
        transaction.rollback()
        connection.close()


def _insert_job(connection, job_id: str, key: str) -> None:
    connection.exec_driver_sql(
        "INSERT INTO memory_transfer_jobs (id,source_space_id,target_space_id,mode,filters_json,approval_policy,conflict_policy,status,created_by,idempotency_key,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (job_id, "source", "target", "copy", "{}", "manual", "skip", "pending", "tester", key, datetime.now(), datetime.now()),
    )


def _m08() -> None:
    assert V47_SCHEMA_VERSION == 47
    assert LATEST_SCHEMA_VERSION == 47


def _m09() -> None:
    connection, transaction = _migrate()
    try:
        _insert_job(connection, "job", "job-key")
        base = ("copy", "paragraph", "p1", "source", "p-source", "target", "p-target", "duplicate-op", datetime.now(), datetime.now())
        sql = "INSERT INTO memory_transfer_items (id,job_id,mode,object_type,source_object_id,source_space_id,source_partition_id,target_space_id,target_partition_id,operation_key,created_at,updated_at) VALUES (?,?,?, ?,?,?,?,?,?,?,?,?)"
        connection.exec_driver_sql(sql, ("item-1", "job", *base))
        with pytest.raises(IntegrityError):
            connection.exec_driver_sql(sql, ("item-2", "job", *base))
    finally:
        transaction.rollback()
        connection.close()


def _m10() -> None:
    connection, transaction = _migrate()
    try:
        _insert_job(connection, "job-1", "same-key")
        with pytest.raises(IntegrityError):
            _insert_job(connection, "job-2", "same-key")
    finally:
        transaction.rollback()
        connection.close()


CASES: tuple[tuple[str, Callable[[], None]], ...] = (
    ("M01", _m01), ("M02", _m02), ("M03", _m03), ("M04", _m04), ("M05", _m05),
    ("M06", _m06), ("M07", _m07), ("M08", _m08), ("M09", _m09), ("M10", _m10),
)


@pytest.mark.parametrize("scenario_id,case", CASES, ids=[item[0] for item in CASES])
def test_phase6_m_matrix(scenario_id: str, case: Callable[[], None]) -> None:
    assert case.__name__ == f"_{scenario_id.lower()}"
    case()
