from sqlalchemy import create_engine

from src.common.database.migrations.models import MigrationExecutionContext
from src.common.database.migrations.v41_to_v42 import migrate_v41_to_v42


def test_v41_to_v42_creates_memory_object_membership_tables_idempotently() -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE memory_spaces (id VARCHAR(64) PRIMARY KEY)")
        context = MigrationExecutionContext(
            connection=connection,
            current_version=41,
            target_version=42,
            step_index=1,
            step_name="v41_to_v42",
            total_steps=1,
        )

        migrate_v41_to_v42(context)
        migrate_v41_to_v42(context)

        tables = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"memory_object_spaces", "memory_space_migration_states"}.issubset(tables)
        indexes = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        assert "ix_memory_object_spaces_memory_space_id" in indexes
