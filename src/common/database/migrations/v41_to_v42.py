"""v41 schema 升级到 v42：补全逻辑记忆空间对象成员关系。"""

from src.common.logger import get_logger

from .models import MigrationExecutionContext

logger = get_logger("database_migration")


def migrate_v41_to_v42(context: MigrationExecutionContext) -> None:
    """建立记忆对象空间成员关系和旧共享组迁移状态表。"""

    statements = (
        """
        CREATE TABLE IF NOT EXISTS memory_object_spaces (
            id INTEGER NOT NULL PRIMARY KEY,
            object_type VARCHAR(32) NOT NULL,
            object_id VARCHAR(255) NOT NULL,
            memory_space_id VARCHAR(64) NOT NULL,
            source_session_id VARCHAR(255) NOT NULL DEFAULT '',
            origin_space_id VARCHAR(64),
            created_at DATETIME NOT NULL,
            CONSTRAINT uq_memory_object_spaces_member UNIQUE (object_type, object_id, memory_space_id),
            FOREIGN KEY(memory_space_id) REFERENCES memory_spaces (id),
            FOREIGN KEY(origin_space_id) REFERENCES memory_spaces (id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS memory_space_migration_states (
            migration_key VARCHAR(100) NOT NULL PRIMARY KEY,
            payload_hash VARCHAR(128) NOT NULL DEFAULT '',
            completed_at DATETIME NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_memory_object_spaces_object_type ON memory_object_spaces (object_type)",
        "CREATE INDEX IF NOT EXISTS ix_memory_object_spaces_object_id ON memory_object_spaces (object_id)",
        "CREATE INDEX IF NOT EXISTS ix_memory_object_spaces_memory_space_id ON memory_object_spaces (memory_space_id)",
        "CREATE INDEX IF NOT EXISTS ix_memory_object_spaces_source_session_id ON memory_object_spaces (source_session_id)",
        "CREATE INDEX IF NOT EXISTS ix_memory_object_spaces_origin_space_id ON memory_object_spaces (origin_space_id)",
    )
    context.start_progress(
        total_tables=2,
        total_records=len(statements),
        description="v41 -> v42 迁移进度",
        table_unit_name="表",
        record_unit_name="项目",
    )
    for index, statement in enumerate(statements):
        context.connection.exec_driver_sql(statement)
        context.advance_progress(records=1, completed_tables=1 if index < 2 else 0)
    logger.info("v41 -> v42 数据库迁移完成：逻辑记忆空间对象成员关系已创建")
