from __future__ import annotations

from datetime import datetime

from sqlalchemy import event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from src.common.database.database_model import (
    MemoryPartition,
    MemoryPermissionGroup,
    MemoryPermissionGroupCapability,
    MemorySpace,
    MemorySpaceACL,
)
from src.workspaces.request_context import BotRequestContext


def database(*, foreign_keys: bool = True):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    if foreign_keys:
        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(dbapi_connection, _record):
            dbapi_connection.execute("PRAGMA foreign_keys=ON")
    SQLModel.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, class_=Session, expire_on_commit=False)


def context(
    *,
    group_id: str = "group-transfer",
    home_space_id: str = "space-target",
    readable=("partition-source",),
    writable=("partition-target",),
    security_domain: str = "normal",
    revision: int = 1,
):
    return BotRequestContext(
        trace_id="trace-transfer",
        session_id="session-transfer",
        workspace_id="",
        person_id="person-transfer",
        active_bot_profile_id="",
        active_bot_profile_type="group",
        permission_group_id=group_id,
        access_mode="normal",
        security_domain=security_domain,
        home_memory_space_id=home_space_id,
        readable_space_ids=("space-source",),
        readable_partition_ids=tuple(readable),
        writable_partition_ids=tuple(writable),
        audience_type="private",
        policy_revision=revision,
    )


def seed_transfer_scope(factory, *, source_domain="normal", target_domain="normal", target_space="space-target"):
    now = datetime.now()
    with factory() as session:
        session.add_all(
            [
                MemorySpace(id="space-source", name="Source", policy_revision=1, created_at=now, updated_at=now),
                MemorySpace(id=target_space, name="Target", policy_revision=1, created_at=now, updated_at=now),
            ]
        )
        session.flush()
        session.add_all(
            [
                MemoryPartition(
                    id="partition-source", memory_space_id="space-source", partition_type="shared",
                    partition_key="source", security_domain=source_domain, policy_revision=1,
                    created_at=now, updated_at=now,
                ),
                MemoryPartition(
                    id="partition-target", memory_space_id=target_space, partition_type="shared",
                    partition_key="shared", security_domain=target_domain, policy_revision=1,
                    created_at=now, updated_at=now,
                ),
                MemoryPermissionGroup(id="group-transfer", name="Transfer", policy_revision=1),
                MemorySpaceACL(
                    owner_space_id="space-source", peer_space_id=target_space,
                    can_publish_to_peer=True, filters_json="{}",
                ),
                MemorySpaceACL(
                    owner_space_id=target_space, peer_space_id="space-source",
                    can_import_from_peer=True, filters_json="{}",
                ),
            ]
        )
        session.commit()


def grant(factory, *capabilities: str):
    with factory() as session:
        for capability in capabilities:
            session.add(
                MemoryPermissionGroupCapability(
                    permission_group_id="group-transfer",
                    capability=capability,
                    enabled=True,
                )
            )
        session.commit()


BASE_CAPABILITIES = (
    "memory.transfer.read_source",
    "memory.transfer.write_target",
    "memory.transfer.copy",
    "memory.transfer.link",
    "memory.transfer.publish",
    "memory.transfer.cross_space_write",
    "memory.transfer.approve",
    "memory.transfer.retry",
    "memory.transfer.cancel",
)
