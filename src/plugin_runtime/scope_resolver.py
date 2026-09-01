"""统一解析 BotProfile 插件、工具与请求级配置策略。"""

from copy import deepcopy
from dataclasses import dataclass
from json import JSONDecodeError
from typing import Any, Mapping, Optional

import json

from sqlmodel import select

from src.common.database.database import get_db_session
from src.common.database.database_model import BotProfile, BotProfilePluginPolicy, BotProfileToolPolicy
from src.workspaces.request_context import BotRequestContext, get_current_request_context

from .config_overlay import PluginConfigOverlayError, apply_plugin_config_overrides, validate_and_collect_override_paths
from .request_scope import ComponentPolicyDecision, PluginPolicyDecision, PluginRequestScope

_TOOL_COMPONENT_TYPES = frozenset({"TOOL", "ACTION"})


@dataclass(frozen=True, slots=True)
class _ResolvedPluginPolicy:
    allowed: bool
    reason_code: str
    overrides: Mapping[str, Any]


class PluginScopeResolver:
    """所有插件分发器共享的唯一 BotProfile 继承解析器。"""

    @staticmethod
    def resolve_scope(context: Optional[BotRequestContext] = None) -> Optional[PluginRequestScope]:
        trusted_context = context if context is not None else get_current_request_context()
        return PluginRequestScope.from_request_context(trusted_context) if trusted_context is not None else None

    @staticmethod
    def _load_lineage(session, profile_id: str) -> list[BotProfile]:
        lineage: list[BotProfile] = []
        visited: set[str] = set()
        current_id = profile_id
        while current_id:
            if current_id in visited:
                raise ValueError("BotProfile 父级形成循环")
            visited.add(current_id)
            profile = session.get(BotProfile, current_id)
            if profile is None or not profile.enabled:
                raise ValueError(f"BotProfile 不存在或已禁用: {current_id}")
            lineage.append(profile)
            current_id = profile.parent_profile_id or ""
        lineage.reverse()
        return lineage

    @staticmethod
    def _decode_overrides(policy: BotProfilePluginPolicy) -> Mapping[str, Any]:
        try:
            decoded = json.loads(policy.overrides_json or "{}")
        except JSONDecodeError as exc:
            raise PluginConfigOverlayError("", "invalid_overrides_json") from exc
        if not isinstance(decoded, dict):
            raise PluginConfigOverlayError("", "invalid_overrides_object")
        return decoded

    def _resolve_plugin_policy_data(self, plugin_id: str, scope: PluginRequestScope) -> _ResolvedPluginPolicy:
        with get_db_session() as session:
            lineage = self._load_lineage(session, scope.bot_profile_id)
            allowed = bool(lineage[0].inherit_parent_plugins)
            reason_code = "global_inherited" if allowed else "profile_default_deny"
            merged_overrides: dict[str, Any] = {}
            for index, profile in enumerate(lineage):
                if index > 0 and not profile.inherit_parent_plugins:
                    allowed = False
                    reason_code = "inheritance_cut"
                    merged_overrides.clear()
                policy = session.exec(
                    select(BotProfilePluginPolicy).where(
                        BotProfilePluginPolicy.bot_profile_id == profile.id,
                        BotProfilePluginPolicy.plugin_id == plugin_id,
                    )
                ).first()
                if policy is None:
                    continue
                if policy.effect == "deny":
                    allowed = False
                    reason_code = "plugin_denied"
                elif policy.effect == "allow":
                    allowed = True
                    reason_code = "plugin_allowed"
                elif policy.effect != "inherit":
                    return _ResolvedPluginPolicy(False, "invalid_plugin_effect", {})
                overrides = self._decode_overrides(policy)
                self._deep_merge(merged_overrides, overrides)
            return _ResolvedPluginPolicy(allowed, reason_code, merged_overrides)

    @staticmethod
    def _deep_merge(target: dict[str, Any], update: Mapping[str, Any]) -> None:
        for key, value in update.items():
            if isinstance(value, Mapping) and isinstance(target.get(key), dict):
                PluginScopeResolver._deep_merge(target[key], value)
            elif isinstance(value, Mapping):
                nested: dict[str, Any] = {}
                PluginScopeResolver._deep_merge(nested, value)
                target[key] = nested
            else:
                target[key] = value

    def resolve_plugin_policy(
        self,
        plugin_id: str,
        scope: Optional[PluginRequestScope],
        *,
        globally_enabled: bool = True,
        config_schema: Optional[Mapping[str, Any]] = None,
        validate_overrides: bool = True,
    ) -> PluginPolicyDecision:
        """解析插件是否允许；无请求作用域时只保留全局行为。"""

        normalized_plugin_id = plugin_id.strip()
        if not globally_enabled:
            return PluginPolicyDecision(normalized_plugin_id, False, "global_disabled", "", 0)
        if scope is None:
            return PluginPolicyDecision(normalized_plugin_id, True, "global_only", "", 0)
        try:
            resolved = self._resolve_plugin_policy_data(normalized_plugin_id, scope)
            allowed_paths: tuple[str, ...] = ()
            if resolved.overrides and validate_overrides:
                if config_schema is None:
                    return PluginPolicyDecision(
                        normalized_plugin_id,
                        False,
                        "override_schema_unavailable",
                        scope.bot_profile_id,
                        scope.policy_revision,
                    )
                allowed_paths = validate_and_collect_override_paths(config_schema, resolved.overrides)
            return PluginPolicyDecision(
                normalized_plugin_id,
                resolved.allowed,
                resolved.reason_code,
                scope.bot_profile_id,
                scope.policy_revision,
                allowed_paths,
            )
        except (PluginConfigOverlayError, ValueError):
            return PluginPolicyDecision(
                normalized_plugin_id,
                False,
                "invalid_profile_policy",
                scope.bot_profile_id,
                scope.policy_revision,
            )

    def is_component_allowed(
        self,
        plugin_id: str,
        component_full_name: str,
        component_type: str,
        scope: Optional[PluginRequestScope],
        *,
        globally_enabled: bool = True,
        config_schema: Optional[Mapping[str, Any]] = None,
        validate_overrides: bool = True,
    ) -> ComponentPolicyDecision:
        """按全局、插件、完整 Tool/Action 策略顺序解析组件。"""

        plugin_decision = self.resolve_plugin_policy(
            plugin_id,
            scope,
            globally_enabled=globally_enabled,
            config_schema=config_schema,
            validate_overrides=validate_overrides,
        )
        if not plugin_decision.allowed or scope is None:
            return ComponentPolicyDecision(
                plugin_id,
                component_full_name,
                component_type,
                plugin_decision.allowed,
                plugin_decision.reason_code,
                plugin_decision.profile_id,
                plugin_decision.policy_revision,
            )
        normalized_type = str(component_type or "").strip().upper()
        if normalized_type not in _TOOL_COMPONENT_TYPES:
            return ComponentPolicyDecision(
                plugin_id,
                component_full_name,
                normalized_type,
                True,
                "plugin_allowed",
                scope.bot_profile_id,
                scope.policy_revision,
            )

        with get_db_session() as session:
            lineage = self._load_lineage(session, scope.bot_profile_id)
            allowed = bool(lineage[0].inherit_parent_tools)
            reason_code = "tool_global_inherited" if allowed else "tool_default_deny"
            for index, profile in enumerate(lineage):
                if index > 0 and not profile.inherit_parent_tools:
                    allowed = False
                    reason_code = "tool_inheritance_cut"
                policy = session.exec(
                    select(BotProfileToolPolicy).where(
                        BotProfileToolPolicy.bot_profile_id == profile.id,
                        BotProfileToolPolicy.component_name == component_full_name,
                    )
                ).first()
                if policy is None:
                    continue
                allowed = policy.effect == "allow"
                reason_code = "tool_allowed" if allowed else "tool_denied"
            return ComponentPolicyDecision(
                plugin_id,
                component_full_name,
                normalized_type,
                allowed,
                reason_code,
                scope.bot_profile_id,
                scope.policy_revision,
            )

    def resolve_effective_plugin_config(
        self,
        plugin_id: str,
        scope: Optional[PluginRequestScope],
        base_config: Mapping[str, Any],
        config_schema: Mapping[str, Any],
    ) -> dict[str, Any]:
        """生成当前请求独立配置；历史脏覆盖会使调用 fail closed。"""

        if scope is None:
            return deepcopy(dict(base_config))
        resolved = self._resolve_plugin_policy_data(plugin_id, scope)
        if not resolved.allowed:
            raise PermissionError(f"当前 BotProfile 不允许插件: {plugin_id}")
        if not resolved.overrides:
            return deepcopy(dict(base_config))
        effective, _paths = apply_plugin_config_overrides(base_config, config_schema, resolved.overrides)
        return effective


plugin_scope_resolver = PluginScopeResolver()
