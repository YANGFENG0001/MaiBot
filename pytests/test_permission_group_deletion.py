"""权限组删除必须连同全部子资源一起清理，且不能被外键约束挡住。

回归背景：``permission_group_service.delete_group`` 早期用 ORM 的
``session.delete()`` 逐个删除子行再删父行。这些模型没有配置 ``relationship()``，
SQLAlchemy 无法推导父子落库顺序，实测会在同一事务里先发父表 DELETE，
直接命中 ``FOREIGN KEY constraint failed``，导致 WebUI 删除权限组报 500。
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, SQLModel, create_engine, select

from workspace_scope_test_support import (
    PUBLIC_SPACE_ID,
    allow_rule,
    ensure_profile,
    ensure_space,
    permission_group,
)

from src.common.database.database_model import (
    KamiSessionState,
    MemoryPermissionGroup,
    MemoryPermissionGroupCapability,
    MemoryPermissionGroupContext,
    MemoryPermissionGroupMember,
    MemoryPermissionRule,
    PermissionGroupBotRule,
)
from src.workspaces import permission_group_service as service_module
from src.workspaces.permission_group_service import PermissionGroupService

_GROUP_ID = "perm-group-regression"


def _database_with_foreign_keys():
    """建库并强制打开外键约束。

    生产库由运行期打开 ``PRAGMA foreign_keys``；共享夹具用的是内存库，默认不校验
    外键，旧实现的外键缺陷在那里不会暴露，因此这里显式打开以对齐生产行为。
    """

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


def _seed(factory) -> None:
    """建一个带齐全部子资源（成员/上下文/能力/规则/Bot 规则）的权限组。"""

    with factory() as session:
        ensure_space(session, PUBLIC_SPACE_ID, space_type="public")
        ensure_profile(session, "bot-profile-public", home_space_id=PUBLIC_SPACE_ID, profile_type="public")
        permission_group(
            session,
            _GROUP_ID,
            persons=("person-1",),
            memory_scope_mode="override",
            capabilities=("memory.read.cross_space",),
        )
        allow_rule(session, _GROUP_ID, space_selector="all_normal", priority=100)
        session.add(
            PermissionGroupBotRule(
                permission_group_id=_GROUP_ID,
                bot_profile_id="bot-profile-public",
                can_read=True,
            )
        )
        session.commit()


@pytest.fixture
def service(monkeypatch):
    factory = _database_with_foreign_keys()

    @contextmanager
    def get_session():
        """与生产一致：退出上下文时提交。"""

        session = factory()
        try:
            yield session
            session.commit()
        finally:
            session.close()

    monkeypatch.setattr(service_module, "get_db_session", get_session)
    return PermissionGroupService(), factory


def test_delete_group_with_children_does_not_hit_foreign_key(service) -> None:
    group_service, factory = service
    _seed(factory)

    assert group_service.delete_group(_GROUP_ID) is True

    with factory() as session:
        assert session.exec(select(MemoryPermissionGroup)).all() == []
        for model in (
            MemoryPermissionGroupMember,
            MemoryPermissionGroupContext,
            MemoryPermissionGroupCapability,
            MemoryPermissionRule,
            PermissionGroupBotRule,
        ):
            assert session.exec(select(model)).all() == [], model.__name__


def test_delete_group_returns_false_for_missing_group(service) -> None:
    group_service, _ = service

    assert group_service.delete_group("perm-group-missing") is False


def test_delete_group_also_terminates_referencing_kami_sessions(service) -> None:
    """权限组是 Kami 的授权来源；删组时必须一并清掉引用它的 Kami 会话。

    kami_session_states.permission_group_id 是非空外键，若只删权限组会直接
    外键失败，WebUI 删除权限组会报 500。
    """

    from datetime import datetime, timedelta

    from workspace_scope_test_support import ensure_profile

    group_service, factory = service
    _seed(factory)

    with factory() as session:
        ensure_profile(session, "bot-profile-kami", home_space_id=PUBLIC_SPACE_ID, profile_type="kami")
        session.add(
            KamiSessionState(
                id="kami-state-1",
                session_id="session-1",
                person_id="person-1",
                kami_bot_profile_id="bot-profile-kami",
                activated_from_bot_profile_id="bot-profile-public",
                permission_group_id=_GROUP_ID,
                status="active",
                activated_at=datetime.now(),
                expires_at=datetime.now() + timedelta(seconds=900),
                last_used_at=datetime.now(),
                process_boot_id="boot-1",
            )
        )
        session.commit()

    assert group_service.delete_group(_GROUP_ID) is True

    with factory() as session:
        assert session.exec(select(KamiSessionState)).all() == []
        assert session.exec(select(MemoryPermissionGroup)).all() == []
