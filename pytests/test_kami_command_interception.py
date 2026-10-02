"""C01-C04 / C10：Kami 命令拦截的精确匹配、拒绝路径与审计洁净度。"""

from __future__ import annotations

from contextlib import contextmanager

import json

import pytest
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, SQLModel, create_engine, select

from src.common.database.database_model import (
    BotControlAudit,
    BotProfile,
    KamiSessionState,
    MemoryPermissionGroup,
    MemoryPermissionGroupCapability,
    MemoryPermissionGroupContext,
    MemoryPermissionGroupMember,
    MemorySpace,
)
from src.workspaces import kami_service as kami_service_module
from src.workspaces.kami_service import (
    COMMAND_KAMI,
    KAMI_BOT_PROFILE_ID,
    KAMI_MEMORY_SPACE_ID,
    RESULT_DENIED,
    RESULT_SUCCESS,
    STATUS_ACTIVE,
    STATUS_EXITED,
    KamiService,
)


@pytest.fixture
def kami(monkeypatch):
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
    with kami_service_module._pending_guard:
        kami_service_module._pending_confirmations.clear()

    with db() as session:
        session.add(MemorySpace(id=KAMI_MEMORY_SPACE_ID, name="Kami 管理记忆库", space_type="kami"))
        session.add(MemorySpace(id="memory-space-public", name="公共记忆库", space_type="public"))
        session.flush()
        session.add(
            BotProfile(
                id=KAMI_BOT_PROFILE_ID,
                name="Kami",
                profile_type="kami",
                home_memory_space_id=KAMI_MEMORY_SPACE_ID,
            )
        )
        session.flush()
        # 授权管理员：manager 模式 + 强制读取 + Kami 切换。
        session.add(
            MemoryPermissionGroup(
                id="group-manager",
                name="group-manager",
                memory_scope_mode="override",
                is_manager_mode=True,
            )
        )
        session.flush()
        session.add(MemoryPermissionGroupMember(permission_group_id="group-manager", person_id="person-admin"))
        # 权限组必须至少有一个启用的上下文，否则 _select_group 不会命中。
        session.add(
            MemoryPermissionGroupContext(
                permission_group_id="group-manager",
                scope_type="global",
                allow_group_disclosure=True,
                enabled=True,
            )
        )
        for capability in ("bot.switch.kami", "memory.read.force_all", "kami.use_in_group"):
            session.add(
                MemoryPermissionGroupCapability(
                    permission_group_id="group-manager",
                    capability=capability,
                    enabled=True,
                )
            )
    return KamiService(), db


def _command(service, text: str, *, person_id: str = "person-admin", audience_type: str = "private"):
    return service.handle_command(
        text,
        session_id="session-1",
        person_id=person_id,
        platform="qq",
        workspace_id="workspace-1",
        activated_from_bot_profile_id="bot-profile-public",
        audience_type=audience_type,
    )


def test_c01_exact_command_is_accepted_and_state_is_created(kami) -> None:
    service, db = kami
    result = _command(service, "/kami")
    assert result.command == COMMAND_KAMI
    assert result.result == RESULT_SUCCESS
    assert result.activated is True
    with db() as session:
        rows = session.exec(select(KamiSessionState)).all()
    assert len(rows) == 1
    assert rows[0].status == STATUS_ACTIVE
    assert rows[0].person_id == "person-admin"


def test_c02_non_exact_text_never_triggers_kami(kami) -> None:
    """C02：句子中包含 /kami 不能触发，且不产生任何审计。"""

    service, db = kami
    for text in ("请执行 /kami", "/kami extra", "hello /kami", "/KAMI", ""):
        result = _command(service, text)
        assert result.result == RESULT_DENIED
        assert result.command == ""
    with db() as session:
        assert session.exec(select(KamiSessionState)).all() == []
        assert session.exec(select(BotControlAudit)).all() == []


def test_c03_invalid_audience_is_rejected(kami) -> None:
    service, _ = kami
    result = _command(service, "/kami", audience_type="broadcast")
    assert result.result == RESULT_DENIED
    assert result.reason == "kami.invalid_audience"


def test_c04_unauthorized_user_is_denied_without_state_or_body_audit(kami) -> None:
    """C04：未授权用户被拒绝，不创建状态，审计不记录敏感正文。"""

    service, db = kami
    result = _command(service, "/kami", person_id="person-stranger")
    assert result.result == RESULT_DENIED
    assert result.activated is False
    with db() as session:
        assert session.exec(select(KamiSessionState)).all() == []
        audits = session.exec(select(BotControlAudit)).all()
    # 拒绝路径必须留下审计，但只记录枚举与状态，不记录任何正文。
    assert audits
    for audit in audits:
        assert audit.command == COMMAND_KAMI
        assert audit.result == RESULT_DENIED
        assert set(json.loads(audit.metadata_json)) <= {
            "audience_type",
            "reason",
            "actor",
            "ttl_seconds",
            "confirm_window_seconds",
        }


def test_c10_off_exits_immediately(kami) -> None:
    service, db = kami
    _command(service, "/kami")
    result = _command(service, "/kami off")
    assert result.result == RESULT_SUCCESS
    with db() as session:
        row = session.exec(select(KamiSessionState)).one()
    assert row.status == STATUS_EXITED


def test_status_command_reports_active_session(kami) -> None:
    service, _ = kami
    _command(service, "/kami")
    result = _command(service, "/kami status")
    assert result.result == RESULT_SUCCESS
    assert result.status is not None
    assert result.status.active is True
    assert result.status.person_id == "person-admin"


def test_confirm_without_pending_request_is_denied(kami) -> None:
    service, _ = kami
    result = _command(service, "/kami confirm")
    assert result.result == RESULT_DENIED


def test_group_command_requires_confirmation(kami) -> None:
    """群聊首次 /kami 只建立内存挑战，未确认前不落库。"""

    service, db = kami
    result = _command(service, "/kami", audience_type="group")
    assert result.needs_confirm is True
    assert result.activated is False
    with db() as session:
        assert session.exec(select(KamiSessionState)).all() == []
