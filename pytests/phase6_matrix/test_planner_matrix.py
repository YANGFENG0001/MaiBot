"""PL01-PL05 planner acceptance cases for Phase 6."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
import json

import pytest

from sqlmodel import select

from memory_transfer_test_support import (
    BASE_CAPABILITIES,
    context,
    database,
    grant,
    seed_transfer_scope,
)
from src.common.database.database_model import (
    MemoryPartition,
    MemorySpace,
    MemorySpaceACL,
    MemoryTransferApproval,
    MemoryTransferAttempt,
    MemoryTransferEvent,
    MemoryTransferItem,
    MemoryTransferJob,
)
from src.workspaces.memory_transfer_authorization import MemoryTransferAuthorization
from src.workspaces.memory_transfer_models import MemoryTransferError, TransferCreateRequest
from src.workspaces.memory_transfer_principal import principal_id
from src.workspaces.memory_transfer_repository import MemoryTransferRepository
from src.workspaces.memory_transfer_service import MemoryTransferService

pytestmark = pytest.mark.phase6_matrix

Case = Callable[[], Awaitable[None]]


def _request(**overrides) -> TransferCreateRequest:
    values = {
        "mode": "copy",
        "source_space_id": "space-source",
        "source_partition_ids": ("partition-source",),
        "target_space_id": "space-target",
        "target_partition_id": "partition-target",
        "object_types": ("paragraph",),
        "object_ids": (),
        "filters": {},
        "approval_policy": "manual",
        "conflict_policy": "skip",
        "idempotency_key": "pl-key",
        "client_nonce": "",
    }
    values.update(overrides)
    return TransferCreateRequest(**values)


def _row(
    object_id: str = "p1",
    *,
    space: str = "space-source",
    partition: str = "partition-source",
    domain: str = "normal",
    fingerprint: str = "fp1",
    version: str = "1",
) -> dict:
    return {
        "object_type": "paragraph",
        "object_id": object_id,
        "memory_space_id": space,
        "partition_id": partition,
        "security_domain": domain,
        "content_fingerprint": fingerprint,
        "root_object_type": "paragraph",
        "root_object_id": object_id,
        "source_version": version,
    }


class AuthoritySpy:
    def __init__(
        self, *, source=None, target=None, source_has_more: bool = False, target_has_more: bool = False
    ) -> None:
        self.source = [_row()] if source is None else [dict(row) for row in source]
        self.target = [] if target is None else [dict(row) for row in target]
        self.source_has_more = source_has_more
        self.target_has_more = target_has_more
        self.list_calls: list[dict] = []
        self.mutation_calls: list[tuple[str, dict]] = []
        self.reconcile_calls: list[str] = []

    async def list_scoped_objects(self, **kwargs):
        self.list_calls.append(dict(kwargs))
        rows = self.source if kwargs["memory_space_id"] == "space-source" else self.target
        requested = set(kwargs.get("object_ids") or ())
        if requested:
            rows = [row for row in rows if row.get("object_id") in requested]
        has_more = (
            self.source_has_more
            if kwargs["memory_space_id"] == "space-source"
            else self.target_has_more
        )
        return {"items": [dict(row) for row in rows], "has_more": has_more}

    async def copy_object_to_scope(self, **kwargs):
        self.mutation_calls.append(("copy", dict(kwargs)))
        raise AssertionError("planning must not mutate authority")

    async def link_object_to_scope(self, **kwargs):
        self.mutation_calls.append(("link", dict(kwargs)))
        raise AssertionError("planning must not mutate authority")

    async def get_transfer_operation(self, operation_key):
        self.reconcile_calls.append(operation_key)
        raise AssertionError("planning must not reconcile authority")


class ProjectionSpy:
    def __init__(self) -> None:
        self.calls = 0

    def register_object_partition(self, **_kwargs):
        self.calls += 1
        raise AssertionError("planning must not project objects")


def _setup(
    *, source=None, target=None, source_has_more: bool = False, target_has_more: bool = False,
    capabilities=BASE_CAPABILITIES,
):
    _engine, factory = database()
    seed_transfer_scope(factory)
    grant(factory, *capabilities)
    authority = AuthoritySpy(
        source=source, target=target,
        source_has_more=source_has_more, target_has_more=target_has_more,
    )
    projection = ProjectionSpy()
    repository = MemoryTransferRepository(factory)
    service = MemoryTransferService(
        repository=repository,
        authorization=MemoryTransferAuthorization(factory),
        authority=authority,
        projection=projection,
    )
    return factory, service, authority, projection


def _counts(factory) -> dict[str, int]:
    models = (
        (MemoryTransferJob, "jobs"),
        (MemoryTransferItem, "items"),
        (MemoryTransferApproval, "approvals"),
        (MemoryTransferAttempt, "attempts"),
        (MemoryTransferEvent, "events"),
    )
    with factory() as session:
        return {key: len(session.exec(select(model)).all()) for model, key in models}


def _assert_no_mutation(authority: AuthoritySpy, projection: ProjectionSpy) -> None:
    assert authority.mutation_calls == authority.reconcile_calls == []
    assert projection.calls == 0


async def _az_plan(service, request, ctx):
    actor = principal_id(ctx)
    job = service.create_job(request, ctx, actor)
    return await service.plan_job(job.id, ctx, actor)


async def _pl01() -> None:
    authorized = _row("authorized", fingerprint="fp-authorized")
    untrusted = _row(
        "foreign", space="space-foreign", partition="partition-foreign",
        domain="kami", fingerprint="fp-foreign",
    )
    factory, service, authority, projection = _setup(source=[untrusted, authorized])
    planned = await _az_plan(
        service, _request(idempotency_key="pl01"), context()
    )
    items = service.repository.items(planned.id)
    assert [item.source_object_id for item in items] == ["authorized"]
    assert all(item.source_space_id == "space-source" for item in items)
    assert len(authority.list_calls) == 2
    _assert_no_mutation(authority, projection)
    assert _counts(factory)["items"] == 1


async def _pl02() -> None:
    missing_scope = _row("missing")
    del missing_scope["security_domain"]
    factory, service, authority, projection = _setup(source=[missing_scope])
    ctx = context()
    actor = principal_id(ctx)
    job = service.create_job(_request(idempotency_key="pl02"), ctx, actor)
    with pytest.raises(MemoryTransferError) as caught:
        await service.plan_job(job.id, ctx, actor)
    assert caught.value.code == "missing_scope_metadata"
    assert service.repository.get(job.id).status == "failed"
    assert service.repository.items(job.id) == []
    assert len(authority.list_calls) == 2
    _assert_no_mutation(authority, projection)
    counts = _counts(factory)
    assert counts["jobs"] == 1 and counts["items"] == 0


async def _pl03() -> None:
    factory, service, authority, projection = _setup(source=[])
    ctx = context()
    actor = principal_id(ctx)
    job = service.create_job(
        _request(object_ids=("missing",), idempotency_key="pl03"), ctx, actor
    )
    with pytest.raises(MemoryTransferError) as caught:
        await service.plan_job(job.id, ctx, actor)
    assert caught.value.code == "source_object_not_found"
    assert service.repository.get(job.id).status == "failed"
    assert service.repository.items(job.id) == []
    assert len(authority.list_calls) == 2
    _assert_no_mutation(authority, projection)
    assert _counts(factory)["items"] == 0


async def _pl04() -> None:
    factory, service, authority, projection = _setup()
    with factory() as session:
        target = session.get(MemoryPartition, "partition-target")
        session.delete(target)
        session.commit()
    ctx = context()
    with pytest.raises(MemoryTransferError) as caught:
        service.create_job(_request(idempotency_key="pl04"), ctx, principal_id(ctx))
    assert caught.value.code == "target_not_found"
    assert _counts(factory) == {
        "jobs": 0, "items": 0, "approvals": 0, "attempts": 0, "events": 0,
    }
    assert authority.list_calls == []
    _assert_no_mutation(authority, projection)


async def _pl05() -> None:
    _factory, service, authority, projection = _setup()
    request = _request(target_partition_id="partition-source", idempotency_key="pl05")
    with pytest.raises(MemoryTransferError) as caught:
        service.planner._build_item(
            "job-pl05", request, _row(), []
        )
    assert caught.value.code == "same_source_target"
    assert authority.list_calls == []
    _assert_no_mutation(authority, projection)


async def _pl06() -> None:
    rows = [
        _row("c", fingerprint="fp-c"),
        _row("b", fingerprint="fp-b"),
        _row("a", fingerprint="fp-a"),
    ]
    factory, service, authority, projection = _setup(source=rows)
    planned = await _az_plan(
        service,
        _request(object_ids=("c", "a", "c"), idempotency_key="pl06"),
        context(),
    )
    items = service.repository.items(planned.id)
    assert [item.source_object_id for item in items] == ["a", "c"]
    assert [item.content_fingerprint for item in items] == ["fp-a", "fp-c"]
    assert len(authority.list_calls) == 2
    _assert_no_mutation(authority, projection)
    assert _counts(factory)["items"] == 2


async def _pl07() -> None:
    rows = [
        _row("a", fingerprint="fp-a"),
        _row("b", fingerprint="fp-b"),
        _row(
            "foreign", space="space-foreign", partition="partition-foreign",
            fingerprint="fp-b",
        ),
    ]
    factory, service, authority, projection = _setup(source=rows)
    planned = await _az_plan(
        service,
        _request(filters={"fingerprint": ["fp-b"]}, idempotency_key="pl07"),
        context(),
    )
    items = service.repository.items(planned.id)
    assert len(items) == 1
    assert items[0].source_object_id == "b"
    assert items[0].content_fingerprint == "fp-b"
    assert len(authority.list_calls) == 2
    _assert_no_mutation(authority, projection)
    assert _counts(factory)["items"] == 1


async def _pl08() -> None:
    for source_has_more, target_has_more, expected_calls in (
        (True, False, 1),
        (False, True, 2),
    ):
        factory, service, authority, projection = _setup(
            source_has_more=source_has_more, target_has_more=target_has_more
        )
        ctx = context()
        actor = principal_id(ctx)
        job = service.create_job(
            _request(
                idempotency_key=f"pl08-{source_has_more}-{target_has_more}"
            ),
            ctx,
            actor,
        )
        with pytest.raises(MemoryTransferError) as caught:
            await service.plan_job(job.id, ctx, actor)
        assert caught.value.code == "invalid_request"
        assert service.repository.get(job.id).status == "failed"
        assert service.repository.items(job.id) == []
        assert len(authority.list_calls) == expected_calls
        _assert_no_mutation(authority, projection)
        assert _counts(factory)["items"] == 0


async def _pl09() -> None:
    factory, service, authority, projection = _setup(
        source=[_row("a", fingerprint="fp-a"), _row("b", fingerprint="fp-b")]
    )
    planned = await _az_plan(
        service, _request(idempotency_key="pl09"), context()
    )
    assert planned.status == "awaiting_approval"
    assert len(service.repository.items(planned.id)) == 2
    assert len(authority.list_calls) == 2
    _assert_no_mutation(authority, projection)
    counts = _counts(factory)
    assert counts["jobs"] == 1 and counts["items"] == 2
    assert counts["approvals"] == counts["attempts"] == 0


async def _pl10() -> None:
    factory, service, authority, projection = _setup(source=[_row("snap", fingerprint="fp-snap")])
    ctx = context()
    request = _request(
        object_ids=("snap",), filters={"fingerprint": "fp-snap"},
        idempotency_key="pl10",
    )
    planned = await _az_plan(service, request, ctx)
    source_snapshot = json.loads(planned.source_snapshot_json)
    target_snapshot = json.loads(planned.target_snapshot_json)
    policy_snapshot = json.loads(planned.policy_snapshot_json)
    selection = json.loads(planned.selection_json)
    assert source_snapshot == {
        "partition_ids": ["partition-source"], "revision": 1,
        "security_domain": "normal", "space_id": "space-source",
    }
    assert target_snapshot == {
        "partition_id": "partition-target", "revision": 1,
        "security_domain": "normal", "space_id": "space-target",
    }
    assert policy_snapshot["source"]["space_id"] == "space-source"
    assert policy_snapshot["target"]["space_id"] == "space-target"
    assert policy_snapshot["policy_revision"] == ctx.policy_revision
    assert selection == request.canonical()
    assert planned.plan_hash and planned.policy_snapshot_hash
    assert planned.source_space_revision == planned.target_space_revision == 1
    assert len(authority.list_calls) == 2
    _assert_no_mutation(authority, projection)
    assert _counts(factory)["items"] == 1


def _semantic_items(service: MemoryTransferService, job_id: str) -> list[tuple]:
    return sorted(
        (
            item.object_type, item.source_object_id, item.source_partition_id,
            item.target_partition_id, item.content_fingerprint, item.source_version,
            item.status, item.conflict_code, item.ancestry_hash,
        )
        for item in service.repository.items(job_id)
    )


async def _pl11() -> None:
    source_a = [_row("b", fingerprint="fp-b"), _row("a", fingerprint="fp-a")]
    source_b = list(reversed(source_a))
    target_a = [
        _row("tb", space="space-target", partition="partition-target", fingerprint="target-b"),
        _row("ta", space="space-target", partition="partition-target", fingerprint="target-a"),
    ]
    target_b = list(reversed(target_a))
    _factory_a, service_a, authority_a, projection_a = _setup(source=source_a, target=target_a)
    _factory_b, service_b, authority_b, projection_b = _setup(source=source_b, target=target_b)
    request_a = _request(object_ids=("b", "a", "b"), idempotency_key="pl11-a")
    request_b = _request(object_ids=("a", "b"), idempotency_key="pl11-b")
    planned_a = await _az_plan(service_a, request_a, context())
    planned_b = await _az_plan(service_b, request_b, context())
    assert planned_a.plan_hash == planned_b.plan_hash
    assert planned_a.policy_snapshot_hash == planned_b.policy_snapshot_hash
    assert _semantic_items(service_a, planned_a.id) == _semantic_items(service_b, planned_b.id)
    assert len(authority_a.list_calls) == len(authority_b.list_calls) == 2
    _assert_no_mutation(authority_a, projection_a)
    _assert_no_mutation(authority_b, projection_b)


async def _pl12() -> None:
    mutations = ("context", "source_space", "target_space", "source_partition", "target_partition", "acl")
    for component in mutations:
        capabilities = (*BASE_CAPABILITIES, "memory.transfer.auto_approve_safe")
        factory, service, authority, projection = _setup(capabilities=capabilities)
        original_ctx = context(revision=1)
        actor = principal_id(original_ctx)
        job = service.create_job(
            _request(approval_policy="auto_safe", idempotency_key=f"pl12-{component}"),
            original_ctx,
            actor,
        )
        planned = await service.plan_job(job.id, original_ctx, actor)
        assert planned.status == "approved"
        execute_ctx = original_ctx
        if component == "context":
            execute_ctx = replace(original_ctx, policy_revision=2)
        else:
            with factory() as session:
                if component == "source_space":
                    session.get(MemorySpace, "space-source").policy_revision += 1
                elif component == "target_space":
                    session.get(MemorySpace, "space-target").policy_revision += 1
                elif component == "source_partition":
                    session.get(MemoryPartition, "partition-source").policy_revision += 1
                elif component == "target_partition":
                    session.get(MemoryPartition, "partition-target").policy_revision += 1
                else:
                    outbound = session.exec(
                        select(MemorySpaceACL).where(
                            MemorySpaceACL.owner_space_id == "space-source",
                            MemorySpaceACL.peer_space_id == "space-target",
                        )
                    ).one()
                    outbound.filters_json = json.dumps({"ids": ["p1"]})
                session.commit()
        before_lists = list(authority.list_calls)
        with pytest.raises(MemoryTransferError) as caught:
            await service.execute_job(job.id, execute_ctx, principal_id(execute_ctx))
        assert caught.value.code == "stale_policy"
        assert service.repository.get(job.id).status == "stale_policy"
        assert authority.list_calls == before_lists
        _assert_no_mutation(authority, projection)


async def _pl13() -> None:
    source = _row("source", fingerprint="same-fp")
    target = _row(
        "copy-existing", space="space-target", partition="partition-target",
        fingerprint="same-fp",
    )
    target["root_object_type"] = "paragraph"
    target["root_object_id"] = "source"
    factory, service, authority, projection = _setup(source=[source], target=[target])
    planned = await _az_plan(
        service, _request(idempotency_key="pl13"), context()
    )
    items = service.repository.items(planned.id)
    assert len(items) == 1
    assert items[0].status == "skipped"
    assert items[0].conflict_code == "duplicate_same_origin"
    assert len(authority.list_calls) == 2
    _assert_no_mutation(authority, projection)
    assert _counts(factory)["items"] == 1


async def _pl14() -> None:
    source = _row("source", fingerprint="same-fp")
    target = _row(
        "unrelated", space="space-target", partition="partition-target",
        fingerprint="same-fp",
    )
    target["root_object_type"] = "paragraph"
    target["root_object_id"] = "other-root"
    skip_factory, skip_service, skip_authority, skip_projection = _setup(
        source=[source], target=[target]
    )
    skipped = await _az_plan(
        skip_service,
        _request(conflict_policy="skip", idempotency_key="pl14-skip"),
        context(),
    )
    skip_items = skip_service.repository.items(skipped.id)
    assert len(skip_items) == 1
    assert skip_items[0].status == "skipped"
    assert skip_items[0].conflict_code == "content_conflict"
    _assert_no_mutation(skip_authority, skip_projection)
    assert _counts(skip_factory)["items"] == 1

    fail_factory, fail_service, fail_authority, fail_projection = _setup(
        source=[source], target=[target]
    )
    fail_ctx = context()
    actor = principal_id(fail_ctx)
    fail_job = fail_service.create_job(
        _request(conflict_policy="fail", idempotency_key="pl14-fail"),
        fail_ctx,
        actor,
    )
    with pytest.raises(MemoryTransferError) as caught:
        await fail_service.plan_job(fail_job.id, fail_ctx, actor)
    assert caught.value.code == "conflict"
    assert fail_service.repository.get(fail_job.id).status == "failed"
    assert fail_service.repository.items(fail_job.id) == []
    _assert_no_mutation(fail_authority, fail_projection)
    assert _counts(fail_factory)["items"] == 0


CASES: tuple[tuple[str, Case], ...] = (
    ("PL01", _pl01), ("PL02", _pl02), ("PL03", _pl03),
    ("PL04", _pl04), ("PL05", _pl05), ("PL06", _pl06),
    ("PL07", _pl07), ("PL08", _pl08), ("PL09", _pl09),
    ("PL10", _pl10), ("PL11", _pl11), ("PL12", _pl12),
    ("PL13", _pl13), ("PL14", _pl14),
)


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario_id,case", CASES, ids=[item[0] for item in CASES])
async def test_phase6_pl_matrix(scenario_id: str, case: Case) -> None:
    assert case.__name__ == f"_{scenario_id.lower()}"
    await case()
