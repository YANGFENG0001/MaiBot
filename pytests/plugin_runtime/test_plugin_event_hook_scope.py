from contextlib import contextmanager
from types import SimpleNamespace

import asyncio
import importlib

import pytest
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, SQLModel, create_engine

from src.common.database.database_model import BotProfile, BotProfilePluginPolicy, MemorySpace
from src.plugin_runtime.host.component_registry import ComponentRegistry
from src.plugin_runtime.host.event_dispatcher import EventDispatcher
from src.plugin_runtime.host.hook_dispatcher import HookDispatcher
from src.plugin_runtime.host.hook_spec_registry import HookSpec, HookSpecRegistry
from src.plugin_runtime.protocol.envelope import Envelope, MessageType
from src.plugin_runtime.request_scope import PluginRequestScope


def _scope(profile_id: str = "profile") -> PluginRequestScope:
    return PluginRequestScope(
        trace_id=f"trace-{profile_id}",
        session_id="session",
        workspace_id="workspace",
        bot_profile_id=profile_id,
        bot_profile_type="group",
        permission_group_id="",
        access_mode="normal",
        security_domain="normal",
        memory_space_id="space",
        audience_type="private",
        policy_revision=1,
    )


def _db(monkeypatch, *, allow: bool):
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

    module = importlib.import_module("src.plugin_runtime.scope_resolver")
    monkeypatch.setattr(module, "get_db_session", db)
    with db() as session:
        session.add(MemorySpace(id="space", name="space"))
        session.flush()
        session.add(
            BotProfile(
                id="profile",
                name="Profile",
                profile_type="group",
                home_memory_space_id="space",
                inherit_parent_plugins=False,
            )
        )
        if allow:
            session.flush()
            session.add(BotProfilePluginPolicy(bot_profile_id="profile", plugin_id="demo", effect="allow"))


class _Supervisor:
    group_name = "third_party"

    def __init__(self, registry: ComponentRegistry, payload: dict) -> None:
        self.component_registry = registry
        self._registered_plugins = {"demo": SimpleNamespace(config_schema={}, normalized_config={})}
        self.calls = []
        self.payload = payload

    async def invoke_plugin(self, method, plugin_id, component_name, args=None, timeout_ms=30000, **kwargs):
        self.calls.append(
            {
                "method": method,
                "plugin_id": plugin_id,
                "component_name": component_name,
                "args": args,
                "timeout_ms": timeout_ms,
                **kwargs,
            }
        )
        return Envelope(request_id=1, message_type=MessageType.RESPONSE, payload=dict(self.payload))


@pytest.mark.asyncio
async def test_ev01_ev02_ev03_denied_handlers_have_no_control_or_task(monkeypatch) -> None:
    _db(monkeypatch, allow=False)
    registry = ComponentRegistry()
    registry.register_component(
        "intercept",
        "EVENT_HANDLER",
        "demo",
        {"event_type": "on_message", "intercept_message": True},
    )
    registry.register_component(
        "observe",
        "EVENT_HANDLER",
        "demo",
        {"event_type": "on_message", "intercept_message": False},
    )
    supervisor = _Supervisor(
        registry,
        {
            "success": True,
            "continue_processing": False,
            "modified_message": {"security_domain": "forged"},
        },
    )
    dispatcher = EventDispatcher(registry)
    should_continue, modified = await dispatcher.dispatch_event(
        "on_message", supervisor, request_scope=_scope()
    )
    assert should_continue is True
    assert modified is None
    assert supervisor.calls == []
    assert dispatcher._background_tasks == set()


@pytest.mark.asyncio
async def test_ev04_ev05_allowed_nonblocking_captures_scope(monkeypatch) -> None:
    _db(monkeypatch, allow=True)
    registry = ComponentRegistry()
    registry.register_component(
        "observe",
        "EVENT_HANDLER",
        "demo",
        {"event_type": "on_message", "intercept_message": False},
    )
    supervisor = _Supervisor(registry, {"success": True, "continue_processing": True})
    dispatcher = EventDispatcher(registry)
    scopes = [_scope("profile"), _scope("profile")]
    scopes[1] = PluginRequestScope(**{**scopes[1].to_payload(), "trace_id": "trace-second"})
    await asyncio.gather(
        *(dispatcher.dispatch_event("on_message", supervisor, request_scope=scope) for scope in scopes)
    )
    if dispatcher._background_tasks:
        await asyncio.gather(*tuple(dispatcher._background_tasks))
    assert {call["request_scope"].trace_id for call in supervisor.calls} == {"trace-profile", "trace-second"}


def test_ev07_security_fields_are_removed_from_event_update() -> None:
    sanitized = EventDispatcher._sanitize_modified_message(
        {
            "text": "ok",
            "bot_profile_id": "forged",
            "security_domain": "forged",
            "memory_space_id": "forged",
            "invocation_token": "forged",
        }
    )
    assert sanitized == {"text": "ok"}


@pytest.mark.asyncio
async def test_hk01_hk02_hk03_denied_hooks_cannot_mutate_abort_or_schedule(monkeypatch) -> None:
    _db(monkeypatch, allow=False)
    specs = HookSpecRegistry()
    specs.register_hook_spec(HookSpec(name="reply.before", allow_abort=True, allow_kwargs_mutation=True))
    registry = ComponentRegistry(hook_spec_registry=specs)
    registry.register_component(
        "blocking",
        "HOOK_HANDLER",
        "demo",
        {"hook": "reply.before", "mode": "blocking"},
    )
    registry.register_component(
        "observe",
        "HOOK_HANDLER",
        "demo",
        {"hook": "reply.before", "mode": "observe"},
    )
    supervisor = _Supervisor(
        registry,
        {"success": True, "action": "abort", "modified_kwargs": {"text": "forged"}},
    )
    dispatcher = HookDispatcher(lambda: [supervisor], hook_spec_registry=specs)
    result = await dispatcher.invoke_hook("reply.before", request_scope=_scope(), text="original")
    assert result.kwargs == {"text": "original"}
    assert result.aborted is False
    assert supervisor.calls == []
    assert dispatcher._background_tasks == set()


@pytest.mark.asyncio
async def test_hk04_hk05_allowed_hooks_keep_scope_and_modify(monkeypatch) -> None:
    _db(monkeypatch, allow=True)
    specs = HookSpecRegistry()
    specs.register_hook_spec(HookSpec(name="reply.before", allow_abort=True, allow_kwargs_mutation=True))
    registry = ComponentRegistry(hook_spec_registry=specs)
    registry.register_component(
        "blocking",
        "HOOK_HANDLER",
        "demo",
        {"hook": "reply.before", "mode": "blocking", "order": "early"},
    )
    supervisor = _Supervisor(
        registry,
        {"success": True, "action": "continue", "modified_kwargs": {"text": "changed"}},
    )
    dispatcher = HookDispatcher(lambda: [supervisor], hook_spec_registry=specs)
    result = await dispatcher.invoke_hook("reply.before", request_scope=_scope(), text="original")
    assert result.kwargs == {"text": "changed"}
    assert supervisor.calls[0]["request_scope"].trace_id == "trace-profile"

@pytest.mark.asyncio
async def test_ev06_hk06_unscoped_lifecycle_dispatch_uses_global_policy() -> None:
    event_registry = ComponentRegistry()
    event_registry.register_component(
        "start",
        "EVENT_HANDLER",
        "demo",
        {"event_type": "on_start", "intercept_message": True},
    )
    event_supervisor = _Supervisor(
        event_registry, {"success": True, "continue_processing": True}
    )
    event_dispatcher = EventDispatcher(event_registry)
    should_continue, _modified = await event_dispatcher.dispatch_event(
        "on_start", event_supervisor, request_scope=None
    )
    assert should_continue is True
    assert len(event_supervisor.calls) == 1
    assert event_supervisor.calls[0]["request_scope"] is None

    specs = HookSpecRegistry()
    specs.register_hook_spec(HookSpec(name="lifecycle.start"))
    hook_registry = ComponentRegistry(hook_spec_registry=specs)
    hook_registry.register_component(
        "start",
        "HOOK_HANDLER",
        "demo",
        {"hook": "lifecycle.start", "mode": "blocking"},
    )
    hook_supervisor = _Supervisor(
        hook_registry, {"success": True, "action": "continue"}
    )
    hook_dispatcher = HookDispatcher(lambda: [hook_supervisor], hook_spec_registry=specs)
    await hook_dispatcher.invoke_hook("lifecycle.start", request_scope=None)
    assert len(hook_supervisor.calls) == 1
    assert hook_supervisor.calls[0]["request_scope"] is None


def test_hk07_forged_scope_and_token_are_removed_from_modified_kwargs() -> None:
    assert HookDispatcher._extract_modified_kwargs(
        {
            "text": "ok",
            "request_scope": {"bot_profile_id": "forged"},
            "invocation_token": "forged",
            "security_domain": "forged",
        }
    ) == {"text": "ok"}

@pytest.mark.asyncio
async def test_event_history_does_not_retain_message_body_or_custom_payload(monkeypatch) -> None:
    _db(monkeypatch, allow=True)
    registry = ComponentRegistry()
    registry.register_component(
        "intercept",
        "EVENT_HANDLER",
        "demo",
        {"event_type": "on_message", "intercept_message": True},
    )
    supervisor = _Supervisor(
        registry,
        {
            "success": True,
            "continue_processing": True,
            "modified_message": {"text": "sensitive-message-body"},
            "custom_result": {"secret": "sensitive-config-value"},
        },
    )
    dispatcher = EventDispatcher(registry)
    dispatcher.enable_history("on_message")
    await dispatcher.dispatch_event("on_message", supervisor, request_scope=_scope())

    history = dispatcher.get_history("on_message")
    assert len(history) == 1
    assert history[0].modified_message is None
    assert history[0].custom_result is None
    assert "sensitive-message-body" not in str(history)
    assert "sensitive-config-value" not in str(history)
