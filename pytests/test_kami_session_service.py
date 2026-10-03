"""Kami 会话管理端：状态列表、强制撤销与控制审计（C12/C14 的管理侧入口）。"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta

import json

import pytest
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, SQLModel, create_engine, select

from src.common.database.database_model import (
    BotControlAudit,
    BotProfile,
    KamiSessionState,
    MemoryPermissionGroup,
    MemorySpace,
)
from src.workspaces import kami_service as kami_service_module
from src.workspaces.kami_service import (
    COMMAND_KAMI_OFF,
    KAMI_BOT_PROFILE_ID,
    PROCESS_BOOT_ID,
    STATUS_ACTIVE,
    STATUS_REVOKED,
    KamiService,
)


@pytest.fixture
def kami(monkeypatch):
    """隔离数据库 + 复位进程级 boot 失效标记。"""

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)

    @contextmanager
    def db(auto_commit=True):
        session = factory()
        try:
            yield session
            if auto_commit:
                session.commit()
        finally:
            session.close()

    monkeypatch.setattr(kami_service_module, "get_db_session", db)
    monkeypatch.setattr(kami_service_module, "_boot_expired_for", None)

    # KamiSessionState 对 bot_profiles / memory_permission_groups 有外键约束。
    with db() as session:
        session.add(MemorySpace(id="memory-space-public", name="公共记忆库", space_type="public"))
        session.flush()
        session.add(
            BotProfile(
                id=KAMI_BOT_PROFILE_ID,
                name="Kami",
                profile_type="kami",
                home_memory_space_id="memory-space-public",
            )
        )
        session.add(
            BotProfile(
                id="bot-profile-public",
                name="Public",
                profile_type="public",
                home_memory_space_id="memory-space-public",
            )
        )
        session.flush()
        session.add(MemoryPermissionGroup(id="group-1", name="group-1"))
    return KamiService(), db


def _state(db, *, state_id: str, session_id: str, person_id: str, status: str = STATUS_ACTIVE, **overrides):
    now = datetime.now()
    values = dict(
        id=state_id,
        session_id=session_id,
        person_id=person_id,
        kami_bot_profile_id=KAMI_BOT_PROFILE_ID,
        activated_from_bot_profile_id="bot-profile-public",
        permission_group_id="group-1",
        status=status,
        activated_at=now,
        expires_at=now + timedelta(seconds=900),
        last_used_at=now,
        process_boot_id=PROCESS_BOOT_ID,
        revision=1,
    )
    values.update(overrides)
    with db() as session:
        session.add(KamiSessionState(**values))


def test_list_session_states_orders_and_filters(kami) -> None:
    service, db = kami
    _state(db, state_id="state-old", session_id="session-1", person_id="person-1")
    _state(
        db,
        state_id="state-new",
        session_id="session-2",
        person_id="person-2",
        activated_at=datetime.now() + timedelta(seconds=30),
    )
    _state(db, state_id="state-off", session_id="session-3", person_id="person-3", status=STATUS_REVOKED)

    everything = service.list_session_states()
    assert [item.id for item in everything][0] == "state-new"
    assert len(everything) == 3

    active_only = service.list_session_states(status_filter=STATUS_ACTIVE)
    assert {item.id for item in active_only} == {"state-old", "state-new"}


def test_list_session_states_respects_limit(kami) -> None:
    service, db = kami
    for index in range(5):
        _state(db, state_id=f"state-{index}", session_id=f"session-{index}", person_id=f"person-{index}")
    assert len(service.list_session_states(limit=2)) == 2


def test_revoke_marks_revoked_and_writes_control_audit(kami) -> None:
    service, db = kami
    _state(db, state_id="state-1", session_id="session-1", person_id="person-1")

    assert service.revoke_session_state("state-1", actor="webui-admin:abc") is True

    with db() as session:
        row = session.get(KamiSessionState, "state-1")
        audits = session.exec(select(BotControlAudit)).all()
    assert row.status == STATUS_REVOKED
    assert row.revision == 2
    assert len(audits) == 1
    assert audits[0].reason == "admin.revoke"
    assert audits[0].platform == "webui"
    assert audits[0].after_bot_profile_id == "bot-profile-public"


def test_revoke_is_idempotent_for_non_active_state(kami) -> None:
    service, db = kami
    _state(db, state_id="state-1", session_id="session-1", person_id="person-1", status=STATUS_REVOKED)
    assert service.revoke_session_state("state-1", actor="webui-admin:abc") is False
    with db() as session:
        assert session.exec(select(BotControlAudit)).all() == []


def test_revoke_unknown_state_raises(kami) -> None:
    service, _ = kami
    with pytest.raises(ValueError, match="Kami 会话不存在"):
        service.revoke_session_state("missing", actor="webui-admin:abc")


def test_revoke_audit_never_contains_message_body(kami) -> None:
    """A02：控制审计只记录枚举与 actor，不记录任何私密正文。"""

    service, db = kami
    _state(db, state_id="state-1", session_id="session-1", person_id="person-1")
    service.revoke_session_state("state-1", actor="webui-admin:abc")
    with db() as session:
        audit = session.exec(select(BotControlAudit)).one()
    # 审计只存枚举命令值，不存用户输入的命令原文。
    assert audit.command == COMMAND_KAMI_OFF
    assert "webui-admin:abc" in audit.metadata_json
    assert "state-1" in audit.metadata_json
    # metadata 只应包含 actor 与 state_id 两个键。
    assert set(json.loads(audit.metadata_json)) == {"actor", "state_id"}


def test_old_boot_active_sessions_are_expired_on_first_use(kami) -> None:
    """C12：进程重启后首次使用批量失效旧 boot 的活动会话。"""

    service, db = kami
    _state(db, state_id="state-old-boot", session_id="session-1", person_id="person-1", process_boot_id="old-boot")
    service.list_session_states()
    with db() as session:
        assert session.get(KamiSessionState, "state-old-boot").status == "expired"
