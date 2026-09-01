"""v45 -> v46：Kami 安全域、控制审计与消息安全标记。"""

from src.common.logger import get_logger

from .models import MigrationExecutionContext
from .v43_to_v44 import build_partition_id

logger = get_logger("database_migration")

KAMI_MEMORY_SPACE_ID = "memory-space-kami"
KAMI_PERSONA_PROFILE_ID = "persona-profile-kami"
KAMI_BOT_PROFILE_ID = "bot-profile-kami"
KAMI_SECURITY_DOMAIN = "kami"

_KAMI_PARTITION_BASE = (
    ("shared", "shared", "Kami 共享记忆"),
    ("person", "person-kami", "Kami 管理者"),
    ("conversation", "conversation-kami", "Kami 管理会话"),
)

_TABLE_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS kami_session_states (
        id VARCHAR(64) NOT NULL PRIMARY KEY,
        session_id VARCHAR(255) NOT NULL,
        person_id VARCHAR(255) NOT NULL,
        kami_bot_profile_id VARCHAR(64) NOT NULL,
        activated_from_bot_profile_id VARCHAR(64) NOT NULL DEFAULT '',
        permission_group_id VARCHAR(64) NOT NULL,
        status VARCHAR(16) NOT NULL DEFAULT 'active',
        activated_at DATETIME NOT NULL,
        expires_at DATETIME NOT NULL,
        last_used_at DATETIME NOT NULL,
        process_boot_id VARCHAR(64) NOT NULL,
        revision INTEGER NOT NULL DEFAULT 1,
        FOREIGN KEY(kami_bot_profile_id) REFERENCES bot_profiles(id),
        FOREIGN KEY(permission_group_id) REFERENCES memory_permission_groups(id)
    )""",
    """CREATE TABLE IF NOT EXISTS bot_control_audit (
        id INTEGER NOT NULL PRIMARY KEY,
        session_id VARCHAR(255) NOT NULL DEFAULT '',
        person_id VARCHAR(255) NOT NULL DEFAULT '',
        platform VARCHAR(100) NOT NULL DEFAULT '',
        command VARCHAR(64) NOT NULL,
        before_bot_profile_id VARCHAR(64) NOT NULL DEFAULT '',
        after_bot_profile_id VARCHAR(64) NOT NULL DEFAULT '',
        permission_group_id VARCHAR(64) NOT NULL DEFAULT '',
        result VARCHAR(16) NOT NULL,
        reason VARCHAR(100) NOT NULL DEFAULT '',
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at DATETIME NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS memory_access_audit (
        id INTEGER NOT NULL PRIMARY KEY,
        trace_id VARCHAR(64) NOT NULL,
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
    )""",
)

_INDEX_STATEMENTS = (
    "CREATE INDEX IF NOT EXISTS ix_kami_session_states_scope ON kami_session_states(session_id, person_id, status)",
    "CREATE INDEX IF NOT EXISTS ix_kami_session_states_permission_group_id ON kami_session_states(permission_group_id)",
    "CREATE INDEX IF NOT EXISTS ix_kami_session_states_expires_at ON kami_session_states(expires_at)",
    "CREATE INDEX IF NOT EXISTS ix_kami_session_states_process_boot_id ON kami_session_states(process_boot_id)",
    "CREATE INDEX IF NOT EXISTS ix_bot_control_audit_scope ON bot_control_audit(session_id, person_id, created_at)",
    "CREATE INDEX IF NOT EXISTS ix_bot_control_audit_command ON bot_control_audit(command, result)",
    "CREATE INDEX IF NOT EXISTS ix_memory_access_audit_trace_id ON memory_access_audit(trace_id)",
    "CREATE INDEX IF NOT EXISTS ix_memory_access_audit_scope ON memory_access_audit(session_id, person_id, access_mode)",
    "CREATE INDEX IF NOT EXISTS ix_memory_access_audit_workspace_id ON memory_access_audit(workspace_id)",
    "CREATE INDEX IF NOT EXISTS ix_memory_access_audit_created_at ON memory_access_audit(created_at)",
)

_MAI_MESSAGE_COLUMNS = (
    ("bot_profile_id", "VARCHAR(64)"),
    ("security_domain", "VARCHAR(16) NOT NULL DEFAULT 'normal'"),
    ("memory_space_id", "VARCHAR(64)"),
    ("conversation_partition_id", "VARCHAR(96)"),
    ("model_visible", "BOOLEAN NOT NULL DEFAULT 1"),
    ("memory_ingest_enabled", "BOOLEAN NOT NULL DEFAULT 1"),
)

_MAI_MESSAGE_INDEX_STATEMENTS = (
    "CREATE INDEX IF NOT EXISTS ix_mai_messages_bot_profile_id ON mai_messages(bot_profile_id)",
    "CREATE INDEX IF NOT EXISTS ix_mai_messages_security_domain ON mai_messages(security_domain)",
    "CREATE INDEX IF NOT EXISTS ix_mai_messages_memory_space_id ON mai_messages(memory_space_id)",
    "CREATE INDEX IF NOT EXISTS ix_mai_messages_conversation_partition_id ON mai_messages(conversation_partition_id)",
    "CREATE INDEX IF NOT EXISTS ix_mai_messages_model_visible ON mai_messages(model_visible)",
    "CREATE INDEX IF NOT EXISTS ix_mai_messages_memory_ingest_enabled ON mai_messages(memory_ingest_enabled)",
)


def migrate_v45_to_v46(context: MigrationExecutionContext) -> None:
    statements = _TABLE_STATEMENTS + _INDEX_STATEMENTS
    context.start_progress(
        total_tables=3,
        total_records=len(statements) + len(_KAMI_PARTITION_BASE) + len(_MAI_MESSAGE_COLUMNS) + 5,
        description="v45 -> v46 迁移进度",
    )
    connection = context.connection
    for index, statement in enumerate(statements):
        connection.exec_driver_sql(statement)
        context.advance_progress(records=1, completed_tables=1 if index < len(_TABLE_STATEMENTS) else 0)

    connection.exec_driver_sql(
        """INSERT OR IGNORE INTO memory_spaces
        (id,name,description,space_type,strict_isolation,enabled,policy_revision,created_at,updated_at)
        VALUES ('memory-space-kami','Kami 管理记忆库','仅供授权管理模式使用的独立安全域','kami',1,1,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
    )
    connection.exec_driver_sql(
        """UPDATE memory_spaces
        SET space_type='kami', strict_isolation=1, enabled=1, updated_at=CURRENT_TIMESTAMP
        WHERE id='memory-space-kami'"""
    )
    context.advance_progress(records=1)

    connection.exec_driver_sql(
        """INSERT OR IGNORE INTO persona_profiles
        (id,name,description,nickname,alias_names_json,personality,behavior_style,reply_style,group_chat_prompt,private_chat_prompt,multiple_reply_style,emotion_trait,created_at,updated_at)
        VALUES ('persona-profile-kami','Kami 管理人设','与普通聊天完全隔离的管理模式人设','Kami','[]','审慎、准确、严格保护隐私','以管理员安全审计视角工作，不泄漏受保护正文','简洁、明确地说明操作结果和权限边界','','','', '', CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
    )
    context.advance_progress(records=1)

    connection.exec_driver_sql(
        """INSERT OR IGNORE INTO bot_profiles
        (id,name,profile_type,parent_profile_id,persona_profile_id,home_memory_space_id,inherit_parent_persona,inherit_parent_tools,inherit_parent_plugins,enabled,is_system,policy_revision,created_at,updated_at)
        VALUES ('bot-profile-kami','Kami 管理 Bot','kami',NULL,'persona-profile-kami','memory-space-kami',0,0,0,1,1,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
    )
    connection.exec_driver_sql(
        """UPDATE bot_profiles
        SET profile_type='kami', parent_profile_id=NULL, persona_profile_id='persona-profile-kami',
            home_memory_space_id='memory-space-kami', inherit_parent_persona=0, inherit_parent_tools=0,
            inherit_parent_plugins=0, enabled=1, is_system=1, updated_at=CURRENT_TIMESTAMP
        WHERE id='bot-profile-kami'"""
    )
    context.advance_progress(records=1)

    for partition_type, partition_key, display_name in _KAMI_PARTITION_BASE:
        partition_id = build_partition_id(KAMI_MEMORY_SPACE_ID, partition_type, partition_key, KAMI_SECURITY_DOMAIN)
        connection.exec_driver_sql(
            """INSERT OR IGNORE INTO memory_partitions
            (id,memory_space_id,partition_type,partition_key,security_domain,display_name,enabled,policy_revision,created_at,updated_at)
            VALUES (?,?,?,?,?,?,1,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
            (partition_id, KAMI_MEMORY_SPACE_ID, partition_type, partition_key, KAMI_SECURITY_DOMAIN, display_name),
        )
        context.advance_progress(records=1)

    existing_columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(mai_messages)")}
    for column_name, column_ddl in _MAI_MESSAGE_COLUMNS:
        if column_name not in existing_columns:
            connection.exec_driver_sql(f"ALTER TABLE mai_messages ADD COLUMN {column_name} {column_ddl}")
        context.advance_progress(records=1)

    connection.exec_driver_sql(
        "UPDATE mai_messages SET security_domain='normal' WHERE security_domain IS NULL OR security_domain=''"
    )
    connection.exec_driver_sql("UPDATE mai_messages SET model_visible=1 WHERE model_visible IS NULL")
    connection.exec_driver_sql("UPDATE mai_messages SET memory_ingest_enabled=1 WHERE memory_ingest_enabled IS NULL")
    for statement in _MAI_MESSAGE_INDEX_STATEMENTS:
        connection.exec_driver_sql(statement)
    context.advance_progress(records=1)

    logger.info("v45 -> v46 数据库迁移完成：Kami 安全域与审计表已创建")
