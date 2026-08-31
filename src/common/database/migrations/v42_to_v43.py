"""v42 schema 升级到 v43：BotProfile 与普通会话路由。"""

from src.common.logger import get_logger

from .models import MigrationExecutionContext

logger = get_logger("database_migration")


def migrate_v42_to_v43(context: MigrationExecutionContext) -> None:
    statements = (
        """CREATE TABLE IF NOT EXISTS bot_profiles (id VARCHAR(64) NOT NULL PRIMARY KEY, name VARCHAR(100) NOT NULL UNIQUE, profile_type VARCHAR(16) NOT NULL, parent_profile_id VARCHAR(64), persona_profile_id VARCHAR(64), home_memory_space_id VARCHAR(64) NOT NULL, inherit_parent_persona BOOLEAN NOT NULL DEFAULT 1, inherit_parent_tools BOOLEAN NOT NULL DEFAULT 1, inherit_parent_plugins BOOLEAN NOT NULL DEFAULT 1, enabled BOOLEAN NOT NULL DEFAULT 1, is_system BOOLEAN NOT NULL DEFAULT 0, policy_revision INTEGER NOT NULL DEFAULT 1, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, FOREIGN KEY(parent_profile_id) REFERENCES bot_profiles(id), FOREIGN KEY(persona_profile_id) REFERENCES persona_profiles(id), FOREIGN KEY(home_memory_space_id) REFERENCES memory_spaces(id))""",
        """CREATE TABLE IF NOT EXISTS bot_profile_tool_policies (id INTEGER NOT NULL PRIMARY KEY, bot_profile_id VARCHAR(64) NOT NULL, component_name VARCHAR(255) NOT NULL, effect VARCHAR(16) NOT NULL, CONSTRAINT uq_bot_profile_tool_policy UNIQUE(bot_profile_id, component_name), FOREIGN KEY(bot_profile_id) REFERENCES bot_profiles(id))""",
        """CREATE TABLE IF NOT EXISTS bot_profile_plugin_policies (id INTEGER NOT NULL PRIMARY KEY, bot_profile_id VARCHAR(64) NOT NULL, plugin_id VARCHAR(255) NOT NULL, effect VARCHAR(16) NOT NULL DEFAULT 'inherit', overrides_json TEXT NOT NULL DEFAULT '{}', CONSTRAINT uq_bot_profile_plugin_policy UNIQUE(bot_profile_id, plugin_id), FOREIGN KEY(bot_profile_id) REFERENCES bot_profiles(id))""",
        """CREATE TABLE IF NOT EXISTS bot_route_states (session_id VARCHAR(255) NOT NULL PRIMARY KEY, active_bot_profile_id VARCHAR(64) NOT NULL, route_mode VARCHAR(16) NOT NULL DEFAULT 'group', changed_by_person_id VARCHAR(255) NOT NULL DEFAULT '', policy_revision INTEGER NOT NULL DEFAULT 1, updated_at DATETIME NOT NULL, FOREIGN KEY(active_bot_profile_id) REFERENCES bot_profiles(id))""",
    )
    context.start_progress(total_tables=4, total_records=12, description="v42 -> v43 迁移进度")
    for statement in statements:
        context.connection.exec_driver_sql(statement)
        context.advance_progress(records=1, completed_tables=1)
    columns = {row[1] for row in context.connection.exec_driver_sql("PRAGMA table_info(workspaces)")}
    if "bot_profile_id" not in columns:
        context.connection.exec_driver_sql("ALTER TABLE workspaces ADD COLUMN bot_profile_id VARCHAR(64)")
    context.advance_progress(records=1)
    context.connection.exec_driver_sql("""INSERT OR IGNORE INTO bot_profiles (id,name,profile_type,parent_profile_id,persona_profile_id,home_memory_space_id,inherit_parent_persona,inherit_parent_tools,inherit_parent_plugins,enabled,is_system,policy_revision,created_at,updated_at) VALUES ('bot-profile-public','公共 Bot','public',NULL,NULL,'memory-space-public',1,1,1,1,1,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""")
    context.connection.exec_driver_sql("UPDATE workspaces SET bot_profile_id='bot-profile-public' WHERE is_default=1 AND (bot_profile_id IS NULL OR bot_profile_id='')")
    context.connection.exec_driver_sql("""UPDATE bot_profiles SET persona_profile_id=(SELECT persona_profile_id FROM workspaces WHERE is_default=1 LIMIT 1), inherit_parent_tools=COALESCE((SELECT inherit_global_tools FROM workspaces WHERE is_default=1 LIMIT 1),1), inherit_parent_plugins=COALESCE((SELECT inherit_global_plugins FROM workspaces WHERE is_default=1 LIMIT 1),1) WHERE id='bot-profile-public'""")
    context.connection.exec_driver_sql("""INSERT OR IGNORE INTO bot_profiles (id,name,profile_type,parent_profile_id,persona_profile_id,home_memory_space_id,inherit_parent_persona,inherit_parent_tools,inherit_parent_plugins,enabled,is_system,policy_revision,created_at,updated_at) SELECT 'bot-profile-' || id, name || ' Bot', 'group', 'bot-profile-public', persona_profile_id, memory_space_id, 1, inherit_global_tools, inherit_global_plugins, enabled, 0, policy_revision, created_at, updated_at FROM workspaces WHERE is_default=0""")
    context.connection.exec_driver_sql("UPDATE workspaces SET bot_profile_id='bot-profile-' || id WHERE is_default=0 AND (bot_profile_id IS NULL OR bot_profile_id='')")
    context.connection.exec_driver_sql("""INSERT OR IGNORE INTO bot_profile_tool_policies (bot_profile_id,component_name,effect) SELECT w.bot_profile_id, CASE WHEN instr(p.tool_name,'.')>0 THEN p.tool_name ELSE 'legacy.' || p.tool_name END, p.effect FROM workspace_tool_policies p JOIN workspaces w ON w.id=p.workspace_id WHERE w.bot_profile_id IS NOT NULL""")
    context.connection.exec_driver_sql("""INSERT OR IGNORE INTO bot_profile_plugin_policies (bot_profile_id,plugin_id,effect,overrides_json) SELECT w.bot_profile_id,p.plugin_id,p.effect,p.overrides_json FROM workspace_plugin_policies p JOIN workspaces w ON w.id=p.workspace_id WHERE w.bot_profile_id IS NOT NULL""")
    context.advance_progress(records=7)
    logger.info("v42 -> v43 数据库迁移完成：BotProfile 与普通路由已创建")
