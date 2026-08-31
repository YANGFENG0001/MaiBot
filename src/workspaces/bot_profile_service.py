"""BotProfile 数据访问、继承与普通会话路由。"""

from datetime import datetime
from typing import Optional

from sqlmodel import select

from src.common.database.database import get_db_session
from src.common.database.database_model import (
    BotProfile,
    BotProfilePluginPolicy,
    BotProfileToolPolicy,
    BotRouteState,
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
            return BotProfileContext(profile.id, profile.profile_type, profile.home_memory_space_id, profile.policy_revision, profile.parent_profile_id or "")

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
            policy = session.exec(select(BotProfileToolPolicy).where(BotProfileToolPolicy.bot_profile_id == profile_id, BotProfileToolPolicy.component_name == normalized)).first()
            if policy is None:
                policy = BotProfileToolPolicy(bot_profile_id=profile_id, component_name=normalized, effect=effect)
            else:
                policy.effect = effect
            session.add(policy)
            return policy

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
