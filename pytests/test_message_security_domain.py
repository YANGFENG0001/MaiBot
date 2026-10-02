"""C06-C09：Kami 安全域与普通安全域的单向隔离。"""

from __future__ import annotations

import pytest

from workspace_scope_test_support import (
    database,
    ensure_partition,
    ensure_profile,
    ensure_space,
    partition_id,
    permission_group,
    seed_space_partitions,
)

from src.workspaces.access_resolver import AccessResolver

KAMI_SPACE_ID = "memory-space-kami"
KAMI_PROFILE_ID = "bot-profile-kami"


def _fixture(*, manager: bool = True, capabilities: tuple[str, ...] = ()):
    """构造 public / A / B 三个普通空间与一个 Kami 空间。"""

    factory = database()
    with factory() as session:
        ensure_space(session, "memory-space-public", space_type="public")
        seed_space_partitions(session, "memory-space-public", persons=("person-y",), sessions=("session-1",))
        for space_id in ("space-a", "space-b"):
            seed_space_partitions(session, space_id, persons=("person-y",), sessions=("session-1",))
        ensure_space(session, KAMI_SPACE_ID, space_type="kami", strict_isolation=True)
        seed_space_partitions(session, KAMI_SPACE_ID)
        ensure_partition(session, KAMI_SPACE_ID, "shared", "shared", security_domain="kami")
        ensure_partition(session, KAMI_SPACE_ID, "person", "person-y", security_domain="kami")
        ensure_partition(session, KAMI_SPACE_ID, "conversation", "session-1", security_domain="kami")
        ensure_profile(
            session,
            KAMI_PROFILE_ID,
            home_space_id=KAMI_SPACE_ID,
            profile_type="kami",
        )
        ensure_profile(session, "bot-a", home_space_id="space-a")
        permission_group(
            session,
            "group-manager",
            persons=("person-y",),
            memory_scope_mode="override",
            is_manager_mode=manager,
            capabilities=capabilities,
        )
        session.commit()
    return factory


def _resolve(session, *, bot_profile_type: str, bot_profile_id: str, audience_type: str = "private"):
    return AccessResolver().resolve(
        session,
        person_id="person-y",
        session_id="session-1",
        workspace_id="workspace-a",
        home_space_id=KAMI_SPACE_ID if bot_profile_type == "kami" else "space-a",
        bot_profile_id=bot_profile_id,
        bot_profile_type=bot_profile_type,
        audience_type=audience_type,
    )


def _kami_fixture(**kwargs):
    return _fixture(capabilities=("bot.switch.kami", "memory.read.force_all", "kami.use_in_group"), **kwargs)


def test_c06_kami_reads_normal_spaces_and_is_audited_as_forced() -> None:
    factory = _kami_fixture()
    with factory() as session:
        decision = _resolve(session, bot_profile_type="kami", bot_profile_id=KAMI_PROFILE_ID)
    assert decision.access_mode == "forced_kami"
    assert decision.security_domain == "kami"
    assert {"space-a", "space-b", "memory-space-public"} <= set(decision.readable_space_ids)
    assert partition_id("space-a", "shared", "shared") in decision.readable_partition_ids


def test_c07_kami_reads_kami_space() -> None:
    factory = _kami_fixture()
    with factory() as session:
        decision = _resolve(session, bot_profile_type="kami", bot_profile_id=KAMI_PROFILE_ID)
    assert KAMI_SPACE_ID in decision.readable_space_ids
    assert partition_id(KAMI_SPACE_ID, "shared", "shared", "kami") in decision.readable_partition_ids


def test_c08_kami_writes_only_to_kami_space_partitions() -> None:
    factory = _kami_fixture()
    with factory() as session:
        decision = _resolve(session, bot_profile_type="kami", bot_profile_id=KAMI_PROFILE_ID)
    assert set(decision.writable_partition_ids) == {
        partition_id(KAMI_SPACE_ID, "shared", "shared", "kami"),
        partition_id(KAMI_SPACE_ID, "person", "person-y", "kami"),
        partition_id(KAMI_SPACE_ID, "conversation", "session-1", "kami"),
    }
    assert partition_id("space-a", "shared", "shared") not in decision.writable_partition_ids


def test_c09_normal_bot_never_sees_kami_partitions() -> None:
    """C09：即使权限组带 force_all，普通 Bot 也必须零 Kami 候选。"""

    factory = _kami_fixture()
    with factory() as session:
        decision = _resolve(session, bot_profile_type="group", bot_profile_id="bot-a")
    assert decision.security_domain == "normal"
    assert decision.access_mode == "normal"
    assert KAMI_SPACE_ID not in decision.readable_space_ids
    kami_partition = partition_id(KAMI_SPACE_ID, "shared", "shared", "kami")
    assert kami_partition not in decision.readable_partition_ids
    assert kami_partition not in decision.writable_partition_ids


def test_kami_requires_manager_mode() -> None:
    factory = _fixture(
        manager=False,
        capabilities=("bot.switch.kami", "memory.read.force_all"),
    )
    with factory() as session:
        with pytest.raises(PermissionError):
            _resolve(session, bot_profile_type="kami", bot_profile_id=KAMI_PROFILE_ID)


def test_kami_requires_force_all_capability() -> None:
    factory = _fixture(manager=True, capabilities=("bot.switch.kami",))
    with factory() as session:
        with pytest.raises(PermissionError):
            _resolve(session, bot_profile_type="kami", bot_profile_id=KAMI_PROFILE_ID)


def test_kami_group_audience_requires_use_in_group() -> None:
    factory = _fixture(manager=True, capabilities=("bot.switch.kami", "memory.read.force_all"))
    with factory() as session:
        with pytest.raises(PermissionError):
            _resolve(
                session,
                bot_profile_type="kami",
                bot_profile_id=KAMI_PROFILE_ID,
                audience_type="group",
            )
