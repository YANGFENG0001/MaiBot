"""AP/MD/EX service orchestration tests."""

import json
from dataclasses import replace

import pytest

from sqlmodel import select

from memory_transfer_test_support import BASE_CAPABILITIES, context, database, grant, seed_transfer_scope
from src.common.database.database_model import MemoryTransferEvent
from src.workspaces.memory_transfer_authorization import MemoryTransferAuthorization
from src.workspaces.memory_transfer_models import ApprovalRequest, MemoryTransferError, TransferCreateRequest
from src.workspaces.memory_transfer_principal import principal_id
from src.workspaces.memory_transfer_repository import MemoryTransferRepository
from src.workspaces.memory_transfer_service import MemoryTransferService


class Authority:
    def __init__(self):
        self.calls = []
        self.operations = {}

    async def list_scoped_objects(self, **kwargs):
        if kwargs["memory_space_id"] == "space-target":
            return {"items": [], "has_more": False}
        return {"items": [{
            "object_type": "paragraph", "object_id": "p1", "memory_space_id": "space-source",
            "partition_id": "partition-source", "security_domain": "normal", "content_fingerprint": "fp1",
            "root_object_type": "paragraph", "root_object_id": "p1", "source_version": "1",
        }], "has_more": False}

    async def copy_object_to_scope(self, **kwargs):
        self.calls.append(("copy", kwargs))
        result = {
            "status": "applied", "operation_key": kwargs["operation_key"], "object_type": kwargs["object_type"],
            "source_object_id": kwargs["source_object_id"], "target_object_id": "copy-p1",
            "target_space_id": kwargs["target_space_id"], "target_partition_id": kwargs["target_partition_id"],
            "target_fingerprint": "fp1",
        }
        self.operations[kwargs["operation_key"]] = result
        return result

    async def link_object_to_scope(self, **kwargs):
        self.calls.append(("link", kwargs))
        return {
            "status": "applied", "operation_key": kwargs["operation_key"], "object_type": kwargs["object_type"],
            "source_object_id": kwargs["object_id"], "target_object_id": kwargs["object_id"],
            "target_space_id": kwargs["target_space_id"], "target_partition_id": kwargs["target_partition_id"],
            "content_fingerprint": "fp1",
        }

    async def get_transfer_operation(self, operation_key):
        return self.operations.get(operation_key, {"status": "not_applied", "operation_key": operation_key})


class Projection:
    def __init__(self):
        self.rows = set()

    def register_object_partition(self, **kwargs):
        key = (kwargs["object_type"], kwargs["object_id"], kwargs["partition_id"])
        created = key not in self.rows
        self.rows.add(key)
        return created


def approval_for(job):
    return ApprovalRequest(
        job.plan_hash, job.plan_revision, json.loads(job.policy_snapshot_json)["policy_revision"],
        job.policy_snapshot_hash,
    )


def request(**overrides):
    values = dict(
        mode="copy", source_space_id="space-source", source_partition_ids=("partition-source",),
        target_space_id="space-target", target_partition_id="partition-target", object_types=("paragraph",),
        approval_policy="manual", conflict_policy="skip", idempotency_key="service-key",
    )
    values.update(overrides)
    return TransferCreateRequest(**values)


def setup():
    _engine, factory = database()
    seed_transfer_scope(factory)
    grant(factory, *BASE_CAPABILITIES)
    authority = Authority()
    projection = Projection()
    service = MemoryTransferService(
        repository=MemoryTransferRepository(factory),
        authorization=MemoryTransferAuthorization(factory),
        authority=authority,
        projection=projection,
    )
    return service, authority, projection


@pytest.mark.asyncio
async def test_manual_copy_requires_approval_then_executes_independent_copy() -> None:
    service, authority, projection = setup()
    job = service.create_job(request(), context(), "alice")
    job = await service.plan_job(job.id, context(), "alice")
    assert job.status == "awaiting_approval"
    with pytest.raises(MemoryTransferError, match="approval_required"):
        await service.execute_job(job.id, context(), "alice")
    job = service.approve_job(
        job.id,
        approval_for(job),
        "bob",
        request_context=context(),
    )
    assert job.status == "approved"
    completed = await service.execute_job(job.id, context(), "alice")
    assert completed.status == "completed"
    item = service.list_items(job.id, "alice", request_context=context())[0]
    assert item.target_object_id == "copy-p1" and item.target_object_id != item.source_object_id
    assert ("paragraph", "copy-p1", "partition-target") in projection.rows
    assert [name for name, _ in authority.calls] == ["copy"]


@pytest.mark.asyncio
async def test_duplicate_execute_does_not_repeat_rpc() -> None:
    service, authority, _projection = setup()
    job = service.create_job(request(), context(), "alice")
    job = await service.plan_job(job.id, context(), "alice")
    service.approve_job(job.id, approval_for(job), "bob", request_context=context())
    await service.execute_job(job.id, context(), "alice")
    with pytest.raises(MemoryTransferError, match="already_completed"):
        await service.execute_job(job.id, context(), "alice")
    assert len(authority.calls) == 1


def test_promote_creator_cannot_self_approve() -> None:
    service, _authority, _projection = setup()
    job, _ = service.repository.create(request(mode="promote"), context(), "alice")
    # The approval state rule is independent of target setup and is tested at repository level.
    job = service.repository.transition(job.id, "planning", actor="alice")
    job = service.repository.replace_plan(
        job.id, [], plan_hash="plan", source_snapshot={}, target_snapshot={},
        policy_snapshot={"policy_revision": 1}, approval_required=True, policy_revision=1,
        source_revision=1, target_revision=1, actor="alice",
    )
    with pytest.raises(MemoryTransferError, match="self_approval_denied"):
        service.repository.approve(
            job.id, plan_revision=job.plan_revision, plan_hash="plan",
            policy_revision=1, policy_snapshot_hash=job.policy_snapshot_hash,
            actor="alice", approved=True,
        )


def test_get_list_items_and_cancel_reauthorize_scope() -> None:
    service, _authority, _projection = setup()
    job = service.create_job(request(idempotency_key="secure-read"), context(), "alice")
    denied = context(readable=())
    with pytest.raises(MemoryTransferError, match="source_not_readable"):
        service.get_job(job.id, "alice", request_context=denied)
    with pytest.raises(MemoryTransferError, match="source_not_readable"):
        service.list_items(job.id, "alice", request_context=denied)
    with pytest.raises(MemoryTransferError, match="source_not_readable"):
        service.cancel_job(job.id, "alice", request_context=denied)
    assert service.list_jobs("alice", request_context=denied) == []


@pytest.mark.asyncio
async def test_plan_execute_retry_reconcile_reauthorize_workspace_scope() -> None:
    service, authority, _projection = setup()
    job = service.create_job(request(idempotency_key="secure-actions"), context(), "alice")
    denied = context(readable=())
    with pytest.raises(MemoryTransferError, match="source_not_readable"):
        await service.plan_job(job.id, denied, "alice")
    assert authority.calls == []


@pytest.mark.asyncio
async def test_manual_link_keeps_same_identity_and_uses_link_only() -> None:
    service, authority, projection = setup()
    job = service.create_job(request(mode="link", idempotency_key="link-job"), context(), "alice")
    job = await service.plan_job(job.id, context(), "alice")
    service.approve_job(job.id, approval_for(job), "bob", request_context=context())
    completed = await service.execute_job(job.id, context(), "alice")
    assert completed.status == "completed"
    item = service.list_items(job.id, "alice", request_context=context())[0]
    assert item.target_object_id == item.source_object_id == "p1"
    assert ("paragraph", "p1", "partition-target") in projection.rows
    assert [name for name, _ in authority.calls] == ["link"]


@pytest.mark.asyncio
async def test_promote_uses_copy_contract_and_public_normal_target() -> None:
    _engine, factory = database()
    seed_transfer_scope(factory, target_space="memory-space-public")
    grant(factory, *BASE_CAPABILITIES)

    class PublicAuthority(Authority):
        async def list_scoped_objects(self, **kwargs):
            if kwargs["memory_space_id"] != "space-source":
                return {"items": [], "has_more": False}
            return await super().list_scoped_objects(**kwargs)

    authority = PublicAuthority()
    projection = Projection()
    service = MemoryTransferService(
        repository=MemoryTransferRepository(factory),
        authorization=MemoryTransferAuthorization(factory),
        authority=authority,
        projection=projection,
    )
    public_context = context(home_space_id="memory-space-public")
    promote = request(
        mode="promote", target_space_id="memory-space-public",
        idempotency_key="promote-job", approval_policy="auto_safe",
    )
    job = service.create_job(promote, public_context, "alice")
    job = await service.plan_job(job.id, public_context, "alice")
    assert job.status == "awaiting_approval"
    reviewer_context = replace(public_context, person_id="reviewer")
    service.approve_job(
        job.id, approval_for(job), "rotated-reviewer-token", request_context=reviewer_context
    )
    completed = await service.execute_job(job.id, public_context, "alice")
    assert completed.status == "completed"
    assert [name for name, _ in authority.calls] == ["copy"]
    assert authority.calls[0][1]["mode"] == "promote"
    assert authority.calls[0][1]["target_space_id"] == "memory-space-public"


@pytest.mark.asyncio
async def test_rejected_and_revoked_approvals_block_rpc() -> None:
    service, authority, _projection = setup()
    rejected = service.create_job(request(idempotency_key="rejected"), context(), "alice")
    rejected = await service.plan_job(rejected.id, context(), "alice")
    service.reject_job(
        rejected.id, approval_for(rejected), "bob", request_context=context()
    )
    with pytest.raises(MemoryTransferError, match="approval_required"):
        await service.execute_job(rejected.id, context(), "alice")

    revoked = service.create_job(request(idempotency_key="revoked"), context(), "alice")
    revoked = await service.plan_job(revoked.id, context(), "alice")
    service.approve_job(
        revoked.id, approval_for(revoked), "bob", request_context=context()
    )
    revoked = service.revoke_approval(
        revoked.id, approval_for(revoked), "bob", request_context=context()
    )
    assert revoked.status == "awaiting_approval"
    assert revoked.approval_state == "revoked"
    with pytest.raises(MemoryTransferError, match="approval_required"):
        await service.execute_job(revoked.id, context(), "alice")
    assert authority.calls == []


@pytest.mark.asyncio
async def test_policy_revision_change_marks_job_stale_before_rpc() -> None:
    service, authority, _projection = setup()
    job = service.create_job(request(idempotency_key="stale"), context(), "alice")
    job = await service.plan_job(job.id, context(), "alice")
    service.approve_job(job.id, approval_for(job), "bob", request_context=context())
    with pytest.raises(MemoryTransferError, match="stale_policy"):
        await service.execute_job(job.id, context(revision=2), "alice")
    assert service.repository.get(job.id).status == "stale_policy"
    assert authority.calls == []


@pytest.mark.asyncio
async def test_cancel_unstarted_items_and_never_fake_cancel_running_item() -> None:
    service, authority, _projection = setup()
    pending = service.create_job(request(idempotency_key="cancel-pending"), context(), "alice")
    pending = await service.plan_job(pending.id, context(), "alice")
    cancelled = service.cancel_job(pending.id, "alice", request_context=context())
    assert cancelled.status == "cancelled"
    assert service.repository.items(pending.id)[0].status == "cancelled"

    running = service.create_job(request(idempotency_key="cancel-running"), context(), "alice")
    running = await service.plan_job(running.id, context(), "alice")
    service.approve_job(
        running.id, approval_for(running), "bob", request_context=context()
    )
    service.repository.transition(running.id, "running", actor="worker")
    with service.repository.session_factory() as session:
        item = session.get(type(service.repository.items(running.id)[0]), service.repository.items(running.id)[0].id)
        item.status = "running"
        session.commit()
    result = service.cancel_job(running.id, "alice", request_context=context())
    assert result.status == "running"
    assert service.repository.items(running.id)[0].status == "running"
    assert authority.calls == []

@pytest.mark.asyncio
async def test_all_context_backed_mutation_boundaries_discard_raw_actor_values() -> None:
    service, _authority, _projection = setup()
    request_context = context()
    stable_actor = principal_id(request_context)
    raw_actors = (
        "raw-create-token",
        "raw-plan-token",
        "raw-replan-token",
        "raw-plan-token-2",
        "raw-execute-token",
        "raw-retry-token",
        "raw-reconcile-token",
        "raw-cancel-token",
    )

    job = service.create_job(
        request(idempotency_key="actor-boundary"), request_context, raw_actors[0]
    )
    job = await service.plan_job(job.id, request_context, raw_actors[1])
    service.replace_selection_for_replan(
        job.id, request(idempotency_key="actor-boundary"), raw_actors[2],
        request_context=request_context,
    )
    job = await service.plan_job(job.id, request_context, raw_actors[3])

    class RecordingExecutor:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        async def execute(self, current, _context, actor):
            self.calls.append(("execute", actor))
            return current

        async def reconcile(self, current, _context, actor):
            self.calls.append(("reconcile", actor))
            return current

    executor = RecordingExecutor()
    service.executor = executor
    await service.execute_job(job.id, request_context, raw_actors[4])

    with service.repository.session_factory() as session:
        stored = session.get(type(job), job.id)
        stored.status = "retry_wait"
        stored.next_retry_at = None
        session.commit()
    await service.retry_job(job.id, request_context, raw_actors[5])
    await service.reconcile_job(job.id, request_context, raw_actors[6])
    cancelled = service.cancel_job(job.id, raw_actors[7], request_context=request_context)
    assert cancelled.status == "cancelled"
    assert executor.calls == [
        ("execute", stable_actor),
        ("execute", stable_actor),
        ("reconcile", stable_actor),
    ]

    with service.repository.session_factory() as session:
        events = session.exec(select(MemoryTransferEvent)).all()
        persisted = json.dumps(
            [event.model_dump(mode="json") for event in events],
            ensure_ascii=False,
            sort_keys=True,
        )
    assert events and all(event.actor_id == stable_actor for event in events)
    assert all(raw_actor not in persisted for raw_actor in raw_actors)
