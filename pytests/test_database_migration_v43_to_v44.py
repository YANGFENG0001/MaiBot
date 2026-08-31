from sqlalchemy import create_engine

from src.common.database.migrations.models import MigrationExecutionContext
from src.common.database.migrations.v43_to_v44 import migrate_v43_to_v44


def test_v43_to_v44_backfills_partitions_idempotently() -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE memory_spaces (id VARCHAR(64) PRIMARY KEY, space_type VARCHAR(32))")
        connection.exec_driver_sql("INSERT INTO memory_spaces VALUES ('space-a','private'),('memory-space-kami','kami')")
        connection.exec_driver_sql("""CREATE TABLE memory_object_spaces (id INTEGER PRIMARY KEY, object_type VARCHAR(32), object_id VARCHAR(255), memory_space_id VARCHAR(64), source_session_id VARCHAR(255), origin_space_id VARCHAR(64), created_at DATETIME, UNIQUE(object_type,object_id,memory_space_id))""")
        connection.exec_driver_sql("INSERT INTO memory_object_spaces VALUES (1,'person_profile','person-1','space-a','chat-a',NULL,CURRENT_TIMESTAMP)")
        connection.exec_driver_sql("INSERT INTO memory_object_spaces VALUES (2,'memory','summary-1','space-a','chat-a',NULL,CURRENT_TIMESTAMP)")
        connection.exec_driver_sql("INSERT INTO memory_object_spaces VALUES (3,'memory','import-1','memory-space-kami','',NULL,CURRENT_TIMESTAMP)")
        context = MigrationExecutionContext(connection=connection,current_version=43,target_version=44,step_index=1,step_name="v43_to_v44",total_steps=1)
        migrate_v43_to_v44(context)
        migrate_v43_to_v44(context)
        rows = connection.exec_driver_sql("SELECT partition_type,partition_key,security_domain FROM memory_partitions ORDER BY memory_space_id,partition_type,partition_key").fetchall()
        assert ('person','person-1','normal') in rows
        assert ('conversation','chat-a','normal') in rows
        assert ('shared','shared','kami') in rows
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM memory_object_partitions").scalar() == 3
        indexes = {row[1] for row in connection.exec_driver_sql("PRAGMA index_list('memory_object_spaces')").fetchall()}
        assert any('sqlite_autoindex_memory_object_spaces' in item for item in indexes)
