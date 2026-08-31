"""v43 schema 升级到 v44：逻辑记忆分区与对象分区成员关系。"""

from hashlib import sha256

from src.common.logger import get_logger

from .models import MigrationExecutionContext

logger = get_logger("database_migration")


def build_partition_id(memory_space_id: str, partition_type: str, partition_key: str, security_domain: str) -> str:
    payload = f"{memory_space_id}|{partition_type}|{partition_key}|{security_domain}"
    return f"memory-partition-{sha256(payload.encode('utf-8')).hexdigest()[:32]}"


def _security_domain(memory_space_id: str, space_type: str) -> str:
    return "kami" if space_type == "kami" or memory_space_id == "memory-space-kami" else "normal"


def migrate_v43_to_v44(context: MigrationExecutionContext) -> None:
    statements = (
        """CREATE TABLE IF NOT EXISTS memory_partitions (id VARCHAR(96) NOT NULL PRIMARY KEY, memory_space_id VARCHAR(64) NOT NULL, partition_type VARCHAR(20) NOT NULL, partition_key VARCHAR(255) NOT NULL, security_domain VARCHAR(16) NOT NULL DEFAULT 'normal', display_name VARCHAR(255) NOT NULL DEFAULT '', enabled BOOLEAN NOT NULL DEFAULT 1, policy_revision INTEGER NOT NULL DEFAULT 1, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, CONSTRAINT uq_memory_partitions_scope UNIQUE(memory_space_id, partition_type, partition_key, security_domain), FOREIGN KEY(memory_space_id) REFERENCES memory_spaces(id))""",
        """CREATE TABLE IF NOT EXISTS memory_object_partitions (id INTEGER NOT NULL PRIMARY KEY, object_type VARCHAR(32) NOT NULL, object_id VARCHAR(255) NOT NULL, partition_id VARCHAR(96) NOT NULL, source_session_id VARCHAR(255) NOT NULL DEFAULT '', origin_space_id VARCHAR(64), origin_partition_id VARCHAR(96), transfer_job_id VARCHAR(64), created_at DATETIME NOT NULL, CONSTRAINT uq_memory_object_partitions_member UNIQUE(object_type, object_id, partition_id), FOREIGN KEY(partition_id) REFERENCES memory_partitions(id), FOREIGN KEY(origin_space_id) REFERENCES memory_spaces(id), FOREIGN KEY(origin_partition_id) REFERENCES memory_partitions(id))""",
        "CREATE INDEX IF NOT EXISTS ix_memory_partitions_memory_space_id ON memory_partitions(memory_space_id)",
        "CREATE INDEX IF NOT EXISTS ix_memory_partitions_type_key ON memory_partitions(partition_type, partition_key)",
        "CREATE INDEX IF NOT EXISTS ix_memory_partitions_security_domain ON memory_partitions(security_domain)",
        "CREATE INDEX IF NOT EXISTS ix_memory_object_partitions_object ON memory_object_partitions(object_type, object_id)",
        "CREATE INDEX IF NOT EXISTS ix_memory_object_partitions_partition_id ON memory_object_partitions(partition_id)",
        "CREATE INDEX IF NOT EXISTS ix_memory_object_partitions_source_session_id ON memory_object_partitions(source_session_id)",
    )
    context.start_progress(total_tables=2, total_records=len(statements) + 2, description="v43 -> v44 迁移进度")
    for index, statement in enumerate(statements):
        context.connection.exec_driver_sql(statement)
        context.advance_progress(records=1, completed_tables=1 if index < 2 else 0)

    spaces = context.connection.exec_driver_sql("SELECT id, COALESCE(space_type, 'private') FROM memory_spaces").fetchall()
    domains = {str(space_id): _security_domain(str(space_id), str(space_type)) for space_id, space_type in spaces}
    for space_id, _space_type in spaces:
        space_id = str(space_id)
        domain = domains[space_id]
        partition_id = build_partition_id(space_id, "shared", "shared", domain)
        context.connection.exec_driver_sql(
            "INSERT OR IGNORE INTO memory_partitions (id,memory_space_id,partition_type,partition_key,security_domain,display_name,enabled,policy_revision,created_at,updated_at) VALUES (?,?,?,?,?,?,1,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
            (partition_id, space_id, "shared", "shared", domain, "共享记忆"),
        )
    context.advance_progress(records=1)

    rows = context.connection.exec_driver_sql(
        "SELECT object_type, object_id, memory_space_id, COALESCE(source_session_id,''), origin_space_id, created_at FROM memory_object_spaces"
    ).fetchall()
    for object_type, object_id, space_id, source_session_id, origin_space_id, created_at in rows:
        object_type, object_id, space_id = str(object_type), str(object_id), str(space_id)
        source_session_id = str(source_session_id or "")
        if object_type == "person_profile":
            partition_type, partition_key, display_name = "person", object_id, f"人物 {object_id}"
        elif source_session_id:
            partition_type, partition_key, display_name = "conversation", source_session_id, f"会话 {source_session_id}"
        else:
            partition_type, partition_key, display_name = "shared", "shared", "共享记忆"
        domain = domains.get(space_id, "normal")
        partition_id = build_partition_id(space_id, partition_type, partition_key, domain)
        context.connection.exec_driver_sql(
            "INSERT OR IGNORE INTO memory_partitions (id,memory_space_id,partition_type,partition_key,security_domain,display_name,enabled,policy_revision,created_at,updated_at) VALUES (?,?,?,?,?,?,1,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
            (partition_id, space_id, partition_type, partition_key, domain, display_name),
        )
        context.connection.exec_driver_sql(
            "INSERT OR IGNORE INTO memory_object_partitions (object_type,object_id,partition_id,source_session_id,origin_space_id,created_at) VALUES (?,?,?,?,?,?)",
            (object_type, object_id, partition_id, source_session_id, origin_space_id, created_at),
        )
    context.advance_progress(records=1)
    logger.info("v43 -> v44 数据库迁移完成：MemoryPartition 已创建并完成兼容回填")
