from contextlib import contextmanager

import importlib

from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, SQLModel, create_engine

from src.common.database.database_model import (
    BotProfile,
    BotProfilePluginPolicy,
    BotProfileToolPolicy,
    MemorySpace,
)
from src.plugin_runtime.request_scope import PluginRequestScope
from src.plugin_runtime.scope_resolver import PluginScopeResolver
from src.workspaces.bot_profile_service import BotProfileService


def _services(monkeypatch):
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

    scope_module = importlib.import_module("src.plugin_runtime.scope_resolver")
    profile_module = importlib.import_module("src.workspaces.bot_profile_service")
    monkeypatch.setattr(scope_module, "get_db_session", db)
    monkeypatch.setattr(profile_module, "get_db_session", db)
    return PluginScopeResolver(), BotProfileService(), db


def _scope(profile_id: str, profile_type: str = "group", revision: int = 1) -> PluginRequestScope:
    return PluginRequestScope(
        trace_id=f"trace-{profile_id}",
        session_id="session",
        workspace_id="workspace",
        bot_profile_id=profile_id,
        bot_profile_type=profile_type,
        permission_group_id="",
        access_mode="normal",
        security_domain="normal",
        memory_space_id="space",
        audience_type="private",
        policy_revision=revision,
    )


def _seed(db) -> None:
    with db() as session:
        session.add(MemorySpace(id="space", name="space"))
        session.flush()
        session.add(
            BotProfile(
                id="public",
                name="Public",
                profile_type="public",
                home_memory_space_id="space",
                inherit_parent_plugins=True,
                inherit_parent_tools=True,
            )
        )
        session.flush()
        session.add(
            BotProfile(
                id="group",
                name="Group",
                profile_type="group",
                parent_profile_id="public",
                home_memory_space_id="space",
            )
        )
        session.add(
            BotProfile(
                id="isolated",
                name="Isolated",
                profile_type="group",
                parent_profile_id="public",
                home_memory_space_id="space",
                inherit_parent_plugins=False,
                inherit_parent_tools=False,
            )
        )
        session.add(
            BotProfile(
                id="kami",
                name="Kami",
                profile_type="kami",
                home_memory_space_id="space",
                inherit_parent_plugins=False,
                inherit_parent_tools=False,
            )
        )


def test_ps01_public_inherits_globally_enabled_plugin(monkeypatch) -> None:
    resolver, _service, db = _services(monkeypatch)
    _seed(db)
    assert resolver.resolve_plugin_policy("demo", _scope("public", "public")).allowed is True


def test_ps02_ps03_group_inherits_allow_and_child_deny(monkeypatch) -> None:
    resolver, _service, db = _services(monkeypatch)
    _seed(db)
    with db() as session:
        session.add(BotProfilePluginPolicy(bot_profile_id="public", plugin_id="demo", effect="allow"))
    assert resolver.resolve_plugin_policy("demo", _scope("group")).allowed is True
    with db() as session:
        session.add(BotProfilePluginPolicy(bot_profile_id="group", plugin_id="demo", effect="deny"))
    assert resolver.resolve_plugin_policy("demo", _scope("group", revision=2)).allowed is False


def test_ps04_ps05_isolated_group_requires_explicit_allow(monkeypatch) -> None:
    resolver, _service, db = _services(monkeypatch)
    _seed(db)
    assert resolver.resolve_plugin_policy("demo", _scope("isolated")).allowed is False
    with db() as session:
        session.add(BotProfilePluginPolicy(bot_profile_id="isolated", plugin_id="demo", effect="allow"))
    assert resolver.resolve_plugin_policy("demo", _scope("isolated", revision=2)).allowed is True
    assert resolver.resolve_plugin_policy("other", _scope("isolated", revision=2)).allowed is False


def test_ps06_ps07_kami_defaults_deny_and_explicit_allow(monkeypatch) -> None:
    resolver, _service, db = _services(monkeypatch)
    _seed(db)
    assert resolver.resolve_plugin_policy("demo", _scope("kami", "kami")).allowed is False
    with db() as session:
        session.add(BotProfilePluginPolicy(bot_profile_id="kami", plugin_id="demo", effect="allow"))
    assert resolver.resolve_plugin_policy("demo", _scope("kami", "kami", 2)).allowed is True
    assert resolver.resolve_plugin_policy("danger", _scope("kami", "kami", 2)).allowed is False


def test_ps08_global_disabled_cannot_be_restored(monkeypatch) -> None:
    resolver, _service, db = _services(monkeypatch)
    _seed(db)
    with db() as session:
        session.add(BotProfilePluginPolicy(bot_profile_id="group", plugin_id="demo", effect="allow"))
    decision = resolver.resolve_plugin_policy("demo", _scope("group"), globally_enabled=False)
    assert decision.allowed is False
    assert decision.reason_code == "global_disabled"


def test_ps09_plugin_deny_precedes_tool_allow(monkeypatch) -> None:
    resolver, _service, db = _services(monkeypatch)
    _seed(db)
    with db() as session:
        session.add(BotProfilePluginPolicy(bot_profile_id="group", plugin_id="demo", effect="deny"))
        session.add(BotProfileToolPolicy(bot_profile_id="group", component_name="demo.reply", effect="allow"))
    decision = resolver.is_component_allowed("demo", "demo.reply", "TOOL", _scope("group"))
    assert decision.allowed is False
    assert decision.reason_code == "plugin_denied"


def test_ps10_policy_writes_increment_revision(monkeypatch) -> None:
    resolver, service, db = _services(monkeypatch)
    _seed(db)
    service.set_tool_policy("group", "demo.reply", "deny")
    service.set_plugin_policy("group", "demo", "allow")
    with db() as session:
        profile = session.get(BotProfile, "group")
        assert profile.policy_revision == 3
    assert resolver.resolve_plugin_policy("demo", _scope("group", revision=3)).policy_revision == 3
