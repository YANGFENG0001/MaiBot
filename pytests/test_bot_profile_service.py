from contextlib import contextmanager

import pytest
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, SQLModel, create_engine

from src.common.database.database_model import BotProfile, BotProfileToolPolicy, MemorySpace, Workspace
from src.workspaces.bot_profile_service import BotProfileService


def _service(monkeypatch):
    engine=create_engine("sqlite://", connect_args={"check_same_thread":False})
    SQLModel.metadata.create_all(engine)
    factory=sessionmaker(bind=engine,class_=Session,expire_on_commit=False)
    @contextmanager
    def db(auto_commit=True):
        session=factory()
        try:
            yield session
            if auto_commit:
                session.commit()
        finally:
            session.close()
    import importlib
    module = importlib.import_module("src.workspaces.bot_profile_service")
    monkeypatch.setattr(module, "get_db_session", db)
    return BotProfileService(),db


def test_parent_cycle_and_short_tool_name_are_rejected(monkeypatch) -> None:
    service,db=_service(monkeypatch)
    with db() as session:
        session.add(MemorySpace(id="space",name="space"))
        session.flush()
        session.add(BotProfile(id="a",name="A",profile_type="group",home_memory_space_id="space"))
        session.flush()
        session.add(BotProfile(id="b",name="B",profile_type="group",parent_profile_id="a",home_memory_space_id="space"))
    with pytest.raises(ValueError,match="循环"):
        service.set_parent("a","b")
    with pytest.raises(ValueError,match="完整名"):
        service.set_tool_policy("a","reply","allow")
    policy=service.set_tool_policy("a","builtin.reply","allow")
    assert policy.component_name == "builtin.reply"


def test_inheritance_and_route_state(monkeypatch) -> None:
    service, db = _service(monkeypatch)
    with db() as session:
        session.add(MemorySpace(id="space-public", name="public"))
        session.add(MemorySpace(id="space-group", name="group"))
        session.flush()
        session.add(BotProfile(id="public", name="Public", profile_type="public", home_memory_space_id="space-public"))
        session.flush()
        session.add(BotProfile(id="group", name="Group", profile_type="group", parent_profile_id="public", home_memory_space_id="space-group"))
        session.add(BotProfileToolPolicy(bot_profile_id="public", component_name="core.reply", effect="allow"))
        session.add(BotProfileToolPolicy(bot_profile_id="group", component_name="core.reply", effect="deny"))
        session.add(Workspace(id="workspace", name="Workspace", memory_space_id="space-group", bot_profile_id="group"))
    assert [profile.id for profile in service.get_lineage("group")] == ["public", "group"]
    assert service.resolve_tool_policies("group") == {"core.reply": "deny"}
    state = service.set_route_state("session-1", "public", "public", "person-1")
    assert state.active_bot_profile_id == "public"
    with db() as session:
        workspace = session.get(Workspace, "workspace")
    assert service.resolve_for_session("session-1", workspace).profile_id == "public"
