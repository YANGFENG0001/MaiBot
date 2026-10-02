"""M03 / I01-I06：逻辑分区隔离与默认读取范围。"""

from __future__ import annotations

from workspace_scope_test_support import (
    PUBLIC_BOT_PROFILE_ID,
    PUBLIC_SPACE_ID,
    database,
    ensure_partition,
    ensure_profile,
    ensure_space,
    partition_id,
    seed_space_partitions,
)

from src.workspaces.access_resolver import AccessResolver


def _two_workspace_fixture():
    """构造 A / B 两个独立空间，以及同时属于两边的用户 Y。"""

    factory = database()
    with factory() as session:
        ensure_space(session, PUBLIC_SPACE_ID, space_type="public")
        ensure_profile(session, PUBLIC_BOT_PROFILE_ID, home_space_id=PUBLIC_SPACE_ID, profile_type="public")
        for space_id, session_id in (("space-a", "session-a"), ("space-b", "session-b")):
            # I01：同一个 Y 在 A、B 各自拥有独立 person 分区。
            seed_space_partitions(
                session, space_id, persons=("person-y", "person-x"), sessions=(session_id, "session-other")
            )
        ensure_profile(session, "bot-a", home_space_id="space-a")
        ensure_profile(session, "bot-b", home_space_id="space-b")
        session.commit()
    return factory


def _resolve(session, *, home_space_id, bot_profile_id, session_id, person_id="person-y"):
    return AccessResolver().resolve(
        session,
        person_id=person_id,
        session_id=session_id,
        workspace_id=f"workspace-{home_space_id}",
        home_space_id=home_space_id,
        bot_profile_id=bot_profile_id,
        bot_profile_type="group",
        audience_type="private",
    )


def test_i01_person_partition_is_isolated_per_space() -> None:
    factory = _two_workspace_fixture()
    with factory() as session:
        a_person = ensure_partition(session, "space-a", "person", "person-y")
        b_person = ensure_partition(session, "space-b", "person", "person-y")
        assert a_person.id != b_person.id
        assert a_person.partition_key == b_person.partition_key == "person-y"


def test_i02_a_default_read_scope_only_contains_a_partitions() -> None:
    factory = _two_workspace_fixture()
    with factory() as session:
        decision = _resolve(session, home_space_id="space-a", bot_profile_id="bot-a", session_id="session-a")
    assert decision.readable_space_ids == ("space-a",)
    assert set(decision.readable_partition_ids) == {
        partition_id("space-a", "shared", "shared"),
        partition_id("space-a", "person", "person-y"),
        partition_id("space-a", "conversation", "session-a"),
    }


def test_i03_b_default_read_scope_only_contains_b_partitions() -> None:
    factory = _two_workspace_fixture()
    with factory() as session:
        decision = _resolve(session, home_space_id="space-b", bot_profile_id="bot-b", session_id="session-b")
    assert decision.readable_space_ids == ("space-b",)
    assert set(decision.readable_partition_ids) == {
        partition_id("space-b", "shared", "shared"),
        partition_id("space-b", "person", "person-y"),
        partition_id("space-b", "conversation", "session-b"),
    }


def test_i04_i05_i06_default_scope_never_reaches_other_space_partitions() -> None:
    """A 的默认范围不得包含 B 的 person / conversation / shared 分区。"""

    factory = _two_workspace_fixture()
    with factory() as session:
        decision = _resolve(session, home_space_id="space-a", bot_profile_id="bot-a", session_id="session-a")
    forbidden = {
        partition_id("space-b", "person", "person-y"),
        partition_id("space-b", "person", "person-x"),
        partition_id("space-b", "conversation", "session-b"),
        partition_id("space-b", "conversation", "session-other"),
        partition_id("space-b", "shared", "shared"),
    }
    assert forbidden.isdisjoint(set(decision.readable_partition_ids))


def test_writable_scope_is_limited_to_home_partitions() -> None:
    factory = _two_workspace_fixture()
    with factory() as session:
        decision = _resolve(session, home_space_id="space-a", bot_profile_id="bot-a", session_id="session-a")
    assert set(decision.writable_partition_ids) == {
        partition_id("space-a", "shared", "shared"),
        partition_id("space-a", "person", "person-y"),
        partition_id("space-a", "conversation", "session-a"),
    }


def test_disabled_partition_is_excluded_from_scope() -> None:
    """M03 回填后的分区一旦禁用，就不再出现在可读范围里。"""

    factory = _two_workspace_fixture()
    with factory() as session:
        ensure_partition(session, "space-a", "person", "person-y").enabled = False
        session.commit()
    with factory() as session:
        decision = _resolve(session, home_space_id="space-a", bot_profile_id="bot-a", session_id="session-a")
    assert partition_id("space-a", "person", "person-y") not in decision.readable_partition_ids
    assert partition_id("space-a", "shared", "shared") in decision.readable_partition_ids


def test_kami_domain_partitions_are_not_in_normal_scope() -> None:
    """普通解析只能看到 normal 安全域分区，Kami 分区必须零候选。"""

    factory = _two_workspace_fixture()
    with factory() as session:
        ensure_space(session, "memory-space-kami", space_type="kami")
        ensure_partition(session, "memory-space-kami", "shared", "shared", security_domain="kami")
        session.commit()
    with factory() as session:
        decision = _resolve(session, home_space_id="space-a", bot_profile_id="bot-a", session_id="session-a")
    assert decision.security_domain == "normal"
    assert "memory-space-kami" not in decision.readable_space_ids
    assert partition_id("memory-space-kami", "shared", "shared", "kami") not in decision.readable_partition_ids
