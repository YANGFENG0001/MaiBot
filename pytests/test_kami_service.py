"""Phase 5A Kami 会话服务测试。

database_model.py 尚未落地 KamiSessionState/BotControlAudit/MemoryAccessAudit，
本测试按 phase_5A.md 的字段定义占位模型并注入 database_model 模块，
使 kami_service 可以按最终模型契约运行；模型正式落地后占位定义自动跳过。
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Optional
from uuid import uuid4

import json
import threading

from sqlalchemy import Column, DateTime, Integer, Text, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import Field, Session, SQLModel, select

import pytest

import src.common.database.database_model as db_model

# ---------------------------------------------------------------------------
# Phase 5A 模型契约（按 phase_5A.md 字段）
#
# 注意：并发实现已在 database_model.py 以另一套结构定义了同名表
# （KamiSessionState 为按 session 的路由状态，审计表使用 action/details_json）。
# 本任务被限定只修改 kami_service.py 与测试，且不得回退他人编辑；
# 因此这里按 phase_5A.md 定义隔离占位模型（表名带 _phase5a 后缀避免与并发
# 实现冲突），并注入 database_model 模块供服务与测试绑定。若 database_model
# 日后真正对齐 phase_5A.md 字段，占位定义会自动跳过并直接使用正式模型。
# ---------------------------------------------------------------------------
_PHASE5A_SESSION_FIELDS = (
    "session_id",
    "person_id",
    "kami_bot_profile_id",
    "activated_from_bot_profile_id",
    "permission_group_id",
    "status",
    "activated_at",
    "expires_at",
    "last_used_at",
    "process_boot_id",
    "revision",
)
_PHASE5A_CONTROL_FIELDS = (
    "session_id",
    "person_id",
    "platform",
    "command",
    "before_bot_profile_id",
    "after_bot_profile_id",
    "permission_group_id",
    "result",
    "reason",
    "metadata_json",
)
_PHASE5A_ACCESS_FIELDS = (
    "trace_id",
    "session_id",
    "person_id",
    "workspace_id",
    "active_bot_profile_id",
    "permission_group_id",
    "access_mode",
    "query_hash",
    "requested_scope_json",
    "allowed_scope_json",
    "denied_scope_json",
    "result_count",
    "latency_ms",
)


def _model_has_fields(model, fields: tuple[str, ...]) -> bool:
    return all(hasattr(model, name) for name in fields)


_NEED_SESSION_MODEL = not (
    hasattr(db_model, "KamiSessionState") and _model_has_fields(db_model.KamiSessionState, _PHASE5A_SESSION_FIELDS)
)
_NEED_CONTROL_MODEL = not (
    hasattr(db_model, "BotControlAudit") and _model_has_fields(db_model.BotControlAudit, _PHASE5A_CONTROL_FIELDS)
)
_NEED_ACCESS_MODEL = not (
    hasattr(db_model, "MemoryAccessAudit") and _model_has_fields(db_model.MemoryAccessAudit, _PHASE5A_ACCESS_FIELDS)
)

if _NEED_SESSION_MODEL:

    class KamiSessionState(SQLModel, table=True):
        __tablename__ = "kami_session_states_phase5a"  # type: ignore

        id: str = Field(primary_key=True, max_length=64)
        session_id: str = Field(index=True, max_length=255)
        person_id: str = Field(index=True, max_length=255)
        kami_bot_profile_id: str = Field(index=True, max_length=64)
        activated_from_bot_profile_id: str = Field(default="", index=True, max_length=64)
        permission_group_id: str = Field(default="", index=True, max_length=64)
        status: str = Field(default="active", index=True, max_length=16)
        activated_at: datetime = Field(default_factory=datetime.now, sa_column=Column(DateTime, nullable=False))
        expires_at: datetime = Field(sa_column=Column(DateTime, nullable=False))
        last_used_at: datetime = Field(default_factory=datetime.now, sa_column=Column(DateTime, nullable=False))
        process_boot_id: str = Field(index=True, max_length=64)
        revision: int = Field(default=1, sa_column=Column(Integer, nullable=False, server_default="1"))


if _NEED_CONTROL_MODEL:

    class BotControlAudit(SQLModel, table=True):
        __tablename__ = "bot_control_audit_phase5a"  # type: ignore

        id: Optional[int] = Field(default=None, primary_key=True)
        session_id: str = Field(index=True, max_length=255)
        person_id: str = Field(index=True, max_length=255)
        platform: str = Field(default="", index=True, max_length=100)
        command: str = Field(index=True, max_length=32)
        before_bot_profile_id: str = Field(default="", max_length=64)
        after_bot_profile_id: str = Field(default="", max_length=64)
        permission_group_id: str = Field(default="", index=True, max_length=64)
        result: str = Field(default="success", index=True, max_length=16)
        reason: str = Field(default="", max_length=100)
        metadata_json: str = Field(default="{}", sa_column=Column(Text, nullable=False, server_default="{}"))
        created_at: datetime = Field(
            default_factory=datetime.now, sa_column=Column(DateTime, index=True, nullable=False)
        )


if _NEED_ACCESS_MODEL:

    class MemoryAccessAudit(SQLModel, table=True):
        __tablename__ = "memory_access_audit_phase5a"  # type: ignore

        id: Optional[int] = Field(default=None, primary_key=True)
        trace_id: str = Field(index=True, max_length=64)
        session_id: str = Field(index=True, max_length=255)
        person_id: str = Field(index=True, max_length=255)
        workspace_id: str = Field(default="", index=True, max_length=64)
        active_bot_profile_id: str = Field(default="", max_length=64)
        permission_group_id: str = Field(default="", index=True, max_length=64)
        access_mode: str = Field(default="normal", index=True, max_length=16)
        query_hash: str = Field(index=True, max_length=64)
        requested_scope_json: str = Field(default="{}", sa_column=Column(Text, nullable=False, server_default="{}"))
        allowed_scope_json: str = Field(default="{}", sa_column=Column(Text, nullable=False, server_default="{}"))
        denied_scope_json: str = Field(default="{}", sa_column=Column(Text, nullable=False, server_default="{}"))
        result_count: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default="0"))
        latency_ms: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default="0"))
        created_at: datetime = Field(
            default_factory=datetime.now, sa_column=Column(DateTime, index=True, nullable=False)
        )


if _NEED_SESSION_MODEL:
    db_model.KamiSessionState = KamiSessionState
if _NEED_CONTROL_MODEL:
    db_model.BotControlAudit = BotControlAudit
if _NEED_ACCESS_MODEL:
    db_model.MemoryAccessAudit = MemoryAccessAudit

# ---------------------------------------------------------------------------
# 服务与测试工具
# ---------------------------------------------------------------------------
from src.common.database.database_model import (  # noqa: E402
    BotControlAudit,
    BotProfile,
    KamiSessionState,
    MemoryAccessAudit,
    MemoryPermissionGroup,
    MemoryPermissionGroupCapability,
    MemoryPermissionGroupContext,
    MemoryPermissionGroupMember,
    MemorySpace,
)
from src.workspaces.kami_service import KamiService  # noqa: E402

T0 = datetime(2026, 9, 1, 0, 0, 0)


def _seed_base(db) -> None:
    now = datetime.now()
    with db() as session:
        session.add(
            MemorySpace(
                id="memory-space-kami",
                name="Kami 记忆库",
                space_type="kami",
                strict_isolation=True,
                enabled=True,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            MemorySpace(
                id="memory-space-public",
                name="公共记忆库",
                space_type="public",
                enabled=True,
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()
        session.add(
            BotProfile(
                id="bot-profile-public",
                name="公共 Bot",
                profile_type="public",
                home_memory_space_id="memory-space-public",
                enabled=True,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            BotProfile(
                id="bot-group",
                name="分组 Bot",
                profile_type="group",
                parent_profile_id="bot-profile-public",
                home_memory_space_id="memory-space-public",
                enabled=True,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            BotProfile(
                id="bot-profile-kami",
                name="Kami Bot",
                profile_type="kami",
                parent_profile_id=None,
                home_memory_space_id="memory-space-kami",
                enabled=True,
                created_at=now,
                updated_at=now,
            )
        )


def _seed_group(
    db,
    *,
    group_id: str = "group-admin",
    person_id: str = "person-1",
    is_manager: bool = True,
    enabled: bool = True,
    capabilities: tuple[str, ...] = ("bot.switch.kami", "memory.read.force_all"),
    scope_type: str = "global",
    context_session_id: Optional[str] = None,
    context_channel_type: Optional[str] = None,
) -> None:
    now = datetime.now()
    with db() as session:
        session.add(
            MemoryPermissionGroup(
                id=group_id,
                name=group_id,
                description="",
                enabled=enabled,
                priority=10,
                memory_scope_mode="override",
                is_manager_mode=is_manager,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(MemoryPermissionGroupMember(permission_group_id=group_id, person_id=person_id))
        session.add(
            MemoryPermissionGroupContext(
                permission_group_id=group_id,
                scope_type=scope_type,
                session_id=context_session_id,
                channel_type=context_channel_type,
            )
        )
        for capability in capabilities:
            session.add(
                MemoryPermissionGroupCapability(
                    permission_group_id=group_id,
                    capability=capability,
                )
            )


@pytest.fixture
def kami(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)

    @contextmanager
    def db(auto_commit: bool = True):
        session = factory()
        try:
            yield session
            if auto_commit:
                session.commit()
        finally:
            session.close()

    import src.workspaces.kami_service as module

    monkeypatch.setattr(module, "get_db_session", db)
    monkeypatch.setattr(module, "_pending_confirmations", {})
    monkeypatch.setattr(module, "_boot_expired_for", None)
    return KamiService(), db


def _cmd(service, text: str, *, now: datetime = T0, audience_type: str = "private", **overrides):
    params = {
        "session_id": "session-1",
        "person_id": "person-1",
        "platform": "qq",
        "workspace_id": "workspace-1",
        "activated_from_bot_profile_id": "bot-group",
        "audience_type": audience_type,
        "now": now,
    }
    params.update(overrides)
    return service.handle_command(text, **params)


def _activate(service, *, now: datetime = T0, **overrides):
    params = {
        "session_id": "session-1",
        "person_id": "person-1",
        "platform": "qq",
        "workspace_id": "workspace-1",
        "activated_from_bot_profile_id": "bot-group",
        "audience_type": "private",
        "now": now,
    }
    params.update(overrides)
    return service.activate(**params)


# ---------------------------------------------------------------------------
# 未授权
# ---------------------------------------------------------------------------
def test_non_manager_private_is_rejected_and_audited(kami) -> None:
    service, db = kami
    _seed_base(db)
    _seed_group(db, is_manager=False)
    result = _cmd(service, "/kami")
    assert result.result == "denied"
    assert result.reason == "kami.denied"
    assert result.activated is False
    with db() as session:
        assert session.exec(select(KamiSessionState)).all() == []
        audit = session.exec(select(BotControlAudit)).one()
        assert audit.command == "kami"
        assert audit.result == "denied"
        assert audit.reason == "kami.denied"
        assert "正文" not in audit.metadata_json


def test_non_member_is_rejected(kami) -> None:
    service, db = kami
    _seed_base(db)
    _seed_group(db, person_id="person-other")
    result = _cmd(service, "/kami")
    assert result.result == "denied"
    assert result.activated is False


def test_missing_force_all_capability_is_rejected(kami) -> None:
    service, db = kami
    _seed_base(db)
    _seed_group(db, capabilities=("bot.switch.kami",))
    result = _cmd(service, "/kami")
    assert result.result == "denied"


def test_disabled_kami_profile_is_rejected(kami) -> None:
    service, db = kami
    _seed_base(db)
    _seed_group(db)
    with db() as session:
        profile = session.get(BotProfile, "bot-profile-kami")
        assert profile is not None
        profile.enabled = False
        session.add(profile)
    result = _cmd(service, "/kami")
    assert result.result == "denied"
    assert result.reason == "kami.denied"


def test_invalid_audience_rejected(kami) -> None:
    service, db = kami
    _seed_base(db)
    _seed_group(db)
    result = _cmd(service, "/kami", audience_type="unknown")
    assert result.result == "denied"
    assert result.reason == "kami.invalid_audience"


# ---------------------------------------------------------------------------
# 私聊激活、TTL
# ---------------------------------------------------------------------------
def test_admin_private_chat_activates_and_status(kami) -> None:
    service, db = kami
    _seed_base(db)
    _seed_group(db)
    result = _cmd(service, "/kami", now=T0)
    assert result.result == "success"
    assert result.activated is True
    assert result.active is not None
    assert result.active.permission_group_id == "group-admin"
    assert result.active.expires_at == T0 + timedelta(seconds=900)

    status = service.status(
        session_id="session-1",
        person_id="person-1",
        workspace_id="workspace-1",
        audience_type="private",
        platform="qq",
        now=T0 + timedelta(seconds=100),
    )
    assert status.active is True
    assert status.remaining_seconds == 800

    status_cmd = _cmd(service, "/kami status", now=T0 + timedelta(seconds=100))
    assert status_cmd.result == "success"
    assert status_cmd.status is not None
    assert status_cmd.status.active is True

    again = _cmd(service, "/kami", now=T0 + timedelta(seconds=120))
    assert again.result == "success"
    assert again.reason == "kami.already_active"


def test_ttl_default_and_clamp(kami) -> None:
    service, db = kami
    _seed_base(db)
    _seed_group(db)
    r_default = _activate(service, ttl_seconds=None, now=T0)
    assert (r_default.active.expires_at - T0).total_seconds() == 900
    r_low = _activate(service, ttl_seconds=30, now=T0)
    assert (r_low.active.expires_at - T0).total_seconds() == 60
    r_high = _activate(service, ttl_seconds=100000, now=T0)
    assert (r_high.active.expires_at - T0).total_seconds() == 86400
    r_mid = _activate(service, ttl_seconds=600, now=T0)
    assert (r_mid.active.expires_at - T0).total_seconds() == 600
    with db() as session:
        actives = session.exec(select(KamiSessionState).where(KamiSessionState.status == "active")).all()
        assert len(actives) == 1


def test_ttl_expiry_marks_expired(kami) -> None:
    service, db = kami
    _seed_base(db)
    _seed_group(db)
    _activate(service, ttl_seconds=60, now=T0)
    active = service.resolve_active(
        session_id="session-1",
        person_id="person-1",
        workspace_id="workspace-1",
        audience_type="private",
        now=T0 + timedelta(seconds=59),
    )
    assert active is not None
    assert (
        service.resolve_active(
            session_id="session-1",
            person_id="person-1",
            workspace_id="workspace-1",
            audience_type="private",
            now=T0 + timedelta(seconds=61),
        )
        is None
    )
    with db() as session:
        row = session.exec(select(KamiSessionState)).one()
        assert row.status == "expired"
        assert row.revision >= 2


def test_resolve_active_touches_last_used(kami) -> None:
    service, db = kami
    _seed_base(db)
    _seed_group(db)
    _activate(service, now=T0)
    later = T0 + timedelta(seconds=30)
    active = service.resolve_active(
        session_id="session-1",
        person_id="person-1",
        workspace_id="workspace-1",
        audience_type="private",
        now=later,
    )
    assert active is not None
    assert active.last_used_at == later


# ---------------------------------------------------------------------------
# 重启
# ---------------------------------------------------------------------------
def test_restart_batch_expires_all_old_actives(kami, monkeypatch) -> None:
    service, db = kami
    _seed_base(db)
    _seed_group(db)
    _seed_group(db, group_id="group-admin-2", person_id="person-2")
    _activate(service, session_id="session-1", person_id="person-1", now=T0)
    _activate(service, session_id="session-2", person_id="person-2", now=T0)

    import src.workspaces.kami_service as module

    monkeypatch.setattr(module, "PROCESS_BOOT_ID", uuid4().hex)
    monkeypatch.setattr(module, "_boot_expired_for", None)

    assert (
        service.resolve_active(
            session_id="session-1",
            person_id="person-1",
            workspace_id="workspace-1",
            audience_type="private",
            now=T0 + timedelta(seconds=1),
        )
        is None
    )
    with db() as session:
        rows = session.exec(select(KamiSessionState)).all()
        assert len(rows) == 2
        assert {row.status for row in rows} == {"expired"}
        assert all(row.process_boot_id != module.PROCESS_BOOT_ID for row in rows)


# ---------------------------------------------------------------------------
# 撤权
# ---------------------------------------------------------------------------
def test_group_disabled_revokes_on_next_message(kami) -> None:
    service, db = kami
    _seed_base(db)
    _seed_group(db)
    _activate(service, now=T0)
    with db() as session:
        group = session.get(MemoryPermissionGroup, "group-admin")
        assert group is not None
        group.enabled = False
        session.add(group)
    assert (
        service.resolve_active(
            session_id="session-1",
            person_id="person-1",
            workspace_id="workspace-1",
            audience_type="private",
            now=T0 + timedelta(seconds=1),
        )
        is None
    )
    with db() as session:
        row = session.exec(select(KamiSessionState)).one()
        assert row.status == "revoked"


def test_membership_removal_revokes_on_next_message(kami) -> None:
    service, db = kami
    _seed_base(db)
    _seed_group(db)
    _activate(service, now=T0)
    with db() as session:
        member = session.exec(select(MemoryPermissionGroupMember)).one()
        session.delete(member)
    assert (
        service.resolve_active(
            session_id="session-1",
            person_id="person-1",
            workspace_id="workspace-1",
            audience_type="private",
            now=T0 + timedelta(seconds=1),
        )
        is None
    )
    with db() as session:
        row = session.exec(select(KamiSessionState)).one()
        assert row.status == "revoked"


def test_capability_removal_revokes_on_next_message(kami) -> None:
    service, db = kami
    _seed_base(db)
    _seed_group(db)
    _activate(service, now=T0)
    with db() as session:
        cap = session.exec(
            select(MemoryPermissionGroupCapability).where(
                MemoryPermissionGroupCapability.capability == "bot.switch.kami"
            )
        ).one()
        cap.enabled = False
        session.add(cap)
    assert (
        service.resolve_active(
            session_id="session-1",
            person_id="person-1",
            workspace_id="workspace-1",
            audience_type="private",
            now=T0 + timedelta(seconds=1),
        )
        is None
    )
    with db() as session:
        row = session.exec(select(KamiSessionState)).one()
        assert row.status == "revoked"


def test_context_mismatch_revokes_on_next_message(kami) -> None:
    service, db = kami
    _seed_base(db)
    _seed_group(db, scope_type="session", context_session_id="session-1")
    _activate(service, now=T0)
    with db() as session:
        context = session.exec(select(MemoryPermissionGroupContext)).one()
        context.enabled = False
        session.add(context)
    assert (
        service.resolve_active(
            session_id="session-1",
            person_id="person-1",
            workspace_id="workspace-1",
            audience_type="private",
            now=T0 + timedelta(seconds=1),
        )
        is None
    )
    with db() as session:
        row = session.exec(select(KamiSessionState)).one()
        assert row.status == "revoked"


def test_kami_profile_disabled_revokes_on_next_message(kami) -> None:
    service, db = kami
    _seed_base(db)
    _seed_group(db)
    _activate(service, now=T0)
    with db() as session:
        profile = session.get(BotProfile, "bot-profile-kami")
        assert profile is not None
        profile.enabled = False
        session.add(profile)
    assert (
        service.resolve_active(
            session_id="session-1",
            person_id="person-1",
            workspace_id="workspace-1",
            audience_type="private",
            now=T0 + timedelta(seconds=1),
        )
        is None
    )
    with db() as session:
        row = session.exec(select(KamiSessionState)).one()
        assert row.status == "revoked"


# ---------------------------------------------------------------------------
# 群聊
# ---------------------------------------------------------------------------
def test_group_chat_without_use_in_group_is_rejected(kami) -> None:
    service, db = kami
    _seed_base(db)
    _seed_group(db)
    result = _cmd(service, "/kami", audience_type="group")
    assert result.result == "denied"
    assert result.needs_confirm is False
    with db() as session:
        assert session.exec(select(KamiSessionState)).all() == []


def test_group_chat_requires_confirm_within_window_and_not_persisted(kami) -> None:
    service, db = kami
    _seed_base(db)
    _seed_group(db, capabilities=("bot.switch.kami", "memory.read.force_all", "kami.use_in_group"))
    first = _cmd(service, "/kami", audience_type="group", now=T0)
    assert first.result == "denied"
    assert first.reason == "kami.group_confirm_required"
    assert first.needs_confirm is True
    # 确认挑战只保存在内存，不落库
    with db() as session:
        assert session.exec(select(KamiSessionState)).all() == []

    # 超过 30 秒窗口后 confirm 失效
    expired = _cmd(
        service,
        "/kami confirm",
        audience_type="group",
        now=T0 + timedelta(seconds=31),
    )
    assert expired.result == "denied"
    assert expired.reason == "kami.confirm_expired"

    # 重新发起后，窗口内 confirm 成功激活
    again = _cmd(service, "/kami", audience_type="group", now=T0 + timedelta(seconds=40))
    assert again.needs_confirm is True
    ok = _cmd(service, "/kami confirm", audience_type="group", now=T0 + timedelta(seconds=45))
    assert ok.result == "success"
    assert ok.activated is True
    with db() as session:
        row = session.exec(select(KamiSessionState)).one()
        assert row.status == "active"
        assert row.process_boot_id != ""


def test_confirm_without_prior_request_is_rejected(kami) -> None:
    service, db = kami
    _seed_base(db)
    _seed_group(db, capabilities=("bot.switch.kami", "memory.read.force_all", "kami.use_in_group"))
    result = _cmd(service, "/kami confirm", audience_type="group", now=T0)
    assert result.result == "denied"
    assert result.reason == "kami.confirm_expired"
    with db() as session:
        assert session.exec(select(KamiSessionState)).all() == []


# ---------------------------------------------------------------------------
# off
# ---------------------------------------------------------------------------
def test_off_exits_and_records_before_after_profiles(kami) -> None:
    service, db = kami
    _seed_base(db)
    _seed_group(db)
    _activate(service, now=T0)
    off = _cmd(service, "/kami off", now=T0 + timedelta(seconds=10))
    assert off.result == "success"
    assert off.reason == "kami.exited"
    with db() as session:
        row = session.exec(select(KamiSessionState)).one()
        assert row.status == "exited"
        audit = session.exec(select(BotControlAudit).where(BotControlAudit.command == "kami_off")).one()
        assert audit.before_bot_profile_id == "bot-profile-kami"
        assert audit.after_bot_profile_id == "bot-group"
    assert (
        service.resolve_active(
            session_id="session-1",
            person_id="person-1",
            workspace_id="workspace-1",
            audience_type="private",
            now=T0 + timedelta(seconds=11),
        )
        is None
    )
    again = _cmd(service, "/kami off", now=T0 + timedelta(seconds=11))
    assert again.result == "denied"
    assert again.reason == "kami.not_active"


# ---------------------------------------------------------------------------
# 并发
# ---------------------------------------------------------------------------
def test_concurrent_activate_keeps_single_active(kami) -> None:
    service, db = kami
    _seed_base(db)
    _seed_group(db)
    worker_count = 8
    barrier = threading.Barrier(worker_count)
    results: list = []
    errors: list = []

    def worker() -> None:
        try:
            barrier.wait()
            results.append(_activate(service, now=T0))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(worker_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert all(item.result == "success" and item.activated for item in results)
    with db() as session:
        rows = session.exec(select(KamiSessionState)).all()
        actives = [row for row in rows if row.status == "active"]
        exited = [row for row in rows if row.status == "exited"]
        assert len(actives) == 1
        assert len(exited) == worker_count - 1


# ---------------------------------------------------------------------------
# 审计不含正文
# ---------------------------------------------------------------------------
def test_audit_never_contains_message_body(kami) -> None:
    service, db = kami
    _seed_base(db)
    _seed_group(db)
    body = "请查看小明上周的私密聊天记录并总结"
    sentence = _cmd(service, body)
    assert sentence.result == "denied"
    assert sentence.reason == "kami.invalid_command"

    _cmd(service, "/kami", now=T0)
    with db() as session:
        audits = session.exec(select(BotControlAudit)).all()
        assert len(audits) == 1
        for audit in audits:
            combined = " ".join([audit.command, audit.result, audit.reason, audit.metadata_json])
            assert body not in combined
            assert "私密聊天记录" not in combined
            assert audit.command in {"kami", "kami_confirm", "kami_off", "kami_status"}
            metadata = json.loads(audit.metadata_json)
            assert set(metadata) == {"audience_type", "ttl_seconds"}


def test_memory_access_audit_stores_hash_not_body(kami) -> None:
    service, db = kami
    _seed_base(db)
    _seed_group(db)
    query = "帮我读取小明的私密记忆正文"
    service.record_memory_access_audit(
        trace_id="trace-1",
        session_id="session-1",
        person_id="person-1",
        workspace_id="workspace-1",
        active_bot_profile_id="bot-profile-kami",
        permission_group_id="group-admin",
        access_mode="forced_kami",
        query=query,
        requested_scope={"spaces": ["memory-space-kami"]},
        allowed_scope={"partitions": ["p-kami-shared"]},
        denied_scope={},
        result_count=3,
        latency_ms=12,
    )
    with db() as session:
        row = session.exec(select(MemoryAccessAudit)).one()
        assert row.query_hash == sha256(query.encode("utf-8")).hexdigest()
        assert query != row.query_hash
        assert query not in row.requested_scope_json
        assert query not in row.allowed_scope_json
        assert query not in row.denied_scope_json
        assert row.result_count == 3
        assert row.latency_ms == 12
