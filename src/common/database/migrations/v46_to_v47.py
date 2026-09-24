"""v46 -> v47: auditable and recoverable memory transfer workflows."""

from src.common.logger import get_logger

from .models import MigrationExecutionContext

logger = get_logger("database_migration")

_JOB_COLUMNS = (
    ("workspace_id", "VARCHAR(64)"),
    ("principal_id", "VARCHAR(128) NOT NULL DEFAULT ''"),
    ("bot_profile_id", "VARCHAR(64)"),
    ("permission_group_id", "VARCHAR(64)"),
    ("security_domain", "VARCHAR(16) NOT NULL DEFAULT 'normal'"),
    ("idempotency_key", "VARCHAR(128) NOT NULL DEFAULT ''"),
    ("selection_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("source_snapshot_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("target_snapshot_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("policy_snapshot_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("policy_snapshot_hash", "VARCHAR(128) NOT NULL DEFAULT ''"),
    ("plan_revision", "INTEGER NOT NULL DEFAULT 1"),
    ("plan_hash", "VARCHAR(128) NOT NULL DEFAULT ''"),
    ("source_space_revision", "INTEGER NOT NULL DEFAULT 1"),
    ("target_space_revision", "INTEGER NOT NULL DEFAULT 1"),
    ("approval_required", "BOOLEAN NOT NULL DEFAULT 1"),
    ("approval_state", "VARCHAR(16) NOT NULL DEFAULT 'not_required'"),
    ("retry_count", "INTEGER NOT NULL DEFAULT 0"),
    ("max_retries", "INTEGER NOT NULL DEFAULT 3"),
    ("next_retry_at", "DATETIME"),
    ("lease_token_hash", "VARCHAR(128)"),
    ("lease_expires_at", "DATETIME"),
    ("started_at", "DATETIME"),
    ("completed_at", "DATETIME"),
    ("cancelled_at", "DATETIME"),
    ("cancel_requested", "BOOLEAN NOT NULL DEFAULT 0"),
    ("last_error_code", "VARCHAR(64) NOT NULL DEFAULT ''"),
    ("last_error_detail", "TEXT NOT NULL DEFAULT ''"),
)

_TABLES = (
    """CREATE TABLE IF NOT EXISTS memory_transfer_items (
        id VARCHAR(64) PRIMARY KEY, job_id VARCHAR(64) NOT NULL, mode VARCHAR(16) NOT NULL,
        plan_revision INTEGER NOT NULL DEFAULT 1, superseded BOOLEAN NOT NULL DEFAULT 0, object_type VARCHAR(32) NOT NULL, source_object_id VARCHAR(255) NOT NULL,
        target_object_id VARCHAR(255), source_space_id VARCHAR(64) NOT NULL,
        source_partition_id VARCHAR(96) NOT NULL, target_space_id VARCHAR(64) NOT NULL,
        target_partition_id VARCHAR(96) NOT NULL, root_object_type VARCHAR(32) NOT NULL DEFAULT '',
        root_object_id VARCHAR(255) NOT NULL DEFAULT '', content_fingerprint VARCHAR(128) NOT NULL DEFAULT '',
        source_version VARCHAR(128) NOT NULL DEFAULT '', source_snapshot_hash VARCHAR(128) NOT NULL DEFAULT '', operation_key VARCHAR(160) NOT NULL, status VARCHAR(24) NOT NULL DEFAULT 'planned',
        conflict_code VARCHAR(64) NOT NULL DEFAULT '', error_code VARCHAR(64) NOT NULL DEFAULT '',
        error_detail TEXT NOT NULL DEFAULT '', attempt_count INTEGER NOT NULL DEFAULT 0,
        ancestry_hash VARCHAR(128) NOT NULL DEFAULT '', created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
        UNIQUE(job_id, plan_revision, object_type, source_object_id, target_partition_id), UNIQUE(operation_key),
        FOREIGN KEY(job_id) REFERENCES memory_transfer_jobs(id),
        FOREIGN KEY(source_space_id) REFERENCES memory_spaces(id),
        FOREIGN KEY(target_space_id) REFERENCES memory_spaces(id),
        FOREIGN KEY(source_partition_id) REFERENCES memory_partitions(id),
        FOREIGN KEY(target_partition_id) REFERENCES memory_partitions(id)
    )""",
    """CREATE TABLE IF NOT EXISTS memory_transfer_lineage (
        id VARCHAR(64) PRIMARY KEY, item_id VARCHAR(64) NOT NULL, job_id VARCHAR(64) NOT NULL,
        relation_type VARCHAR(16) NOT NULL, object_type VARCHAR(32) NOT NULL, object_id VARCHAR(255) NOT NULL,
        source_object_type VARCHAR(32) NOT NULL, source_object_id VARCHAR(255) NOT NULL,
        source_space_id VARCHAR(64) NOT NULL, source_partition_id VARCHAR(96) NOT NULL,
        target_space_id VARCHAR(64) NOT NULL, target_partition_id VARCHAR(96) NOT NULL,
        root_object_type VARCHAR(32) NOT NULL DEFAULT '', root_object_id VARCHAR(255) NOT NULL DEFAULT '',
        depth INTEGER NOT NULL DEFAULT 0, ancestry_hash VARCHAR(128) NOT NULL DEFAULT '',
        edge_key VARCHAR(512), created_at DATETIME NOT NULL,
        UNIQUE(item_id, relation_type, object_type, object_id, target_partition_id),
        FOREIGN KEY(item_id) REFERENCES memory_transfer_items(id),
        FOREIGN KEY(job_id) REFERENCES memory_transfer_jobs(id)
    )""",
    """CREATE TABLE IF NOT EXISTS memory_transfer_approvals (
        id VARCHAR(64) PRIMARY KEY, job_id VARCHAR(64) NOT NULL, decision VARCHAR(16) NOT NULL,
        actor_id VARCHAR(255) NOT NULL, actor_type VARCHAR(32) NOT NULL DEFAULT 'user',
        policy_revision INTEGER NOT NULL, policy_snapshot_hash VARCHAR(128) NOT NULL DEFAULT '',
        plan_revision INTEGER NOT NULL DEFAULT 1, plan_hash VARCHAR(128) NOT NULL, comment TEXT NOT NULL DEFAULT '',
        created_at DATETIME NOT NULL,
        UNIQUE(job_id, plan_revision, plan_hash, policy_snapshot_hash, actor_id, decision),
        FOREIGN KEY(job_id) REFERENCES memory_transfer_jobs(id)
    )""",
    """CREATE TABLE IF NOT EXISTS memory_transfer_attempts (
        id VARCHAR(64) PRIMARY KEY, job_id VARCHAR(64) NOT NULL, item_id VARCHAR(64),
        attempt_no INTEGER NOT NULL, lease_token_hash VARCHAR(128) NOT NULL, worker_id VARCHAR(128) NOT NULL,
        status VARCHAR(24) NOT NULL DEFAULT 'running', error_code VARCHAR(64) NOT NULL DEFAULT '',
        error_detail TEXT NOT NULL DEFAULT '', started_at DATETIME NOT NULL, finished_at DATETIME,
        UNIQUE(job_id, item_id, attempt_no), FOREIGN KEY(job_id) REFERENCES memory_transfer_jobs(id),
        FOREIGN KEY(item_id) REFERENCES memory_transfer_items(id)
    )""",
    """CREATE TABLE IF NOT EXISTS memory_transfer_events (
        id VARCHAR(64) PRIMARY KEY, job_id VARCHAR(64) NOT NULL, item_id VARCHAR(64),
        event_type VARCHAR(48) NOT NULL, actor_id VARCHAR(255) NOT NULL DEFAULT 'system',
        from_status VARCHAR(24) NOT NULL DEFAULT '', to_status VARCHAR(24) NOT NULL DEFAULT '',
        result_code VARCHAR(64) NOT NULL DEFAULT '', details_json TEXT NOT NULL DEFAULT '{}',
        created_at DATETIME NOT NULL, FOREIGN KEY(job_id) REFERENCES memory_transfer_jobs(id),
        FOREIGN KEY(item_id) REFERENCES memory_transfer_items(id)
    )""",
)

_INDEXES = (
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_memory_transfer_jobs_idempotency "
    "ON memory_transfer_jobs(idempotency_key) WHERE idempotency_key <> ''",
    "CREATE INDEX IF NOT EXISTS ix_memory_transfer_jobs_next_retry ON memory_transfer_jobs(status, next_retry_at)",
    "CREATE INDEX IF NOT EXISTS ix_memory_transfer_items_job_status ON memory_transfer_items(job_id, status)",
    "CREATE INDEX IF NOT EXISTS ix_memory_transfer_items_source "
    "ON memory_transfer_items(object_type, source_object_id, source_partition_id)",
    "CREATE INDEX IF NOT EXISTS ix_memory_transfer_items_target ON memory_transfer_items(target_partition_id)",
    "CREATE INDEX IF NOT EXISTS ix_memory_transfer_items_target_fingerprint "
    "ON memory_transfer_items(target_partition_id, content_fingerprint)",
    "CREATE INDEX IF NOT EXISTS ix_memory_transfer_lineage_object_target "
    "ON memory_transfer_lineage(object_type, object_id, target_partition_id)",
    "CREATE INDEX IF NOT EXISTS ix_memory_transfer_lineage_root_target "
    "ON memory_transfer_lineage(root_object_type, root_object_id, target_partition_id)",
    "CREATE INDEX IF NOT EXISTS ix_memory_transfer_lineage_source "
    "ON memory_transfer_lineage(source_object_type, source_object_id, source_partition_id)",
    "CREATE INDEX IF NOT EXISTS ix_memory_transfer_lineage_ancestry ON memory_transfer_lineage(ancestry_hash)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_memory_transfer_lineage_edge_key "
    "ON memory_transfer_lineage(edge_key) WHERE edge_key <> ''",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_memory_transfer_approval_identity "
    "ON memory_transfer_approvals(job_id, plan_revision, plan_hash, policy_snapshot_hash, actor_id, decision)",
    "CREATE INDEX IF NOT EXISTS ix_memory_transfer_events_job ON memory_transfer_events(job_id, created_at)",
)


def migrate_v46_to_v47(context: MigrationExecutionContext) -> None:
    connection = context.connection
    context.start_progress(
        total_tables=len(_TABLES),
        total_records=len(_JOB_COLUMNS) + len(_TABLES) + len(_INDEXES),
        description="v46 -> v47 memory transfer",
    )
    table = connection.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_transfer_jobs'"
    ).fetchone()
    if table is None:
        raise RuntimeError("memory_transfer_jobs missing from v46 schema")
    columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(memory_transfer_jobs)")}
    for name, ddl in _JOB_COLUMNS:
        if name not in columns:
            connection.exec_driver_sql(f"ALTER TABLE memory_transfer_jobs ADD COLUMN {name} {ddl}")
        context.advance_progress(records=1)
    for statement in _TABLES:
        connection.exec_driver_sql(statement)
        context.advance_progress(records=1, completed_tables=1)
    lineage_columns = {
        row[1] for row in connection.exec_driver_sql("PRAGMA table_info(memory_transfer_lineage)")
    }
    if "edge_key" not in lineage_columns:
        connection.exec_driver_sql(
            "ALTER TABLE memory_transfer_lineage ADD COLUMN edge_key VARCHAR(512)"
        )
    approval_columns = {
        row[1] for row in connection.exec_driver_sql("PRAGMA table_info(memory_transfer_approvals)")
    }
    if "policy_snapshot_hash" not in approval_columns:
        connection.exec_driver_sql(
            "ALTER TABLE memory_transfer_approvals "
            "ADD COLUMN policy_snapshot_hash VARCHAR(128) NOT NULL DEFAULT ''"
        )
    if "plan_revision" not in approval_columns:
        connection.exec_driver_sql(
            "ALTER TABLE memory_transfer_approvals ADD COLUMN plan_revision INTEGER NOT NULL DEFAULT 1"
        )
    approval_unique_sets = []
    for index_row in connection.exec_driver_sql("PRAGMA index_list(memory_transfer_approvals)"):
        if not bool(index_row[2]):
            continue
        approval_unique_sets.append(tuple(
            row[2] for row in connection.exec_driver_sql(f"PRAGMA index_info('{index_row[1]}')")
        ))
    legacy_identity = ("job_id", "actor_id", "decision", "plan_hash")
    if legacy_identity in approval_unique_sets:
        connection.exec_driver_sql(
            "ALTER TABLE memory_transfer_approvals RENAME TO memory_transfer_approvals_legacy_v47"
        )
        connection.exec_driver_sql(_TABLES[2])
        connection.exec_driver_sql(
            "INSERT INTO memory_transfer_approvals "
            "(id,job_id,decision,actor_id,actor_type,policy_revision,policy_snapshot_hash,"
            "plan_revision,plan_hash,comment,created_at) "
            "SELECT id,job_id,decision,actor_id,actor_type,policy_revision,policy_snapshot_hash,"
            "plan_revision,plan_hash,comment,created_at FROM memory_transfer_approvals_legacy_v47"
        )
        connection.exec_driver_sql("DROP TABLE memory_transfer_approvals_legacy_v47")
    for statement in _INDEXES:
        connection.exec_driver_sql(statement)
        context.advance_progress(records=1)
    logger.info("v46 -> v47 数据库迁移完成：MemoryTransfer 审批、lineage、幂等和恢复表已创建")
