"""v47 -> v48: 记忆访问审计补齐观察页所需的决策元数据。"""

from src.common.logger import get_logger

from .models import MigrationExecutionContext

logger = get_logger("database_migration")

# 观察页要求每次请求可见 security_domain / policy revision / decision reason，
# 这三项此前只存在于运行期 BotRequestContext，审计表没有落库，导致观察页无法解释
# 「为什么这次请求能读到/读不到某段记忆」。
_AUDIT_COLUMNS = (
    ("security_domain", "VARCHAR(16) NOT NULL DEFAULT 'normal'"),
    ("policy_revision", "INTEGER NOT NULL DEFAULT 0"),
    ("decision_reason", "VARCHAR(64) NOT NULL DEFAULT ''"),
)

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_memory_access_audit_security_domain ON memory_access_audit(security_domain)",
    "CREATE INDEX IF NOT EXISTS ix_memory_access_audit_decision_reason ON memory_access_audit(decision_reason)",
)


def migrate_v47_to_v48(context: MigrationExecutionContext) -> None:
    connection = context.connection
    context.start_progress(
        total_tables=1,
        total_records=len(_AUDIT_COLUMNS) + len(_INDEXES),
        description="v47 -> v48 memory access audit decision metadata",
    )
    table = connection.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_access_audit'"
    ).fetchone()
    if table is None:
        raise RuntimeError("memory_access_audit missing from v47 schema")
    columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(memory_access_audit)")}
    for name, ddl in _AUDIT_COLUMNS:
        if name not in columns:
            connection.exec_driver_sql(f"ALTER TABLE memory_access_audit ADD COLUMN {name} {ddl}")
        context.advance_progress(records=1)
    for statement in _INDEXES:
        connection.exec_driver_sql(statement)
        context.advance_progress(records=1)
    logger.info("v47 -> v48 数据库迁移完成：记忆访问审计新增 security_domain/policy_revision/decision_reason")
