from types import SimpleNamespace

import pytest

from src.core.tooling import ToolExecutionResult, ToolInvocation, ToolRegistry, ToolSpec
from src.workspaces.request_context import BotRequestContext, bind_request_context


class _Provider:
    provider_name = "plugin_runtime"
    provider_type = "plugin"

    def __init__(self) -> None:
        self.invoked: list[str] = []

    async def list_tools(self, context=None):
        del context
        return [
            ToolSpec(name="allowed", description="allowed", provider_name="plugin.demo"),
            ToolSpec(name="denied", description="denied", provider_name="plugin.demo"),
        ]

    async def invoke(self, invocation, context=None):
        del context
        self.invoked.append(invocation.tool_name)
        return ToolExecutionResult(tool_name=invocation.tool_name, success=True)

    async def close(self) -> None:
        return None


def _request_context() -> BotRequestContext:
    return BotRequestContext(
        trace_id="trace-tool",
        session_id="session-tool",
        workspace_id="workspace-tool",
        person_id="person-tool",
        active_bot_profile_id="profile-tool",
        active_bot_profile_type="group",
        permission_group_id="",
        access_mode="normal",
        security_domain="normal",
        home_memory_space_id="space-tool",
        readable_space_ids=("space-tool",),
        readable_partition_ids=(),
        writable_partition_ids=(),
        audience_type="private",
        policy_revision=3,
    )


@pytest.mark.asyncio
async def test_tool_registry_uses_current_bot_profile_full_component_policy(monkeypatch) -> None:
    provider = _Provider()
    registry = ToolRegistry()
    registry.register_provider(provider)
    workspace_context = SimpleNamespace(
        inherit_global_tools=False,
        allowed_tools=frozenset({"plugin.demo.allowed"}),
        denied_tools=frozenset({"plugin.demo.denied"}),
    )
    monkeypatch.setattr(
        "src.core.tooling.workspace_service.resolve_context_for_request",
        lambda request_context: workspace_context,
    )

    with bind_request_context(_request_context()):
        specs = await registry.list_tools()
        assert [spec.name for spec in specs] == ["allowed"]

        denied = await registry.invoke(ToolInvocation(tool_name="denied", arguments={}))
        assert denied.success is False
        assert "plugin.demo.denied" in denied.error_message

        allowed = await registry.invoke(ToolInvocation(tool_name="allowed", arguments={}))
        assert allowed.success is True
        assert provider.invoked == ["allowed"]
