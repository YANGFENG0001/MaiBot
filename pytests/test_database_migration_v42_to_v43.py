from sqlalchemy import create_engine

from src.common.database.migrations.models import MigrationExecutionContext
from src.common.database.migrations.v42_to_v43 import migrate_v42_to_v43


def test_v42_to_v43_creates_profiles_and_backfills_idempotently() -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE memory_spaces (id VARCHAR(64) PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE persona_profiles (id VARCHAR(64) PRIMARY KEY)")
        connection.exec_driver_sql("INSERT INTO memory_spaces(id) VALUES ('memory-space-public'), ('space-a')")
        connection.exec_driver_sql("""CREATE TABLE workspaces (id VARCHAR(64) PRIMARY KEY, name VARCHAR(100), memory_space_id VARCHAR(64), persona_profile_id VARCHAR(64), is_default BOOLEAN, enabled BOOLEAN, inherit_global_tools BOOLEAN, inherit_global_plugins BOOLEAN, policy_revision INTEGER, created_at DATETIME, updated_at DATETIME)""")
        connection.exec_driver_sql("CREATE TABLE workspace_tool_policies (id INTEGER PRIMARY KEY, workspace_id VARCHAR(64), tool_name VARCHAR(255), effect VARCHAR(16))")
        connection.exec_driver_sql("CREATE TABLE workspace_plugin_policies (id INTEGER PRIMARY KEY, workspace_id VARCHAR(64), plugin_id VARCHAR(255), effect VARCHAR(16), overrides_json TEXT)")
        connection.exec_driver_sql("INSERT INTO workspaces VALUES ('workspace-default','默认','memory-space-public',NULL,1,1,1,1,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
        connection.exec_driver_sql("INSERT INTO workspaces VALUES ('workspace-a','A','space-a',NULL,0,1,1,1,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
        connection.exec_driver_sql("INSERT INTO workspace_tool_policies VALUES (1,'workspace-a','reply','allow')")
        context = MigrationExecutionContext(connection=connection,current_version=42,target_version=43,step_index=1,step_name="v42_to_v43",total_steps=1)
        migrate_v42_to_v43(context)
        migrate_v42_to_v43(context)
        profiles = connection.exec_driver_sql("SELECT id, profile_type, parent_profile_id FROM bot_profiles ORDER BY id").fetchall()
        assert profiles == [('bot-profile-public','public',None), ('bot-profile-workspace-a','group','bot-profile-public')]
        assert connection.exec_driver_sql("SELECT bot_profile_id FROM workspaces WHERE id='workspace-a'").scalar() == 'bot-profile-workspace-a'
        assert connection.exec_driver_sql("SELECT component_name FROM bot_profile_tool_policies").scalar() == 'legacy.reply'
