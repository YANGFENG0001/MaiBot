"""EX04-EX17: retry, lease, crash recovery and reconciliation."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from memory_transfer_test_support import BASE_CAPABILITIES, context, database, grant, seed_transfer_scope
from src.workspaces.memory_transfer_authorization import MemoryTransferAuthorization
from src.workspaces.memory_transfer_executor import deterministic_retry_jitter
from src.workspaces.memory_transfer_models import ApprovalRequest, MemoryTransferError, TransferCreateRequest
from src.workspaces.memory_transfer_repository import MemoryTransferRepository
from src.workspaces.memory_transfer_service import MemoryTransferService


class RecoveringAuthority:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.mutation_keys = []
        self.operations = {}
        self.get_status = None

    async def list_scoped_objects(self, **kwargs):
        if kwargs["memory_space_id"] != "space-source":
            return {"items": [], "has_more": False}
        return {
            "items": [
                {
                    "object_type": "paragraph",
                    "object_id": "p1",
                    "memory_space_id": "space-source",
                    "partition_id": "partition-source",
                    "security_domain": "normal",
                    "content_fingerprint": "fp1",
                    "root_object_type": "paragraph",
                    "root_object_id": "p1",
                    "source_version": "1",
                }
            ],
            "has_more": False,
        }

    async def copy_object_to_scope(self, **kwargs):
        self.mutation_keys.append(kwargs["operation_key"])
        outcome = self.outcomes.pop(0) if self.outcomes else "success"
        result = {
            "status": "applied",
            "operation_key": kwargs["operation_key"],
            "object_type": kwargs["object_type"],
            "source_object_id": kwargs["source_object_id"],
            "target_object_id": "copy-p1",
            "target_space_id": kwargs["target_space_id"],
            "target_partition_id": kwargs["target_partition_id"],
            "target_fingerprint": "fp1",
        }
        if outcome == "applied_timeout":
            self.operations[kwargs["operation_key"]] = result
            raise TimeoutError
        if outcome == "timeout":
            raise TimeoutError
        if outcome == "forbidden":
            raise PermissionError
        if outcome == "server_error":
            raise RuntimeError("upstream body must not enter audit")
        self.operations[kwargs["operation_key"]] = result
        return result

    async def get_transfer_operation(self, operation_key):
        if self.get_status == "unknown":
            return {"status": "unknown", "operation_key": operation_key}
        return self.operations.get(operation_key, {"status": "not_applied", "operation_key": operation_key})


class FlakyProjection:
    def __init__(self, failures=0):
        self.failures = failures
        self.rows = set()

    def register_object_partition(self, **kwargs):
        if self.failures:
            self.failures -= 1
            raise RuntimeError("database busy")
        self.rows.add((kwargs["object_type"], kwargs["object_id"], kwargs["partition_id"]))
        return True


def request(key="retry-key"):
    return TransferCreateRequest(
        mode="copy",
        source_space_id="space-source",
        source_partition_ids=("partition-source",),
        target_space_id="space-target",
        target_partition_id="partition-target",
        object_types=("paragraph",),
        approval_policy="manual",
        conflict_policy="skip",
        idempotency_key=key,
    )


def setup(outcomes, *, projection=None):
    _engine, factory = database()
    seed_transfer_scope(factory)
    grant(factory, *BASE_CAPABILITIES)
    authority = RecoveringAuthority(outcomes)
    service = MemoryTransferService(
        repository=MemoryTransferRepository(factory),
        authorization=MemoryTransferAuthorization(factory),
        authority=authority,
        projection=projection or FlakyProjection(),
    )
    return factory, service, authority


async def prepared(service, *, key="retry-key"):
    job = service.create_job(request(key), context(), "alice")
    job = await service.plan_job(job.id, context(), "alice")
    return service.approve_job(
        job.id,
        ApprovalRequest(job.plan_hash, job.plan_revision, 1, job.policy_snapshot_hash),
        "bob",
        request_context=context(),
    )


def make_retry_due(service, job_id):
    with service.repository.session_factory() as session:
        stored = session.get(type(service.repository.get(job_id)), job_id)
        stored.next_retry_at = datetime.now() - timedelta(seconds=1)
        session.commit()


def test_retry_jitter_is_deterministic_and_small():
    assert deterministic_retry_jitter("job", "item") == deterministic_retry_jitter("job", "item")
    assert 0 <= deterministic_retry_jitter("job", "item") <= 2


@pytest.mark.asyncio
async def test_timeout_then_not_applied_retries_with_same_operation_key() -> None:
    _factory, service, authority = setup(["timeout", "success"])
    job = await prepared(service)
    first = await service.execute_job(job.id, context(), "alice")
    assert first.status == "retry_wait"
    make_retry_due(service, job.id)
    second = await service.retry_job(job.id, context(), "alice")
    assert second.status == "completed"
    assert len(authority.mutation_keys) == 2
    assert len(set(authority.mutation_keys)) == 1


@pytest.mark.asyncio
async def test_timeout_after_apply_reconciles_without_second_mutation() -> None:
    _factory, service, authority = setup(["applied_timeout"])
    job = await prepared(service, key="applied-timeout")
    assert (await service.execute_job(job.id, context(), "alice")).status == "retry_wait"
    make_retry_due(service, job.id)
    assert (await service.retry_job(job.id, context(), "alice")).status == "completed"
    assert len(authority.mutation_keys) == 1


@pytest.mark.asyncio
async def test_unknown_result_stays_retry_wait_and_does_not_change_key() -> None:
    _factory, service, authority = setup(["timeout"])
    job = await prepared(service, key="unknown")
    await service.execute_job(job.id, context(), "alice")
    make_retry_due(service, job.id)
    authority.get_status = "unknown"
    retried = await service.retry_job(job.id, context(), "alice")
    assert retried.status == "retry_wait"
    item = service.list_items(job.id, "alice", request_context=context())[0]
    assert item.error_code == "unknown_result_needs_reconcile"
    assert authority.mutation_keys == [item.operation_key]


@pytest.mark.asyncio
async def test_local_projection_failure_is_repaired_from_applied_operation() -> None:
    projection = FlakyProjection(failures=1)
    _factory, service, authority = setup(["success"], projection=projection)
    job = await prepared(service, key="projection-failure")
    assert (await service.execute_job(job.id, context(), "alice")).status == "retry_wait"
    assert len(authority.mutation_keys) == 1
    make_retry_due(service, job.id)
    assert (await service.retry_job(job.id, context(), "alice")).status == "completed"
    assert len(authority.mutation_keys) == 1
    assert ("paragraph", "copy-p1", "partition-target") in projection.rows


@pytest.mark.asyncio
async def test_permission_error_is_permanent_and_never_retried() -> None:
    _factory, service, authority = setup(["forbidden"])
    job = await prepared(service, key="forbidden")
    failed = await service.execute_job(job.id, context(), "alice")
    assert failed.status == "failed"
    item = service.list_items(job.id, "alice", request_context=context())[0]
    assert item.status == "failed_permanent" and item.error_code == "permission_denied"
    assert len(authority.mutation_keys) == 1


@pytest.mark.asyncio
async def test_retry_permission_or_revision_revocation_stops_before_rpc() -> None:
    factory, service, authority = setup(["timeout"])
    job = await prepared(service, key="revoked-retry")
    await service.execute_job(job.id, context(), "alice")
    with factory() as session:
        from src.common.database.database_model import MemoryPermissionGroupCapability
        from sqlmodel import select

        capability = session.exec(
            select(MemoryPermissionGroupCapability).where(
                MemoryPermissionGroupCapability.capability == "memory.transfer.read_source"
            )
        ).one()
        capability.enabled = False
        session.commit()
    with pytest.raises(MemoryTransferError, match="transfer_capability_required"):
        await service.retry_job(job.id, context(), "alice")
    assert len(authority.mutation_keys) == 1


def test_expired_lease_can_be_taken_over_but_live_lease_cannot() -> None:
    factory, service, _authority = setup([])
    job = service.create_job(request("lease"), context(), "alice")
    first = service.repository.acquire_lease(job.id, worker_id="worker-a", ttl_seconds=60)
    with pytest.raises(MemoryTransferError, match="lease_lost"):
        service.repository.acquire_lease(job.id, worker_id="worker-b", ttl_seconds=60)
    with factory() as session:
        stored = session.get(type(job), job.id)
        stored.lease_expires_at = datetime.now() - timedelta(seconds=1)
        session.commit()
    second = service.repository.acquire_lease(job.id, worker_id="worker-b", ttl_seconds=60)
    assert second != first
    service.repository.release_lease(job.id, second)
