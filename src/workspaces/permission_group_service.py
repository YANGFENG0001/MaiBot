"""用户记忆权限组的持久化、校验与策略版本维护。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Optional

import json
import uuid

from sqlmodel import col, delete, select

from src.common.database.database import get_db_session
from src.common.database.database_model import (
    KamiSessionState,
    MemoryPermissionGroup,
    MemoryPermissionGroupCapability,
    MemoryPermissionGroupContext,
    MemoryPermissionGroupMember,
    MemoryPermissionRule,
    PermissionGroupBotRule,
)

from .access_resolver import NORMAL_CAPABILITIES, AccessResolver

SCOPE_TYPES = ("global", "workspace", "session", "channel")
SPACE_SELECTORS = ("current", "public", "specific", "all_normal")
PARTITION_TYPES = ("any", "shared", "person", "conversation")
PARTITION_SELECTORS = ("any", "self", "current", "specific")
BOT_SELECTORS = ("public", "current_group", "kami", "specific")

# “管理者权限模式”预设只授予强制读取与 Kami 切换，绝不自动授予权限组管理能力。
MANAGER_PRESET_CAPABILITIES = ("bot.switch.kami", "memory.read.force_all")


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def _load_json_list(raw: str) -> list[Any]:
    try:
        loaded = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    return loaded if isinstance(loaded, list) else []


def _dump_json_list(values: Optional[Iterable[Any]]) -> str:
    return json.dumps(list(values or []), ensure_ascii=False, sort_keys=True)


class PermissionGroupService:
    """权限组及其子资源的统一入口；所有子表写入都在同一事务递增父级 revision。"""

    # ---- 权限组本体 ----

    def list_groups(self) -> list[MemoryPermissionGroup]:
        with get_db_session() as session:
            return list(
                session.exec(
                    select(MemoryPermissionGroup).order_by(
                        col(MemoryPermissionGroup.priority).desc(),
                        MemoryPermissionGroup.name,
                    )
                ).all()
            )

    def get_group(self, group_id: str) -> Optional[MemoryPermissionGroup]:
        with get_db_session() as session:
            return session.get(MemoryPermissionGroup, group_id)

    def create_group(
        self,
        *,
        name: str,
        description: str = "",
        priority: int = 0,
        memory_scope_mode: str = "inherit",
        is_manager_mode: bool = False,
        enabled: bool = True,
    ) -> MemoryPermissionGroup:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("权限组名称不能为空")
        self._validate_scope_mode(memory_scope_mode)
        with get_db_session() as session:
            existing = session.exec(
                select(MemoryPermissionGroup).where(MemoryPermissionGroup.name == normalized_name)
            ).first()
            if existing is not None:
                raise ValueError(f"权限组名称已存在: {normalized_name}")
            group = MemoryPermissionGroup(
                id=_new_id("perm-group"),
                name=normalized_name,
                description=description,
                priority=priority,
                memory_scope_mode=memory_scope_mode,
                is_manager_mode=is_manager_mode,
                enabled=enabled,
            )
            session.add(group)
            if is_manager_mode:
                self._apply_manager_preset(session, group)
            return group

    def update_group(self, group_id: str, **changes: Any) -> MemoryPermissionGroup:
        allowed = {"name", "description", "priority", "memory_scope_mode", "is_manager_mode", "enabled"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"不支持的权限组字段: {', '.join(sorted(unknown))}")
        if "memory_scope_mode" in changes:
            self._validate_scope_mode(changes["memory_scope_mode"])
        with get_db_session() as session:
            group = session.get(MemoryPermissionGroup, group_id)
            if group is None:
                raise ValueError(f"权限组不存在: {group_id}")
            for key, value in changes.items():
                setattr(group, key, value)
            if changes.get("is_manager_mode"):
                self._apply_manager_preset(session, group)
            group.policy_revision += 1
            group.updated_at = datetime.now()
            session.add(group)
            return group

    def delete_group(self, group_id: str) -> bool:
        """删除权限组及其全部子资源，避免留下可命中的旧缓存。

        子表必须用独立 DELETE 语句逐张清空：这些模型没有配置 ``relationship()``，
        ORM 的 ``session.delete()`` 无法推导父子落库顺序，实测会在同一事务里先删父表
        并直接命中 ``FOREIGN KEY constraint failed``。

        ``kami_session_states.permission_group_id`` 也指向权限组，且该列非空，因此
        删除权限组时必须同时终止引用它的 Kami 会话（权限组是 Kami 的授权来源，
        组没了会话就不该继续存在），否则同样会外键失败。
        """

        with get_db_session() as session:
            group = session.get(MemoryPermissionGroup, group_id)
            if group is None:
                return False
            for model in (
                MemoryPermissionGroupMember,
                MemoryPermissionGroupContext,
                MemoryPermissionGroupCapability,
                MemoryPermissionRule,
                PermissionGroupBotRule,
            ):
                session.exec(delete(model).where(col(model.permission_group_id) == group_id))
            session.exec(
                delete(KamiSessionState).where(col(KamiSessionState.permission_group_id) == group_id)
            )
            session.exec(delete(MemoryPermissionGroup).where(col(MemoryPermissionGroup.id) == group_id))
            return True

    @staticmethod
    def _validate_scope_mode(mode: str) -> None:
        if mode not in {"inherit", "override"}:
            raise ValueError("memory_scope_mode 只能是 inherit/override")

    @staticmethod
    def _apply_manager_preset(session, group: MemoryPermissionGroup) -> None:
        """管理者预设：只保留强制读取与 Kami 切换，不授予 kami.manage_permissions。"""

        group.memory_scope_mode = "override"
        for row in session.exec(
            select(MemoryPermissionGroupCapability).where(
                MemoryPermissionGroupCapability.permission_group_id == group.id
            )
        ).all():
            session.delete(row)
        for capability in MANAGER_PRESET_CAPABILITIES:
            session.add(
                MemoryPermissionGroupCapability(
                    permission_group_id=group.id,
                    capability=capability,
                    enabled=True,
                )
            )

    # ---- 成员 ----

    def list_members(self, group_id: str) -> list[MemoryPermissionGroupMember]:
        with get_db_session() as session:
            return list(
                session.exec(
                    select(MemoryPermissionGroupMember)
                    .where(MemoryPermissionGroupMember.permission_group_id == group_id)
                    .order_by(MemoryPermissionGroupMember.person_id)
                ).all()
            )

    def add_member(self, group_id: str, person_id: str) -> MemoryPermissionGroupMember:
        normalized = person_id.strip()
        if not normalized:
            raise ValueError("person_id 不能为空")
        with get_db_session() as session:
            group = self._bump(session, group_id)
            existing = session.exec(
                select(MemoryPermissionGroupMember).where(
                    MemoryPermissionGroupMember.permission_group_id == group_id,
                    MemoryPermissionGroupMember.person_id == normalized,
                )
            ).first()
            if existing is not None:
                return existing
            member = MemoryPermissionGroupMember(permission_group_id=group.id, person_id=normalized)
            session.add(member)
            return member

    def remove_member(self, group_id: str, person_id: str) -> bool:
        with get_db_session() as session:
            member = session.exec(
                select(MemoryPermissionGroupMember).where(
                    MemoryPermissionGroupMember.permission_group_id == group_id,
                    MemoryPermissionGroupMember.person_id == person_id,
                )
            ).first()
            if member is None:
                return False
            self._bump(session, group_id)
            session.delete(member)
            return True

    # ---- 上下文 ----

    def list_contexts(self, group_id: str) -> list[MemoryPermissionGroupContext]:
        with get_db_session() as session:
            return list(
                session.exec(
                    select(MemoryPermissionGroupContext)
                    .where(MemoryPermissionGroupContext.permission_group_id == group_id)
                    .order_by(MemoryPermissionGroupContext.id)
                ).all()
            )

    def add_context(
        self,
        group_id: str,
        *,
        scope_type: str,
        workspace_id: Optional[str] = None,
        session_id: Optional[str] = None,
        channel_type: Optional[str] = None,
        allow_group_disclosure: bool = False,
        enabled: bool = True,
    ) -> MemoryPermissionGroupContext:
        if scope_type not in SCOPE_TYPES:
            raise ValueError(f"不支持的权限组作用域: {scope_type}")
        context = MemoryPermissionGroupContext(
            permission_group_id=group_id,
            scope_type=scope_type,
            workspace_id=workspace_id or None,
            session_id=session_id or None,
            channel_type=channel_type or None,
            allow_group_disclosure=allow_group_disclosure,
            enabled=enabled,
        )
        # 复用运行期校验，保证管理端无法写入 AccessResolver 会拒绝的上下文。
        AccessResolver._validate_context(context)
        with get_db_session() as session:
            self._bump(session, group_id)
            context.permission_group_id = group_id
            session.add(context)
            return context

    def remove_context(self, group_id: str, context_id: int) -> bool:
        with get_db_session() as session:
            row = session.get(MemoryPermissionGroupContext, context_id)
            if row is None or row.permission_group_id != group_id:
                return False
            self._bump(session, group_id)
            session.delete(row)
            return True

    # ---- 能力 ----

    def list_capabilities(self, group_id: str) -> list[MemoryPermissionGroupCapability]:
        with get_db_session() as session:
            return list(
                session.exec(
                    select(MemoryPermissionGroupCapability)
                    .where(MemoryPermissionGroupCapability.permission_group_id == group_id)
                    .order_by(MemoryPermissionGroupCapability.capability)
                ).all()
            )

    def set_capability(self, group_id: str, capability: str, enabled: bool = True) -> MemoryPermissionGroupCapability:
        if capability not in NORMAL_CAPABILITIES:
            raise ValueError(f"不支持的能力: {capability}")
        with get_db_session() as session:
            self._bump(session, group_id)
            row = session.exec(
                select(MemoryPermissionGroupCapability).where(
                    MemoryPermissionGroupCapability.permission_group_id == group_id,
                    MemoryPermissionGroupCapability.capability == capability,
                )
            ).first()
            if row is None:
                row = MemoryPermissionGroupCapability(
                    permission_group_id=group_id,
                    capability=capability,
                    enabled=enabled,
                )
            else:
                row.enabled = enabled
            session.add(row)
            return row

    def remove_capability(self, group_id: str, capability: str) -> bool:
        with get_db_session() as session:
            row = session.exec(
                select(MemoryPermissionGroupCapability).where(
                    MemoryPermissionGroupCapability.permission_group_id == group_id,
                    MemoryPermissionGroupCapability.capability == capability,
                )
            ).first()
            if row is None:
                return False
            self._bump(session, group_id)
            session.delete(row)
            return True

    # ---- 记忆规则 ----

    def list_rules(self, group_id: str) -> list[MemoryPermissionRule]:
        with get_db_session() as session:
            return list(
                session.exec(
                    select(MemoryPermissionRule)
                    .where(MemoryPermissionRule.permission_group_id == group_id)
                    .order_by(col(MemoryPermissionRule.priority).desc(), MemoryPermissionRule.id)
                ).all()
            )

    def add_rule(
        self,
        group_id: str,
        *,
        effect: str = "allow",
        space_selector: str = "current",
        memory_space_id: Optional[str] = None,
        partition_type: str = "any",
        partition_selector: str = "any",
        partition_key: Optional[str] = None,
        memory_types: Optional[Iterable[str]] = None,
        tags: Optional[Iterable[str]] = None,
        sensitivity_max: Optional[int] = None,
        time_start: Optional[datetime] = None,
        time_end: Optional[datetime] = None,
        priority: int = 0,
        enabled: bool = True,
    ) -> MemoryPermissionRule:
        rule = MemoryPermissionRule(
            permission_group_id=group_id,
            effect=effect,
            space_selector=space_selector,
            memory_space_id=memory_space_id or None,
            partition_type=partition_type,
            partition_selector=partition_selector,
            partition_key=partition_key or None,
            memory_types_json=_dump_json_list(memory_types),
            tags_json=_dump_json_list(tags),
            sensitivity_max=sensitivity_max,
            time_start=time_start,
            time_end=time_end,
            priority=priority,
            enabled=enabled,
        )
        self._validate_rule(rule)
        with get_db_session() as session:
            group = self._bump(session, group_id)
            rule.permission_group_id = group.id
            session.add(rule)
            session.flush()
            # 同一权限组内不允许出现结果不确定的同级同优先级规则。
            AccessResolver.validate_rule_conflicts(self._rules_in_session(session, group_id))
            return rule

    def update_rule(self, group_id: str, rule_id: int, **changes: Any) -> MemoryPermissionRule:
        allowed = {
            "effect",
            "space_selector",
            "memory_space_id",
            "partition_type",
            "partition_selector",
            "partition_key",
            "sensitivity_max",
            "time_start",
            "time_end",
            "priority",
            "enabled",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"不支持的规则字段: {', '.join(sorted(unknown))}")
        with get_db_session() as session:
            rule = session.get(MemoryPermissionRule, rule_id)
            if rule is None or rule.permission_group_id != group_id:
                raise ValueError("记忆权限规则不存在")
            for key, value in changes.items():
                setattr(rule, key, value)
            self._validate_rule(rule)
            self._bump(session, group_id)
            session.add(rule)
            session.flush()
            AccessResolver.validate_rule_conflicts(self._rules_in_session(session, group_id))
            return rule

    def delete_rule(self, group_id: str, rule_id: int) -> bool:
        with get_db_session() as session:
            rule = session.get(MemoryPermissionRule, rule_id)
            if rule is None or rule.permission_group_id != group_id:
                return False
            self._bump(session, group_id)
            session.delete(rule)
            return True

    @staticmethod
    def _validate_rule(rule: MemoryPermissionRule) -> None:
        if rule.effect not in {"allow", "deny"}:
            raise ValueError("规则 effect 只能是 allow/deny")
        if rule.space_selector not in SPACE_SELECTORS:
            raise ValueError(f"不支持的记忆空间选择器: {rule.space_selector}")
        if rule.partition_type not in PARTITION_TYPES:
            raise ValueError(f"不支持的分区类型: {rule.partition_type}")
        if rule.partition_selector not in PARTITION_SELECTORS:
            raise ValueError(f"不支持的分区选择器: {rule.partition_selector}")
        if rule.space_selector == "specific" and not rule.memory_space_id:
            raise ValueError("specific 空间选择器必须指定 memory_space_id")
        if rule.partition_selector == "specific" and not rule.partition_key:
            raise ValueError("specific 分区选择器必须指定 partition_key")
        if rule.time_start and rule.time_end and rule.time_start > rule.time_end:
            raise ValueError("规则生效起始时间不能晚于结束时间")

    @staticmethod
    def _rules_in_session(session, group_id: str) -> tuple[MemoryPermissionRule, ...]:
        return tuple(
            session.exec(
                select(MemoryPermissionRule).where(
                    MemoryPermissionRule.permission_group_id == group_id,
                    MemoryPermissionRule.enabled == True,  # noqa: E712
                )
            ).all()
        )

    # ---- Bot 规则 ----

    def list_bot_rules(self, group_id: str) -> list[PermissionGroupBotRule]:
        with get_db_session() as session:
            return list(
                session.exec(
                    select(PermissionGroupBotRule)
                    .where(PermissionGroupBotRule.permission_group_id == group_id)
                    .order_by(PermissionGroupBotRule.id)
                ).all()
            )

    def set_bot_rule(
        self,
        group_id: str,
        *,
        effect: str,
        bot_selector: str,
        bot_profile_id: Optional[str] = None,
    ) -> PermissionGroupBotRule:
        if effect not in {"allow", "deny"}:
            raise ValueError("Bot 规则 effect 只能是 allow/deny")
        if bot_selector not in BOT_SELECTORS:
            raise ValueError(f"不支持的 Bot 选择器: {bot_selector}")
        if bot_selector == "specific" and not bot_profile_id:
            raise ValueError("specific Bot 选择器必须指定 bot_profile_id")
        with get_db_session() as session:
            self._bump(session, group_id)
            rule = session.exec(
                select(PermissionGroupBotRule).where(
                    PermissionGroupBotRule.permission_group_id == group_id,
                    PermissionGroupBotRule.bot_selector == bot_selector,
                    PermissionGroupBotRule.bot_profile_id == (bot_profile_id or None),
                )
            ).first()
            if rule is None:
                rule = PermissionGroupBotRule(
                    permission_group_id=group_id,
                    effect=effect,
                    bot_selector=bot_selector,
                    bot_profile_id=bot_profile_id or None,
                )
            else:
                rule.effect = effect
            session.add(rule)
            return rule

    def remove_bot_rule(self, group_id: str, bot_rule_id: int) -> bool:
        with get_db_session() as session:
            rule = session.get(PermissionGroupBotRule, bot_rule_id)
            if rule is None or rule.permission_group_id != group_id:
                return False
            self._bump(session, group_id)
            session.delete(rule)
            return True

    # ---- 内部工具 ----

    @staticmethod
    def _bump(session, group_id: str) -> MemoryPermissionGroup:
        """同一事务内递增父级策略版本，保证下一条消息即可看到新策略。"""

        group = session.get(MemoryPermissionGroup, group_id)
        if group is None:
            raise ValueError(f"权限组不存在: {group_id}")
        group.policy_revision += 1
        group.updated_at = datetime.now()
        session.add(group)
        return group

    @staticmethod
    def rule_view(rule: MemoryPermissionRule) -> dict[str, Any]:
        return {
            "id": rule.id,
            "effect": rule.effect,
            "space_selector": rule.space_selector,
            "memory_space_id": rule.memory_space_id,
            "partition_type": rule.partition_type,
            "partition_selector": rule.partition_selector,
            "partition_key": rule.partition_key,
            "memory_types": _load_json_list(rule.memory_types_json),
            "tags": _load_json_list(rule.tags_json),
            "sensitivity_max": rule.sensitivity_max,
            "time_start": rule.time_start,
            "time_end": rule.time_end,
            "priority": rule.priority,
            "enabled": rule.enabled,
        }


permission_group_service = PermissionGroupService()
