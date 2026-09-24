"""AZ01-AZ20: transfer authorization, ACL and security domains."""

import json

import pytest
from sqlmodel import select

from memory_transfer_test_support import BASE_CAPABILITIES, context, database, grant, seed_transfer_scope
from src.common.database.database_model import MemoryPermissionGroupCapability, MemorySpace, MemorySpaceACL
from src.workspaces.memory_transfer_authorization import MemoryTransferAuthorization
from src.workspaces.memory_transfer_models import MemoryTransferError, TransferCreateRequest


def request(**overrides):
    values = dict(
        mode="copy", source_space_id="space-source", source_partition_ids=("partition-source",),
        target_space_id="space-target", target_partition_id="partition-target", object_types=("paragraph",),
        approval_policy="manual", conflict_policy="skip", idempotency_key="auth-job",
    )
    values.update(overrides)
    return TransferCreateRequest(**values)


def setup():
    _engine, factory = database()
    seed_transfer_scope(factory)
    grant(factory, *BASE_CAPABILITIES)
    return factory, MemoryTransferAuthorization(factory)


def test_az01_az06_source_target_capability_acl_and_write_scope() -> None:
    factory, authorization = setup()
    result = authorization.authorize(request(), context())
    assert result["source_domain"] == result["target_domain"] == "normal"
    assert result["approval_required"] is True
    with factory() as session:
        capability = session.exec(
            select(MemoryPermissionGroupCapability).where(
                MemoryPermissionGroupCapability.capability == "memory.transfer.read_source"
            )
        ).one()
        capability.enabled = False
        session.commit()
    with pytest.raises(MemoryTransferError, match="transfer_capability_required"):
        authorization.authorize(request(), context())


def test_az07_az10_acl_handshake_and_filter_intersection() -> None:
    factory, authorization = setup()
    with factory() as session:
        rows = session.exec(select(MemorySpaceACL)).all()
        rows[0].filters_json = json.dumps({"object_type": ["paragraph", "entity"], "limit": 50})
        rows[1].filters_json = json.dumps({"object_type": ["paragraph"], "limit": 10})
        session.commit()
    result = authorization.authorize(request(), context())
    assert result["effective_filters"] == {"limit": 10, "object_type": ["paragraph"]}
    with factory() as session:
        inbound = session.exec(
            select(MemorySpaceACL).where(MemorySpaceACL.owner_space_id == "space-target")
        ).one()
        inbound.can_import_from_peer = False
        session.commit()
    with pytest.raises(MemoryTransferError, match="target_not_writable"):
        authorization.authorize(request(), context())


def test_az11_az16_force_all_does_not_grant_transfer_and_cross_domain_is_copy_only() -> None:
    _engine, factory = database()
    seed_transfer_scope(factory, target_domain="kami")
    grant(factory, "memory.read.force_all", *BASE_CAPABILITIES)
    authorization = MemoryTransferAuthorization(factory)
    with pytest.raises(MemoryTransferError, match="cross_domain_transfer_denied"):
        authorization.authorize(request(mode="link"), context())
    with pytest.raises(MemoryTransferError, match="cross_domain_transfer_denied"):
        authorization.authorize(request(mode="copy"), context())
    grant(factory, "memory.transfer.cross_domain_copy")
    result = authorization.authorize(request(mode="copy"), context())
    assert result["target_domain"] == "kami"


def test_az17_az20_kami_cannot_downgrade_and_promote_is_manual() -> None:
    _engine, factory = database()
    seed_transfer_scope(factory, source_domain="kami", target_domain="normal")
    grant(factory, *BASE_CAPABILITIES, "memory.transfer.kami_copy")
    authorization = MemoryTransferAuthorization(factory)
    with pytest.raises(MemoryTransferError, match="kami_source_forbidden"):
        authorization.authorize(request(), context(security_domain="kami"))


def test_auto_safe_requires_explicit_capability_and_low_risk_normal_domain() -> None:
    factory, authorization = setup()
    automatic = request(approval_policy="auto_safe")
    assert authorization.authorize(automatic, context())["approval_required"] is True
    grant(factory, "memory.transfer.auto_approve_safe")
    assert authorization.authorize(automatic, context())["approval_required"] is False


def test_promote_requires_public_normal_shared_target_and_always_approval() -> None:
    _engine, factory = database()
    seed_transfer_scope(factory, target_space="memory-space-public")
    grant(factory, *BASE_CAPABILITIES)
    authorization = MemoryTransferAuthorization(factory)
    promote = request(
        mode="promote", target_space_id="memory-space-public", approval_policy="auto_safe"
    )
    result = authorization.authorize(
        promote, context(home_space_id="memory-space-public")
    )
    assert result["approval_required"] is True
    _factory2, authorization2 = setup()
    with pytest.raises(MemoryTransferError, match="promote_target_invalid"):
        authorization2.authorize(request(mode="promote"), context())


def test_kami_to_kami_requires_dedicated_mode_capabilities() -> None:
    _engine, factory = database()
    seed_transfer_scope(factory, source_domain="kami", target_domain="kami")
    grant(factory, *BASE_CAPABILITIES)
    authorization = MemoryTransferAuthorization(factory)
    kami_context = context(security_domain="kami")
    with pytest.raises(MemoryTransferError, match="transfer_capability_required"):
        authorization.authorize(request(mode="link"), kami_context)
    with pytest.raises(MemoryTransferError, match="transfer_capability_required"):
        authorization.authorize(request(mode="copy"), kami_context)
    grant(factory, "memory.transfer.kami_link", "memory.transfer.kami_copy")
    assert authorization.authorize(request(mode="link"), kami_context)["source_domain"] == "kami"
    assert authorization.authorize(request(mode="copy"), kami_context)["target_domain"] == "kami"


def test_disabled_target_and_group_scope_without_read_access_are_denied() -> None:
    factory, authorization = setup()
    with factory() as session:
        target = session.get(MemorySpace, "space-target")
        target.enabled = False
        session.commit()
    with pytest.raises(MemoryTransferError, match="target_not_found"):
        authorization.authorize(request(), context())
    factory2, authorization2 = setup()
    group_context = context(readable=())
    object.__setattr__(group_context, "audience_type", "group")
    with pytest.raises(MemoryTransferError, match="source_not_readable"):
        authorization2.authorize(request(), group_context)
