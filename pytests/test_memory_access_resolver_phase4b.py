from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, SQLModel, select

from src.common.database.database_model import (
    BotProfile,
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
from src.workspaces.access_resolver import AccessResolver


def _session() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False)()


def _seed(session: Session) -> dict[str, str]:
    now = datetime.now()
    spaces = (
        MemorySpace(id="space-home", name="Home", space_type="private", created_at=now, updated_at=now),
        MemorySpace(id="space-other", name="Other", space_type="private", created_at=now, updated_at=now),
        MemorySpace(id="memory-space-public", name="Public", space_type="public", created_at=now, updated_at=now),
        MemorySpace(id="memory-space-kami", name="Kami", space_type="kami", created_at=now, updated_at=now),
    )
    session.add_all(spaces)
    session.commit()
    session.add(
        BotProfile(
            id="bot-group",
            name="Group",
            profile_type="group",
            home_memory_space_id="space-home",
            created_at=now,
            updated_at=now,
        )
    )
    session.commit()
    for space_id, domain in (
        ("space-home", "normal"),
        ("space-other", "normal"),
        ("memory-space-public", "normal"),
        ("memory-space-kami", "kami"),
    ):
        for partition_type, key in (
            ("shared", "shared"),
            ("person", "person-self"),
            ("person", "person-other"),
            ("conversation", "session-current"),
            ("conversation", "session-other"),
        ):
            partition_id = build_partition_id(space_id, partition_type, key, domain)
            session.add(
                MemoryPartition(
                    id=partition_id,
                    memory_space_id=space_id,
                    partition_type=partition_type,
                    partition_key=key,
                    security_domain=domain,
                    display_name=key,
                    created_at=now,
                    updated_at=now,
                )
            )
    session.commit()
    return {
        "home_shared": build_partition_id("space-home", "shared", "shared", "normal"),
        "home_self": build_partition_id("space-home", "person", "person-self", "normal"),
        "home_other": build_partition_id("space-home", "person", "person-other", "normal"),
        "home_current": build_partition_id("space-home", "conversation", "session-current", "normal"),
        "home_other_conversation": build_partition_id("space-home", "conversation", "session-other", "normal"),
        "other_person": build_partition_id("space-other", "person", "person-other", "normal"),
        "other_conversation": build_partition_id("space-other", "conversation", "session-other", "normal"),
    }


def _group(
    session: Session,
    *,
    group_id: str = "group-access",
    mode: str = "override",
    allow_group_disclosure: bool = False,
    priority: int = 10,
    scope_type: str = "global",
) -> None:
    now = datetime.now()
    session.add(
        MemoryPermissionGroup(
            id=group_id,
            name=group_id,
            memory_scope_mode=mode,
            priority=priority,
            created_at=now,
            updated_at=now,
        )
    )
    session.add(MemoryPermissionGroupMember(permission_group_id=group_id, person_id="person-self"))
    session.add(
        MemoryPermissionGroupContext(
            permission_group_id=group_id,
            scope_type=scope_type,
            session_id="session-current" if scope_type == "session" else None,
            allow_group_disclosure=allow_group_disclosure,
        )
    )
    session.commit()


def _capabilities(session: Session, *names: str, group_id: str = "group-access") -> None:
    session.add_all(
        [MemoryPermissionGroupCapability(permission_group_id=group_id, capability=name) for name in names]
    )
    session.commit()


def _rule(
    session: Session,
    *,
    effect: str = "allow",
    space_selector: str = "current",
    memory_space_id: str | None = None,
    partition_type: str = "any",
    partition_selector: str = "any",
    partition_key: str | None = None,
    priority: int = 0,
    group_id: str = "group-access",
) -> None:
    session.add(
        MemoryPermissionRule(
            permission_group_id=group_id,
            effect=effect,
            space_selector=space_selector,
            memory_space_id=memory_space_id,
            partition_type=partition_type,
            partition_selector=partition_selector,
            partition_key=partition_key,
            priority=priority,
        )
    )
    session.commit()


def _handshake(session: Session, space_id: str, *, outbound: bool = True, inbound: bool = True) -> None:
    session.add(BotProfileMemoryRule(bot_profile_id="bot-group", target_space_id=space_id, can_read=outbound))
    session.add(MemorySpaceBotRule(memory_space_id=space_id, bot_profile_id="bot-group", can_read=inbound))
    session.commit()


def _resolve(session: Session, audience: str = "private"):
    return AccessResolver().resolve(
        session,
        person_id="person-self",
        session_id="session-current",
        workspace_id="workspace-current",
        home_space_id="space-home",
        bot_profile_id="bot-group",
        bot_profile_type="group",
        audience_type=audience,
    )


def test_default_scope_only_contains_current_user_and_conversation() -> None:
    session = _session()
    ids = _seed(session)
    decision = _resolve(session)
    assert set(decision.readable_space_ids) == {"space-home"}
    assert set(decision.readable_partition_ids) == {ids["home_shared"], ids["home_self"], ids["home_current"]}


def test_override_reads_other_person_and_conversation_after_five_layer_handshake() -> None:
    session = _session()
    ids = _seed(session)
    _group(session)
    _capabilities(
        session,
        "memory.read.cross_space",
        "memory.read.other_person",
        "memory.read.other_conversation",
    )
    _rule(session, space_selector="specific", memory_space_id="space-other", partition_type="person", partition_selector="specific", partition_key="person-other")
    _rule(session, space_selector="specific", memory_space_id="space-other", partition_type="conversation", partition_selector="specific", partition_key="session-other")
    _handshake(session, "space-other")

    decision = _resolve(session)
    assert decision.readable_space_ids == ("space-other",)
    assert set(decision.readable_partition_ids) == {ids["other_person"], ids["other_conversation"]}


@pytest.mark.parametrize("outbound,inbound", [(False, True), (True, False)])
def test_bidirectional_acl_requires_both_sides(outbound: bool, inbound: bool) -> None:
    session = _session()
    _seed(session)
    _group(session)
    _capabilities(session, "memory.read.cross_space")
    _rule(session, space_selector="specific", memory_space_id="space-other", partition_type="shared")
    _handshake(session, "space-other", outbound=outbound, inbound=inbound)
    assert _resolve(session).readable_space_ids == ()


def test_inherit_cannot_expand_and_explicit_deny_shrinks_default() -> None:
    session = _session()
    ids = _seed(session)
    _group(session, mode="inherit")
    _capabilities(session, "memory.read.cross_space", "memory.read.other_person")
    _rule(session, space_selector="specific", memory_space_id="space-other", partition_type="person", partition_selector="specific", partition_key="person-other")
    _rule(session, effect="deny", space_selector="current", partition_type="conversation", partition_selector="current")
    _handshake(session, "space-other")
    decision = _resolve(session)
    assert decision.readable_space_ids == ("space-home",)
    assert set(decision.readable_partition_ids) == {ids["home_shared"], ids["home_self"]}


def test_group_audience_blocks_other_people_until_disclosure_is_explicit() -> None:
    session = _session()
    _seed(session)
    _group(session, allow_group_disclosure=False)
    _capabilities(session, "memory.read.other_person")
    _rule(session, partition_type="person", partition_selector="specific", partition_key="person-other")
    assert _resolve(session, "group").readable_partition_ids == ()

    context = session.exec(select(MemoryPermissionGroupContext)).one()
    context.allow_group_disclosure = True
    session.add(context)
    session.commit()
    assert _resolve(session, "group").readable_partition_ids


def test_all_normal_never_includes_kami() -> None:
    session = _session()
    _seed(session)
    _group(session)
    _capabilities(session, "memory.read.cross_space")
    _rule(session, space_selector="all_normal", partition_type="shared")
    _handshake(session, "space-other")
    _handshake(session, "memory-space-public")
    decision = _resolve(session)
    assert set(decision.readable_space_ids) == {"space-home", "space-other", "memory-space-public"}
    assert "memory-space-kami" not in decision.readable_space_ids


def test_permission_group_context_conflict_and_bot_deny_fail_closed() -> None:
    session = _session()
    _seed(session)
    _group(session, group_id="group-a", priority=10)
    _group(session, group_id="group-b", priority=10)
    with pytest.raises(ValueError, match="权限组存在同级同优先级冲突"):
        _resolve(session)

    disabled_group = session.get(MemoryPermissionGroup, "group-b")
    assert disabled_group is not None
    disabled_group.enabled = False
    session.add(disabled_group)
    session.add(PermissionGroupBotRule(permission_group_id="group-a", effect="deny", bot_selector="current_group"))
    session.commit()
    with pytest.raises(PermissionError, match="显式拒绝"):
        _resolve(session)


def test_strict_isolation_requires_explicit_bidirectional_home_rules() -> None:
    session = _session()
    _seed(session)
    space = session.get(MemorySpace, "space-home")
    assert space is not None
    space.strict_isolation = True
    session.add(space)
    session.commit()
    assert _resolve(session).readable_space_ids == ()
    _handshake(session, "space-home")
    assert _resolve(session).readable_space_ids == ("space-home",)


def test_invalid_context_field_combination_is_rejected() -> None:
    session = _session()
    _seed(session)
    _group(session)
    context = session.exec(select(MemoryPermissionGroupContext)).one()
    context.session_id = "session-invalid"
    session.add(context)
    session.commit()
    with pytest.raises(ValueError, match="global 权限组上下文"):
        _resolve(session)


def test_kami_requires_manager_capabilities_and_writes_only_kami_space() -> None:
    session = _session()
    _seed(session)
    _group(session)
    group = session.get(MemoryPermissionGroup, "group-access")
    assert group is not None
    group.is_manager_mode = True
    session.add(group)
    _capabilities(session, "bot.switch.kami", "memory.read.force_all")

    decision = AccessResolver().resolve(
        session,
        person_id="person-self",
        session_id="session-current",
        workspace_id="workspace-current",
        home_space_id="memory-space-kami",
        bot_profile_id="bot-kami",
        bot_profile_type="kami",
        audience_type="private",
    )

    assert decision.access_mode == "forced_kami"
    assert decision.security_domain == "kami"
    assert "memory-space-kami" in decision.readable_space_ids
    assert decision.writable_partition_ids
    kami_partition_ids = {
        item.id
        for item in session.exec(
            select(MemoryPartition).where(MemoryPartition.memory_space_id == "memory-space-kami")
        ).all()
    }
    assert set(decision.writable_partition_ids).issubset(kami_partition_ids)
