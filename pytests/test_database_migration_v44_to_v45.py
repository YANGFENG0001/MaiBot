from sqlalchemy import create_engine

from src.common.database.migrations.models import MigrationExecutionContext
from src.common.database.migrations.v44_to_v45 import migrate_v44_to_v45


def test_v44_to_v45_is_idempotent_and_migrates_only_completed_acl_handshakes() -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE memory_spaces (id VARCHAR(64) PRIMARY KEY, space_type VARCHAR(32), enabled BOOLEAN)")
        connection.exec_driver_sql("INSERT INTO memory_spaces VALUES ('space-a','private',1),('space-b','private',1),('space-c','private',1)")
        connection.exec_driver_sql("CREATE TABLE bot_profiles (id VARCHAR(64) PRIMARY KEY)")
        connection.exec_driver_sql("INSERT INTO bot_profiles VALUES ('bot-a')")
        connection.exec_driver_sql("CREATE TABLE workspaces (id VARCHAR(64) PRIMARY KEY, memory_space_id VARCHAR(64), bot_profile_id VARCHAR(64))")
        connection.exec_driver_sql("INSERT INTO workspaces VALUES ('workspace-a','space-a','bot-a')")
        connection.exec_driver_sql("""CREATE TABLE memory_space_acl (id INTEGER PRIMARY KEY, owner_space_id VARCHAR(64), peer_space_id VARCHAR(64), can_read_from_peer BOOLEAN, expose_to_peer BOOLEAN, filters_json TEXT)""")
        connection.exec_driver_sql("INSERT INTO memory_space_acl VALUES (1,'space-a','space-b',1,0,'{}')")
        connection.exec_driver_sql("INSERT INTO memory_space_acl VALUES (2,'space-b','space-a',0,1,'{}')")
        connection.exec_driver_sql("INSERT INTO memory_space_acl VALUES (3,'space-a','space-c',1,0,'{}')")
        context = MigrationExecutionContext(
            connection=connection,
            current_version=44,
            target_version=45,
            step_index=1,
            step_name="v44_to_v45",
            total_steps=1,
        )
        migrate_v44_to_v45(context)
        migrate_v44_to_v45(context)

        columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(memory_spaces)")}
        assert "strict_isolation" in columns
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM bot_profile_memory_rules").scalar() == 1
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM memory_space_bot_rules").scalar() == 1
        assert connection.exec_driver_sql("SELECT target_space_id FROM bot_profile_memory_rules").scalar() == "space-b"
        assert connection.exec_driver_sql("SELECT memory_space_id FROM memory_space_bot_rules").scalar() == "space-b"
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM memory_permission_groups").scalar() == 0
