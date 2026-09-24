"""Correction-first red tests for principal, ownership and recursive audit."""

import pytest
from sqlmodel import select

from memory_transfer_test_support import context, database, seed_transfer_scope, grant, BASE_CAPABILITIES
from src.workspaces.memory_transfer_models import MemoryTransferError, safe_audit_details
from src.workspaces.memory_transfer_repository import MemoryTransferRepository
from src.workspaces.memory_transfer_service import MemoryTransferService
from src.workspaces.memory_transfer_authorization import MemoryTransferAuthorization
from src.workspaces.memory_transfer_models import TransferCreateRequest
from src.common.database.database_model import MemoryPartition, MemorySpaceACL


def req(key="shared"):
    return TransferCreateRequest(
        mode="copy", source_space_id="space-source", source_partition_ids=("partition-source",),
        target_space_id="space-target", target_partition_id="partition-target", object_types=("paragraph",),
        idempotency_key=key,
    )


def setup():
    _engine, factory = database()
    seed_transfer_scope(factory)
    grant(factory, *BASE_CAPABILITIES)
    service = MemoryTransferService(
        repository=MemoryTransferRepository(factory),
        authorization=MemoryTransferAuthorization(factory),
        authority=object(),
    )
    return factory, service


def test_same_workspace_different_person_cannot_read_job():
    _factory, service = setup()
    owner = context()
    job = service.create_job(req(), owner, "alice")
    mallory = context()
    object.__setattr__(mallory, "person_id", "mallory")
    with pytest.raises(MemoryTransferError):
        service.get_job(job.id, "mallory", request_context=mallory)


def test_same_client_key_different_principal_must_not_return_owner_job():
    _factory, service = setup()
    owner = context()
    first = service.create_job(req("same-client"), owner, "alice")
    other = context()
    object.__setattr__(other, "person_id", "mallory")
    second = service.create_job(req("same-client"), other, "mallory")
    assert second.id != first.id


def test_revision_components_cannot_collide_by_maximum():
    factory, service = setup()
    request_context = context(revision=2)
    before = service.authorization.authorize(req("revision"), request_context)
    with factory() as session:
        partition = session.get(MemoryPartition, "partition-source")
        partition.policy_revision += 1
        session.commit()
    after = service.authorization.authorize(req("revision"), request_context)
    assert after["policy_snapshot_hash"] != before["policy_snapshot_hash"]
    assert after["policy_snapshot"]["source"]["partitions"] != before["policy_snapshot"]["source"]["partitions"]


def test_acl_content_change_changes_policy_hash_even_without_numeric_revision():
    factory, service = setup()
    request_context = context()
    before = service.authorization.authorize(req("acl-hash"), request_context)["policy_snapshot_hash"]
    with factory() as session:
        outbound = session.exec(
            select(MemorySpaceACL).where(MemorySpaceACL.owner_space_id == "space-source")
        ).one()
        outbound.filters_json = '{"object_type":["paragraph"]}'
        session.commit()
    after = service.authorization.authorize(req("acl-hash"), request_context)["policy_snapshot_hash"]
    assert after != before


def test_nested_audit_body_is_rejected():
    with pytest.raises(MemoryTransferError):
        safe_audit_details({"outer": {"content": "secret-body"}})
