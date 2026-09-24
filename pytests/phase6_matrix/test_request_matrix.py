"""RQ01-RQ05 and RQ13 DTO acceptance cases for Phase 6."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime
import json

import pytest

from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from memory_transfer_test_support import (
    BASE_CAPABILITIES,
    context,
    database,
    grant,
    seed_transfer_scope,
)
from src.common.database.database_model import (
    MemoryTransferApproval,
    MemoryTransferAttempt,
    MemoryTransferEvent,
    MemoryTransferItem,
    MemoryTransferJob,
)
from src.common.database.migrations.models import MigrationExecutionContext
from src.common.database.migrations.v46_to_v47 import migrate_v46_to_v47
from src.workspaces.memory_transfer_authorization import MemoryTransferAuthorization
from src.workspaces.memory_transfer_models import ApprovalRequest, MemoryTransferError, TransferCreateRequest
from src.workspaces.memory_transfer_principal import namespaced_idempotency_key, principal_id
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
        "idempotency_key": "rq-key",
        "client_nonce": "",
    }
    values.update(overrides)
    return TransferCreateRequest(**values)


class CountingAuthority:
    def __init__(self, *, source=None, target=None) -> None:
        self.source = [] if source is None else [dict(row) for row in source]
        self.target = [] if target is None else [dict(row) for row in target]
        self.list_calls: list[dict] = []
        self.mutation_calls: list[tuple[str, dict]] = []
        self.reconcile_calls: list[str] = []

    async def list_scoped_objects(self, **kwargs):
        self.list_calls.append(dict(kwargs))
        rows = self.source if kwargs["memory_space_id"] == "space-source" else self.target
        object_ids = set(kwargs.get("object_ids") or ())
        if object_ids:
            rows = [row for row in rows if row["object_id"] in object_ids]
        return {"items": [dict(row) for row in rows], "has_more": False}

    async def copy_object_to_scope(self, **kwargs):
        self.mutation_calls.append(("copy", dict(kwargs)))
        return {
            "status": "applied",
            "operation_key": kwargs["operation_key"],
            "mode": kwargs["mode"],
            "object_type": kwargs["object_type"],
            "source_object_id": kwargs["source_object_id"],
            "target_object_id": f"copy-{kwargs['source_object_id']}",
            "source_space_id": kwargs["source_space_id"],
            "source_partition_id": kwargs["source_partition_id"],
            "target_space_id": kwargs["target_space_id"],
            "target_partition_id": kwargs["target_partition_id"],
            "source_security_domain": kwargs["source_security_domain"],
            "target_security_domain": kwargs["target_security_domain"],
            "source_fingerprint": kwargs.get("expected_fingerprint", ""),
            "target_fingerprint": kwargs.get("expected_fingerprint", ""),
            "root_object_type": kwargs["root_object_type"],
            "root_object_id": kwargs["root_object_id"],
        }

    async def link_object_to_scope(self, **kwargs):
        self.mutation_calls.append(("link", dict(kwargs)))
        return {
            "status": "applied",
            "operation_key": kwargs["operation_key"],
            "object_type": kwargs["object_type"],
            "object_id": kwargs["object_id"],
            "source_object_id": kwargs["object_id"],
            "target_object_id": kwargs["object_id"],
            "source_space_id": kwargs["source_space_id"],
            "source_partition_id": kwargs["source_partition_id"],
            "target_space_id": kwargs["target_space_id"],
            "target_partition_id": kwargs["target_partition_id"],
            "source_security_domain": kwargs["source_security_domain"],
            "target_security_domain": kwargs["target_security_domain"],
        }

    async def get_transfer_operation(self, operation_key):
        self.reconcile_calls.append(operation_key)
        return {"status": "not_applied", "operation_key": operation_key}


class ProjectionSpy:
    def __init__(self) -> None:
        self.rows: set[tuple[str, str, str]] = set()
        self.calls = 0

    def register_object_partition(self, **kwargs):
        self.calls += 1
        key = (kwargs["object_type"], kwargs["object_id"], kwargs["partition_id"])
        created = key not in self.rows
        self.rows.add(key)
        return created


def _setup(*, source=None, target=None):
    _engine, factory = database()
    seed_transfer_scope(factory)
    grant(factory, *BASE_CAPABILITIES)
    authority = CountingAuthority(source=source, target=target)
    repository = MemoryTransferRepository(factory)
    service = MemoryTransferService(
        repository=repository,
        authorization=MemoryTransferAuthorization(factory),
        authority=authority,
        projection=ProjectionSpy(),
    )
    return factory, service, authority


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


def _events(factory, job_id: str) -> list[tuple]:
    with factory() as session:
        rows = session.exec(
            select(MemoryTransferEvent)
            .where(MemoryTransferEvent.job_id == job_id)
            .order_by(MemoryTransferEvent.created_at, MemoryTransferEvent.id)
        ).all()
        return [
            (row.event_type, row.from_status, row.to_status, row.result_code, row.details_json)
            for row in rows
        ]


def _all_items(factory, job_id: str) -> list[MemoryTransferItem]:
    with factory() as session:
        rows = session.exec(
            select(MemoryTransferItem)
            .where(MemoryTransferItem.job_id == job_id)
            .order_by(MemoryTransferItem.plan_revision, MemoryTransferItem.id)
        ).all()
        for row in rows:
            session.expunge(row)
        return list(rows)


def _assert_error(request: TransferCreateRequest, code: str) -> None:
    with pytest.raises(MemoryTransferError) as caught:
        request.canonical()
    assert caught.value.code == code
    assert caught.value.retryable is False


async def _rq01() -> None:
    _assert_error(_request(mode="overwrite"), "invalid_mode")


async def _rq02() -> None:
    for value in (
        "memory", "summary", "episode", "external_memory", "file", "image",
        "person_profile", "arbitrary",
    ):
        _assert_error(_request(object_types=(value,)), "unsupported_object_type")
    _assert_error(_request(object_types=("paragraph", "file")), "unsupported_object_type")


async def _rq03() -> None:
    for overrides in (
        {"source_space_id": ""}, {"source_space_id": "   "},
        {"target_space_id": ""}, {"target_space_id": "   "},
        {"target_partition_id": ""}, {"target_partition_id": "   "},
        {"source_partition_ids": ()}, {"source_partition_ids": ("", "   ")},
    ):
        _assert_error(_request(**overrides), "invalid_partition")
    for overrides in (
        {"source_space_id": None}, {"target_space_id": 123},
        {"target_partition_id": []}, {"source_partition_ids": (123,)},
    ):
        _assert_error(_request(**overrides), "invalid_request")
    canonical = _request(source_partition_ids=("partition-source", "")).canonical()
    assert canonical["source_partition_ids"] == ["partition-source"]


async def _rq04() -> None:
    hostile_filters = (
        {"sql": "SELECT * FROM memories"},
        {"script": "<script>alert(1)</script>"},
        {"url": "https://evil.example/x"},
        {"ids": ["https://evil.example/x"]},
        {"fingerprint": "<script>alert(1)</script>"},
        {"updated_after": "SELECT * FROM memories"},
        {"updated_before": "javascript:alert(1)"},
        {"object_type": ["paragraph", "https://evil.example/type"]},
    )
    for filters in hostile_filters:
        _assert_error(_request(filters=filters), "invalid_request")

    factory, service, authority = _setup()
    with pytest.raises(MemoryTransferError) as caught:
        service.create_job(_request(filters={"ids": ["https://evil.example/x"]}), context(), "alice")
    assert caught.value.code == "invalid_request"
    assert _counts(factory) == {"jobs": 0, "items": 0, "approvals": 0, "attempts": 0, "events": 0}
    assert authority.list_calls == authority.mutation_calls == authority.reconcile_calls == []


async def _rq05() -> None:
    invalid = (
        {"object_ids": tuple(f"id-{index}" for index in range(10_001))},
        {"source_partition_ids": tuple(f"partition-{index}" for index in range(129))},
        {"filters": {"limit": 10_001}}, {"filters": {"limit": 0}},
        {"filters": {"limit": True}}, {"filters": {"ids": {object()}}},
        {"source_space_id": "x" * 65}, {"target_partition_id": "x" * 97},
        {"object_ids": ("x" * 256,)}, {"object_ids": (123,)},
        {"source_partition_ids": (123,)}, {"idempotency_key": "x" * 129},
        {"client_nonce": "x" * 129},
        {"filters": {"ids": [f"{index:04d}-" + "x" * 195 for index in range(400)]}},
    )
    for overrides in invalid:
        _assert_error(_request(**overrides), "invalid_request")

    assert _request(filters={"limit": 1}).canonical()["filters"]["limit"] == 1
    assert _request(filters={"limit": 10_000}).canonical()["filters"]["limit"] == 10_000
    assert len(_request(object_ids=tuple(f"id-{index}" for index in range(10_000))).canonical()["object_ids"]) == 10_000
    assert len(_request(source_partition_ids=tuple(f"p-{index}" for index in range(128))).canonical()["source_partition_ids"]) == 128
    safe_ids = [f"id-{index}" for index in range(100)]
    assert _request(filters={"ids": safe_ids}).canonical()["filters"]["ids"] == sorted(safe_ids)


def _row(object_id: str, *, fingerprint: str, version: str = "1") -> dict:
    return {
        "object_type": "paragraph",
        "object_id": object_id,
        "memory_space_id": "space-source",
        "partition_id": "partition-source",
        "security_domain": "normal",
        "content_fingerprint": fingerprint,
        "root_object_type": "paragraph",
        "root_object_id": object_id,
        "source_version": version,
    }


def _target_row(object_id: str, *, fingerprint: str) -> dict:
    row = _row(object_id, fingerprint=fingerprint)
    row.update(memory_space_id="space-target", partition_id="partition-target")
    return row


def _item_semantics(repository: MemoryTransferRepository, job_id: str) -> list[tuple]:
    return sorted(
        (
            item.object_type,
            item.source_object_id,
            item.source_partition_id,
            item.target_partition_id,
            item.content_fingerprint,
            item.source_version,
            item.status,
            item.conflict_code,
            item.ancestry_hash,
        )
        for item in repository.items(job_id)
    )


async def _rq06() -> None:
    source_a = [_row("b", fingerprint="fp-b", version="2"), _row("a", fingerprint="fp-a")]
    source_b = list(reversed(source_a))
    target_a = [_target_row("tb", fingerprint="target-b"), _target_row("ta", fingerprint="target-a")]
    target_b = list(reversed(target_a))
    request_a = _request(
        source_partition_ids=("partition-source", "partition-source"),
        object_ids=("b", "a", "b"),
        filters={
            "ids": ["b", "a", "b"],
            "object_type": ["paragraph", "paragraph"],
            "fingerprint": ["fp-b", "fp-a"],
            "limit": 10,
        },
        idempotency_key="rq06-a",
    )
    request_b = _request(
        source_partition_ids=("partition-source",),
        object_ids=("a", "b"),
        filters={
            "limit": 10,
            "fingerprint": ["fp-a", "fp-b"],
            "object_type": "paragraph",
            "ids": ["a", "b"],
        },
        idempotency_key="rq06-b",
    )
    assert request_a.canonical() == request_b.canonical()
    assert request_a.payload_hash() == request_b.payload_hash()

    factory_a, service_a, authority_a = _setup(source=source_a, target=target_a)
    factory_b, service_b, authority_b = _setup(source=source_b, target=target_b)
    ctx_a = context()
    ctx_b = context()
    actor_a = principal_id(ctx_a)
    actor_b = principal_id(ctx_b)
    job_a = service_a.create_job(request_a, ctx_a, actor_a)
    job_b = service_b.create_job(request_b, ctx_b, actor_b)
    planned_a = await service_a.plan_job(job_a.id, ctx_a, actor_a)
    planned_b = await service_b.plan_job(job_b.id, ctx_b, actor_b)

    assert planned_a.plan_hash == planned_b.plan_hash
    assert planned_a.policy_snapshot_hash == planned_b.policy_snapshot_hash
    assert planned_a.status == planned_b.status == "awaiting_approval"
    assert planned_a.plan_revision == planned_b.plan_revision
    assert _item_semantics(service_a.repository, planned_a.id) == _item_semantics(service_b.repository, planned_b.id)
    assert len(authority_a.list_calls) == len(authority_b.list_calls) == 2
    assert authority_a.mutation_calls == authority_b.mutation_calls == []
    assert authority_a.reconcile_calls == authority_b.reconcile_calls == []
    assert _counts(factory_a)["jobs"] == _counts(factory_b)["jobs"] == 1


async def _rq07() -> None:
    factory, service, authority = _setup()
    repository = service.repository
    ctx = context()
    actor = principal_id(ctx)
    request_a = _request(idempotency_key="same-client-key", object_ids=("b", "a", "b"))
    request_b = _request(idempotency_key=" same-client-key ", object_ids=("a", "b"))
    first, first_created = repository.create(request_a, ctx, actor)
    second, second_created = repository.create(request_b, ctx, actor)
    assert first_created is True
    assert second_created is False
    assert first.id == second.id
    assert first.idempotency_key == namespaced_idempotency_key(ctx, "same-client-key")
    assert first.selection_json == second.selection_json
    assert second.status == "pending"
    assert _counts(factory) == {"jobs": 1, "items": 0, "approvals": 0, "attempts": 0, "events": 1}
    assert authority.list_calls == authority.mutation_calls == authority.reconcile_calls == []

    nonce_factory, nonce_service, nonce_authority = _setup()
    nonce_repository = nonce_service.repository
    nonce_request = _request(idempotency_key="", client_nonce="nonce-1", object_ids=("a", "b"))
    nonce_first, nonce_created = nonce_repository.create(nonce_request, ctx, actor)
    nonce_second, nonce_reused = nonce_repository.create(nonce_request, ctx, actor)
    assert nonce_created is True and nonce_reused is False
    assert nonce_first.id == nonce_second.id
    assert _counts(nonce_factory)["jobs"] == 1
    assert nonce_authority.list_calls == nonce_authority.mutation_calls == nonce_authority.reconcile_calls == []


async def _rq08() -> None:
    factory, service, authority = _setup()
    repository = service.repository
    ctx = context()
    actor = principal_id(ctx)
    first_request = _request(idempotency_key="conflict-key", object_ids=("p1",))
    second_request = _request(idempotency_key="conflict-key", object_ids=("p2",))
    original, created = repository.create(first_request, ctx, actor)
    assert created is True
    with pytest.raises(MemoryTransferError) as caught:
        repository.create(second_request, ctx, actor)
    assert caught.value.code == "idempotency_key_reused_with_different_payload"
    persisted = repository.get(original.id)
    assert persisted.selection_json == original.selection_json
    assert persisted.status == "pending"
    assert _counts(factory) == {"jobs": 1, "items": 0, "approvals": 0, "attempts": 0, "events": 1}
    assert original.id not in str(caught.value)
    assert actor not in str(caught.value)

    other_ctx = replace(ctx, person_id="person-other")
    other_actor = principal_id(other_ctx)
    other, other_created = repository.create(second_request, other_ctx, other_actor)
    assert other_created is True
    assert other.id != original.id
    assert other.idempotency_key != original.idempotency_key
    assert _counts(factory)["jobs"] == 2
    assert _counts(factory)["events"] == 2
    assert authority.list_calls == authority.mutation_calls == authority.reconcile_calls == []


async def _rq09() -> None:
    factory, service, authority = _setup(
        source=[_row("a", fingerprint="fp-a"), _row("b", fingerprint="fp-b")]
    )
    ctx = context()
    creator = principal_id(ctx)
    job = service.create_job(
        _request(idempotency_key="rq09", object_ids=("a",)), ctx, creator
    )
    old_job = await service.plan_job(job.id, ctx, creator)
    assert old_job.status == "awaiting_approval"
    assert old_job.approval_state == "pending"
    assert _counts(factory)["approvals"] == 0
    old_plan_hash = old_job.plan_hash
    old_plan_revision = old_job.plan_revision
    old_policy_hash = old_job.policy_snapshot_hash
    old_approval = ApprovalRequest(
        plan_hash=old_job.plan_hash,
        plan_revision=old_job.plan_revision,
        policy_revision=json.loads(old_job.policy_snapshot_json)["policy_revision"],
        policy_snapshot_hash=old_job.policy_snapshot_hash,
    )

    new_request = _request(idempotency_key="rq09", object_ids=("b",))
    stale = service.replace_selection_for_replan(
        old_job.id, new_request, creator, request_context=ctx
    )
    assert stale.status == "stale_policy"
    assert stale.approval_state == "pending"
    new_job = await service.plan_job(stale.id, ctx, creator)
    assert new_job.plan_hash and new_job.plan_hash != old_plan_hash
    assert new_job.plan_revision == old_plan_revision + 1
    assert new_job.policy_snapshot_hash == old_policy_hash
    assert new_job.status == "awaiting_approval"
    assert new_job.approval_state == "pending"

    all_items = _all_items(factory, new_job.id)
    old_items = [item for item in all_items if item.plan_revision == old_plan_revision]
    new_items = [item for item in all_items if item.plan_revision == new_job.plan_revision]
    assert len(old_items) == len(new_items) == 1
    assert old_items[0].source_object_id == "a"
    assert old_items[0].superseded is True
    assert old_items[0].status == "superseded"
    assert new_items[0].source_object_id == "b"
    assert new_items[0].superseded is False

    before_counts = _counts(factory)
    before_events = _events(factory, new_job.id)
    with pytest.raises(MemoryTransferError) as caught:
        service.approve_job(new_job.id, old_approval, "reviewer", request_context=ctx)
    assert caught.value.code == "approval_plan_mismatch"
    assert _counts(factory) == before_counts
    assert _events(factory, new_job.id) == before_events
    current = service.repository.get(new_job.id)
    assert current.status == "awaiting_approval"
    assert current.approval_state == "pending"
    assert len(authority.list_calls) == 4
    assert authority.mutation_calls == authority.reconcile_calls == []


async def _rq10() -> None:
    factory, service, authority = _setup()
    repository = service.repository
    ctx = context()
    job, created = repository.create(
        _request(idempotency_key="rq10"), ctx, principal_id(ctx)
    )
    assert created is True
    before_job = repository.get(job.id)
    before_counts = _counts(factory)
    before_events = _events(factory, job.id)
    with pytest.raises(MemoryTransferError) as caught:
        repository.transition(job.id, "completed", actor="tester")
    assert caught.value.code == "invalid_state_transition"
    after_job = repository.get(job.id)
    assert after_job.status == before_job.status == "pending"
    assert after_job.updated_at == before_job.updated_at
    assert after_job.started_at is after_job.completed_at is after_job.cancelled_at is None
    assert after_job.last_error_code == before_job.last_error_code
    assert after_job.last_error_detail == before_job.last_error_detail
    assert _counts(factory) == before_counts
    assert _events(factory, job.id) == before_events

    cancelled = repository.transition(job.id, "cancelled", actor="tester")
    assert cancelled.status == "cancelled"
    cancelled_counts = _counts(factory)
    cancelled_events = _events(factory, job.id)
    with pytest.raises(MemoryTransferError) as terminal_caught:
        repository.transition(job.id, "running", actor="tester")
    assert terminal_caught.value.code == "invalid_state_transition"
    assert repository.get(job.id).status == "cancelled"
    assert _counts(factory) == cancelled_counts
    assert _events(factory, job.id) == cancelled_events
    assert authority.list_calls == authority.mutation_calls == authority.reconcile_calls == []


async def _rq11() -> None:
    factory, service, authority = _setup(source=[_row("p1", fingerprint="fp1")])
    projection = service.executor.projection
    ctx = context()
    creator = principal_id(ctx)
    job = service.create_job(_request(idempotency_key="rq11", object_ids=("p1",)), ctx, creator)
    planned = await service.plan_job(job.id, ctx, creator)
    policy = json.loads(planned.policy_snapshot_json)
    approved = service.approve_job(
        planned.id,
        ApprovalRequest(
            plan_hash=planned.plan_hash,
            plan_revision=planned.plan_revision,
            policy_revision=policy["policy_revision"],
            policy_snapshot_hash=planned.policy_snapshot_hash,
        ),
        "reviewer",
        request_context=ctx,
    )
    assert approved.status == "approved"
    completed = await service.execute_job(approved.id, ctx, creator)
    assert completed.status == "completed"
    assert len(authority.mutation_calls) == 1

    before_job = service.repository.get(job.id)
    before_counts = _counts(factory)
    before_events = _events(factory, job.id)
    before_items = [
        (
            item.id, item.status, item.target_object_id, item.attempt_count,
            item.updated_at, item.superseded,
        )
        for item in _all_items(factory, job.id)
    ]
    before_mutations = list(authority.mutation_calls)
    before_lists = list(authority.list_calls)
    before_reconcile = list(authority.reconcile_calls)
    before_projection = set(projection.rows)
    before_projection_calls = projection.calls

    with pytest.raises(MemoryTransferError) as caught:
        await service.execute_job(job.id, ctx, creator)
    assert caught.value.code == "already_completed"
    after_job = service.repository.get(job.id)
    assert after_job.status == "completed"
    assert after_job.updated_at == before_job.updated_at
    assert after_job.completed_at == before_job.completed_at
    assert after_job.lease_token_hash == before_job.lease_token_hash is None
    assert after_job.lease_expires_at == before_job.lease_expires_at is None
    assert _counts(factory) == before_counts
    assert _events(factory, job.id) == before_events
    assert [
        (
            item.id, item.status, item.target_object_id, item.attempt_count,
            item.updated_at, item.superseded,
        )
        for item in _all_items(factory, job.id)
    ] == before_items
    assert authority.mutation_calls == before_mutations
    assert authority.list_calls == before_lists
    assert authority.reconcile_calls == before_reconcile
    assert projection.rows == before_projection
    assert projection.calls == before_projection_calls


async def _rq12() -> None:
    factory, service, authority = _setup()
    projection = service.executor.projection
    ctx = context()
    creator = principal_id(ctx)
    job = service.create_job(_request(idempotency_key="rq12"), ctx, creator)
    cancelled = service.cancel_job(job.id, creator, request_context=ctx)
    assert cancelled.status == "cancelled"
    before_job = service.repository.get(job.id)
    before_counts = _counts(factory)
    before_events = _events(factory, job.id)
    before_authority = (
        list(authority.list_calls), list(authority.mutation_calls), list(authority.reconcile_calls)
    )
    before_projection = (set(projection.rows), projection.calls)
    with pytest.raises(MemoryTransferError) as caught:
        await service.retry_job(job.id, ctx, creator)
    assert caught.value.code == "invalid_state_transition"
    after_job = service.repository.get(job.id)
    assert after_job.status == "cancelled"
    assert after_job.cancelled_at == before_job.cancelled_at
    assert after_job.updated_at == before_job.updated_at
    assert after_job.retry_count == before_job.retry_count
    assert after_job.next_retry_at == before_job.next_retry_at
    assert after_job.lease_token_hash == before_job.lease_token_hash is None
    assert after_job.lease_expires_at == before_job.lease_expires_at is None
    assert _counts(factory) == before_counts
    assert _events(factory, job.id) == before_events
    assert (
        authority.list_calls, authority.mutation_calls, authority.reconcile_calls
    ) == before_authority
    assert (projection.rows, projection.calls) == before_projection


def _migration_context(connection) -> MigrationExecutionContext:
    return MigrationExecutionContext(
        connection=connection,
        current_version=46,
        target_version=47,
        step_index=1,
        step_name="v46_to_v47",
        total_steps=1,
    )


def _v46_transfer_schema(connection) -> None:
    connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    connection.exec_driver_sql("CREATE TABLE memory_spaces (id VARCHAR(64) PRIMARY KEY)")
    connection.exec_driver_sql(
        "CREATE TABLE memory_partitions (id VARCHAR(96) PRIMARY KEY, memory_space_id VARCHAR(64) NOT NULL)"
    )
    connection.exec_driver_sql(
        """CREATE TABLE memory_transfer_jobs (
        id VARCHAR(64) PRIMARY KEY, source_space_id VARCHAR(64) NOT NULL,
        target_space_id VARCHAR(64) NOT NULL, mode VARCHAR(16) NOT NULL,
        filters_json TEXT NOT NULL DEFAULT '{}', approval_policy VARCHAR(16) NOT NULL DEFAULT 'manual',
        conflict_policy VARCHAR(16) NOT NULL DEFAULT 'skip', status VARCHAR(24) NOT NULL DEFAULT 'pending',
        created_by VARCHAR(64) NOT NULL DEFAULT 'webui', created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL, CHECK(source_space_id <> target_space_id))"""
    )
    connection.exec_driver_sql("INSERT INTO memory_spaces VALUES ('source'), ('target')")
    connection.exec_driver_sql(
        "INSERT INTO memory_partitions VALUES ('p-source','source'), ('p-target','target')"
    )


def _insert_legacy_job(connection, job_id: str, source: str, target: str) -> None:
    connection.exec_driver_sql(
        """INSERT INTO memory_transfer_jobs (
        id,source_space_id,target_space_id,mode,filters_json,approval_policy,
        conflict_policy,status,created_by,created_at,updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            job_id, source, target, "copy", "{}", "manual", "skip", "pending",
            "legacy", datetime.now(), datetime.now(),
        ),
    )


async def _rq13() -> None:
    _assert_error(
        _request(source_space_id=" same-space ", target_space_id="same-space"),
        "same_source_target_space",
    )

    engine = create_engine("sqlite://")
    connection = engine.connect()
    transaction = connection.begin()
    try:
        _v46_transfer_schema(connection)
        _insert_legacy_job(connection, "legal", "source", "target")
        before = connection.exec_driver_sql(
            "SELECT id,source_space_id,target_space_id FROM memory_transfer_jobs WHERE id='legal'"
        ).one()
        migrate_v46_to_v47(_migration_context(connection))
        with pytest.raises(IntegrityError):
            _insert_legacy_job(connection, "invalid", "source", "source")
        assert connection.exec_driver_sql(
            "SELECT COUNT(*) FROM memory_transfer_jobs WHERE id='invalid'"
        ).scalar_one() == 0
        assert connection.exec_driver_sql(
            "SELECT id,source_space_id,target_space_id FROM memory_transfer_jobs WHERE id='legal'"
        ).one() == before
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        table_sql = connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='memory_transfer_jobs'"
        ).scalar_one()
        assert "CHECK(source_space_id <> target_space_id)" in table_sql
    finally:
        transaction.rollback()
        connection.close()


CASES: tuple[tuple[str, Case], ...] = (
    ("RQ01", _rq01), ("RQ02", _rq02), ("RQ03", _rq03),
    ("RQ04", _rq04), ("RQ05", _rq05), ("RQ06", _rq06),
    ("RQ07", _rq07), ("RQ08", _rq08), ("RQ09", _rq09),
    ("RQ10", _rq10), ("RQ11", _rq11), ("RQ12", _rq12),
    ("RQ13", _rq13),
)


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario_id,case", CASES, ids=[item[0] for item in CASES])
async def test_phase6_rq_matrix(scenario_id: str, case: Case) -> None:
    assert case.__name__ == f"_{scenario_id.lower()}"
    await case()
