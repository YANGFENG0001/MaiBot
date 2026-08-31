from contextlib import contextmanager

from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, SQLModel, create_engine

from src.common.database.database_model import ChatSession
from src.workspaces.service import WorkspaceService


def _service_with_memory_database(monkeypatch):
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

    monkeypatch.setattr("src.workspaces.service.get_db_session", fake_get_db_session)
    return WorkspaceService(), fake_get_db_session


def test_memory_scope_isolates_workspaces_and_opens_only_after_acl_handshake(monkeypatch) -> None:
    service, get_session = _service_with_memory_database(monkeypatch)
    service.ensure_defaults()
    with get_session() as session:
        session.add_all([
            ChatSession(session_id="chat-a", platform="qq", user_id="1"),
            ChatSession(session_id="chat-b", platform="qq", user_id="2"),
        ])

    workspace_a = service.create_workspace(name="A 子系统", memory_mode="private")
    workspace_b = service.create_workspace(name="B 子系统", memory_mode="private")
    service.assign_sessions(workspace_a.id, ["chat-a"])
    service.assign_sessions(workspace_b.id, ["chat-b"])

    isolated = service.resolve_memory_scope("chat-a")
    assert isolated.primary_space_id == workspace_a.memory_space_id
    assert isolated.readable_space_ids == (workspace_a.memory_space_id,)
    assert isolated.shared_session_ids == ("chat-a",)

    service.set_memory_space_acl(
        workspace_a.memory_space_id,
        workspace_b.memory_space_id,
        can_read_from_peer=True,
        expose_to_peer=False,
    )
    assert service.resolve_memory_scope("chat-a").readable_space_ids == (workspace_a.memory_space_id,)

    service.set_memory_space_acl(
        workspace_b.memory_space_id,
        workspace_a.memory_space_id,
        can_read_from_peer=False,
        expose_to_peer=True,
    )
    opened = service.resolve_memory_scope("chat-a")
    assert opened.readable_space_ids == (workspace_a.memory_space_id, workspace_b.memory_space_id)
    assert set(opened.shared_session_ids) == {"chat-a", "chat-b"}


def test_unassigned_chat_falls_back_to_public_space_and_object_membership_is_idempotent(monkeypatch) -> None:
    service, get_session = _service_with_memory_database(monkeypatch)
    service.ensure_defaults()
    with get_session() as session:
        session.add(ChatSession(session_id="legacy-chat", platform="qq", user_id="3"))

    scope = service.resolve_memory_scope("legacy-chat")
    assert scope.primary_space_id == "memory-space-public"
    assert scope.readable_space_ids == ("memory-space-public",)

    assert service.register_memory_objects(
        object_type="memory",
        object_ids=["paragraph-1", "paragraph-1"],
        memory_space_id=scope.primary_space_id,
        source_session_id="legacy-chat",
    ) == 1
    assert service.register_memory_objects(
        object_type="memory",
        object_ids=["paragraph-1"],
        memory_space_id=scope.primary_space_id,
        source_session_id="legacy-chat",
    ) == 0
    assert service.memory_object_space_ids("memory", ["paragraph-1"]) == {
        "paragraph-1": {"memory-space-public"}
    }
