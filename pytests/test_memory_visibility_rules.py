"""I10-I13：记忆可见性规则（空间/分区/规则优先级）的确定性。"""

from __future__ import annotations

import pytest

from workspace_scope_test_support import (
    PUBLIC_SPACE_ID,
    allow_rule,
    database,
    ensure_partition,
    ensure_profile,
    ensure_space,
    handshake,
    partition_id,
    permission_group,
    seed_space_partitions,
)

from src.common.database.database_model import BotProfileMemoryRule, MemorySpaceBotRule
from src.workspaces.access_resolver import AccessResolver


def _fixture():
    factory = database()
    with factory() as session:
        ensure_space(session, PUBLIC_SPACE_ID, space_type="public")
        seed_space_partitions(
            session, "space-a", persons=("person-y", "person-x"), sessions=("session-a", "session-other")
        )
        ensure_profile(session, "bot-a", home_space_id="space-a")
        session.commit()
    return factory


def _resolve(session, *, person_id="person-y", session_id="session-a"):
    return AccessResolver().resolve(
        session,
        person_id=person_id,
        session_id=session_id,
        workspace_id="workspace-a",
        home_space_id="space-a",
        bot_profile_id="bot-a",
        bot_profile_type="group",
        audience_type="private",
    )


def test_i10_outbound_deny_removes_home_space_from_scope() -> None:
    """I10：BotProfile 出站 deny 即使对 home space 也必须生效。"""

    factory = _fixture()
    with factory() as session:
        session.add(BotProfileMemoryRule(bot_profile_id="bot-a", target_space_id="space-a", can_read=False))
        session.commit()
    with factory() as session:
        decision = _resolve(session)
    assert decision.readable_space_ids == ()
    assert decision.readable_partition_ids == ()


def test_i11_inbound_deny_removes_home_space_from_scope() -> None:
    """I11：MemorySpace 入站 deny 同样必须生效，与出站方向对称。"""

    factory = _fixture()
    with factory() as session:
        session.add(MemorySpaceBotRule(memory_space_id="space-a", bot_profile_id="bot-a", can_read=False))
        session.commit()
    with factory() as session:
        decision = _resolve(session)
    assert decision.readable_space_ids == ()


def test_i12_space_allowed_but_partition_rule_still_denies() -> None:
    """I12：空间允许，但目标 person 分区被规则拒绝时仍不可见。"""

    factory = _fixture()
    with factory() as session:
        permission_group(
            session,
            "group-rule-deny",
            persons=("person-y",),
            memory_scope_mode="override",
        )
        allow_rule(session, "group-rule-deny", space_selector="current", partition_type="any", partition_selector="any")
        allow_rule(
            session,
            "group-rule-deny",
            space_selector="current",
            partition_type="person",
            partition_selector="self",
            effect="deny",
            priority=10,
        )
        session.commit()
    with factory() as session:
        decision = _resolve(session)
    assert "space-a" in decision.readable_space_ids
    assert partition_id("space-a", "person", "person-y") not in decision.readable_partition_ids
    assert partition_id("space-a", "shared", "shared") in decision.readable_partition_ids


def test_i13_same_specificity_same_priority_allow_deny_is_rejected() -> None:
    """I13：同级同优先级 allow/deny 必须显式失败，禁止不确定结果。"""

    factory = _fixture()
    with factory() as session:
        permission_group(session, "group-ambiguous", persons=("person-y",), memory_scope_mode="override")
        allow_rule(session, "group-ambiguous", space_selector="current", partition_type="any", partition_selector="any")
        allow_rule(
            session,
            "group-ambiguous",
            space_selector="current",
            partition_type="any",
            partition_selector="any",
            effect="deny",
        )
        session.commit()
    with factory() as session:
        with pytest.raises(ValueError, match="冲突"):
            _resolve(session)


def test_disabled_space_is_invisible_even_when_handshaked() -> None:
    factory = _fixture()
    with factory() as session:
        handshake(session, bot_profile_id="bot-a", space_id="space-a")
        ensure_space(session, "space-a").enabled = False
        session.commit()
    with factory() as session:
        decision = _resolve(session)
    assert decision.readable_space_ids == ()


def test_disabled_partition_is_invisible_in_override_mode() -> None:
    factory = _fixture()
    with factory() as session:
        permission_group(session, "group-any", persons=("person-y",), memory_scope_mode="override")
        allow_rule(session, "group-any", space_selector="current", partition_type="any", partition_selector="any")
        ensure_partition(session, "space-a", "conversation", "session-a").enabled = False
        session.commit()
    with factory() as session:
        decision = _resolve(session)
    assert partition_id("space-a", "conversation", "session-a") not in decision.readable_partition_ids
    assert partition_id("space-a", "shared", "shared") in decision.readable_partition_ids


def test_other_person_requires_capability_even_in_private_audience() -> None:
    """缺少 memory.read.other_person 时，他人 person 分区不可见。"""

    factory = _fixture()
    with factory() as session:
        permission_group(session, "group-any", persons=("person-y",), memory_scope_mode="override")
        allow_rule(session, "group-any", space_selector="current", partition_type="any", partition_selector="any")
        session.commit()
    with factory() as session:
        decision = _resolve(session)
    assert partition_id("space-a", "person", "person-x") not in decision.readable_partition_ids
    assert partition_id("space-a", "person", "person-y") in decision.readable_partition_ids
