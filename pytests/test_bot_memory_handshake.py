"""I11 / K09：BotProfile 出站与 MemorySpace 入站的双向 ACL 握手。"""

from __future__ import annotations

from workspace_scope_test_support import (
    allow_rule,
    database,
    ensure_profile,
    ensure_space,
    handshake,
    partition_id,
    permission_group,
    seed_space_partitions,
)

from sqlmodel import select

from src.common.database.database_model import BotProfileMemoryRule, MemorySpaceBotRule
from src.workspaces.access_resolver import AccessResolver


def _fixture(*, strict_isolation: bool = False):
    factory = database()
    with factory() as session:
        seed_space_partitions(session, "space-a", persons=("person-y",), sessions=("session-a",),)
        ensure_space(session, "space-b", strict_isolation=strict_isolation)
        seed_space_partitions(session, "space-b", persons=("person-y",), sessions=("session-a",))
        ensure_profile(session, "bot-a", home_space_id="space-a")
        session.commit()
    return factory


def _cross_space_grant(session) -> None:
    """授予 A 读取 B.shared 的权限组规则。"""

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
        partition_type="shared",
        partition_selector="current",
    )


def _resolve(session):
    return AccessResolver().resolve(
        session,
        person_id="person-y",
        session_id="session-a",
        workspace_id="workspace-a",
        home_space_id="space-a",
        bot_profile_id="bot-a",
        bot_profile_type="group",
        audience_type="private",
    )


def test_cross_space_requires_both_directions() -> None:
    """只有单向 can_read 时不得开放跨空间读取。"""

    factory = _fixture()
    with factory() as session:
        _cross_space_grant(session)
        session.add(BotProfileMemoryRule(bot_profile_id="bot-a", target_space_id="space-b", can_read=True))
        session.commit()
    with factory() as session:
        decision = _resolve(session)
    assert "space-b" not in decision.readable_space_ids


def test_cross_space_opens_after_full_handshake() -> None:
    factory = _fixture()
    with factory() as session:
        _cross_space_grant(session)
        handshake(session, bot_profile_id="bot-a", space_id="space-b")
        session.commit()
    with factory() as session:
        decision = _resolve(session)
    assert "space-b" in decision.readable_space_ids
    assert partition_id("space-b", "shared", "shared") in decision.readable_partition_ids


def test_cross_space_revocation_closes_access_immediately() -> None:
    factory = _fixture()
    with factory() as session:
        _cross_space_grant(session)
        handshake(session, bot_profile_id="bot-a", space_id="space-b")
        session.commit()
    with factory() as session:
        assert "space-b" in _resolve(session).readable_space_ids

    with factory() as session:
        rule = session.exec(
            select(MemorySpaceBotRule).where(
                MemorySpaceBotRule.memory_space_id == "space-b",
                MemorySpaceBotRule.bot_profile_id == "bot-a",
            )
        ).one()
        rule.can_read = False
        session.add(rule)
        session.commit()
    with factory() as session:
        assert "space-b" not in _resolve(session).readable_space_ids


def test_strict_isolation_home_space_requires_handshake() -> None:
    """strict_isolation 空间即使作为 home 也必须完成双向握手。"""

    factory = database()
    with factory() as session:
        ensure_space(session, "space-a", strict_isolation=True)
        seed_space_partitions(session, "space-a", persons=("person-y",), sessions=("session-a",))
        ensure_profile(session, "bot-a", home_space_id="space-a")
        session.commit()
    with factory() as session:
        assert _resolve(session).readable_partition_ids == ()
    with factory() as session:
        handshake(session, bot_profile_id="bot-a", space_id="space-a")
        session.commit()
    with factory() as session:
        decision = _resolve(session)
    assert decision.readable_space_ids == ("space-a",)
    assert partition_id("space-a", "shared", "shared") in decision.readable_partition_ids


def test_non_strict_home_space_needs_no_handshake() -> None:
    factory = _fixture()
    with factory() as session:
        decision = _resolve(session)
    assert decision.readable_space_ids == ("space-a",)
    assert partition_id("space-a", "shared", "shared") in decision.readable_partition_ids
