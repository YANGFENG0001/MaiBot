"""v45 -> v46 数据库迁移测试：Kami 子系统数据层。"""

from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.engine import Connection
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, SQLModel, select

from src.common.database.database_model import (
    BotControlAudit,
    BotProfile,
    KamiSessionState,
    MemoryAccessAudit,
    MemoryPartition,
    MemorySpace,
    Messages,
    PersonaProfile,
)
from src.common.database.migrations.models import MigrationExecutionContext
from src.common.database.migrations.v45_to_v46 import (
    KAMI_BOT_PROFILE_ID,
    KAMI_MEMORY_SPACE_ID,
    KAMI_PERSONA_PROFILE_ID,
    migrate_v45_to_v46,
)


def _context(connection: Connection, current_version: int = 45) -> MigrationExecutionContext:
    return MigrationExecutionContext(
        connection=connection,
        current_version=current_version,
        target_version=46,
        step_index=1,
        step_name="v45_to_v46",
        total_steps=1,
    )


def _create_v45_schema(connection: Connection) -> None:
    """创建与 v45 对齐的最小结构，覆盖迁移与 ORM 读取所需列。"""
    connection.exec_driver_sql(
        """CREATE TABLE memory_spaces (
            id VARCHAR(64) NOT NULL PRIMARY KEY, name VARCHAR(100) NOT NULL UNIQUE,
            description TEXT NOT NULL DEFAULT '', space_type VARCHAR(32) NOT NULL DEFAULT 'private',
            strict_isolation BOOLEAN NOT NULL DEFAULT 0, enabled BOOLEAN NOT NULL DEFAULT 1,
            policy_revision INTEGER NOT NULL DEFAULT 1, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
        )"""
    )
    connection.exec_driver_sql(
        """CREATE TABLE persona_profiles (
            id VARCHAR(64) NOT NULL PRIMARY KEY, name VARCHAR(100) NOT NULL UNIQUE,
            description TEXT NOT NULL DEFAULT '', nickname VARCHAR(100) NOT NULL DEFAULT '',
            alias_names_json TEXT NOT NULL DEFAULT '[]', personality TEXT NOT NULL DEFAULT '',
            behavior_style TEXT NOT NULL DEFAULT '', reply_style TEXT NOT NULL DEFAULT '',
            group_chat_prompt TEXT NOT NULL DEFAULT '', private_chat_prompt TEXT NOT NULL DEFAULT '',
            multiple_reply_style TEXT NOT NULL DEFAULT '', emotion_trait TEXT NOT NULL DEFAULT '',
            created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
        )"""
    )
    connection.exec_driver_sql(
        """CREATE TABLE bot_profiles (
            id VARCHAR(64) NOT NULL PRIMARY KEY, name VARCHAR(100) NOT NULL UNIQUE,
            profile_type VARCHAR(16) NOT NULL, parent_profile_id VARCHAR(64),
            persona_profile_id VARCHAR(64), home_memory_space_id VARCHAR(64) NOT NULL,
            inherit_parent_persona BOOLEAN NOT NULL DEFAULT 1, inherit_parent_tools BOOLEAN NOT NULL DEFAULT 1,
            inherit_parent_plugins BOOLEAN NOT NULL DEFAULT 1, enabled BOOLEAN NOT NULL DEFAULT 1,
            is_system BOOLEAN NOT NULL DEFAULT 0, policy_revision INTEGER NOT NULL DEFAULT 1,
            created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
        )"""
    )
    connection.exec_driver_sql(
        """CREATE TABLE memory_partitions (
            id VARCHAR(96) NOT NULL PRIMARY KEY, memory_space_id VARCHAR(64) NOT NULL,
            partition_type VARCHAR(20) NOT NULL, partition_key VARCHAR(255) NOT NULL,
            security_domain VARCHAR(16) NOT NULL DEFAULT 'normal', display_name VARCHAR(255) NOT NULL DEFAULT '',
            enabled BOOLEAN NOT NULL DEFAULT 1, policy_revision INTEGER NOT NULL DEFAULT 1,
            created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
        )"""
    )
    connection.exec_driver_sql(
        """CREATE TABLE mai_messages (
            id INTEGER NOT NULL PRIMARY KEY, message_id VARCHAR(255), timestamp DATETIME,
            platform VARCHAR(100), user_id VARCHAR(255), user_nickname VARCHAR(255),
            user_cardname VARCHAR(255), group_id VARCHAR(255), group_name VARCHAR(255),
            is_mentioned BOOLEAN, is_at BOOLEAN, session_id VARCHAR(255), reply_to VARCHAR(255),
            is_emoji BOOLEAN, is_picture BOOLEAN, is_command BOOLEAN, is_notify BOOLEAN,
            raw_content BLOB, processed_plain_text TEXT, additional_config TEXT, reply_frequency FLOAT
        )"""
    )
    connection.exec_driver_sql(
        """INSERT INTO memory_spaces VALUES
            ('memory-space-public','公共记忆库','兼容默认公共空间','public',0,1,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
    )
    connection.exec_driver_sql(
        """INSERT INTO bot_profiles VALUES
            ('bot-profile-public','公共 Bot','public',NULL,NULL,'memory-space-public',1,1,1,1,1,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""
    )
    connection.exec_driver_sql(
        """INSERT INTO mai_messages
            (id,message_id,timestamp,platform,user_id,user_nickname,session_id,raw_content)
            VALUES (1,'msg-1',CURRENT_TIMESTAMP,'qq','user-1','用户','session-1',X'00')"""
    )


def test_v45_to_v46_is_idempotent_and_backfills_legacy_messages() -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        _create_v45_schema(connection)
        migrate_v45_to_v46(_context(connection))
        migrate_v45_to_v46(_context(connection))

        # 系统实体幂等：只存在一份
        assert (
            connection.exec_driver_sql("SELECT COUNT(*) FROM memory_spaces WHERE id='memory-space-kami'").scalar() == 1
        )
        assert (
            connection.exec_driver_sql("SELECT COUNT(*) FROM persona_profiles WHERE id='persona-profile-kami'").scalar()
            == 1
        )
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM bot_profiles WHERE id='bot-profile-kami'").scalar() == 1

        # 新表全部存在
        tables = {row[0] for row in connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"kami_session_states", "bot_control_audit", "memory_access_audit"} <= tables

        # Kami 分区基础：shared/person/conversation 且 security_domain=kami
        partitions = connection.exec_driver_sql(
            "SELECT partition_type, partition_key, security_domain FROM memory_partitions WHERE memory_space_id='memory-space-kami' ORDER BY partition_type"
        ).fetchall()
        assert partitions == [
            ("conversation", "conversation-kami", "kami"),
            ("person", "person-kami", "kami"),
            ("shared", "shared", "kami"),
        ]

        # mai_messages 新增列
        columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(mai_messages)")}
        for expected in (
            "bot_profile_id",
            "security_domain",
            "memory_space_id",
            "conversation_partition_id",
            "model_visible",
            "memory_ingest_enabled",
        ):
            assert expected in columns

        # 旧数据回填：bot_profile_id/memory_space_id/conversation_partition_id 为空，normal/1/1
        row = connection.exec_driver_sql(
            "SELECT bot_profile_id,security_domain,memory_space_id,conversation_partition_id,model_visible,memory_ingest_enabled FROM mai_messages WHERE id=1"
        ).fetchone()
        assert row == (None, "normal", None, None, 1, 1)

        # 新增索引
        indexes = {row[1] for row in connection.exec_driver_sql("PRAGMA index_list('mai_messages')").fetchall()}
        for expected in (
            "ix_mai_messages_bot_profile_id",
            "ix_mai_messages_security_domain",
            "ix_mai_messages_memory_space_id",
            "ix_mai_messages_conversation_partition_id",
        ):
            assert expected in indexes


def test_v45_to_v46_kami_entities_are_fully_independent() -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        _create_v45_schema(connection)
        migrate_v45_to_v46(_context(connection))
        migrate_v45_to_v46(_context(connection))

        space = connection.exec_driver_sql(
            "SELECT space_type,strict_isolation,enabled FROM memory_spaces WHERE id='memory-space-kami'"
        ).fetchone()
        assert space == ("kami", 1, 1)

        bot = connection.exec_driver_sql(
            """SELECT profile_type,parent_profile_id,persona_profile_id,home_memory_space_id,
            inherit_parent_persona,inherit_parent_tools,inherit_parent_plugins,is_system,enabled
            FROM bot_profiles WHERE id='bot-profile-kami'"""
        ).fetchone()
        assert bot == (
            "kami",
            None,
            "persona-profile-kami",
            "memory-space-kami",
            0,
            0,
            0,
            1,
            1,
        )

        persona = connection.exec_driver_sql(
            "SELECT name, nickname FROM persona_profiles WHERE id='persona-profile-kami'"
        ).fetchone()
        assert persona == ("Kami 管理人设", "Kami")


def test_v45_to_v46_orm_models_align_with_create_all() -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with engine.begin() as connection:
        tables = {row[0] for row in connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"kami_session_states", "bot_control_audit", "memory_access_audit"} <= tables
        columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(mai_messages)")}
        assert {
            "bot_profile_id",
            "security_domain",
            "memory_space_id",
            "conversation_partition_id",
            "model_visible",
            "memory_ingest_enabled",
        } <= columns
        # 在已是最新结构的空库上重复执行迁移也必须安全幂等
        migrate_v45_to_v46(_context(connection, current_version=46))
        migrate_v45_to_v46(_context(connection, current_version=46))

    # ORM 对象属性默认值
    now = datetime.now()
    message = Messages(
        message_id="msg-1",
        timestamp=now,
        platform="qq",
        user_id="user-1",
        user_nickname="用户",
        session_id="session-1",
        raw_content=b"",
    )
    assert message.security_domain == "normal"
    assert message.model_visible is True
    assert message.memory_ingest_enabled is True
    assert message.bot_profile_id is None
    assert message.memory_space_id is None
    assert message.conversation_partition_id is None

    state = KamiSessionState(
        id="state-1",
        session_id="session-1",
        person_id="person-1",
        kami_bot_profile_id="bot-profile-kami",
        permission_group_id="group-admin",
        expires_at=now,
        process_boot_id="boot-1",
    )
    assert state.activated_from_bot_profile_id == ""
    assert state.status == "active"
    assert state.revision == 1

    control = BotControlAudit(command="kami", result="success")
    assert control.platform == ""
    assert control.metadata_json == "{}"

    audit = MemoryAccessAudit(trace_id="trace-1")
    assert audit.access_mode == "normal"
    assert audit.query_hash == ""
    assert audit.requested_scope_json == "{}"
    assert audit.allowed_scope_json == "{}"
    assert audit.denied_scope_json == "{}"
    assert audit.result_count == 0
    assert audit.latency_ms == 0


def test_v45_to_v46_orm_reads_migrated_kami_entities() -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    with engine.begin() as connection:
        _create_v45_schema(connection)
        migrate_v45_to_v46(_context(connection))
        migrate_v45_to_v46(_context(connection))
        factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)

    with factory() as session:
        space = session.get(MemorySpace, KAMI_MEMORY_SPACE_ID)
        assert space is not None
        assert space.space_type == "kami"
        assert space.strict_isolation is True

        persona = session.get(PersonaProfile, KAMI_PERSONA_PROFILE_ID)
        assert persona is not None
        assert persona.name == "Kami 管理人设"

        bot = session.get(BotProfile, KAMI_BOT_PROFILE_ID)
        assert bot is not None
        assert bot.profile_type == "kami"
        assert bot.is_system is True
        assert bot.parent_profile_id is None
        assert bot.persona_profile_id == KAMI_PERSONA_PROFILE_ID
        assert bot.home_memory_space_id == KAMI_MEMORY_SPACE_ID
        assert bot.inherit_parent_persona is False
        assert bot.inherit_parent_tools is False
        assert bot.inherit_parent_plugins is False

        partitions = session.exec(
            select(MemoryPartition).where(MemoryPartition.memory_space_id == KAMI_MEMORY_SPACE_ID)
        ).all()
        assert {item.partition_type for item in partitions} == {"shared", "person", "conversation"}
        assert all(item.security_domain == "kami" for item in partitions)

        legacy = session.get(Messages, 1)
        assert legacy is not None
        assert legacy.security_domain == "normal"
        assert legacy.model_visible is True
        assert legacy.memory_ingest_enabled is True
        assert legacy.bot_profile_id is None
        assert legacy.memory_space_id is None
        assert legacy.conversation_partition_id is None
