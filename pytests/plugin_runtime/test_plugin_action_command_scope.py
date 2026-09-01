from contextlib import contextmanager
from types import SimpleNamespace

import asyncio
import importlib
import re

import pytest
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, SQLModel, create_engine

from src.common.database.database_model import (
    BotProfile,
    BotProfilePluginPolicy,
    BotProfileToolPolicy,
    MemorySpace,
)
from src.core.tooling import ToolExecutionContext, ToolInvocation
from src.plugin_runtime.component_query import ComponentQueryService
from src.plugin_runtime.host.component_registry import ComponentRegistry
from src.plugin_runtime.protocol.envelope import Envelope, MessageType
from src.workspaces.request_context import BotRequestContext, bind_request_context


def _db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)

    @contextmanager
    def get_session(auto_commit=True):
        session = factory()
        try:
            yield session
            if auto_commit:
                session.commit()
        finally:
            session.close()

    scope_module = importlib.import_module("src.plugin_runtime.scope_resolver")
    monkeypatch.setattr(scope_module, "get_db_session", get_session)
    with get_session() as session:
        session.add(MemorySpace(id="space", name="space"))
        session.flush()
        session.add(
            BotProfile(
                id="profile",
                name="Profile",
                profile_type="group",
                home_memory_space_id="space",
                inherit_parent_plugins=False,
                inherit_parent_tools=False,
            )
        )
    return get_session


def _request_context(
    revision: int = 1,
    *,
    profile_id: str = "profile",
    profile_type: str = "group",
    access_mode: str = "normal",
) -> BotRequestContext:
    return BotRequestContext(
        trace_id=f"trace-{revision}",
        session_id="session",
        workspace_id="workspace",
        person_id="person",
        active_bot_profile_id=profile_id,
        active_bot_profile_type=profile_type,
        permission_group_id="",
        access_mode=access_mode,
        security_domain="normal",
        home_memory_space_id="space",
        readable_space_ids=("space",),
        readable_partition_ids=(),
        writable_partition_ids=(),
        audience_type="private",
        policy_revision=revision,
    )


class _Supervisor:
    def __init__(self) -> None:
        self.component_registry = ComponentRegistry()
        self._registered_plugins = {}
        self.rpc_calls = 0

    def register(self, plugin_id: str, name: str, component_type: str, metadata: dict) -> None:
        self.component_registry.register_component(name, component_type, plugin_id, metadata)
        self._registered_plugins[plugin_id] = SimpleNamespace(config_schema={}, normalized_config={})

    async def invoke_plugin(self, method, plugin_id, component_name, args=None, timeout_ms=30000, **kwargs):
        del method, plugin_id, component_name, args, timeout_ms, kwargs
        self.rpc_calls += 1
        return Envelope(
            request_id=1,
            message_type=MessageType.RESPONSE,
            payload={"success": True, "result": "ok"},
        )


def _service(supervisors, monkeypatch) -> ComponentQueryService:
    service = ComponentQueryService()
    monkeypatch.setattr(service, "_get_runtime_manager", lambda: SimpleNamespace(supervisors=supervisors))
    return service


@pytest.mark.asyncio
async def test_tl05_tool_filter_happens_before_duplicate_short_name_and_rpc_gate(monkeypatch) -> None:
    db = _db(monkeypatch)
    denied = _Supervisor()
    allowed = _Supervisor()
    denied.register("plugin.denied", "reply", "TOOL", {"description": "denied"})
    allowed.register("plugin.allowed", "reply", "TOOL", {"description": "allowed"})
    with db() as session:
        session.add(BotProfilePluginPolicy(bot_profile_id="profile", plugin_id="plugin.denied", effect="deny"))
        session.add(BotProfilePluginPolicy(bot_profile_id="profile", plugin_id="plugin.allowed", effect="allow"))
        session.add(BotProfileToolPolicy(bot_profile_id="profile", component_name="plugin.allowed.reply", effect="allow"))

    service = _service([denied, allowed], monkeypatch)
    with bind_request_context(_request_context()):
        specs = service.get_llm_available_tool_specs()
        assert specs["reply"].metadata["plugin_id"] == "plugin.allowed"
        result = await service.invoke_tool_as_tool(
            ToolInvocation(tool_name="reply", arguments={}),
            ToolExecutionContext(session_id="session"),
        )
    assert result.success is True
    assert denied.rpc_calls == 0
    assert allowed.rpc_calls == 1


@pytest.mark.asyncio
async def test_tl08_cached_action_executor_rechecks_current_profile(monkeypatch) -> None:
    db = _db(monkeypatch)
    supervisor = _Supervisor()
    supervisor.register("plugin.action", "wave", "ACTION", {"description": "wave"})
    with db() as session:
        session.add(BotProfilePluginPolicy(bot_profile_id="profile", plugin_id="plugin.action", effect="allow"))
        session.add(BotProfileToolPolicy(bot_profile_id="profile", component_name="plugin.action.wave", effect="allow"))
    service = _service([supervisor], monkeypatch)

    with bind_request_context(_request_context()):
        executor = service.get_action_executor("wave")
    assert executor is not None
    with db() as session:
        session.exec(
            BotProfilePluginPolicy.__table__.update()
            .where(BotProfilePluginPolicy.plugin_id == "plugin.action")
            .values(effect="deny")
        )
    with bind_request_context(_request_context(2)):
        success, _text = await executor(action_data={})
    assert success is False
    assert supervisor.rpc_calls == 0


@pytest.mark.asyncio
async def test_cm03_cm05_allowed_duplicate_command_keeps_execution_semantics(monkeypatch) -> None:
    db = _db(monkeypatch)
    denied = _Supervisor()
    allowed = _Supervisor()
    metadata = {
        "command_pattern": r"^/demo$",
        "aliases": [],
        "description": "demo",
        "intercept_message_level": 1,
    }
    denied.register("plugin.denied", "demo", "COMMAND", metadata)
    allowed.register("plugin.allowed", "demo", "COMMAND", metadata)
    with db() as session:
        session.add(BotProfilePluginPolicy(bot_profile_id="profile", plugin_id="plugin.denied", effect="deny"))
        session.add(BotProfilePluginPolicy(bot_profile_id="profile", plugin_id="plugin.allowed", effect="allow"))
    service = _service([denied, allowed], monkeypatch)

    with bind_request_context(_request_context()):
        match = service.find_command_by_text("/demo")
        assert match is not None
        executor, groups, info = match
        assert groups == {}
        assert info.plugin_name == "plugin.allowed"
        assert re.search(r"demo", info.name)
        success, text, intercept = await executor(message=None, matched_groups={})
    assert success is True
    assert text == "ok"
    assert intercept is True
    assert denied.rpc_calls == 0
    assert allowed.rpc_calls == 1

@pytest.mark.asyncio
async def test_tl01_tl02_denied_tool_is_hidden_and_direct_invoke_has_zero_rpc(monkeypatch) -> None:
    db = _db(monkeypatch)
    supervisor = _Supervisor()
    supervisor.register("plugin.denied", "secret", "TOOL", {"description": "secret"})
    with db() as session:
        session.add(BotProfilePluginPolicy(bot_profile_id="profile", plugin_id="plugin.denied", effect="deny"))
    service = _service([supervisor], monkeypatch)
    with bind_request_context(_request_context()):
        assert service.get_llm_available_tool_specs() == {}
        result = await service.invoke_tool_as_tool(
            ToolInvocation(tool_name="secret", arguments={}),
            ToolExecutionContext(session_id="session"),
        )
    assert result.success is False
    assert supervisor.rpc_calls == 0


@pytest.mark.asyncio
async def test_tl03_cached_tool_call_is_rejected_after_policy_change(monkeypatch) -> None:
    db = _db(monkeypatch)
    supervisor = _Supervisor()
    supervisor.register("plugin.tool", "cached", "TOOL", {"description": "cached"})
    with db() as session:
        session.add(BotProfilePluginPolicy(bot_profile_id="profile", plugin_id="plugin.tool", effect="allow"))
        session.add(BotProfileToolPolicy(bot_profile_id="profile", component_name="plugin.tool.cached", effect="allow"))
    service = _service([supervisor], monkeypatch)
    with bind_request_context(_request_context()):
        cached = service.get_llm_available_tool_specs()["cached"]
    assert cached.name == "cached"
    with db() as session:
        policy = session.exec(
            BotProfileToolPolicy.__table__.select().where(
                BotProfileToolPolicy.component_name == "plugin.tool.cached"
            )
        ).first()
        session.exec(
            BotProfileToolPolicy.__table__.update()
            .where(BotProfileToolPolicy.id == policy.id)
            .values(effect="deny")
        )
    with bind_request_context(_request_context(2)):
        result = await service.invoke_tool_as_tool(
            ToolInvocation(tool_name=cached.name, arguments={}),
            ToolExecutionContext(session_id="session"),
        )
    assert result.success is False
    assert supervisor.rpc_calls == 0


def test_tl04_short_tool_rule_does_not_match_full_component(monkeypatch) -> None:
    db = _db(monkeypatch)
    supervisor = _Supervisor()
    supervisor.register("plugin.tool", "reply", "TOOL", {"description": "reply"})
    with db() as session:
        session.add(BotProfilePluginPolicy(bot_profile_id="profile", plugin_id="plugin.tool", effect="allow"))
        session.add(BotProfileToolPolicy(bot_profile_id="profile", component_name="reply", effect="allow"))
    service = _service([supervisor], monkeypatch)
    with bind_request_context(_request_context()):
        assert service.get_llm_available_tool_specs() == {}


def test_tl06_tl07_legacy_action_requires_plugin_and_tool_allow(monkeypatch) -> None:
    db = _db(monkeypatch)
    supervisor = _Supervisor()
    supervisor.register("plugin.action", "wave", "ACTION", {"description": "wave"})
    with db() as session:
        session.add(BotProfilePluginPolicy(bot_profile_id="profile", plugin_id="plugin.action", effect="allow"))
    service = _service([supervisor], monkeypatch)
    with bind_request_context(_request_context()):
        assert service.get_action_info("wave") is None
        assert service.get_default_actions() == {}

    with db() as session:
        session.add(
            BotProfileToolPolicy(
                bot_profile_id="profile",
                component_name="plugin.action.wave",
                effect="allow",
            )
        )
    with bind_request_context(_request_context(2)):
        assert service.get_action_info("wave") is not None

    with db() as session:
        session.exec(
            BotProfilePluginPolicy.__table__.update()
            .where(BotProfilePluginPolicy.plugin_id == "plugin.action")
            .values(effect="deny")
        )
    with bind_request_context(_request_context(3)):
        assert service.get_action_info("wave") is None


@pytest.mark.asyncio
async def test_tl09_concurrent_profiles_do_not_share_tool_visibility(monkeypatch) -> None:
    db = _db(monkeypatch)
    supervisor = _Supervisor()
    supervisor.register("plugin.tool", "scoped", "TOOL", {"description": "scoped"})
    with db() as session:
        session.add(
            BotProfile(
                id="profile-b",
                name="Profile B",
                profile_type="group",
                home_memory_space_id="space",
                inherit_parent_plugins=False,
                inherit_parent_tools=False,
            )
        )
        session.add(BotProfilePluginPolicy(bot_profile_id="profile", plugin_id="plugin.tool", effect="allow"))
        session.add(BotProfileToolPolicy(bot_profile_id="profile", component_name="plugin.tool.scoped", effect="allow"))
        session.add(BotProfilePluginPolicy(bot_profile_id="profile-b", plugin_id="plugin.tool", effect="deny"))
    service = _service([supervisor], monkeypatch)

    async def names(profile_id: str):
        with bind_request_context(_request_context(profile_id=profile_id)):
            await asyncio.sleep(0)
            return set(service.get_llm_available_tool_specs())

    allowed, denied = await asyncio.gather(names("profile"), names("profile-b"))
    assert allowed == {"scoped"}
    assert denied == set()


def test_tl10_kami_force_all_memory_does_not_open_plugins_or_tools(monkeypatch) -> None:
    db = _db(monkeypatch)
    supervisor = _Supervisor()
    supervisor.register("plugin.danger", "shell", "TOOL", {"description": "danger"})
    with db() as session:
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
    service = _service([supervisor], monkeypatch)
    with bind_request_context(
        _request_context(profile_id="kami", profile_type="kami", access_mode="memory.read.force_all")
    ):
        assert service.get_llm_available_tool_specs() == {}


def test_cm01_cm02_denied_command_is_not_candidate(monkeypatch) -> None:
    db = _db(monkeypatch)
    supervisor = _Supervisor()
    supervisor.register(
        "plugin.denied",
        "demo",
        "COMMAND",
        {"command_pattern": r"^/demo$", "description": "demo"},
    )
    with db() as session:
        session.add(BotProfilePluginPolicy(bot_profile_id="profile", plugin_id="plugin.denied", effect="deny"))
    service = _service([supervisor], monkeypatch)
    with bind_request_context(_request_context()):
        assert service.find_command_by_text("/demo") is None
    assert supervisor.rpc_calls == 0


@pytest.mark.asyncio
async def test_cm08_cached_command_uses_bound_profile_not_forged_args(monkeypatch) -> None:
    db = _db(monkeypatch)
    supervisor = _Supervisor()
    supervisor.register(
        "plugin.command",
        "demo",
        "COMMAND",
        {"command_pattern": r"^/demo$", "description": "demo"},
    )
    with db() as session:
        session.add(
            BotProfile(
                id="profile-forged",
                name="Forged Profile",
                profile_type="group",
                home_memory_space_id="space",
                inherit_parent_plugins=False,
                inherit_parent_tools=False,
            )
        )
        session.add(BotProfilePluginPolicy(bot_profile_id="profile", plugin_id="plugin.command", effect="allow"))
        session.add(
            BotProfilePluginPolicy(
                bot_profile_id="profile-forged",
                plugin_id="plugin.command",
                effect="deny",
            )
        )
    service = _service([supervisor], monkeypatch)
    with bind_request_context(_request_context()):
        executor, _groups, _info = service.find_command_by_text("/demo")
    with bind_request_context(_request_context(2)):
        success, text, intercept = await executor(
            message=None,
            request_context={"bot_profile_id": "profile-forged"},
        )
    assert (success, text, intercept) == (True, "ok", False)
    assert supervisor.rpc_calls == 1


@pytest.mark.asyncio
async def test_cm04_cached_command_executor_rechecks_policy_before_rpc(monkeypatch) -> None:
    db = _db(monkeypatch)
    supervisor = _Supervisor()
    supervisor.register(
        "plugin.command",
        "demo",
        "COMMAND",
        {"command_pattern": r"^/demo$", "description": "demo"},
    )
    with db() as session:
        session.add(BotProfilePluginPolicy(bot_profile_id="profile", plugin_id="plugin.command", effect="allow"))
    service = _service([supervisor], monkeypatch)
    with bind_request_context(_request_context()):
        executor, _groups, _info = service.find_command_by_text("/demo")

    with db() as session:
        session.exec(
            BotProfilePluginPolicy.__table__.update()
            .where(
                BotProfilePluginPolicy.plugin_id == "plugin.command",
                BotProfilePluginPolicy.bot_profile_id == "profile",
            )
            .values(effect="deny")
        )
    with bind_request_context(_request_context(2)):
        success, text, intercept = await executor(message=None, matched_groups={})
    assert (success, text, intercept) == (False, None, False)
    assert supervisor.rpc_calls == 0


def test_cm06_cm07_kami_only_sees_explicitly_allowed_command(monkeypatch) -> None:
    db = _db(monkeypatch)
    supervisor = _Supervisor()
    supervisor.register(
        "plugin.command",
        "demo",
        "COMMAND",
        {"command_pattern": r"^/demo$", "description": "demo"},
    )
    with db() as session:
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
    service = _service([supervisor], monkeypatch)
    with bind_request_context(_request_context(profile_id="kami", profile_type="kami")):
        assert service.find_command_by_text("/demo") is None
    with db() as session:
        session.add(BotProfilePluginPolicy(bot_profile_id="kami", plugin_id="plugin.command", effect="allow"))
    with bind_request_context(_request_context(2, profile_id="kami", profile_type="kami")):
        assert service.find_command_by_text("/demo") is not None

def test_rg01_rg02_component_registry_chat_scope_and_allowed_session_remain_hard_gates() -> None:
    registry = ComponentRegistry()
    registry.register_component(
        "private_only",
        "TOOL",
        "plugin.scope",
        {"description": "private"},
        chat_scope="private",
        allowed_session=["session-allowed"],
    )
    assert registry.get_components_by_type(
        "TOOL",
        session_id="session-allowed",
        is_group_chat=False,
    )
    assert registry.get_components_by_type(
        "TOOL",
        session_id="session-allowed",
        is_group_chat=True,
    ) == []
    assert registry.get_components_by_type(
        "TOOL",
        session_id="session-denied",
        is_group_chat=False,
    ) == []
