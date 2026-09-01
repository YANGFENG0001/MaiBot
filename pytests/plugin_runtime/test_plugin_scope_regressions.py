from types import SimpleNamespace

import pytest

from src.common.database.migrations.builtin import LATEST_SCHEMA_VERSION, V46_SCHEMA_VERSION
from src.core.tooling import ToolExecutionResult, ToolInvocation, ToolRegistry, ToolSpec
from src.platform_io.types import DeliveryBatch, DeliveryReceipt, DeliveryStatus, DriverKind, RouteKey
from src.plugin_runtime.host.circuit_breaker import PluginCircuitBreaker
from src.plugin_runtime.host.component_registry import ComponentRegistry
from src.plugin_runtime.host.component_timeout import (
    DEFAULT_COMPONENT_RPC_TIMEOUT_MS,
    resolve_component_rpc_timeout_ms,
)
from src.plugin_runtime.host.invocation_scope_registry import InvocationScopeRegistry
from src.plugin_runtime.host.message_gateway import MessageGateway
from src.plugin_runtime.host.supervisor import PluginRunnerSupervisor
from src.plugin_runtime.protocol.envelope import Envelope, MessageType, RegisterPluginPayload
from src.plugin_runtime.protocol.errors import ErrorCode, RPCError
from src.workspaces.request_context import BotRequestContext, bind_request_context


def _request_context(profile_id: str) -> BotRequestContext:
    return BotRequestContext(
        trace_id=f"trace-{profile_id}",
        session_id="session",
        workspace_id="workspace",
        person_id="person",
        active_bot_profile_id=profile_id,
        active_bot_profile_type="group",
        permission_group_id="",
        access_mode="normal",
        security_domain="normal",
        home_memory_space_id="space",
        readable_space_ids=("space",),
        readable_partition_ids=(),
        writable_partition_ids=(),
        audience_type="private",
        policy_revision=1,
    )


def test_rg03_circuit_breaker_timeout_and_shutdown_gates_remain_active(monkeypatch) -> None:
    breaker = PluginCircuitBreaker(
        failure_threshold=1,
        base_cooldown_sec=60,
        max_cooldown_sec=60,
    )
    permit = breaker.try_acquire("demo", "demo.reply", "tool")
    assert permit.allowed is True
    breaker.record_failure(permit, "rpc failed")
    assert breaker.try_acquire("demo", "demo.reply", "tool").allowed is False

    assert resolve_component_rpc_timeout_ms(0) == DEFAULT_COMPONENT_RPC_TIMEOUT_MS
    assert resolve_component_rpc_timeout_ms(2500) == 2500

    supervisor = PluginRunnerSupervisor.__new__(PluginRunnerSupervisor)
    supervisor._running = False
    monkeypatch.setattr("src.plugin_runtime.host.supervisor.is_shutdown_requested", lambda: False)
    with pytest.raises(RPCError) as stopped_error:
        supervisor._ensure_accepting_runner_rpc()
    assert stopped_error.value.code == ErrorCode.E_SHUTTING_DOWN

    supervisor._running = True
    monkeypatch.setattr("src.plugin_runtime.host.supervisor.is_shutdown_requested", lambda: True)
    with pytest.raises(RPCError) as shutdown_error:
        supervisor._ensure_accepting_runner_rpc()
    assert shutdown_error.value.code == ErrorCode.E_SHUTTING_DOWN


@pytest.mark.asyncio
async def test_rg06_message_gateway_does_not_restart_or_switch_accounts_per_profile(monkeypatch) -> None:
    route_key = RouteKey(platform="qq", account_id="bot-account", scope="napcat")

    class _PlatformIOManager:
        is_started = True

        def __init__(self) -> None:
            self.send_calls = 0
            self.lifecycle_calls = 0

        def build_route_key_from_message(self, message):
            del message
            return route_key

        async def send_message(self, message, current_route_key):
            self.send_calls += 1
            assert current_route_key == route_key
            return DeliveryBatch(
                internal_message_id=message.message_id,
                route_key=route_key,
                receipts=[
                    DeliveryReceipt(
                        internal_message_id=message.message_id,
                        route_key=route_key,
                        status=DeliveryStatus.SENT,
                        driver_id="napcat",
                        driver_kind=DriverKind.PLUGIN,
                        external_message_id=f"external-{self.send_calls}",
                    )
                ],
            )

        async def start(self):
            self.lifecycle_calls += 1
            raise AssertionError("BotProfile 切换不应重启 Platform IO")

        async def stop(self):
            self.lifecycle_calls += 1
            raise AssertionError("BotProfile 切换不应停止 Platform IO")

    manager = _PlatformIOManager()
    monkeypatch.setattr(
        "src.plugin_runtime.host.message_gateway.get_platform_io_manager",
        lambda: manager,
    )
    gateway = MessageGateway(ComponentRegistry())
    supervisor = SimpleNamespace()

    first = SimpleNamespace(message_id="internal-a")
    with bind_request_context(_request_context("profile-a")):
        assert await gateway.send_message_to_external(
            first,
            supervisor,
            save_to_db=False,
        )
    second = SimpleNamespace(message_id="internal-b")
    with bind_request_context(_request_context("profile-b")):
        assert await gateway.send_message_to_external(
            second,
            supervisor,
            save_to_db=False,
        )

    assert manager.send_calls == 2
    assert manager.lifecycle_calls == 0
    assert first.message_id == "external-1"
    assert second.message_id == "external-2"


@pytest.mark.asyncio
async def test_rg06_gateway_supervisor_ignores_bound_profile_policy() -> None:
    class _RPCServer:
        def __init__(self) -> None:
            self.payload = None

        async def send_request(self, method, plugin_id, payload, timeout_ms):
            del timeout_ms
            assert method == "plugin.invoke_message_gateway"
            assert plugin_id == "adapter.demo"
            self.payload = payload
            return Envelope(
                request_id=1,
                message_type=MessageType.RESPONSE,
                payload={"success": True, "result": {"success": True}},
            )

    rpc_server = _RPCServer()
    supervisor = PluginRunnerSupervisor.__new__(PluginRunnerSupervisor)
    supervisor._running = True
    supervisor._rpc_server = rpc_server
    supervisor._invocation_scopes = InvocationScopeRegistry("gateway")
    supervisor._component_registry = ComponentRegistry()
    supervisor._component_registry.register_component(
        "gateway",
        "MESSAGE_GATEWAY",
        "adapter.demo",
        {"platform": "qq", "route_type": "duplex"},
    )
    supervisor._registered_plugins = {
        "adapter.demo": RegisterPluginPayload(
            plugin_id="adapter.demo",
            normalized_config={"account_id": "bot-account"},
            config_schema={"type": "object"},
        )
    }

    with bind_request_context(_request_context("isolated-profile")):
        response = await supervisor.invoke_message_gateway(
            "adapter.demo",
            "gateway",
            {"message": {"text": "hello"}},
        )

    assert response.error is None
    assert rpc_server.payload is not None
    assert rpc_server.payload["request_scope"] == {}
    assert rpc_server.payload["invocation_token"] == ""
    assert rpc_server.payload["effective_plugin_config"] == {"account_id": "bot-account"}


@pytest.mark.asyncio
async def test_rg04_builtin_tool_still_obeys_current_bot_profile_policy(monkeypatch) -> None:
    class _BuiltinProvider:
        provider_name = "builtin"
        provider_type = "builtin"

        def __init__(self) -> None:
            self.invoked = 0

        async def list_tools(self, context=None):
            del context
            return [
                ToolSpec(
                    name="echo",
                    provider_name=self.provider_name,
                    metadata={"component_full_name": "builtin.echo"},
                )
            ]

        async def invoke(self, invocation: ToolInvocation, context=None):
            del invocation, context
            self.invoked += 1
            return ToolExecutionResult(tool_name="echo", success=True)

        async def close(self) -> None:
            return None

    provider = _BuiltinProvider()
    registry = ToolRegistry()
    registry.register_provider(provider)
    workspace_context = SimpleNamespace(
        inherit_global_tools=False,
        allowed_tools=frozenset({"builtin.echo"}),
        denied_tools=frozenset(),
    )
    monkeypatch.setattr(
        "src.core.tooling.workspace_service.resolve_context_for_request",
        lambda request_context: workspace_context,
    )

    with bind_request_context(_request_context("profile-a")):
        result = await registry.invoke(ToolInvocation(tool_name="echo", arguments={}))
    assert result.success is True
    assert provider.invoked == 1

    workspace_context.denied_tools = frozenset({"builtin.echo"})
    with bind_request_context(_request_context("profile-a")):
        denied = await registry.invoke(ToolInvocation(tool_name="echo", arguments={}))
    assert denied.success is False
    assert "builtin.echo" in denied.error_message
    assert provider.invoked == 1


def test_rg08_schema_remains_v46_without_phase5b_migration() -> None:
    assert LATEST_SCHEMA_VERSION == V46_SCHEMA_VERSION == 46
