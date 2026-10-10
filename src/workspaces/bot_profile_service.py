"""BotProfile 数据访问、继承与普通会话路由。"""

from datetime import datetime
from typing import Any, Mapping, Optional

import json
import uuid

from sqlmodel import select

from src.common.database.database import get_db_session
from src.common.database.database_model import (
    BotProfile,
    BotProfileMemoryRule,
    BotProfilePluginPolicy,
    BotProfileToolPolicy,
    BotRouteState,
    MemorySpace,
    MemorySpaceBotRule,
    PermissionGroupBotRule,
    PersonaProfile,
    Workspace,
)

from .context import BotProfileContext

PUBLIC_BOT_PROFILE_ID = "bot-profile-public"


class BotProfileService:
    def get_profile(self, profile_id: str) -> Optional[BotProfile]:
        with get_db_session() as session:
            return session.get(BotProfile, profile_id)

    def resolve_profile_context(self, profile_id: str) -> BotProfileContext:
        with get_db_session() as session:
            profile = session.get(BotProfile, profile_id)
            if profile is None or not profile.enabled:
                raise ValueError(f"BotProfile 不存在或已禁用: {profile_id}")
            return BotProfileContext(
                profile.id,
                profile.profile_type,
                profile.home_memory_space_id,
                profile.policy_revision,
                profile.parent_profile_id or "",
            )

    def validate_parent(self, profile_id: str, parent_profile_id: Optional[str]) -> None:
        if not parent_profile_id:
            return
        with get_db_session() as session:
            current = parent_profile_id
            visited = {profile_id}
            while current:
                if current in visited:
                    raise ValueError("BotProfile 父级形成循环")
                visited.add(current)
                parent = session.get(BotProfile, current)
                if parent is None:
                    raise ValueError(f"父级 BotProfile 不存在: {current}")
                current = parent.parent_profile_id or ""

    def get_lineage(self, profile_id: str) -> tuple[BotProfile, ...]:
        """返回从公共父级到当前 Profile 的继承链。"""
        with get_db_session() as session:
            lineage: list[BotProfile] = []
            visited: set[str] = set()
            current = profile_id
            while current:
                if current in visited:
                    raise ValueError("BotProfile 父级形成循环")
                visited.add(current)
                profile = session.get(BotProfile, current)
                if profile is None:
                    raise ValueError(f"BotProfile 不存在: {current}")
                lineage.append(profile)
                current = profile.parent_profile_id or ""
            lineage.reverse()
            return tuple(lineage)

    def resolve_tool_policies(self, profile_id: str) -> dict[str, str]:
        """按父到子顺序合并工具策略，子级同名规则覆盖父级。"""
        resolved: dict[str, str] = {}
        lineage = self.get_lineage(profile_id)
        with get_db_session() as session:
            for index, profile in enumerate(lineage):
                if index > 0 and not profile.inherit_parent_tools:
                    resolved.clear()
                policies = session.exec(
                    select(BotProfileToolPolicy).where(BotProfileToolPolicy.bot_profile_id == profile.id)
                ).all()
                resolved.update({policy.component_name: policy.effect for policy in policies})
        return resolved

    def resolve_plugin_policies(self, profile_id: str) -> dict[str, BotProfilePluginPolicy]:
        """按继承链解析插件策略，返回最终 Profile 规则。"""
        resolved: dict[str, BotProfilePluginPolicy] = {}
        lineage = self.get_lineage(profile_id)
        with get_db_session() as session:
            for index, profile in enumerate(lineage):
                if index > 0 and not profile.inherit_parent_plugins:
                    resolved.clear()
                policies = session.exec(
                    select(BotProfilePluginPolicy).where(BotProfilePluginPolicy.bot_profile_id == profile.id)
                ).all()
                resolved.update({policy.plugin_id: policy for policy in policies})
        return resolved

    def list_profiles(self) -> list[BotProfile]:
        """按公共 -> 分组 -> Kami 的顺序返回全部 BotProfile。"""

        with get_db_session() as session:
            profiles = session.exec(select(BotProfile)).all()
        order = {"public": 0, "group": 1, "kami": 2}
        return sorted(profiles, key=lambda item: (order.get(item.profile_type, 9), item.name))

    def create_profile(
        self,
        *,
        name: str,
        home_memory_space_id: str,
        profile_type: str = "group",
        parent_profile_id: Optional[str] = None,
        persona_profile_id: Optional[str] = None,
        inherit_parent_persona: bool = True,
        inherit_parent_tools: bool = True,
        inherit_parent_plugins: bool = True,
        enabled: bool = True,
        profile_id: Optional[str] = None,
    ) -> BotProfile:
        """新建 BotProfile。

        只允许创建 `group` 型：`public` 是迁移预置的兜底身份、`kami` 由
        v45→v46 迁移与 Kami 专用安全流程独占，都不该从普通管理界面里造出来。
        """

        normalized_name = (name or "").strip()
        if not normalized_name:
            raise ValueError("Bot 名称不能为空")
        if profile_type != "group":
            raise ValueError("只能创建 group 型 Bot；public/kami 由系统管理")

        normalized_space = (home_memory_space_id or "").strip()
        if not normalized_space:
            raise ValueError("必须指定主记忆空间")

        parent = (parent_profile_id or "").strip() or PUBLIC_BOT_PROFILE_ID

        with get_db_session() as session:
            if session.exec(select(BotProfile).where(BotProfile.name == normalized_name)).first():
                raise ValueError(f"Bot 名称已存在: {normalized_name}")
            if session.get(MemorySpace, normalized_space) is None:
                raise ValueError(f"主记忆空间不存在: {normalized_space}")
            if session.get(BotProfile, parent) is None:
                raise ValueError(f"父级 BotProfile 不存在: {parent}")
            if persona_profile_id and session.get(PersonaProfile, persona_profile_id) is None:
                raise ValueError(f"人设不存在: {persona_profile_id}")

            new_id = (profile_id or "").strip() or f"bot-profile-{uuid.uuid4().hex[:12]}"
            if session.get(BotProfile, new_id) is not None:
                raise ValueError(f"BotProfile ID 已存在: {new_id}")

            profile = BotProfile(
                id=new_id,
                name=normalized_name,
                profile_type=profile_type,
                parent_profile_id=parent,
                persona_profile_id=persona_profile_id or None,
                home_memory_space_id=normalized_space,
                inherit_parent_persona=inherit_parent_persona,
                inherit_parent_tools=inherit_parent_tools,
                inherit_parent_plugins=inherit_parent_plugins,
                enabled=enabled,
                is_system=False,
            )
            session.add(profile)

        # 复用既有的成环校验：新建时父链必然已存在，这里只防「自己指向自己」之类的脏数据。
        self.validate_parent(new_id, parent)
        return self.get_profile(new_id)  # type: ignore[return-value]

    def delete_profile(self, profile_id: str) -> bool:
        """删除 BotProfile 及其策略，带引用守卫。

        不允许级联删除：删掉一个仍被 Workspace 或会话路由引用的 Bot，
        会让 `resolve_for_session` 落到 `PUBLIC_BOT_PROFILE_ID` 兜底，
        表现为「某个群的人格突然变了」——比直接报错更难排查。
        """

        with get_db_session() as session:
            profile = session.get(BotProfile, profile_id)
            if profile is None:
                return False
            if profile.is_system:
                raise ValueError(f"系统内置 BotProfile 不可删除: {profile.name}")

            children = session.exec(select(BotProfile).where(BotProfile.parent_profile_id == profile_id)).all()
            if children:
                names = ", ".join(child.name for child in children)
                raise ValueError(f"仍有子级 BotProfile 继承它，无法删除: {names}")

            workspaces = session.exec(select(Workspace).where(Workspace.bot_profile_id == profile_id)).all()
            if workspaces:
                names = ", ".join(workspace.name for workspace in workspaces)
                raise ValueError(f"仍被子系统引用，无法删除: {names}")

            routes = session.exec(select(BotRouteState).where(BotRouteState.active_bot_profile_id == profile_id)).all()
            if routes:
                sessions = ", ".join(route.session_id for route in routes[:5])
                raise ValueError(f"仍有会话正在使用它，无法删除: {sessions}")

            # 记忆权限组里的 Bot 规则是管理端显式配置，静默丢弃会让「某些人突然读不到
            # 记忆」；而且它带 bot_profiles 外键，不拦就会抛裸 FK 错误。
            group_rules = session.exec(
                select(PermissionGroupBotRule).where(PermissionGroupBotRule.bot_profile_id == profile_id)
            ).all()
            if group_rules:
                groups = ", ".join(str(rule.permission_group_id) for rule in group_rules[:5])
                raise ValueError(f"仍被记忆权限组的 Bot 规则引用，请先移除该规则: {groups}")

            # `memory_space_bot_rules` 是跨空间读取「双向握手」的入站半边，与出站半边
            # `bot_profile_memory_rules` 成对存在，所以一起级联删除，避免留下半条授权。
            for model in (BotProfileToolPolicy, BotProfilePluginPolicy, BotProfileMemoryRule, MemorySpaceBotRule):
                for row in session.exec(select(model).where(model.bot_profile_id == profile_id)).all():
                    session.delete(row)
            # 必须先落盘子表的删除：工作单元在给 DELETE 排序时会把 `bot_profiles` 排在
            # `memory_space_bot_rules` 之前（`bot_profiles` 有自引用外键，排序会退化），
            # 于是父行先删就撞外键约束。显式 flush 把顺序钉死。
            session.flush()
            session.delete(profile)
            return True

    def update_profile(self, profile_id: str, **changes: Any) -> BotProfile:
        """更新可继承字段；父级变更走 validate_parent 防止成环。"""

        allowed = {
            "parent_profile_id",
            "persona_profile_id",
            "inherit_parent_persona",
            "inherit_parent_tools",
            "inherit_parent_plugins",
            "enabled",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"不支持的 BotProfile 字段: {', '.join(sorted(unknown))}")
        if "parent_profile_id" in changes:
            self.validate_parent(profile_id, changes["parent_profile_id"])
        if changes.get("persona_profile_id"):
            # 悬空的人设外键会让运行期 `_resolve_persona` 静默退回空覆盖层，
            # 表现为「人设设了但没生效」，所以在写入侧就拦掉。
            with get_db_session() as session:
                if session.get(PersonaProfile, changes["persona_profile_id"]) is None:
                    raise ValueError(f"人设不存在: {changes['persona_profile_id']}")
        with get_db_session() as session:
            profile = session.get(BotProfile, profile_id)
            if profile is None:
                raise ValueError(f"BotProfile 不存在: {profile_id}")
            if profile.profile_type == "kami" and changes.get("parent_profile_id"):
                raise ValueError("Kami BotProfile 必须完全独立")
            for key, value in changes.items():
                setattr(profile, key, value)
            profile.policy_revision += 1
            profile.updated_at = datetime.now()
            session.add(profile)
            return profile

    def remove_tool_policy(self, profile_id: str, component_name: str) -> bool:
        """删除 Profile 工具策略并递增策略版本。"""

        normalized = component_name.strip()
        with get_db_session() as session:
            profile = session.get(BotProfile, profile_id)
            if profile is None:
                raise ValueError(f"BotProfile 不存在: {profile_id}")
            policy = session.exec(
                select(BotProfileToolPolicy).where(
                    BotProfileToolPolicy.bot_profile_id == profile_id,
                    BotProfileToolPolicy.component_name == normalized,
                )
            ).first()
            if policy is None:
                return False
            session.delete(policy)
            profile.policy_revision += 1
            profile.updated_at = datetime.now()
            session.add(profile)
            return True

    def remove_plugin_policy(self, profile_id: str, plugin_id: str) -> bool:
        """删除 Profile 插件策略并递增策略版本。"""

        normalized = plugin_id.strip()
        with get_db_session() as session:
            profile = session.get(BotProfile, profile_id)
            if profile is None:
                raise ValueError(f"BotProfile 不存在: {profile_id}")
            policy = session.exec(
                select(BotProfilePluginPolicy).where(
                    BotProfilePluginPolicy.bot_profile_id == profile_id,
                    BotProfilePluginPolicy.plugin_id == normalized,
                )
            ).first()
            if policy is None:
                return False
            session.delete(policy)
            profile.policy_revision += 1
            profile.updated_at = datetime.now()
            session.add(profile)
            return True

    def list_memory_rules(self, profile_id: str) -> list[BotProfileMemoryRule]:
        with get_db_session() as session:
            return list(
                session.exec(
                    select(BotProfileMemoryRule)
                    .where(BotProfileMemoryRule.bot_profile_id == profile_id)
                    .order_by(BotProfileMemoryRule.target_space_id)
                ).all()
            )

    def set_memory_rule(
        self,
        profile_id: str,
        target_space_id: str,
        can_read: bool,
        filters: Optional[Mapping[str, Any]] = None,
    ) -> BotProfileMemoryRule:
        """保存 Profile 对目标记忆空间的读取规则并递增策略版本。"""

        normalized_space = target_space_id.strip()
        if not normalized_space:
            raise ValueError("target_space_id 不能为空")
        filters_json = json.dumps(dict(filters or {}), ensure_ascii=False, sort_keys=True)
        with get_db_session() as session:
            profile = session.get(BotProfile, profile_id)
            if profile is None:
                raise ValueError(f"BotProfile 不存在: {profile_id}")
            rule = session.exec(
                select(BotProfileMemoryRule).where(
                    BotProfileMemoryRule.bot_profile_id == profile_id,
                    BotProfileMemoryRule.target_space_id == normalized_space,
                )
            ).first()
            if rule is None:
                rule = BotProfileMemoryRule(
                    bot_profile_id=profile_id,
                    target_space_id=normalized_space,
                    can_read=can_read,
                    filters_json=filters_json,
                )
            else:
                rule.can_read = can_read
                rule.filters_json = filters_json
            profile.policy_revision += 1
            profile.updated_at = datetime.now()
            session.add(profile)
            session.add(rule)
            return rule

    def remove_memory_rule(self, profile_id: str, target_space_id: str) -> bool:
        with get_db_session() as session:
            profile = session.get(BotProfile, profile_id)
            if profile is None:
                raise ValueError(f"BotProfile 不存在: {profile_id}")
            rule = session.exec(
                select(BotProfileMemoryRule).where(
                    BotProfileMemoryRule.bot_profile_id == profile_id,
                    BotProfileMemoryRule.target_space_id == target_space_id,
                )
            ).first()
            if rule is None:
                return False
            session.delete(rule)
            profile.policy_revision += 1
            profile.updated_at = datetime.now()
            session.add(profile)
            return True

    def set_parent(self, profile_id: str, parent_profile_id: Optional[str]) -> BotProfile:
        self.validate_parent(profile_id, parent_profile_id)
        with get_db_session() as session:
            profile = session.get(BotProfile, profile_id)
            if profile is None:
                raise ValueError(f"BotProfile 不存在: {profile_id}")
            if profile.profile_type == "kami" and parent_profile_id:
                raise ValueError("Kami BotProfile 必须完全独立")
            profile.parent_profile_id = parent_profile_id
            profile.policy_revision += 1
            profile.updated_at = datetime.now()
            session.add(profile)
            return profile

    def set_tool_policy(self, profile_id: str, component_name: str, effect: str) -> BotProfileToolPolicy:
        normalized = component_name.strip()
        if "." not in normalized or normalized.startswith(".") or normalized.endswith("."):
            raise ValueError("component_name 必须使用 plugin_id.component_name 完整名")
        if effect not in {"allow", "deny"}:
            raise ValueError("工具策略 effect 只能是 allow/deny")
        with get_db_session() as session:
            profile = session.get(BotProfile, profile_id)
            if profile is None:
                raise ValueError(f"BotProfile 不存在: {profile_id}")
            policy = session.exec(
                select(BotProfileToolPolicy).where(
                    BotProfileToolPolicy.bot_profile_id == profile_id,
                    BotProfileToolPolicy.component_name == normalized,
                )
            ).first()
            if policy is None:
                policy = BotProfileToolPolicy(bot_profile_id=profile_id, component_name=normalized, effect=effect)
            else:
                policy.effect = effect
            profile.policy_revision += 1
            profile.updated_at = datetime.now()
            session.add(profile)
            session.add(policy)
            return policy

    def set_plugin_policy(
        self,
        profile_id: str,
        plugin_id: str,
        effect: str,
        *,
        overrides: Optional[Mapping[str, Any]] = None,
        config_schema: Optional[Mapping[str, Any]] = None,
    ) -> BotProfilePluginPolicy:
        """保存插件策略，并在同一事务递增 Profile 策略版本。"""

        normalized_plugin_id = plugin_id.strip()
        if not normalized_plugin_id:
            raise ValueError("plugin_id 不能为空")
        if effect not in {"allow", "deny", "inherit"}:
            raise ValueError("插件策略 effect 只能是 allow/deny/inherit")
        normalized_overrides = dict(overrides or {})
        if normalized_overrides:
            if config_schema is None:
                raise ValueError("保存插件配置覆盖时必须提供配置 Schema")
            from src.plugin_runtime.config_overlay import validate_and_collect_override_paths

            validate_and_collect_override_paths(config_schema, normalized_overrides)
        overrides_json = json.dumps(normalized_overrides, ensure_ascii=False, sort_keys=True)

        with get_db_session() as session:
            profile = session.get(BotProfile, profile_id)
            if profile is None:
                raise ValueError(f"BotProfile 不存在: {profile_id}")
            policy = session.exec(
                select(BotProfilePluginPolicy).where(
                    BotProfilePluginPolicy.bot_profile_id == profile_id,
                    BotProfilePluginPolicy.plugin_id == normalized_plugin_id,
                )
            ).first()
            if policy is None:
                policy = BotProfilePluginPolicy(
                    bot_profile_id=profile_id,
                    plugin_id=normalized_plugin_id,
                    effect=effect,
                    overrides_json=overrides_json,
                )
            else:
                policy.effect = effect
                policy.overrides_json = overrides_json
            profile.policy_revision += 1
            profile.updated_at = datetime.now()
            session.add(profile)
            session.add(policy)
            return policy

    def resolve_group_profile(
        self,
        *,
        current_workspace_id: str,
        workspace_id_or_name: str = "",
    ) -> tuple[Workspace, BotProfile]:
        """解析当前或指定 Workspace 的分组 BotProfile。"""

        normalized_target = workspace_id_or_name.strip()
        with get_db_session() as session:
            if normalized_target:
                workspace = session.get(Workspace, normalized_target)
                if workspace is None:
                    workspace = session.exec(select(Workspace).where(Workspace.name == normalized_target)).first()
            else:
                workspace = session.get(Workspace, current_workspace_id)
            if workspace is None or not workspace.enabled:
                raise ValueError("指定的子系统不存在或已禁用")
            profile = session.get(BotProfile, workspace.bot_profile_id or "")
            if profile is None or not profile.enabled:
                raise ValueError("指定子系统没有可用的分组 Bot")
            if profile.profile_type != "group":
                raise ValueError("指定子系统没有独立的分组 Bot")
            return workspace, profile

    def get_route_state(self, session_id: str) -> Optional[BotRouteState]:
        with get_db_session() as session:
            return session.get(BotRouteState, session_id)

    def list_route_states(self) -> list[BotRouteState]:
        """列出全部会话级 Bot 路由，供管理界面展示。"""

        with get_db_session() as session:
            return list(session.exec(select(BotRouteState).order_by(BotRouteState.session_id)).all())

    def reset_route_state(self, session_id: str) -> bool:
        """恢复 Workspace 默认 Bot 路由。"""

        with get_db_session() as session:
            state = session.get(BotRouteState, session_id)
            if state is None:
                return False
            session.delete(state)
            return True

    def set_route_state(
        self,
        session_id: str,
        profile_id: str,
        route_mode: str,
        changed_by_person_id: str,
    ) -> BotRouteState:
        """设置普通会话的活动 BotProfile；Kami 激活由后续专用安全流程处理。"""
        if route_mode not in {"public", "group", "specific"}:
            raise ValueError("route_mode 只能是 public/group/specific")
        with get_db_session() as session:
            profile = session.get(BotProfile, profile_id)
            if profile is None or not profile.enabled:
                raise ValueError(f"BotProfile 不存在或已禁用: {profile_id}")
            if profile.profile_type == "kami":
                raise ValueError("普通路由不能直接激活 Kami BotProfile")
            state = session.get(BotRouteState, session_id)
            if state is None:
                state = BotRouteState(
                    session_id=session_id,
                    active_bot_profile_id=profile.id,
                    route_mode=route_mode,
                    changed_by_person_id=changed_by_person_id,
                )
            else:
                state.active_bot_profile_id = profile.id
                state.route_mode = route_mode
                state.changed_by_person_id = changed_by_person_id
                state.policy_revision += 1
                state.updated_at = datetime.now()
            session.add(state)
            return state

    def resolve_for_session(self, session_id: str, workspace: Workspace) -> BotProfileContext:
        with get_db_session() as session:
            route = session.get(BotRouteState, session_id)
            profile_id = route.active_bot_profile_id if route is not None else workspace.bot_profile_id
        return self.resolve_profile_context(profile_id or PUBLIC_BOT_PROFILE_ID)


bot_profile_service = BotProfileService()
