"""Repository, state, idempotency, approval and audit tests."""


import pytest
from sqlmodel import select
from src.common.database.database_model import MemoryTransferApproval, MemoryTransferItem

from memory_transfer_test_support import context, database, seed_transfer_scope
from src.workspaces.memory_transfer_models import MemoryTransferError, TransferCreateRequest
from src.workspaces.memory_transfer_repository import MemoryTransferRepository


def request(key="repo-key", **overrides):
    values = dict(
        mode="copy", source_space_id="space-source", source_partition_ids=("partition-source",),
        target_space_id="space-target", target_partition_id="partition-target", object_types=("paragraph",),
        approval_policy="manual", conflict_policy="skip", idempotency_key=key,
    )
    values.update(overrides)
    return TransferCreateRequest(**values)


def setup():
    _engine, factory = database()
    seed_transfer_scope(factory)
    return factory, MemoryTransferRepository(factory)


def test_job_idempotency_same_payload_and_conflict() -> None:
    _factory, repository = setup()
    first, created = repository.create(request(), context(), "alice")
    second, repeated = repository.create(request(), context(), "alice")
    assert created is True and repeated is False and first.id == second.id
    with pytest.raises(MemoryTransferError, match="idempotency_key_reused"):
        repository.create(request(object_ids=("different",)), context(), "alice")


def test_missing_idempotency_requires_nonce() -> None:
    _factory, repository = setup()
    with pytest.raises(MemoryTransferError, match="idempotency_key_required"):
        repository.create(request(key=""), context(), "alice")


def test_state_machine_rejects_illegal_transition() -> None:
    _factory, repository = setup()
    job, _ = repository.create(request(), context(), "alice")
    with pytest.raises(MemoryTransferError, match="invalid_state_transition"):
        repository.transition(job.id, "completed")


def test_approval_hash_revision_self_and_duplicate_rules() -> None:
    factory, repository = setup()
    job, _ = repository.create(request(), context(), "alice")
    repository.transition(job.id, "planning", actor="alice")
    planned = repository.replace_plan(
        job.id, [], plan_hash="plan", source_snapshot={}, target_snapshot={},
        policy_snapshot={"policy_revision": 1}, approval_required=True, policy_revision=1,
        source_revision=1, target_revision=1, actor="alice",
    )
    with pytest.raises(MemoryTransferError, match="approval_plan_mismatch"):
        repository.approve(
            planned.id, plan_revision=planned.plan_revision, plan_hash="wrong",
            policy_revision=1, policy_snapshot_hash=planned.policy_snapshot_hash,
            actor="bob", approved=True,
        )
    with pytest.raises(MemoryTransferError, match="approval_policy_mismatch"):
        repository.approve(
            planned.id, plan_revision=planned.plan_revision, plan_hash="plan",
            policy_revision=2, policy_snapshot_hash=planned.policy_snapshot_hash,
            actor="bob", approved=True,
        )
    approved = repository.approve(
        planned.id, plan_revision=planned.plan_revision, plan_hash="plan",
        policy_revision=1, policy_snapshot_hash=planned.policy_snapshot_hash,
        actor="bob", approved=True,
    )
    repeated = repository.approve(
        planned.id, plan_revision=planned.plan_revision, plan_hash="plan",
        policy_revision=1, policy_snapshot_hash=planned.policy_snapshot_hash,
        actor="bob", approved=True,
    )
    assert approved.status == repeated.status == "approved"
    with factory() as session:
        count = session.exec(select(MemoryTransferApproval)).all()
        assert len(count) == 1


def test_job_lease_is_exclusive_and_raw_token_is_not_persisted() -> None:
    factory, repository = setup()
    job, _ = repository.create(request(), context(), "alice")
    token = repository.acquire_lease(job.id, worker_id="worker-a", ttl_seconds=60)
    with pytest.raises(MemoryTransferError, match="lease_lost"):
        repository.acquire_lease(job.id, worker_id="worker-b", ttl_seconds=60)
    stored = repository.get(job.id)
    assert stored.lease_token_hash and stored.lease_token_hash != token
    repository.release_lease(job.id, token)
    assert repository.get(job.id).lease_token_hash is None

def test_items_use_deterministic_business_order_when_created_at_ties() -> None:
    factory, repository = setup()
    job, _ = repository.create(request(key="stable-items"), context(), "alice")
    repository.transition(job.id, "planning", actor="alice")
    items = [
        MemoryTransferItem(
            id=f"stable-item-{index}",
            job_id=job.id,
            mode="copy",
            plan_revision=1,
            object_type=object_type,
            source_object_id=source_object_id,
            source_space_id="space-source",
            source_partition_id="partition-source",
            target_space_id="space-target",
            target_partition_id="partition-target",
            operation_key=operation_key,
        )
        for index, (object_type, source_object_id, operation_key) in enumerate((
            ("paragraph", "z", "op-z"),
            ("paragraph", "a", "op-a"),
            ("memory", "x", "op-x"),
        ))
    ]
    repository.replace_plan(
        job.id, items, plan_hash="stable-plan", source_snapshot={}, target_snapshot={},
        policy_snapshot={"policy_revision": 1}, approval_required=False, policy_revision=1,
        source_revision=1, target_revision=1, actor="alice",
    )
    with factory() as session:
        created_at = session.exec(select(MemoryTransferItem).where(MemoryTransferItem.job_id == job.id)).all()
        assert len(created_at) == 3
        for item in created_at:
            item.created_at = created_at[0].created_at
        session.commit()

    result = repository.items(job.id)
    assert [(item.object_type, item.source_object_id, item.operation_key) for item in result] == [
        ("memory", "x", "op-x"),
        ("paragraph", "a", "op-a"),
        ("paragraph", "z", "op-z"),
    ]
