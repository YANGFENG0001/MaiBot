"""插件组件请求级不可变作用域。"""

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from src.workspaces.request_context import BotRequestContext


@dataclass(frozen=True, slots=True)
class PluginRequestScope:
    """Host 从可信 ``BotRequestContext`` 派生的插件调用作用域。"""

    trace_id: str
    session_id: str
    workspace_id: str
    bot_profile_id: str
    bot_profile_type: str
    permission_group_id: str
    access_mode: str
    security_domain: str
    memory_space_id: str
    audience_type: str
    policy_revision: int

    @classmethod
    def from_request_context(cls, context: BotRequestContext) -> "PluginRequestScope":
        """只从已绑定的可信请求上下文创建插件作用域。"""

        return cls(
            trace_id=context.trace_id,
            session_id=context.session_id,
            workspace_id=context.workspace_id,
            bot_profile_id=context.active_bot_profile_id,
            bot_profile_type=context.active_bot_profile_type,
            permission_group_id=context.permission_group_id,
            access_mode=context.access_mode,
            security_domain=context.security_domain,
            memory_space_id=context.home_memory_space_id,
            audience_type=context.audience_type,
            policy_revision=context.policy_revision,
        )

    def to_payload(self) -> dict[str, Any]:
        """生成只包含非配置值元数据的 RPC 快照。"""

        return asdict(self)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "PluginRequestScope":
        """Runner 将 Host 保留字段还原为只读快照。"""

        return cls(
            trace_id=str(payload.get("trace_id") or ""),
            session_id=str(payload.get("session_id") or ""),
            workspace_id=str(payload.get("workspace_id") or ""),
            bot_profile_id=str(payload.get("bot_profile_id") or ""),
            bot_profile_type=str(payload.get("bot_profile_type") or ""),
            permission_group_id=str(payload.get("permission_group_id") or ""),
            access_mode=str(payload.get("access_mode") or ""),
            security_domain=str(payload.get("security_domain") or ""),
            memory_space_id=str(payload.get("memory_space_id") or ""),
            audience_type=str(payload.get("audience_type") or ""),
            policy_revision=int(payload.get("policy_revision") or 0),
        )


@dataclass(frozen=True, slots=True)
class PluginPolicyDecision:
    """不携带覆盖值的插件策略决策，可安全用于日志与诊断。"""

    plugin_id: str
    allowed: bool
    reason_code: str
    profile_id: str
    policy_revision: int
    allowed_override_paths: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ComponentPolicyDecision:
    """插件组件在当前请求中的最终策略决策。"""

    plugin_id: str
    component_full_name: str
    component_type: str
    allowed: bool
    reason_code: str
    profile_id: str
    policy_revision: int
