"""PL01-PL14: non-mutating planning, snapshots, conflict and lineage."""

import pytest

from memory_transfer_test_support import BASE_CAPABILITIES, context, database, grant, seed_transfer_scope
from src.workspaces.memory_transfer_authorization import MemoryTransferAuthorization
from src.workspaces.memory_transfer_models import MemoryTransferError, TransferCreateRequest
from src.workspaces.memory_transfer_planner import MemoryTransferPlanner
from src.workspaces.memory_transfer_repository import MemoryTransferRepository


class Authority:
    def __init__(self, source=None, target=None):
        self.source = source or []
        self.target = target or []
        self.mutations = 0

    async def list_scoped_objects(self, **kwargs):
        items = self.source if kwargs["memory_space_id"] == "space-source" else self.target
        return {"items": items, "has_more": False}


def row(object_id="p1", *, space="space-source", partition="partition-source", fingerprint="fp1"):
    return {
        "object_type": "paragraph", "object_id": object_id, "memory_space_id": space,
        "partition_id": partition, "security_domain": "normal", "content_fingerprint": fingerprint,
        "root_object_type": "paragraph", "root_object_id": object_id, "source_version": "1",
    }


def request(**overrides):
    values = dict(
        mode="copy", source_space_id="space-source", source_partition_ids=("partition-source",),
        target_space_id="space-target", target_partition_id="partition-target", object_types=("paragraph",),
        approval_policy="manual", conflict_policy="skip", idempotency_key="plan-key",
    )
    values.update(overrides)
    return TransferCreateRequest(**values)


def setup(authority):
    _engine, factory = database()
    seed_transfer_scope(factory)
    grant(factory, *BASE_CAPABILITIES)
    repository = MemoryTransferRepository(factory)
    authorization = MemoryTransferAuthorization(factory)
    planner = MemoryTransferPlanner(repository, authorization, authority)
    return repository, planner


@pytest.mark.asyncio
async def test_pl01_pl12_plan_is_non_mutating_and_stable() -> None:
    authority = Authority(source=[row()])
    repository, planner = setup(authority)
    job, _ = repository.create(request(), context(), "alice")
    planned = await planner.plan(job, context(), "alice")
    assert planned.status == "awaiting_approval"
    assert planned.plan_hash
    assert authority.mutations == 0
    item = repository.items(job.id)[0]
    assert item.operation_key == f"memory-transfer-item:{job.id}:{item.id}"
    assert item.ancestry_hash


@pytest.mark.asyncio
async def test_pl13_duplicate_same_origin_is_skipped() -> None:
    source = row()
    target = row(space="space-target", partition="partition-target")
    authority = Authority(source=[source], target=[target])
    repository, planner = setup(authority)
    job, _ = repository.create(request(), context(), "alice")
    await planner.plan(job, context(), "alice")
    item = repository.items(job.id)[0]
    assert item.status == "skipped"
    assert item.conflict_code == "duplicate_same_origin"


@pytest.mark.asyncio
async def test_untrusted_scope_rows_are_discarded() -> None:
    authority = Authority(source=[row(space="wrong")])
    repository, planner = setup(authority)
    job, _ = repository.create(request(), context(), "alice")
    planned = await planner.plan(job, context(), "alice")
    assert planned.status == "awaiting_approval"
    assert repository.items(job.id) == []
    assert authority.mutations == 0


@pytest.mark.asyncio
async def test_content_conflict_skip_and_fail_do_not_mutate_target() -> None:
    source = row(fingerprint="same")
    target = row(object_id="other", space="space-target", partition="partition-target", fingerprint="same")
    authority = Authority(source=[source], target=[target])
    repository, planner = setup(authority)
    skipped, _ = repository.create(request(idempotency_key="skip-conflict"), context(), "alice")
    await planner.plan(skipped, context(), "alice")
    item = repository.items(skipped.id)[0]
    assert item.status == "skipped" and item.conflict_code == "content_conflict"
    repository2, planner2 = setup(authority)
    failed, _ = repository2.create(
        request(idempotency_key="fail-conflict", conflict_policy="fail"), context(), "alice"
    )
    with pytest.raises(MemoryTransferError, match="conflict"):
        await planner2.plan(failed, context(), "alice")
    assert authority.mutations == 0


@pytest.mark.asyncio
async def test_explicit_missing_object_id_is_rejected() -> None:
    authority = Authority(source=[row("p1")])
    repository, planner = setup(authority)
    job, _ = repository.create(
        request(idempotency_key="missing-id", object_ids=("missing",)), context(), "alice"
    )
    with pytest.raises(MemoryTransferError, match="source_object_not_found"):
        await planner.plan(job, context(), "alice")


@pytest.mark.asyncio
async def test_has_more_candidate_page_is_rejected_without_mutation() -> None:
    class PagedAuthority(Authority):
        async def list_scoped_objects(self, **kwargs):
            result = await super().list_scoped_objects(**kwargs)
            if kwargs["memory_space_id"] == "space-source":
                result["has_more"] = True
            return result

    authority = PagedAuthority(source=[row()])
    repository, planner = setup(authority)
    job, _ = repository.create(request(idempotency_key="too-many"), context(), "alice")
    with pytest.raises(MemoryTransferError, match="invalid_request"):
        await planner.plan(job, context(), "alice")
    assert authority.mutations == 0


@pytest.mark.asyncio
async def test_plan_hash_is_stable_for_same_canonical_candidates() -> None:
    authority = Authority(source=[row("b"), row("a")])
    repository, planner = setup(authority)
    first, _ = repository.create(request(idempotency_key="stable-a"), context(), "alice")
    second, _ = repository.create(request(idempotency_key="stable-b"), context(), "alice")
    planned_a = await planner.plan(first, context(), "alice")
    planned_b = await planner.plan(second, context(), "alice")
    assert planned_a.plan_hash == planned_b.plan_hash
