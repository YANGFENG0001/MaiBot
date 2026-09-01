"""v44 -> v45：用户记忆权限组与 Bot/空间双向 ACL。"""

from src.common.logger import get_logger
from .models import MigrationExecutionContext

logger = get_logger("database_migration")


def migrate_v44_to_v45(context: MigrationExecutionContext) -> None:
    statements = (
        """CREATE TABLE IF NOT EXISTS memory_permission_groups (id VARCHAR(64) NOT NULL PRIMARY KEY, name VARCHAR(100) NOT NULL UNIQUE, description TEXT NOT NULL DEFAULT '', enabled BOOLEAN NOT NULL DEFAULT 1, priority INTEGER NOT NULL DEFAULT 0, memory_scope_mode VARCHAR(16) NOT NULL DEFAULT 'inherit', is_manager_mode BOOLEAN NOT NULL DEFAULT 0, policy_revision INTEGER NOT NULL DEFAULT 1, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS memory_permission_group_members (id INTEGER NOT NULL PRIMARY KEY, permission_group_id VARCHAR(64) NOT NULL, person_id VARCHAR(255) NOT NULL, CONSTRAINT uq_memory_permission_group_member UNIQUE(permission_group_id, person_id), FOREIGN KEY(permission_group_id) REFERENCES memory_permission_groups(id))""",
        """CREATE TABLE IF NOT EXISTS memory_permission_group_contexts (id INTEGER NOT NULL PRIMARY KEY, permission_group_id VARCHAR(64) NOT NULL, scope_type VARCHAR(16) NOT NULL DEFAULT 'global', workspace_id VARCHAR(64), session_id VARCHAR(255), channel_type VARCHAR(16), allow_group_disclosure BOOLEAN NOT NULL DEFAULT 0, enabled BOOLEAN NOT NULL DEFAULT 1, FOREIGN KEY(permission_group_id) REFERENCES memory_permission_groups(id), FOREIGN KEY(workspace_id) REFERENCES workspaces(id))""",
        """CREATE TABLE IF NOT EXISTS memory_permission_group_capabilities (id INTEGER NOT NULL PRIMARY KEY, permission_group_id VARCHAR(64) NOT NULL, capability VARCHAR(100) NOT NULL, enabled BOOLEAN NOT NULL DEFAULT 1, CONSTRAINT uq_memory_permission_group_capability UNIQUE(permission_group_id, capability), FOREIGN KEY(permission_group_id) REFERENCES memory_permission_groups(id))""",
        """CREATE TABLE IF NOT EXISTS memory_permission_rules (id INTEGER NOT NULL PRIMARY KEY, permission_group_id VARCHAR(64) NOT NULL, effect VARCHAR(8) NOT NULL DEFAULT 'allow', space_selector VARCHAR(16) NOT NULL DEFAULT 'current', memory_space_id VARCHAR(64), partition_type VARCHAR(20) NOT NULL DEFAULT 'any', partition_selector VARCHAR(16) NOT NULL DEFAULT 'any', partition_key VARCHAR(255), memory_types_json TEXT NOT NULL DEFAULT '[]', tags_json TEXT NOT NULL DEFAULT '[]', sensitivity_max INTEGER, time_start DATETIME, time_end DATETIME, priority INTEGER NOT NULL DEFAULT 0, enabled BOOLEAN NOT NULL DEFAULT 1, FOREIGN KEY(permission_group_id) REFERENCES memory_permission_groups(id), FOREIGN KEY(memory_space_id) REFERENCES memory_spaces(id))""",
        """CREATE TABLE IF NOT EXISTS permission_group_bot_rules (id INTEGER NOT NULL PRIMARY KEY, permission_group_id VARCHAR(64) NOT NULL, effect VARCHAR(8) NOT NULL DEFAULT 'allow', bot_selector VARCHAR(20) NOT NULL DEFAULT 'current_group', bot_profile_id VARCHAR(64), FOREIGN KEY(permission_group_id) REFERENCES memory_permission_groups(id), FOREIGN KEY(bot_profile_id) REFERENCES bot_profiles(id))""",
        """CREATE TABLE IF NOT EXISTS bot_profile_memory_rules (id INTEGER NOT NULL PRIMARY KEY, bot_profile_id VARCHAR(64) NOT NULL, target_space_id VARCHAR(64) NOT NULL, can_read BOOLEAN NOT NULL DEFAULT 1, filters_json TEXT NOT NULL DEFAULT '{}', CONSTRAINT uq_bot_profile_memory_rule UNIQUE(bot_profile_id, target_space_id), FOREIGN KEY(bot_profile_id) REFERENCES bot_profiles(id), FOREIGN KEY(target_space_id) REFERENCES memory_spaces(id))""",
        """CREATE TABLE IF NOT EXISTS memory_space_bot_rules (id INTEGER NOT NULL PRIMARY KEY, memory_space_id VARCHAR(64) NOT NULL, bot_profile_id VARCHAR(64) NOT NULL, can_read BOOLEAN NOT NULL DEFAULT 1, filters_json TEXT NOT NULL DEFAULT '{}', CONSTRAINT uq_memory_space_bot_rule UNIQUE(memory_space_id, bot_profile_id), FOREIGN KEY(memory_space_id) REFERENCES memory_spaces(id), FOREIGN KEY(bot_profile_id) REFERENCES bot_profiles(id))""",
        "CREATE INDEX IF NOT EXISTS ix_memory_permission_group_member_person ON memory_permission_group_members(person_id)",
        "CREATE INDEX IF NOT EXISTS ix_memory_permission_context_lookup ON memory_permission_group_contexts(scope_type, workspace_id, session_id, channel_type)",
        "CREATE INDEX IF NOT EXISTS ix_memory_permission_rule_lookup ON memory_permission_rules(permission_group_id, enabled, priority)",
    )
    context.start_progress(total_tables=8, total_records=len(statements) + 3, description="v44 -> v45 迁移进度")
    for index, statement in enumerate(statements):
        context.connection.exec_driver_sql(statement)
        context.advance_progress(records=1, completed_tables=1 if index < 8 else 0)
    columns = {row[1] for row in context.connection.exec_driver_sql("PRAGMA table_info(memory_spaces)")}
    if "strict_isolation" not in columns:
        context.connection.exec_driver_sql("ALTER TABLE memory_spaces ADD COLUMN strict_isolation BOOLEAN NOT NULL DEFAULT 0")
    context.advance_progress(records=1)

    # 旧 MemorySpaceACL 只有在读方和暴露方完成双向握手时，才迁移为新 Bot/Space 双向许可。
    context.connection.exec_driver_sql(
        """INSERT OR IGNORE INTO bot_profile_memory_rules (bot_profile_id,target_space_id,can_read,filters_json)
        SELECT w.bot_profile_id, acl.peer_space_id, 1, COALESCE(acl.filters_json,'{}')
        FROM memory_space_acl acl
        JOIN memory_space_acl reciprocal
          ON reciprocal.owner_space_id=acl.peer_space_id
         AND reciprocal.peer_space_id=acl.owner_space_id
         AND reciprocal.expose_to_peer=1
        JOIN workspaces w ON w.memory_space_id=acl.owner_space_id
        WHERE acl.can_read_from_peer=1 AND w.bot_profile_id IS NOT NULL"""
    )
    context.advance_progress(records=1)
    context.connection.exec_driver_sql(
        """INSERT OR IGNORE INTO memory_space_bot_rules (memory_space_id,bot_profile_id,can_read,filters_json)
        SELECT acl.peer_space_id, w.bot_profile_id, 1, COALESCE(reciprocal.filters_json,'{}')
        FROM memory_space_acl acl
        JOIN memory_space_acl reciprocal
          ON reciprocal.owner_space_id=acl.peer_space_id
         AND reciprocal.peer_space_id=acl.owner_space_id
         AND reciprocal.expose_to_peer=1
        JOIN workspaces w ON w.memory_space_id=acl.owner_space_id
        WHERE acl.can_read_from_peer=1 AND w.bot_profile_id IS NOT NULL"""
    )
    context.advance_progress(records=1)
    logger.info("v44 -> v45 数据库迁移完成：用户记忆权限组与双向 ACL 已创建")
