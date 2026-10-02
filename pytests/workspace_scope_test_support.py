"""工作区 / 记忆空间 / 分区 / 权限组范围测试的共享夹具。

这些夹具只构造真实表结构里的最小数据，不复制 AccessResolver 的任何算法，
保证测试断言的是运行期真实解析结果。
"""

from __future__ import annotations

from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, SQLModel, create_engine

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
)
from src.common.database.migrations.v43_to_v44 import build_partition_id

PUBLIC_SPACE_ID = "memory-space-public"
PUBLIC_BOT_PROFILE_ID = "bot-profile-public"


def database():
    """返回一个隔离的内存数据库会话工厂。"""

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


def ensure_space(
    session: Session,
    space_id: str,
    *,
    name: str = "",
    space_type: str = "private",
    strict_isolation: bool = False,
    enabled: bool = True,
) -> MemorySpace:
    space = session.get(MemorySpace, space_id)
    if space is None:
        space = MemorySpace(
            id=space_id,
            name=name or space_id,
            space_type=space_type,
            strict_isolation=strict_isolation,
            enabled=enabled,
        )
        session.add(space)
        session.flush()
    return space


def ensure_partition(
    session: Session,
    space_id: str,
    partition_type: str,
    partition_key: str,
    *,
    security_domain: str = "normal",
    enabled: bool = True,
) -> MemoryPartition:
    partition_id = build_partition_id(space_id, partition_type, partition_key, security_domain)
    partition = session.get(MemoryPartition, partition_id)
    if partition is None:
        partition = MemoryPartition(
            id=partition_id,
            memory_space_id=space_id,
            partition_type=partition_type,
            partition_key=partition_key,
            security_domain=security_domain,
            enabled=enabled,
        )
        session.add(partition)
        session.flush()
    return partition


def partition_id(space_id: str, partition_type: str, partition_key: str, security_domain: str = "normal") -> str:
    return build_partition_id(space_id, partition_type, partition_key, security_domain)


def ensure_profile(
    session: Session,
    profile_id: str,
    *,
    home_space_id: str,
    profile_type: str = "group",
    parent_profile_id: str | None = None,
    enabled: bool = True,
) -> BotProfile:
    profile = session.get(BotProfile, profile_id)
    if profile is None:
        profile = BotProfile(
            id=profile_id,
            name=profile_id,
            profile_type=profile_type,
            parent_profile_id=parent_profile_id,
            home_memory_space_id=home_space_id,
            enabled=enabled,
        )
        session.add(profile)
        session.flush()
    return profile


def seed_space_partitions(session: Session, space_id: str, persons: tuple[str, ...] = (), sessions: tuple[str, ...] = ()) -> None:
    """为一个记忆空间补齐 shared/person/conversation 分区。"""

    ensure_space(session, space_id)
    ensure_partition(session, space_id, "shared", "shared")
    for person in persons:
        ensure_partition(session, space_id, "person", person)
    for session_key in sessions:
        ensure_partition(session, space_id, "conversation", session_key)


def handshake(
    session: Session,
    *,
    bot_profile_id: str,
    space_id: str,
    can_read: bool = True,
) -> None:
    """建立 BotProfile 出站与 MemorySpace 入站的双向 ACL 握手。"""

    session.add(BotProfileMemoryRule(bot_profile_id=bot_profile_id, target_space_id=space_id, can_read=can_read))
    session.add(MemorySpaceBotRule(memory_space_id=space_id, bot_profile_id=bot_profile_id, can_read=can_read))
    session.flush()


def permission_group(
    session: Session,
    group_id: str,
    *,
    persons: tuple[str, ...],
    scope_type: str = "global",
    workspace_id: str | None = None,
    session_id: str | None = None,
    channel_type: str | None = None,
    priority: int = 0,
    memory_scope_mode: str = "inherit",
    is_manager_mode: bool = False,
    capabilities: tuple[str, ...] = (),
    allow_group_disclosure: bool = False,
) -> MemoryPermissionGroup:
    group = MemoryPermissionGroup(
        id=group_id,
        name=group_id,
        priority=priority,
        memory_scope_mode=memory_scope_mode,
        is_manager_mode=is_manager_mode,
        enabled=True,
    )
    session.add(group)
    session.flush()
    for person in persons:
        session.add(MemoryPermissionGroupMember(permission_group_id=group_id, person_id=person))
    session.add(
        MemoryPermissionGroupContext(
            permission_group_id=group_id,
            scope_type=scope_type,
            workspace_id=workspace_id,
            session_id=session_id,
            channel_type=channel_type,
            allow_group_disclosure=allow_group_disclosure,
            enabled=True,
        )
    )
    for capability in capabilities:
        session.add(
            MemoryPermissionGroupCapability(
                permission_group_id=group_id,
                capability=capability,
                enabled=True,
            )
        )
    session.flush()
    return group


def allow_rule(
    session: Session,
    group_id: str,
    *,
    space_selector: str = "current",
    memory_space_id: str | None = None,
    partition_type: str = "any",
    partition_selector: str = "any",
    partition_key: str | None = None,
    priority: int = 0,
    effect: str = "allow",
) -> MemoryPermissionRule:
    rule = MemoryPermissionRule(
        permission_group_id=group_id,
        effect=effect,
        space_selector=space_selector,
        memory_space_id=memory_space_id,
        partition_type=partition_type,
        partition_selector=partition_selector,
        partition_key=partition_key,
        priority=priority,
        enabled=True,
    )
    session.add(rule)
    session.flush()
    return rule
