"""Workspace 记忆访问解析器。

该模块把权限组、Bot 出站许可、记忆空间入站许可和群聊披露安全策略
合并为一次请求的不可变访问决策；A-Memorix 仍是最终分区检索边界。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from sqlmodel import Session, col, select

from src.common.database.database_model import (
    BotProfileMemoryRule,
    MemoryPartition,
    MemoryPermissionGroup,
    MemoryPermissionGroupCapability,
    MemoryPermissionGroupContext,
    MemoryPermissionGroupMember,
    MemoryPermissionRule,
    MemorySpace,
    MemorySpaceBotRule,
)
from src.common.database.migrations.v43_to_v44 import build_partition_id

NORMAL_CAPABILITIES = frozenset(
    {
        "memory.read.other_person",
        "memory.read.other_conversation",
        "memory.read.cross_space",
        "bot.switch.public",
        "bot.switch.group",
        "bot.switch.kami",
        "memory.read.force_all",
        "kami.manage_permissions",
        "kami.use_in_group",
        "memory.transfer.import",
        "memory.transfer.publish",
    }
)


@dataclass(frozen=True, slots=True)
class MemoryAccessDecision:
    permission_group_id: str
    access_mode: str
    security_domain: str
    readable_space_ids: tuple[str, ...]
    readable_partition_ids: tuple[str, ...]
    writable_partition_ids: tuple[str, ...]
    allow_group_disclosure: bool = False
    capabilities: frozenset[str] = frozenset()
    policy_revision: int = 1

    def has_capability(self, capability: str) -> bool:
        return capability in self.capabilities


class AccessResolver:
    """计算请求级记忆访问范围，不读取或记录记忆正文。"""

    def resolve(
        self,
        session: Session,
        *,
        person_id: str,
        session_id: str,
        workspace_id: str,
        home_space_id: str,
        bot_profile_id: str,
        bot_profile_type: str = "public",
        audience_type: str = "private",
    ) -> MemoryAccessDecision:
        if audience_type not in {"private", "group"}:
            raise ValueError("audience_type 只能是 private/group")
        group, context = self._select_group(session, person_id, session_id, workspace_id, audience_type)
        capabilities = self._capabilities(session, group.id if group else "")
        if bot_profile_type == "kami":
            if not group or not group.is_manager_mode or "bot.switch.kami" not in capabilities or "memory.read.force_all" not in capabilities:
                raise PermissionError("当前请求不具备 Kami 记忆访问权限")
            spaces = tuple(str(row.id) for row in session.exec(select(MemorySpace).where(MemorySpace.enabled == True)).all())  # noqa: E712
            partitions = self._partitions(session, spaces, security_domain=None)
            return MemoryAccessDecision(group.id, "kami", "kami", spaces, partitions, partitions, bool(context and context.allow_group_disclosure), capabilities, group.policy_revision)

        spaces = self._spaces_for_rules(session, group, home_space_id, capabilities)
        spaces = self._apply_bidirectional_acl(session, spaces, bot_profile_id)
        if audience_type == "group" and not (context and context.allow_group_disclosure):
            spaces = (home_space_id,) if home_space_id in spaces else ()
        partitions = self._partitions(session, spaces, security_domain="normal")
        partitions = self._apply_partition_rules(session, group, partitions, home_space_id, session_id, person_id, capabilities, audience_type, context)
        writable = tuple(
            item for item in partitions
            if item in {
                build_partition_id(home_space_id, "shared", "shared", "normal"),
                build_partition_id(home_space_id, "person", person_id, "normal"),
                build_partition_id(home_space_id, "conversation", session_id, "normal"),
            }
        )
        return MemoryAccessDecision(
            group.id if group else "",
            "normal",
            "normal",
            tuple(dict.fromkeys(spaces)),
            tuple(dict.fromkeys(partitions)),
            tuple(dict.fromkeys(writable)),
            bool(context and context.allow_group_disclosure),
            capabilities,
            group.policy_revision if group else 1,
        )

    @staticmethod
    def validate_rule_conflicts(rules: Iterable[MemoryPermissionRule]) -> None:
        """拒绝同一 specificity/priority 下会产生不确定结果的 allow/deny 规则。"""

        seen: dict[tuple[tuple[str, str, str, str], int], str] = {}
        for rule in rules:
            if not rule.enabled:
                continue
            specificity = (rule.space_selector, rule.partition_type, rule.partition_selector, rule.partition_key or "")
            key = (specificity, rule.priority)
            previous = seen.get(key)
            if previous is not None and previous != rule.effect:
                raise ValueError("权限规则存在同级同优先级 allow/deny 冲突")
            seen[key] = rule.effect

    @staticmethod
    def _select_group(session: Session, person_id: str, session_id: str, workspace_id: str, channel: str):
        memberships = session.exec(
            select(MemoryPermissionGroupMember, MemoryPermissionGroup)
            .join(MemoryPermissionGroup, MemoryPermissionGroup.id == MemoryPermissionGroupMember.permission_group_id)
            .where(MemoryPermissionGroupMember.person_id == person_id, MemoryPermissionGroup.enabled == True)  # noqa: E712
            .order_by(col(MemoryPermissionGroup.priority).desc(), MemoryPermissionGroup.id)
        ).all()
        ranked = []
        specificity = {"session": 4, "workspace": 3, "channel": 2, "global": 1}
        for _member, group in memberships:
            contexts = session.exec(
                select(MemoryPermissionGroupContext).where(
                    MemoryPermissionGroupContext.permission_group_id == group.id,
                    MemoryPermissionGroupContext.enabled == True,  # noqa: E712
                )
            ).all()
            for item in contexts:
                if item.scope_type == "session" and item.session_id != session_id:
                    continue
                if item.scope_type == "workspace" and item.workspace_id != workspace_id:
                    continue
                if item.scope_type == "channel" and item.channel_type != channel:
                    continue
                if item.scope_type not in {"global", "workspace", "session", "channel"}:
                    raise ValueError(f"不支持的权限组作用域: {item.scope_type}")
                ranked.append((specificity[item.scope_type], group.priority, group.id, group, item))
        if not ranked:
            return None, None
        ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
        best = ranked[0]
        conflicts = [item for item in ranked if item[:2] == best[:2] and item[2] != best[2]]
        if conflicts:
            raise ValueError("权限组存在同级同优先级冲突，拒绝扩大记忆权限")
        return best[3], best[4]

    @staticmethod
    def _capabilities(session: Session, group_id: str) -> frozenset[str]:
        if not group_id:
            return frozenset()
        return frozenset(
            item.capability
            for item in session.exec(
                select(MemoryPermissionGroupCapability).where(
                    MemoryPermissionGroupCapability.permission_group_id == group_id,
                    MemoryPermissionGroupCapability.enabled == True,  # noqa: E712
                )
            ).all()
            if item.capability in NORMAL_CAPABILITIES
        )

    @staticmethod
    def _spaces_for_rules(session: Session, group: MemoryPermissionGroup | None, home_space_id: str, capabilities: frozenset[str]) -> tuple[str, ...]:
        if group is None:
            return (home_space_id,)
        rules = session.exec(select(MemoryPermissionRule).where(MemoryPermissionRule.permission_group_id == group.id, MemoryPermissionRule.enabled == True).order_by(col(MemoryPermissionRule.priority).desc(), MemoryPermissionRule.id)).all()  # noqa: E712
        if group.memory_scope_mode == "inherit" and not rules:
            return (home_space_id,)
        candidates = [home_space_id]
        if group.memory_scope_mode == "override":
            if any(rule.space_selector == "all_normal" for rule in rules) and "memory.read.cross_space" in capabilities:
                candidates = [str(item.id) for item in session.exec(select(MemorySpace).where(MemorySpace.enabled == True, MemorySpace.space_type != "kami")).all()]  # noqa: E712
            for rule in rules:
                if rule.effect != "allow":
                    continue
                if rule.space_selector == "public":
                    candidates.append("memory-space-public")
                elif rule.space_selector == "specific" and rule.memory_space_id:
                    candidates.append(rule.memory_space_id)
        denied = {rule.memory_space_id for rule in rules if rule.effect == "deny" and rule.memory_space_id}
        return tuple(item for item in dict.fromkeys(candidates) if item not in denied)

    @staticmethod
    def _apply_bidirectional_acl(session: Session, spaces: Iterable[str], bot_profile_id: str) -> tuple[str, ...]:
        allowed = []
        for space_id in spaces:
            outbound = session.exec(select(BotProfileMemoryRule).where(BotProfileMemoryRule.bot_profile_id == bot_profile_id, BotProfileMemoryRule.target_space_id == space_id)).first()
            inbound = session.exec(select(MemorySpaceBotRule).where(MemorySpaceBotRule.memory_space_id == space_id, MemorySpaceBotRule.bot_profile_id == bot_profile_id)).first()
            if outbound is not None and not outbound.can_read:
                continue
            if inbound is not None and not inbound.can_read:
                continue
            allowed.append(space_id)
        return tuple(allowed)

    @staticmethod
    def _partitions(session: Session, spaces: Iterable[str], security_domain: str | None) -> tuple[str, ...]:
        query = select(MemoryPartition).where(MemoryPartition.memory_space_id.in_(tuple(spaces)), MemoryPartition.enabled == True)  # noqa: E712
        if security_domain is not None:
            query = query.where(MemoryPartition.security_domain == security_domain)
        return tuple(item.id for item in session.exec(query).all())

    @staticmethod
    def _apply_partition_rules(session: Session, group: MemoryPermissionGroup | None, partitions: tuple[str, ...], home_space_id: str, session_id: str, person_id: str, capabilities: frozenset[str], audience_type: str, context) -> tuple[str, ...]:
        if group is None:
            return tuple(item for item in partitions if item in {build_partition_id(home_space_id, "shared", "shared", "normal"), build_partition_id(home_space_id, "person", person_id, "normal"), build_partition_id(home_space_id, "conversation", session_id, "normal")})
        rules = session.exec(select(MemoryPermissionRule).where(MemoryPermissionRule.permission_group_id == group.id, MemoryPermissionRule.enabled == True).order_by(col(MemoryPermissionRule.priority).desc(), MemoryPermissionRule.id)).all()  # noqa: E712
        if audience_type == "group" and not (context and context.allow_group_disclosure):
            return tuple(item for item in partitions if item in {build_partition_id(home_space_id, "shared", "shared", "normal"), build_partition_id(home_space_id, "person", person_id, "normal"), build_partition_id(home_space_id, "conversation", session_id, "normal")})
        if not rules:
            return tuple(partitions)
        allowed = set()
        denied = set()
        for rule in rules:
            for partition in session.exec(select(MemoryPartition).where(MemoryPartition.id.in_(partitions), MemoryPartition.partition_type == rule.partition_type if rule.partition_type != "any" else True)).all():
                if rule.partition_selector in {"any", "current"} or (rule.partition_selector == "self" and partition.partition_key == person_id) or (rule.partition_selector == "specific" and partition.partition_key == rule.partition_key):
                    (allowed if rule.effect == "allow" else denied).add(partition.id)
        if group.memory_scope_mode == "override":
            return tuple(item for item in partitions if item in allowed and item not in denied)
        return tuple(item for item in partitions if item not in denied and (not allowed or item in allowed))


access_resolver = AccessResolver()
