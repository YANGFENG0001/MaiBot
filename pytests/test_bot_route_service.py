"""R01-R05：会话级 Bot 路由状态与默认路由回退。"""

from __future__ import annotations

from contextlib import contextmanager

import importlib

import pytest
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, SQLModel, create_engine

from src.common.database.database_model import BotProfile, MemorySpace, Workspace
from src.workspaces.bot_profile_service import PUBLIC_BOT_PROFILE_ID, BotProfileService
from src.workspaces.service import WorkspaceService


@pytest.fixture
def routed(monkeypatch):
    """两个 Workspace + 公共 Workspace 的最小路由夹具。"""

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

    # 注意：src.workspaces 包内同名符号是单例实例，这里必须取模块本身。
    monkeypatch.setattr(importlib.import_module("src.workspaces.bot_profile_service"), "get_db_session", db)
    monkeypatch.setattr(importlib.import_module("src.workspaces.service"), "get_db_session", db)

    with db() as session:
        session.add(MemorySpace(id="memory-space-public", name="公共记忆库", space_type="public"))
        session.flush()
        session.add(
            BotProfile(
                id=PUBLIC_BOT_PROFILE_ID,
                name="Public",
                profile_type="public",
                home_memory_space_id="memory-space-public",
            )
        )
        for suffix in ("a", "b"):
            session.add(MemorySpace(id=f"memory-space-{suffix}", name=f"space-{suffix}"))
            session.flush()
            session.add(
                BotProfile(
                    id=f"bot-{suffix}",
                    name=f"Bot {suffix.upper()}",
                    profile_type="group",
                    parent_profile_id=PUBLIC_BOT_PROFILE_ID,
                    home_memory_space_id=f"memory-space-{suffix}",
                )
            )
            session.flush()
            session.add(
                Workspace(
                    id=f"workspace-{suffix}",
                    name=f"子系统 {suffix.upper()}",
                    memory_space_id=f"memory-space-{suffix}",
                    bot_profile_id=f"bot-{suffix}",
                )
            )
        session.add(
            Workspace(
                id="workspace-default",
                name="默认子系统",
                memory_space_id="memory-space-public",
                bot_profile_id=PUBLIC_BOT_PROFILE_ID,
                is_default=True,
            )
        )
    return BotProfileService(), WorkspaceService(), db


def _workspace(db, workspace_id: str) -> Workspace:
    with db() as session:
        return session.get(Workspace, workspace_id)


def test_r01_workspace_a_session_resolves_to_bot_a(routed) -> None:
    service, _, db = routed
    context = service.resolve_for_session("session-a", _workspace(db, "workspace-a"))
    assert context.profile_id == "bot-a"


def test_r02_workspace_b_session_resolves_to_bot_b(routed) -> None:
    service, _, db = routed
    context = service.resolve_for_session("session-b", _workspace(db, "workspace-b"))
    assert context.profile_id == "bot-b"


def test_r03_unassigned_session_falls_back_to_default_workspace(routed) -> None:
    """R03：未绑定子系统的会话回落到默认 Workspace 的 Public Bot。"""

    _, workspace_service, db = routed
    with db() as session:
        resolved = workspace_service._resolve_workspace(session, "session-unassigned")
    assert resolved.id == "workspace-default"
    assert resolved.bot_profile_id == PUBLIC_BOT_PROFILE_ID


def test_r04_route_switch_is_scoped_to_one_session(routed) -> None:
    """R04：切换只影响当前 session，其他会话保持原有路由。"""

    service, _, db = routed
    service.set_route_state("session-a", PUBLIC_BOT_PROFILE_ID, "public", "person-1")
    assert service.resolve_for_session("session-a", _workspace(db, "workspace-a")).profile_id == PUBLIC_BOT_PROFILE_ID
    assert service.resolve_for_session("session-b", _workspace(db, "workspace-b")).profile_id == "bot-b"


def test_r05_route_state_rejects_kami_and_invalid_mode(routed) -> None:
    """R05：普通路由不得激活 Kami，非法 route_mode 直接拒绝。"""

    service, _, db = routed
    with db() as session:
        session.add(
            BotProfile(
                id="bot-profile-kami",
                name="Kami",
                profile_type="kami",
                home_memory_space_id="memory-space-public",
            )
        )
    with pytest.raises(ValueError, match="Kami"):
        service.set_route_state("session-a", "bot-profile-kami", "public", "person-1")
    with pytest.raises(ValueError, match="route_mode"):
        service.set_route_state("session-a", "bot-a", "kami", "person-1")
    # 失败的切换不得留下路由状态。
    assert service.resolve_for_session("session-a", _workspace(db, "workspace-a")).profile_id == "bot-a"


def test_route_revision_increments_on_repeated_switch(routed) -> None:
    service, _, _ = routed
    first = service.set_route_state("session-a", PUBLIC_BOT_PROFILE_ID, "public", "person-1")
    second = service.set_route_state("session-a", "bot-a", "group", "person-2")
    assert first.session_id == second.session_id == "session-a"
    assert second.policy_revision > first.policy_revision
    assert second.changed_by_person_id == "person-2"
