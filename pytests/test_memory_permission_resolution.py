"""I07-I16 / U01-U04：权限组匹配、显式授予、双向 ACL 与受众安全。"""

from __future__ import annotations

import pytest

from workspace_scope_test_support import (
    PUBLIC_SPACE_ID,
    allow_rule,
    database,
    ensure_profile,
    ensure_space,
    handshake,
    partition_id,
    permission_group,
    seed_space_partitions,
)

from src.common.database.database_model import MemoryPermissionGroup
from src.workspaces.access_resolver import AccessResolver


def _fixture():
    """A / B 两个空间 + 同一用户 Y，用于验证跨空间显式授权。"""

    factory = database()
    with factory() as session:
        ensure_space(session, PUBLIC_SPACE_ID, space_type="public")
        for space_id, session_id in (("space-a", "session-a"), ("space-b", "session-b")):
            seed_space_partitions(
                session, space_id, persons=("person-y", "person-x"), sessions=(session_id, "session-other")
            )
        ensure_profile(session, "bot-a", home_space_id="space-a")
        session.commit()
    return factory


def _resolve(session, *, person_id="person-y", session_id="session-a", audience_type="private"):
    return AccessResolver().resolve(
        session,
        person_id=person_id,
        session_id=session_id,
        workspace_id="workspace-a",
        home_space_id="space-a",
        bot_profile_id="bot-a",
        bot_profile_type="group",
        audience_type=audience_type,
    )


def test_i07_explicit_grant_reaches_only_the_named_partition() -> None:
    """I07：显式授予 A 读取 B.person:Y，仍不能读取 B.person:X 与 B conversation。"""

    factory = _fixture()
    with factory() as session:
        permission_group(
            session,
            "group-cross",
            persons=("person-y",),
            memory_scope_mode="override",
            capabilities=("memory.read.cross_space",),
        )
        allow_rule(
            session,
            "group-cross",
            space_selector="specific",
            memory_space_id="space-b",
            partition_type="person",
            partition_selector="specific",
            partition_key="person-y",
        )
        handshake(session, bot_profile_id="bot-a", space_id="space-b")
        session.commit()
    with factory() as session:
        decision = _resolve(session)
    # override 模式下范围由规则决定，这里只包含被显式授予的 space-b。
    assert decision.readable_space_ids == ("space-b",)
    assert partition_id("space-b", "person", "person-y") in decision.readable_partition_ids
    assert partition_id("space-b", "person", "person-x") not in decision.readable_partition_ids
    assert partition_id("space-b", "conversation", "session-b") not in decision.readable_partition_ids


def test_i08_explicit_grant_does_not_extend_to_sibling_conversations() -> None:
    """I08：只授予指定 session 分区，不扩展到 B 的其他会话。"""

    factory = _fixture()
    with factory() as session:
        permission_group(
            session,
            "group-conv",
            persons=("person-y",),
            memory_scope_mode="override",
            # 读取其它空间的 conversation 分区必须同时具备 other_conversation 能力。
            capabilities=("memory.read.cross_space", "memory.read.other_conversation"),
        )
        allow_rule(
            session,
            "group-conv",
            space_selector="specific",
            memory_space_id="space-b",
            partition_type="conversation",
            partition_selector="specific",
            partition_key="session-b",
        )
        handshake(session, bot_profile_id="bot-a", space_id="space-b")
        session.commit()
    with factory() as session:
        decision = _resolve(session)
    assert partition_id("space-b", "conversation", "session-b") in decision.readable_partition_ids
    assert partition_id("space-b", "conversation", "session-other") not in decision.readable_partition_ids


def test_i09_override_scope_replaces_default_scope() -> None:
    """I09：命中 override 权限组后按组规则解析，不与默认范围无条件并集。"""

    factory = _fixture()
    with factory() as session:
        permission_group(session, "group-narrow", persons=("person-y",), memory_scope_mode="override")
        allow_rule(
            session,
            "group-narrow",
            space_selector="current",
            partition_type="shared",
            partition_selector="current",
        )
        session.commit()
    with factory() as session:
        decision = _resolve(session)
    # 只保留规则显式允许的 shared，默认的 person/conversation 不再自动并集。
    assert set(decision.readable_partition_ids) == {partition_id("space-a", "shared", "shared")}


def test_inherit_scope_keeps_default_partitions() -> None:
    factory = _fixture()
    with factory() as session:
        permission_group(session, "group-inherit", persons=("person-y",), memory_scope_mode="inherit")
        session.commit()
    with factory() as session:
        decision = _resolve(session)
    assert set(decision.readable_partition_ids) == {
        partition_id("space-a", "shared", "shared"),
        partition_id("space-a", "person", "person-y"),
        partition_id("space-a", "conversation", "session-a"),
    }


def test_i10_bot_profile_outbound_deny_blocks_even_with_group_allow() -> None:
    """I10：BotProfile 出站 deny 优先于权限组允许。"""

    factory = _fixture()
    with factory() as session:
        permission_group(
            session,
            "group-cross",
            persons=("person-y",),
            memory_scope_mode="override",
            capabilities=("memory.read.cross_space",),
        )
        allow_rule(
            session,
            "group-cross",
            space_selector="specific",
            memory_space_id="space-b",
            partition_type="person",
            partition_selector="specific",
            partition_key="person-y",
        )
        handshake(session, bot_profile_id="bot-a", space_id="space-b", can_read=False)
        session.commit()
    with factory() as session:
        decision = _resolve(session)
    assert "space-b" not in decision.readable_space_ids
    assert partition_id("space-b", "person", "person-y") not in decision.readable_partition_ids


def test_i12_denied_partition_rule_wins_over_allow() -> None:
    """I12 / I13：同一分区同时被 allow 与 deny 命中时 deny 优先。"""

    factory = _fixture()
    with factory() as session:
        permission_group(
            session,
            "group-deny",
            persons=("person-y",),
            memory_scope_mode="override",
            capabilities=("memory.read.cross_space",),
        )
        allow_rule(
            session,
            "group-deny",
            space_selector="specific",
            memory_space_id="space-b",
            partition_type="person",
            partition_selector="specific",
            partition_key="person-y",
        )
        allow_rule(
            session,
            "group-deny",
            space_selector="specific",
            memory_space_id="space-b",
            partition_type="person",
            partition_selector="specific",
            partition_key="person-y",
            effect="deny",
            priority=5,
        )
        handshake(session, bot_profile_id="bot-a", space_id="space-b")
        session.commit()
    with factory() as session:
        decision = _resolve(session)
    assert partition_id("space-b", "person", "person-y") not in decision.readable_partition_ids


def test_i14_all_normal_never_includes_kami_space() -> None:
    """I14：普通 all_normal 可匹配普通域，但永远不包含 Kami。"""

    factory = _fixture()
    with factory() as session:
        ensure_space(session, "memory-space-kami", space_type="kami")
        seed_space_partitions(session, "memory-space-kami")
        permission_group(
            session,
            "group-all",
            persons=("person-y",),
            memory_scope_mode="override",
            capabilities=("memory.read.cross_space",),
        )
        allow_rule(session, "group-all", space_selector="all_normal", partition_type="shared", partition_selector="any")
        handshake(session, bot_profile_id="bot-a", space_id="space-b")
        session.commit()
    with factory() as session:
        decision = _resolve(session)
    assert "memory-space-kami" not in decision.readable_space_ids
    assert "space-b" in decision.readable_space_ids


def test_i15_group_audience_blocks_other_person_without_disclosure() -> None:
    """I15：群聊默认受 audience safety 阻止读取他人 person 分区。"""

    factory = _fixture()
    with factory() as session:
        permission_group(
            session,
            "group-other",
            persons=("person-y",),
            memory_scope_mode="override",
            capabilities=("memory.read.other_person", "memory.read.cross_space"),
            allow_group_disclosure=False,
        )
        allow_rule(
            session,
            "group-other",
            space_selector="specific",
            memory_space_id="space-b",
            partition_type="person",
            partition_selector="specific",
            partition_key="person-x",
        )
        handshake(session, bot_profile_id="bot-a", space_id="space-b")
        session.commit()
    with factory() as session:
        private_decision = _resolve(session, audience_type="private")
        group_decision = _resolve(session, audience_type="group")
    # 私聊下能力允许，群聊下必须被受众安全拦截。
    assert partition_id("space-b", "person", "person-x") in private_decision.readable_partition_ids
    assert partition_id("space-b", "person", "person-x") not in group_decision.readable_partition_ids


def test_i16_group_disclosure_permits_other_person_and_is_recorded() -> None:
    """I16：只有专门披露规则命中时群聊才可读取，并反映在决策里。"""

    factory = _fixture()
    with factory() as session:
        permission_group(
            session,
            "group-disclose",
            persons=("person-y",),
            memory_scope_mode="override",
            capabilities=("memory.read.other_person", "memory.read.cross_space"),
            allow_group_disclosure=True,
        )
        allow_rule(
            session,
            "group-disclose",
            space_selector="specific",
            memory_space_id="space-b",
            partition_type="person",
            partition_selector="specific",
            partition_key="person-x",
        )
        handshake(session, bot_profile_id="bot-a", space_id="space-b")
        session.commit()
    with factory() as session:
        decision = _resolve(session, audience_type="group")
    assert decision.allow_group_disclosure is True
    assert partition_id("space-b", "person", "person-x") in decision.readable_partition_ids


def test_u01_group_matched_by_person_and_highest_priority_wins() -> None:
    """U01：按 person 命中权限组，并取优先级最高者。"""

    factory = _fixture()
    with factory() as session:
        permission_group(session, "group-low", persons=("person-y",), priority=1)
        permission_group(session, "group-high", persons=("person-y",), priority=9)
        permission_group(session, "group-other-person", persons=("person-x",), priority=100)
        session.commit()
    with factory() as session:
        decision = _resolve(session)
    assert decision.permission_group_id == "group-high"


def test_u02_session_context_only_applies_to_matching_session() -> None:
    """U02：session 级上下文只对匹配的会话生效。"""

    factory = _fixture()
    with factory() as session:
        permission_group(
            session,
            "group-session",
            persons=("person-y",),
            scope_type="session",
            session_id="session-a",
            priority=5,
        )
        session.commit()
    with factory() as session:
        matched = _resolve(session, session_id="session-a")
        unmatched = _resolve(session, session_id="session-b")
    assert matched.permission_group_id == "group-session"
    assert unmatched.permission_group_id == ""


def test_u03_same_rank_group_conflict_is_rejected() -> None:
    """U03：同级同优先级多组冲突必须显式失败，而不是依赖数据库返回顺序。"""

    factory = _fixture()
    with factory() as session:
        permission_group(session, "group-one", persons=("person-y",), priority=7)
        permission_group(session, "group-two", persons=("person-y",), priority=7)
        session.commit()
    with factory() as session:
        with pytest.raises(ValueError, match="同级同优先级冲突"):
            _resolve(session)


def test_u04_policy_revision_reflects_latest_group_revision() -> None:
    """U04：策略版本随权限组变更递增，下一条消息即可重新计算。"""

    factory = _fixture()
    with factory() as session:
        initial = permission_group(session, "group-rev", persons=("person-y",)).policy_revision
        session.commit()
    with factory() as session:
        decision_before = _resolve(session)
    with factory() as session:
        group = session.get(MemoryPermissionGroup, "group-rev")
        group.policy_revision += 1
        session.add(group)
        session.commit()
    with factory() as session:
        decision_after = _resolve(session)
    assert decision_before.policy_revision == initial
    assert decision_after.policy_revision == initial + 1
