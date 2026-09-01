import asyncio

import pytest

from src.plugin_runtime.host.authorization import AuthorizationManager
from src.plugin_runtime.host.capability_service import CapabilityService
from src.plugin_runtime.host.component_registry import ComponentRegistry
from src.plugin_runtime.host.invocation_scope_registry import InvocationScopeRegistry
from src.plugin_runtime.host.supervisor import PluginRunnerSupervisor
from src.plugin_runtime.protocol.envelope import Envelope, MessageType, RegisterPluginPayload
from src.plugin_runtime.request_scope import ComponentPolicyDecision, PluginRequestScope
from src.plugin_runtime.runner.invocation_context import PluginInvocationContext, bind_invocation_context
from src.plugin_runtime.runner.runner_main import PluginRunner


def _scope(profile_id: str = "profile") -> PluginRequestScope:
    return PluginRequestScope(
        trace_id="trace",
        session_id="session",
        workspace_id="workspace",
        bot_profile_id=profile_id,
        bot_profile_type="group",
        permission_group_id="",
        access_mode="normal",
        security_domain="normal",
        memory_space_id="space",
        audience_type="private",
        policy_revision=7,
    )


def _request(payload: dict, plugin_id: str = "demo") -> Envelope:
    return Envelope(
        request_id=1,
        message_type=MessageType.REQUEST,
        method="cap.call",
        plugin_id=plugin_id,
        payload=payload,
    )


@pytest.mark.asyncio
async def test_iv01_iv02_iv03_iv04_capability_token_identity_and_revocation(monkeypatch) -> None:
    authorization = AuthorizationManager()
    authorization.register_plugin("demo", ["send.text"])
    authorization.register_plugin("other", ["send.text"])
    registry = InvocationScopeRegistry("supervisor")
    service = CapabilityService(authorization, registry)
    calls = []

    async def impl(plugin_id, capability, args):
        calls.append((plugin_id, capability, args))
        return "sent"

    service.register_capability("send.text", impl)
    monkeypatch.setattr(
        "src.plugin_runtime.host.capability_service.plugin_scope_resolver.is_component_allowed",
        lambda *args, **kwargs: ComponentPolicyDecision(
            "demo", "demo.reply", "TOOL", True, "allowed", "profile", 7
        ),
    )
    token = registry.issue(
        plugin_id="demo",
        component_full_name="demo.reply",
        component_type="TOOL",
        scope=_scope(),
    )

    ok = await service.handle_capability_request(
        _request({"capability": "send.text", "args": {"text": "ok"}, "invocation_token": token})
    )
    assert ok.error is None
    assert calls == [("demo", "send.text", {"text": "ok"})]

    forged = await service.handle_capability_request(
        _request({"capability": "send.text", "args": {}, "invocation_token": "forged"})
    )
    assert forged.error is not None
    assert "forged" not in forged.error["message"]

    cross_plugin = await service.handle_capability_request(
        _request({"capability": "send.text", "args": {}, "invocation_token": token}, plugin_id="other")
    )
    assert cross_plugin.error is not None

    registry.revoke(token)
    expired = await service.handle_capability_request(
        _request({"capability": "send.text", "args": {}, "invocation_token": token})
    )
    assert expired.error is not None
    assert token not in str(expired.error)
    assert len(calls) == 1


class _RPCServer:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0
        self.payload = None

    async def send_request(self, method, plugin_id, payload, timeout_ms):
        del method, plugin_id, timeout_ms
        self.calls += 1
        self.payload = payload
        if self.fail:
            raise RuntimeError("rpc failed")
        return Envelope(request_id=1, message_type=MessageType.RESPONSE, payload={"success": True})


def _supervisor(rpc_server: _RPCServer) -> PluginRunnerSupervisor:
    supervisor = PluginRunnerSupervisor.__new__(PluginRunnerSupervisor)
    supervisor._running = True
    supervisor._group_name = "test"
    supervisor._rpc_server = rpc_server
    supervisor._invocation_scopes = InvocationScopeRegistry("supervisor")
    supervisor._component_registry = ComponentRegistry()
    supervisor._component_registry.register_component("reply", "TOOL", "demo", {"description": "reply"})
    supervisor._registered_plugins = {
        "demo": RegisterPluginPayload(
            plugin_id="demo",
            normalized_config={"style": "base"},
            config_schema={"type": "object"},
        )
    }
    return supervisor


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_iv05_supervisor_revokes_token_on_success_and_error(monkeypatch, fail: bool) -> None:
    rpc_server = _RPCServer(fail=fail)
    supervisor = _supervisor(rpc_server)
    monkeypatch.setattr(
        "src.plugin_runtime.host.supervisor.plugin_scope_resolver.is_component_allowed",
        lambda *args, **kwargs: ComponentPolicyDecision(
            "demo", "demo.reply", "TOOL", True, "allowed", "profile", 7
        ),
    )
    monkeypatch.setattr(
        "src.plugin_runtime.host.supervisor.plugin_scope_resolver.resolve_effective_plugin_config",
        lambda *args, **kwargs: {"style": "request"},
    )

    if fail:
        with pytest.raises(RuntimeError, match="rpc failed"):
            await supervisor.invoke_plugin(
                "plugin.invoke_tool", "demo", "reply", {}, request_scope=_scope()
            )
    else:
        await supervisor.invoke_plugin(
            "plugin.invoke_tool", "demo", "reply", {}, request_scope=_scope()
        )

    token = rpc_server.payload["invocation_token"]
    assert token
    assert rpc_server.payload["request_scope"]["bot_profile_id"] == "profile"
    assert rpc_server.payload["effective_plugin_config"] == {"style": "request"}
    assert supervisor._invocation_scopes.validate(token, "demo") is None


@pytest.mark.asyncio
async def test_iv05_supervisor_revokes_token_on_cancellation(monkeypatch) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class _BlockingRPCServer(_RPCServer):
        async def send_request(self, method, plugin_id, payload, timeout_ms):
            del method, plugin_id, timeout_ms
            self.calls += 1
            self.payload = payload
            started.set()
            await release.wait()
            return Envelope(
                request_id=1,
                message_type=MessageType.RESPONSE,
                payload={"success": True},
            )

    rpc_server = _BlockingRPCServer()
    supervisor = _supervisor(rpc_server)
    monkeypatch.setattr(
        "src.plugin_runtime.host.supervisor.plugin_scope_resolver.is_component_allowed",
        lambda *args, **kwargs: ComponentPolicyDecision(
            "demo", "demo.reply", "TOOL", True, "allowed", "profile", 7
        ),
    )
    monkeypatch.setattr(
        "src.plugin_runtime.host.supervisor.plugin_scope_resolver.resolve_effective_plugin_config",
        lambda *args, **kwargs: {"style": "request"},
    )

    task = asyncio.create_task(
        supervisor.invoke_plugin(
            "plugin.invoke_tool",
            "demo",
            "reply",
            {},
            request_scope=_scope(),
        )
    )
    await started.wait()
    token = rpc_server.payload["invocation_token"]
    assert supervisor._invocation_scopes.validate(token, "demo") is not None
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert supervisor._invocation_scopes.validate(token, "demo") is None


@pytest.mark.asyncio
async def test_iv06_denied_plugin_never_sends_rpc_or_issues_token(monkeypatch) -> None:
    rpc_server = _RPCServer()
    supervisor = _supervisor(rpc_server)
    monkeypatch.setattr(
        "src.plugin_runtime.host.supervisor.plugin_scope_resolver.is_component_allowed",
        lambda *args, **kwargs: ComponentPolicyDecision(
            "demo", "demo.reply", "TOOL", False, "plugin_denied", "profile", 7
        ),
    )
    with pytest.raises(PermissionError, match="plugin_denied"):
        await supervisor.invoke_plugin(
            "plugin.invoke_tool", "demo", "reply", {}, request_scope=_scope()
        )
    assert rpc_server.calls == 0
    assert supervisor._invocation_scopes._records == {}

@pytest.mark.asyncio
async def test_iv07_request_triggered_auto_reply_runs_only_for_allowed_profile(monkeypatch) -> None:
    authorization = AuthorizationManager()
    authorization.register_plugin("demo", ["send.text"])
    registry = InvocationScopeRegistry("supervisor")
    service = CapabilityService(authorization, registry)
    sent = []

    async def send_impl(plugin_id, capability, args):
        sent.append((plugin_id, capability, args))
        return {"success": True}

    service.register_capability("send.text", send_impl)
    monkeypatch.setattr(
        "src.plugin_runtime.host.capability_service.plugin_scope_resolver.is_component_allowed",
        lambda *args, **kwargs: ComponentPolicyDecision(
            "demo", "demo.auto_reply", "EVENT_HANDLER", True, "allowed", "profile", 7
        ),
    )
    token = registry.issue(
        plugin_id="demo",
        component_full_name="demo.auto_reply",
        component_type="EVENT_HANDLER",
        scope=_scope(),
    )
    response = await service.handle_capability_request(
        _request(
            {
                "capability": "send.text",
                "args": {"stream_id": "session", "text": "reply"},
                "invocation_token": token,
            }
        )
    )
    assert response.error is None
    assert sent == [
        (
            "demo",
            "send.text",
            {"stream_id": "session", "text": "reply"},
        )
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "capability",
    ["send.text", "database.save", "maisaka.context.append"],
)
async def test_iv08_policy_change_blocks_send_database_and_memory_side_effects(
    monkeypatch,
    capability: str,
) -> None:
    authorization = AuthorizationManager()
    authorization.register_plugin("demo", [capability])
    registry = InvocationScopeRegistry("supervisor")
    service = CapabilityService(authorization, registry)
    calls = []

    async def impl(plugin_id, capability_name, args):
        calls.append((plugin_id, capability_name, args))
        return True

    service.register_capability(capability, impl)
    monkeypatch.setattr(
        "src.plugin_runtime.host.capability_service.plugin_scope_resolver.is_component_allowed",
        lambda *args, **kwargs: ComponentPolicyDecision(
            "demo", "demo.auto_reply", "EVENT_HANDLER", False, "plugin_denied", "profile", 8
        ),
    )
    token = registry.issue(
        plugin_id="demo",
        component_full_name="demo.auto_reply",
        component_type="EVENT_HANDLER",
        scope=_scope(),
    )
    response = await service.handle_capability_request(
        _request({"capability": capability, "args": {}, "invocation_token": token})
    )
    assert response.error is not None
    assert calls == []


@pytest.mark.asyncio
async def test_iv09_unscoped_timer_uses_existing_global_capability_policy() -> None:
    authorization = AuthorizationManager()
    authorization.register_plugin("demo", ["send.text"])
    registry = InvocationScopeRegistry("supervisor")
    service = CapabilityService(authorization, registry)
    service.register_capability("send.text", lambda *_args: _async_value("sent"))
    response = await service.handle_capability_request(
        _request({"capability": "send.text", "args": {"text": "timer"}})
    )
    assert response.error is None
    assert response.payload["result"] == "sent"


@pytest.mark.asyncio
async def test_iv10_token_value_is_absent_from_diagnostics_and_errors() -> None:
    token = "phase5b-secret-invocation-token"
    summary = PluginRunner._summarize_envelope_payload(
        {
            "component_name": "reply",
            "args": {"text": "not-logged"},
            "invocation_token": token,
        }
    )
    assert token not in str(summary)

    authorization = AuthorizationManager()
    authorization.register_plugin("demo", ["send.text"])
    service = CapabilityService(
        authorization,
        InvocationScopeRegistry("supervisor"),
    )
    response = await service.handle_capability_request(
        _request(
            {
                "capability": "send.text",
                "args": {},
                "invocation_token": token,
            }
        )
    )
    assert response.error is not None
    assert token not in str(response.error)


async def _async_value(value):
    return value


class _RunnerRPCClient:
    def __init__(self) -> None:
        self.payload = None

    async def send_request(self, method, plugin_id, payload, timeout_ms):
        del method, plugin_id, timeout_ms
        self.payload = payload
        return Envelope(
            request_id=1,
            message_type=MessageType.RESPONSE,
            payload={"result": True},
        )


class _ContextPlugin:
    def _set_context(self, context) -> None:
        self.ctx = context


@pytest.mark.asyncio
async def test_runner_overwrites_forged_capability_token_with_bound_token() -> None:
    runner = PluginRunner.__new__(PluginRunner)
    runner._rpc_client = _RunnerRPCClient()
    plugin = _ContextPlugin()
    runner._inject_context("test.demo", plugin)
    invocation = PluginInvocationContext(
        plugin_id="test.demo",
        component_full_name="test.demo.reply",
        request_scope=_scope(),
        effective_plugin_config={"style": "request"},
        invocation_token="host-issued",
    )
    with bind_invocation_context(invocation):
        await plugin.ctx.call_host_method(
            "cap.call",
            payload={
                "capability": "send.text",
                "args": {"text": "hello"},
                "invocation_token": "forged",
            },
        )
        request_scope = plugin.ctx.get_request_scope()
        effective_config = plugin.ctx.get_effective_plugin_config()
    assert runner._rpc_client.payload["invocation_token"] == "host-issued"
    assert request_scope.bot_profile_id == "profile"
    assert effective_config == {"style": "request"}
