"""R06 / 请求上下文构造：每条消息独立生成不可变访问快照。"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import FrozenInstanceError

import pytest
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, SQLModel, create_engine

from src.common.database.database_model import BotProfile, MemorySpace, Workspace
from src.workspaces.request_context import get_current_request_context
from src.workspaces.service import WorkspaceService


def _service(monkeypatch):
    """把 WorkspaceService 的会话工厂替换为隔离的内存数据库。"""

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

    import importlib

    module = importlib.import_module("src.workspaces.service")
    monkeypatch.setattr(module, "get_db_session", db)

    with db() as session:
        session.add(MemorySpace(id="memory-space-public", name="公共记忆库", space_type="public"))
        session.flush()
        session.add(
            BotProfile(
                id="bot-profile-public",
                name="Public",
                profile_type="public",
                home_memory_space_id="memory-space-public",
            )
        )
        session.flush()
        session.add(
            Workspace(
                id="workspace-default",
                name="默认子系统",
                memory_space_id="memory-space-public",
                bot_profile_id="bot-profile-public",
                is_default=True,
            )
        )
    return WorkspaceService(), db


def test_request_context_snapshot_fields(monkeypatch) -> None:
    service, _ = _service(monkeypatch)
    context = service.build_bot_request_context("session-1", "person-1", "private")
    assert context.session_id == "session-1"
    assert context.person_id == "person-1"
    assert context.workspace_id == "workspace-default"
    assert context.active_bot_profile_id == "bot-profile-public"
    assert context.active_bot_profile_type == "public"
    assert context.access_mode == "normal"
    assert context.security_domain == "normal"
    assert context.audience_type == "private"
    assert context.home_memory_space_id == "memory-space-public"
    assert context.readable_space_ids == ("memory-space-public",)


def test_request_context_is_immutable(monkeypatch) -> None:
    service, _ = _service(monkeypatch)
    context = service.build_bot_request_context("session-1", "person-1", "private")
    with pytest.raises(FrozenInstanceError):
        context.person_id = "person-2"  # type: ignore[misc]


def test_invalid_audience_type_is_rejected(monkeypatch) -> None:
    service, _ = _service(monkeypatch)
    with pytest.raises(ValueError, match="audience_type"):
        service.build_bot_request_context("session-1", "person-1", "broadcast")


def test_r06_consecutive_messages_from_different_users_are_independent(monkeypatch) -> None:
    """R06：连续两条不同用户的消息各自生成上下文，不复用上一个发送者。"""

    service, _ = _service(monkeypatch)
    first = service.build_bot_request_context("session-1", "person-1", "private")
    second = service.build_bot_request_context("session-1", "person-2", "private")
    assert first.person_id == "person-1"
    assert second.person_id == "person-2"
    assert first.trace_id != second.trace_id
    assert first.readable_partition_ids != second.readable_partition_ids


def test_request_context_trace_id_is_unique(monkeypatch) -> None:
    service, _ = _service(monkeypatch)
    traces = {
        service.build_bot_request_context("session-1", "person-1", "private").trace_id for _ in range(5)
    }
    assert len(traces) == 5


def test_no_context_is_bound_by_default() -> None:
    assert get_current_request_context() is None
    with pytest.raises(RuntimeError, match="BotRequestContext"):
        get_current_request_context(required=True)
