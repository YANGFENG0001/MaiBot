"""Workspace 请求级记忆访问解析器。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

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
    PermissionGroupBotRule,
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
    policy_revision: int = 0

    def has_capability(self, capability: str) -> bool:
        return capability in self.capabilities


class AccessResolver:
    """把权限组、双向 ACL、分区范围和受众安全合并为一个不可变决策。"""

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
        if group is not None:
            self._validate_bot_rules(session, group.id, bot_profile_id, bot_profile_type)

        if bot_profile_type == "kami":
            return self._resolve_kami(session, group, context, capabilities, audience_type)

        rules = self._rules(session, group.id if group else "")
        self.validate_rule_conflicts(rules)
        spaces = self._resolve_spaces(session, group, rules, home_space_id, capabilities)
        spaces = self._apply_bidirectional_acl(session, spaces, home_space_id, bot_profile_id)
        partitions = self._partitions(session, spaces, security_domain="normal")
        partitions = self._resolve_partitions(
            session,
            group=group,
            rules=rules,
            partitions=partitions,
            home_space_id=home_space_id,
            session_id=session_id,
            person_id=person_id,
            capabilities=capabilities,
            audience_type=audience_type,
            allow_group_disclosure=bool(context and context.allow_group_disclosure),
        )
        writable_candidates = {
            build_partition_id(home_space_id, "shared", "shared", "normal"),
            build_partition_id(home_space_id, "person", person_id, "normal"),
            build_partition_id(home_space_id, "conversation", session_id, "normal"),
        }
        writable = tuple(item for item in partitions if item in writable_candidates)
        return MemoryAccessDecision(
            permission_group_id=group.id if group else "",
            access_mode="normal",
            security_domain="normal",
            readable_space_ids=spaces,
            readable_partition_ids=partitions,
            writable_partition_ids=writable,
            allow_group_disclosure=bool(context and context.allow_group_disclosure),
            capabilities=capabilities,
            policy_revision=group.policy_revision if group else 0,
        )

    @staticmethod
    def validate_rule_conflicts(rules: Iterable[MemoryPermissionRule]) -> None:
        """拒绝同一 specificity/priority 下结果不确定的 allow/deny 规则。"""

        seen: dict[tuple[tuple[str, str, str, str], int], str] = {}
        for rule in rules:
            if not rule.enabled:
                continue
            if rule.effect not in {"allow", "deny"}:
                raise ValueError(f"不支持的权限规则效果: {rule.effect}")
            specificity = (
                rule.space_selector,
                rule.partition_type,
                rule.partition_selector,
                rule.partition_key or rule.memory_space_id or "",
            )
            key = (specificity, rule.priority)
            previous = seen.get(key)
            if previous is not None and previous != rule.effect:
                raise ValueError("权限规则存在同级同优先级 allow/deny 冲突")
            seen[key] = rule.effect

    def _resolve_kami(
        self,
        session: Session,
        group: Optional[MemoryPermissionGroup],
        context: Optional[MemoryPermissionGroupContext],
        capabilities: frozenset[str],
        audience_type: str,
    ) -> MemoryAccessDecision:
        if (
            group is None
            or not group.is_manager_mode
            or "bot.switch.kami" not in capabilities
            or "memory.read.force_all" not in capabilities
            or (audience_type == "group" and "kami.use_in_group" not in capabilities)
        ):
            raise PermissionError("当前请求不具备 Kami 记忆访问权限")
        spaces = tuple(
            str(row.id)
            for row in session.exec(select(MemorySpace).where(MemorySpace.enabled == True)).all()  # noqa: E712
        )
        partitions = self._partitions(session, spaces, security_domain=None)
        kami_writable = tuple(
            item.id
            for item in session.exec(
                select(MemoryPartition).where(
                    MemoryPartition.memory_space_id == "memory-space-kami",
                    MemoryPartition.security_domain == "kami",
                    MemoryPartition.enabled == True,  # noqa: E712
                )
            ).all()
        )
        return MemoryAccessDecision(
            group.id,
            "kami",
            "kami",
            spaces,
            partitions,
            kami_writable,
            bool(context and context.allow_group_disclosure),
            capabilities,
            group.policy_revision,
        )

    @staticmethod
    def _validate_context(context: MemoryPermissionGroupContext) -> None:
        if context.scope_type == "global":
            if context.workspace_id or context.session_id or context.channel_type:
                raise ValueError("global 权限组上下文不能指定 workspace/session/channel")
            return
        if context.scope_type == "workspace":
            if not context.workspace_id or context.session_id or context.channel_type:
                raise ValueError("workspace 权限组上下文必须且只能指定 workspace_id")
            return
        if context.scope_type == "session":
            if not context.session_id or context.workspace_id or context.channel_type:
                raise ValueError("session 权限组上下文必须且只能指定 session_id")
            return
        if context.scope_type == "channel":
            if context.channel_type not in {"private", "group"} or context.workspace_id or context.session_id:
                raise ValueError("channel 权限组上下文必须且只能指定 private/group")
            return
        raise ValueError(f"不支持的权限组作用域: {context.scope_type}")

    @staticmethod
    def _select_group(session: Session, person_id: str, session_id: str, workspace_id: str, channel: str):
        memberships = session.exec(
            select(MemoryPermissionGroupMember, MemoryPermissionGroup)
            .join(MemoryPermissionGroup, MemoryPermissionGroup.id == MemoryPermissionGroupMember.permission_group_id)
            .where(
                MemoryPermissionGroupMember.person_id == person_id,
                MemoryPermissionGroup.enabled == True,  # noqa: E712
            )
            .order_by(col(MemoryPermissionGroup.priority).desc(), MemoryPermissionGroup.id)
        ).all()
        specificity = {"session": 4, "workspace": 3, "channel": 2, "global": 1}
        ranked = []
        for _member, group in memberships:
            if group.memory_scope_mode not in {"inherit", "override"}:
                raise ValueError(f"不支持的权限组记忆范围模式: {group.memory_scope_mode}")
            contexts = session.exec(
                select(MemoryPermissionGroupContext).where(
                    MemoryPermissionGroupContext.permission_group_id == group.id,
                    MemoryPermissionGroupContext.enabled == True,  # noqa: E712
                )
            ).all()
            for item in contexts:
                AccessResolver._validate_context(item)
                if item.scope_type == "session" and item.session_id != session_id:
                    continue
                if item.scope_type == "workspace" and item.workspace_id != workspace_id:
                    continue
                if item.scope_type == "channel" and item.channel_type != channel:
                    continue
                ranked.append((specificity[item.scope_type], group.priority, group.id, group, item))
        if not ranked:
            return None, None
        ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
        best = ranked[0]
        same_rank = [item for item in ranked if item[:2] == best[:2]]
        if any(item[2] != best[2] for item in same_rank[1:]):
            raise ValueError("权限组存在同级同优先级冲突，拒绝扩大记忆权限")
        if any(
            item[2] == best[2] and item[4].allow_group_disclosure != best[4].allow_group_disclosure
            for item in same_rank[1:]
        ):
            raise ValueError("权限组存在同级上下文披露策略冲突")
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
    def _rules(session: Session, group_id: str) -> tuple[MemoryPermissionRule, ...]:
        if not group_id:
            return ()
        return tuple(
            session.exec(
                select(MemoryPermissionRule)
                .where(
                    MemoryPermissionRule.permission_group_id == group_id,
                    MemoryPermissionRule.enabled == True,  # noqa: E712
                )
                .order_by(col(MemoryPermissionRule.priority).desc(), MemoryPermissionRule.id)
            ).all()
        )

    @staticmethod
    def _bot_rule_matches(rule: PermissionGroupBotRule, bot_profile_id: str, bot_profile_type: str) -> bool:
        if rule.bot_selector == "public":
            return bot_profile_type == "public"
        if rule.bot_selector == "current_group":
            return bot_profile_type == "group"
        if rule.bot_selector == "kami":
            return bot_profile_type == "kami"
        if rule.bot_selector == "specific":
            return bool(rule.bot_profile_id) and rule.bot_profile_id == bot_profile_id
        raise ValueError(f"不支持的 Bot 选择器: {rule.bot_selector}")

    def _validate_bot_rules(
        self,
        session: Session,
        group_id: str,
        bot_profile_id: str,
        bot_profile_type: str,
    ) -> None:
        rules = session.exec(
            select(PermissionGroupBotRule).where(PermissionGroupBotRule.permission_group_id == group_id)
        ).all()
        if any(rule.effect not in {"allow", "deny"} for rule in rules):
            raise ValueError("权限组 Bot 规则 effect 只能是 allow/deny")
        matching = [rule for rule in rules if self._bot_rule_matches(rule, bot_profile_id, bot_profile_type)]
        if any(rule.effect == "deny" for rule in matching):
            raise PermissionError("当前权限组显式拒绝此 BotProfile")
        if any(rule.effect == "allow" for rule in rules) and not any(rule.effect == "allow" for rule in matching):
            raise PermissionError("当前权限组未允许此 BotProfile")

    @staticmethod
    def _select_spaces_for_rule(
        session: Session,
        rule: MemoryPermissionRule,
        home_space_id: str,
        capabilities: frozenset[str],
    ) -> set[str]:
        if rule.space_selector == "current":
            return {home_space_id}
        if rule.space_selector == "public":
            return {"memory-space-public"}
        if rule.space_selector == "specific":
            return {rule.memory_space_id} if rule.memory_space_id else set()
        if rule.space_selector == "all_normal":
            if "memory.read.cross_space" not in capabilities:
                return {home_space_id}
            return {
                str(item.id)
                for item in session.exec(
                    select(MemorySpace).where(
                        MemorySpace.enabled == True,  # noqa: E712
                        MemorySpace.space_type != "kami",
                        MemorySpace.id != "memory-space-kami",
                    )
                ).all()
            }
        raise ValueError(f"不支持的记忆空间选择器: {rule.space_selector}")

    def _resolve_spaces(
        self,
        session: Session,
        group: Optional[MemoryPermissionGroup],
        rules: tuple[MemoryPermissionRule, ...],
        home_space_id: str,
        capabilities: frozenset[str],
    ) -> tuple[str, ...]:
        if group is None or group.memory_scope_mode == "inherit":
            candidates = {home_space_id}
        else:
            candidates = set()
            for rule in rules:
                if rule.effect == "allow":
                    candidates.update(self._select_spaces_for_rule(session, rule, home_space_id, capabilities))
        denied = set()
        for rule in rules:
            if rule.effect == "deny" and rule.partition_type == "any" and rule.partition_selector == "any":
                denied.update(self._select_spaces_for_rule(session, rule, home_space_id, capabilities))
        candidates.difference_update(denied)
        if "memory.read.cross_space" not in capabilities:
            candidates.intersection_update({home_space_id})
        enabled = {
            str(item.id)
            for item in session.exec(select(MemorySpace).where(MemorySpace.enabled == True)).all()  # noqa: E712
        }
        return tuple(sorted(candidates.intersection(enabled)))

    @staticmethod
    def _apply_bidirectional_acl(
        session: Session,
        spaces: Iterable[str],
        home_space_id: str,
        bot_profile_id: str,
    ) -> tuple[str, ...]:
        allowed = []
        for space_id in spaces:
            space = session.get(MemorySpace, space_id)
            if space is None or not space.enabled:
                continue
            outbound = session.exec(
                select(BotProfileMemoryRule).where(
                    BotProfileMemoryRule.bot_profile_id == bot_profile_id,
                    BotProfileMemoryRule.target_space_id == space_id,
                )
            ).first()
            inbound = session.exec(
                select(MemorySpaceBotRule).where(
                    MemorySpaceBotRule.memory_space_id == space_id,
                    MemorySpaceBotRule.bot_profile_id == bot_profile_id,
                )
            ).first()
            if outbound is not None and not outbound.can_read:
                continue
            if inbound is not None and not inbound.can_read:
                continue
            if space.strict_isolation and not (
                outbound is not None and outbound.can_read and inbound is not None and inbound.can_read
            ):
                continue
            if space_id != home_space_id and not (
                outbound is not None and outbound.can_read and inbound is not None and inbound.can_read
            ):
                continue
            allowed.append(space_id)
        return tuple(allowed)

    @staticmethod
    def _partitions(session: Session, spaces: Iterable[str], security_domain: Optional[str]) -> tuple[str, ...]:
        space_ids = tuple(spaces)
        if not space_ids:
            return ()
        query = select(MemoryPartition).where(
            col(MemoryPartition.memory_space_id).in_(space_ids),
            MemoryPartition.enabled == True,  # noqa: E712
        )
        if security_domain is not None:
            query = query.where(MemoryPartition.security_domain == security_domain)
        return tuple(item.id for item in session.exec(query).all())

    @staticmethod
    def _base_partition_ids(home_space_id: str, session_id: str, person_id: str) -> set[str]:
        return {
            build_partition_id(home_space_id, "shared", "shared", "normal"),
            build_partition_id(home_space_id, "person", person_id, "normal"),
            build_partition_id(home_space_id, "conversation", session_id, "normal"),
        }

    def _rule_applies_to_partition_space(
        self,
        session: Session,
        rule: MemoryPermissionRule,
        partition: MemoryPartition,
        home_space_id: str,
        capabilities: frozenset[str],
    ) -> bool:
        return partition.memory_space_id in self._select_spaces_for_rule(
            session,
            rule,
            home_space_id,
            capabilities,
        )

    @staticmethod
    def _partition_matches(
        partition: MemoryPartition,
        rule: MemoryPermissionRule,
        session_id: str,
        person_id: str,
    ) -> bool:
        if rule.partition_type != "any" and partition.partition_type != rule.partition_type:
            return False
        if rule.partition_selector == "any":
            return True
        if rule.partition_selector == "self":
            return partition.partition_type == "person" and partition.partition_key == person_id
        if rule.partition_selector == "current":
            if partition.partition_type == "person":
                return partition.partition_key == person_id
            if partition.partition_type == "conversation":
                return partition.partition_key == session_id
            return partition.partition_type == "shared" and partition.partition_key == "shared"
        if rule.partition_selector == "specific":
            return bool(rule.partition_key) and partition.partition_key == rule.partition_key
        raise ValueError(f"不支持的分区选择器: {rule.partition_selector}")

    @staticmethod
    def _partition_capability_allows(
        partition: MemoryPartition,
        session_id: str,
        person_id: str,
        capabilities: frozenset[str],
        audience_type: str,
        allow_group_disclosure: bool,
    ) -> bool:
        is_other_person = partition.partition_type == "person" and partition.partition_key != person_id
        is_other_conversation = (
            partition.partition_type == "conversation" and partition.partition_key != session_id
        )
        if is_other_person and "memory.read.other_person" not in capabilities:
            return False
        if is_other_conversation and "memory.read.other_conversation" not in capabilities:
            return False
        if audience_type == "group" and not allow_group_disclosure and (is_other_person or is_other_conversation):
            return False
        return True

    def _resolve_partitions(
        self,
        session: Session,
        *,
        group: Optional[MemoryPermissionGroup],
        rules: tuple[MemoryPermissionRule, ...],
        partitions: tuple[str, ...],
        home_space_id: str,
        session_id: str,
        person_id: str,
        capabilities: frozenset[str],
        audience_type: str,
        allow_group_disclosure: bool,
    ) -> tuple[str, ...]:
        if not partitions:
            return ()
        rows = session.exec(select(MemoryPartition).where(col(MemoryPartition.id).in_(partitions))).all()
        by_id = {item.id: item for item in rows}
        if group is None or group.memory_scope_mode == "inherit":
            allowed = self._base_partition_ids(home_space_id, session_id, person_id)
        else:
            allowed = {
                partition.id
                for rule in rules
                if rule.effect == "allow"
                for partition in rows
                if self._rule_applies_to_partition_space(
                    session, rule, partition, home_space_id, capabilities
                )
                and self._partition_matches(partition, rule, session_id, person_id)
            }
        denied = {
            partition.id
            for rule in rules
            if rule.effect == "deny"
            for partition in rows
            if self._rule_applies_to_partition_space(
                session, rule, partition, home_space_id, capabilities
            )
            and self._partition_matches(partition, rule, session_id, person_id)
        }
        return tuple(
            partition_id
            for partition_id in partitions
            if partition_id in allowed
            and partition_id not in denied
            and self._partition_capability_allows(
                by_id[partition_id],
                session_id,
                person_id,
                capabilities,
                audience_type,
                allow_group_disclosure,
            )
        )


access_resolver = AccessResolver()
