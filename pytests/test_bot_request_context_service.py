from contextlib import contextmanager

import importlib
import pytest
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, SQLModel, create_engine

from src.common.database.database_model import BotProfile, BotRouteState, ChatSession
from src.workspaces.bot_profile_service import BotProfileService
from src.workspaces.request_context import bind_request_context
from src.workspaces.service import WorkspaceService


def _services(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)

    @contextmanager
    def fake_get_db_session(auto_commit: bool = True):
        session = factory()
        try:
            yield session
            if auto_commit:
                session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    workspace_module = importlib.import_module("src.workspaces.service")
    profile_module = importlib.import_module("src.workspaces.bot_profile_service")
    monkeypatch.setattr(workspace_module, "get_db_session", fake_get_db_session)
    monkeypatch.setattr(profile_module, "get_db_session", fake_get_db_session)
    return WorkspaceService(), BotProfileService(), fake_get_db_session


def test_request_context_uses_message_snapshot_after_route_changes(monkeypatch) -> None:
    workspace_service, profile_service, get_session = _services(monkeypatch)
    workspace_service.ensure_defaults()
    with get_session() as session:
        session.add(ChatSession(session_id="chat-a", platform="qq", user_id="1"))
    workspace = workspace_service.create_workspace(name="A 子系统", memory_mode="private")
    workspace_service.assign_sessions(workspace.id, ["chat-a"])

    group_request = workspace_service.build_bot_request_context("chat-a", "person-a", "private")
    assert group_request.active_bot_profile_type == "group"
    assert group_request.home_memory_space_id == workspace.memory_space_id

    profile_service.set_route_state("chat-a", "bot-profile-public", "public", "person-a")
    public_request = workspace_service.build_bot_request_context("chat-a", "person-a", "private")
    assert public_request.active_bot_profile_id == "bot-profile-public"
    assert public_request.home_memory_space_id == "memory-space-public"

    stale_but_valid_context = workspace_service.resolve_context_for_request(group_request)
    assert stale_but_valid_context.bot_profile.profile_id == group_request.active_bot_profile_id
    assert stale_but_valid_context.memory_space_id == workspace.memory_space_id

    with bind_request_context(group_request):
        memory_scope = workspace_service.resolve_memory_scope("chat-a")
    assert memory_scope.primary_space_id == workspace.memory_space_id
    assert memory_scope.readable_space_ids == group_request.readable_space_ids


def test_normal_request_rejects_kami_route(monkeypatch) -> None:
    workspace_service, _, get_session = _services(monkeypatch)
    workspace_service.ensure_defaults()
    with get_session() as session:
        session.add(ChatSession(session_id="chat-kami", platform="qq", user_id="2"))
        session.add(
            BotProfile(
                id="bot-profile-kami-test",
                name="Kami Test",
                profile_type="kami",
                home_memory_space_id="memory-space-public",
                enabled=True,
            )
        )
        session.add(
            BotRouteState(
                session_id="chat-kami",
                active_bot_profile_id="bot-profile-kami-test",
                route_mode="specific",
                changed_by_person_id="person-2",
            )
        )

    with pytest.raises(ValueError, match="不能进入 Kami"):
        workspace_service.build_bot_request_context("chat-kami", "person-2", "private")
