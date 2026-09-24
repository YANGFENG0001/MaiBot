"""CL01-CL16 conflict and lineage acceptance cases for Phase 6."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
import json
import sqlite3
from uuid import uuid4

import pytest

from sqlmodel import select

from memory_transfer_test_support import BASE_CAPABILITIES, context, database, grant, seed_transfer_scope
from src.A_memorix.core.runtime.services.memory_transfer_service import MemoryTransferAuthorityService
from src.common.database.database_model import (
    MemoryPartition,
    MemoryTransferEvent,
    MemoryTransferItem,
    MemoryTransferJob,
    MemoryTransferLineage,
)
from src.workspaces.memory_transfer_authorization import MemoryTransferAuthorization
from src.workspaces.memory_transfer_models import ApprovalRequest, MemoryTransferError, TransferCreateRequest
from src.workspaces.memory_transfer_repository import MemoryTransferRepository
from src.workspaces.memory_transfer_service import MemoryTransferService

pytestmark = pytest.mark.phase6_matrix

Case = Callable[[], Awaitable[None]]


class AuthorityStore:
    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
        CREATE TABLE paragraphs(hash TEXT PRIMARY KEY, content TEXT NOT NULL, created_at REAL, updated_at REAL);
        CREATE TABLE entities(hash TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, created_at REAL);
        CREATE TABLE relations(hash TEXT PRIMARY KEY, subject TEXT, predicate TEXT, object TEXT, created_at REAL);
        CREATE TABLE memory_scope_members(
          object_type TEXT NOT NULL, object_id TEXT NOT NULL, memory_space_id TEXT NOT NULL,
          partition_id TEXT NOT NULL, security_domain TEXT NOT NULL, source_session_id TEXT,
          created_at REAL NOT NULL, PRIMARY KEY(object_type, object_id, partition_id));
        """)
        self.conn.execute("INSERT INTO paragraphs VALUES ('p1','private body',1,1)")
        self.register_scope_member(
            object_type="paragraph", object_id="p1", memory_space_id="space-source",
            partition_id="partition-source", security_domain="normal",
        )

    def _resolve_conn(self):
        return self.conn

    def _ensure_memory_scope_tables(self, _cursor):
        return None

    def register_scope_member(self, **kwargs):
        connection = kwargs.get("connection") or self.conn
        connection.execute(
            "INSERT OR IGNORE INTO memory_scope_members VALUES (?,?,?,?,?,?,?)",
            (kwargs["object_type"], kwargs["object_id"], kwargs["memory_space_id"],
             kwargs["partition_id"], kwargs["security_domain"], None, 1),
        )
        if kwargs.get("commit", True):
            connection.commit()


class AsyncAuthority:
    def __init__(self, store: AuthorityStore) -> None:
        self.store = store
        self.core = MemoryTransferAuthorityService(store)

    async def list_scoped_objects(self, **kwargs):
        return self.core.list_scoped_objects(kwargs)

    async def link_object_to_scope(self, **kwargs):
        return self.core.link_object_to_scope(kwargs)

    async def copy_object_to_scope(self, **kwargs):
        return self.core.copy_object_to_scope(kwargs)

    async def get_transfer_operation(self, operation_key):
        return self.core.get_transfer_operation(operation_key)


class AuthoritySpy:
    def __init__(self, source_rows=None, target_rows=None) -> None:
        self.source_rows = source_rows or [_row()]
        self.target_rows = target_rows or []
        self.mutations: list[tuple[str, dict]] = []

    async def list_scoped_objects(self, **kwargs):
        rows = self.source_rows if kwargs["memory_space_id"] == "space-source" else self.target_rows
        return {"items": [dict(row) for row in rows], "has_more": False}

    async def link_object_to_scope(self, **kwargs):
        self.mutations.append(("link", kwargs))
        return {
            "status": "applied", "operation_key": kwargs["operation_key"],
            "object_type": kwargs["object_type"], "source_object_id": kwargs["object_id"],
            "target_object_id": kwargs["object_id"], "target_space_id": kwargs["target_space_id"],
            "target_partition_id": kwargs["target_partition_id"], "content_fingerprint": "fp1",
        }

    async def copy_object_to_scope(self, **kwargs):
        self.mutations.append((kwargs["mode"], kwargs))
        return {
            "status": "applied", "operation_key": kwargs["operation_key"],
            "object_type": kwargs["object_type"], "source_object_id": kwargs["source_object_id"],
            "target_object_id": f"copy-{kwargs['source_object_id']}",
            "target_space_id": kwargs["target_space_id"],
            "target_partition_id": kwargs["target_partition_id"], "target_fingerprint": "fp1",
        }

    async def get_transfer_operation(self, operation_key):
        return {"status": "not_applied", "operation_key": operation_key}


class Projection:
    def register_object_partition(self, **_kwargs):
        return True


def _row(**patch):
    row = {
        "object_type": "paragraph", "object_id": "p1", "memory_space_id": "space-source",
        "partition_id": "partition-source", "security_domain": "normal",
        "content_fingerprint": "fp1", "root_object_type": "paragraph", "root_object_id": "p1",
        "source_version": "1",
    }
    row.update(patch)
    return row


def _target(**patch):
    row = _row(memory_space_id="space-target", partition_id="partition-target")
    row.update(patch)
    return row


def _request(**patch):
    values = {
        "mode": "copy", "source_space_id": "space-source",
        "source_partition_ids": ("partition-source",), "target_space_id": "space-target",
        "target_partition_id": "partition-target", "object_types": ("paragraph",),
        "object_ids": ("p1",), "approval_policy": "auto_safe", "conflict_policy": "skip",
        "idempotency_key": uuid4().hex,
    }
    values.update(patch)
    return TransferCreateRequest(**values)


def _setup(*, source_rows=None, target_rows=None, target_space="space-target"):
    _engine, factory = database()
    seed_transfer_scope(factory, target_space=target_space)
    grant(factory, *BASE_CAPABILITIES, "memory.transfer.auto_approve_safe")
    authority = AuthoritySpy(source_rows, target_rows)
    service = MemoryTransferService(
        repository=MemoryTransferRepository(factory), authorization=MemoryTransferAuthorization(factory),
        authority=authority, projection=Projection(),
    )
    ctx = context(home_space_id=target_space)
    return service, authority, factory, ctx


async def _plan(service, ctx, **patch):
    job = service.create_job(_request(**patch), ctx, "display-token")
    return await service.plan_job(job.id, ctx, "display-token")


def _seed_lineage(factory, edges, *, root_id="p1", relation="copy"):
    job_id = f"history-{uuid4().hex}"
    with factory() as session:
        known_partitions = {
            partition_id for partition_id in session.exec(select(MemoryPartition.id)).all()
        }
        required_partitions = {partition for edge in edges for partition in edge}
        for partition_id in sorted(required_partitions - known_partitions):
            session.add(MemoryPartition(
                id=partition_id, memory_space_id="space-source", partition_type="shared",
                partition_key=f"history-{partition_id}", security_domain="normal", policy_revision=1,
            ))
        session.flush()
        session.add(MemoryTransferJob(
            id=job_id, source_space_id="space-source", target_space_id="space-target", mode=relation,
            status="completed", created_by="system", idempotency_key=job_id,
        ))
        session.flush()
        pending_lineage = []
        for index, (source, target) in enumerate(edges, 1):
            item_id = f"history-item-{uuid4().hex}"
            session.add(MemoryTransferItem(
                id=item_id, job_id=job_id, mode=relation, object_type="paragraph",
                source_object_id="p1", source_space_id="space-source", source_partition_id=source,
                target_space_id="space-target", target_partition_id=target,
                operation_key=f"history-op-{uuid4().hex}", status="completed",
                root_object_type="paragraph", root_object_id=root_id,
            ))
            session.flush()
            pending_lineage.append(MemoryTransferLineage(
                id=uuid4().hex, item_id=item_id, job_id=job_id, relation_type=relation,
                object_type="paragraph", object_id="p1", source_object_type="paragraph",
                source_object_id="p1", source_space_id="space-source", source_partition_id=source,
                target_space_id="space-target", target_partition_id=target,
                root_object_type="paragraph", root_object_id=root_id, depth=index,
                ancestry_hash=f"history-{index}", edge_key=f"paragraph:{root_id}:{source}<->{target}",
            ))
        session.add_all(pending_lineage)
        session.commit()


def _add_partition(factory, partition_id, space_id, *, key=None):
    with factory() as session:
        session.add(MemoryPartition(
            id=partition_id, memory_space_id=space_id, partition_type="shared",
            partition_key=key or partition_id, security_domain="normal", policy_revision=1,
        ))
        session.commit()


def _approval(job):
    policy = json.loads(job.policy_snapshot_json)
    return ApprovalRequest(job.plan_hash, job.plan_revision, policy["policy_revision"], job.policy_snapshot_hash)


async def _applied_job(service, ctx, *, reviewer=None, **patch):
    job = await _plan(service, ctx, **patch)
    if job.status == "awaiting_approval":
        reviewer = reviewer or replace(ctx, person_id="reviewer")
        job = service.approve_job(job.id, _approval(job), "review-token", request_context=reviewer)
    return await service.execute_job(job.id, ctx, "creator-token")


async def _cl01() -> None:
    authority = MemoryTransferAuthorityService(AuthorityStore())
    request = {
        "operation_key": "cl01", "object_type": "paragraph", "object_id": "p1",
        "source_space_id": "space-source", "source_partition_id": "partition-source",
        "target_space_id": "space-target", "target_partition_id": "partition-target",
        "source_security_domain": "normal", "target_security_domain": "normal",
    }
    first = authority.link_object_to_scope(request)
    count = authority._conn.execute("SELECT COUNT(*) FROM memory_scope_members WHERE partition_id='partition-target'").fetchone()[0]
    second = authority.link_object_to_scope(request)
    assert first["status"] == "applied" and second["status"] == "already_applied"
    for field in (
        "operation_key", "source_object_id", "target_object_id", "target_space_id",
        "target_partition_id", "content_fingerprint",
    ):
        assert second[field] == first[field]
    assert authority._conn.execute("SELECT COUNT(*) FROM memory_scope_members WHERE partition_id='partition-target'").fetchone()[0] == count == 1


async def _cl02() -> None:
    service, authority, factory, ctx = _setup()
    _seed_lineage(factory, [("partition-source", "partition-target")], relation="copy")
    job = await _plan(service, ctx)
    item = service.repository.items(job.id)[0]
    assert (item.status, item.conflict_code) == ("skipped", "already_applied")
    await service.execute_job(job.id, ctx, "actor")
    assert authority.mutations == []


async def _cl03() -> None:
    service, authority, _factory, ctx = _setup(target_rows=[_target(root_object_id="different-origin")])
    job = await _plan(service, ctx)
    item = service.repository.items(job.id)[0]
    assert (item.status, item.conflict_code) == ("skipped", "object_identity_conflict")
    assert authority.mutations == []


async def _cl04() -> None:
    service, authority, _factory, ctx = _setup(target_rows=[_target(object_id="other-id")])
    job = await _plan(service, ctx)
    item = service.repository.items(job.id)[0]
    assert (item.status, item.conflict_code) == ("skipped", "duplicate_same_origin")
    assert authority.mutations == []


async def _cl05() -> None:
    service, authority, _factory, ctx = _setup(
        target_rows=[_target(object_id="other-id", root_object_id="different-origin")]
    )
    job = await _plan(service, ctx)
    item = service.repository.items(job.id)[0]
    assert (item.status, item.conflict_code) == ("skipped", "content_conflict")
    assert authority.mutations == []


async def _cl06() -> None:
    service, authority, _factory, ctx = _setup(
        target_rows=[_target(object_id="other-id", root_object_id="different-origin")]
    )
    completed = await _applied_job(service, ctx, conflict_policy="skip")
    item = service.repository.items(completed.id)[0]
    assert completed.status == "completed" and (item.status, item.conflict_code) == ("skipped", "content_conflict")
    assert authority.mutations == []


async def _cl07() -> None:
    service, authority, _factory, ctx = _setup(
        target_rows=[_target(object_id="other-id", root_object_id="different-origin")]
    )
    job = service.create_job(_request(conflict_policy="fail"), ctx, "actor")
    with pytest.raises(MemoryTransferError) as caught:
        await service.plan_job(job.id, ctx, "actor")
    assert caught.value.code == "conflict"
    assert service.repository.get(job.id).status == "failed"
    assert service.repository.items(job.id) == [] and authority.mutations == []


async def _cl08() -> None:
    service, authority, factory, ctx = _setup()
    with pytest.raises(MemoryTransferError) as caught:
        service.create_job(
            _request(target_partition_id="partition-source", idempotency_key="cl08"), ctx, "actor"
        )
    assert caught.value.code == "same_source_target"
    with factory() as session:
        assert session.exec(select(MemoryTransferJob)).all() == []
        assert session.exec(select(MemoryTransferEvent)).all() == []
    assert authority.mutations == []


async def _cl09() -> None:
    service, authority, factory, ctx = _setup()
    _seed_lineage(factory, [("partition-target", "partition-mid"), ("partition-mid", "partition-source")])
    with pytest.raises(MemoryTransferError) as caught:
        await _plan(service, ctx)
    assert caught.value.code == "cycle_detected" and authority.mutations == []
    _add_partition(factory, "partition-sibling", "space-target")
    sibling_ctx = replace(ctx, writable_partition_ids=("partition-target", "partition-sibling"))
    sibling = await _plan(service, sibling_ctx, target_partition_id="partition-sibling", idempotency_key="cl09-sibling")
    assert service.repository.items(sibling.id)[0].status == "planned"


async def _cl10() -> None:
    service, authority, factory, ctx = _setup()
    _seed_lineage(factory, [("partition-target", "partition-source")])
    with pytest.raises(MemoryTransferError) as caught:
        await _plan(service, ctx)
    assert caught.value.code == "cycle_detected" and authority.mutations == []


async def _cl11() -> None:
    service, authority, factory, _ctx = _setup()
    edges = [(f"depth-{i}", f"depth-{i + 1}") for i in range(32)]
    _seed_lineage(factory, edges)
    _add_partition(factory, "depth-target", "space-target")
    deep_ctx = context(readable=("depth-32",), writable=("depth-target",))
    source = _row(partition_id="depth-32")
    authority.source_rows = [source]
    with pytest.raises(MemoryTransferError) as caught:
        await _plan(
            service, deep_ctx, source_partition_ids=("depth-32",), target_partition_id="depth-target",
            idempotency_key="cl11",
        )
    assert caught.value.code == "lineage_depth_exceeded" and authority.mutations == []
    _add_partition(factory, "depth-sibling", "space-target")
    shallow_ctx = context(readable=("depth-0",), writable=("depth-sibling",))
    authority.source_rows = [_row(partition_id="depth-0")]
    sibling = await _plan(
        service, shallow_ctx, source_partition_ids=("depth-0",),
        target_partition_id="depth-sibling", idempotency_key="cl11-sibling",
    )
    assert service.repository.items(sibling.id)[0].status == "planned"


async def _cl12() -> None:
    service, authority, factory, ctx = _setup(
        target_rows=[_target(object_id="other-id", root_object_id="unrelated")]
    )
    assert service.repository.lineage_for("paragraph", "p1") == []
    job = await _plan(service, ctx)
    item = service.repository.items(job.id)[0]
    assert item.conflict_code == "content_conflict" and item.status == "skipped"
    assert authority.mutations == []


async def _cl13() -> None:
    source = _row(object_id="child", root_object_id="original-root")
    service, authority, factory, ctx = _setup(source_rows=[source])
    completed = await _applied_job(
        service, ctx, mode="link", object_ids=("child",), idempotency_key="cl13"
    )
    with factory() as session:
        edge = session.exec(select(MemoryTransferLineage).where(MemoryTransferLineage.job_id == completed.id)).one()
    assert (edge.relation_type, edge.object_id, edge.source_object_id) == ("link", "child", "child")
    assert (edge.root_object_type, edge.root_object_id) == ("paragraph", "original-root")
    assert [name for name, _ in authority.mutations] == ["link"]


async def _cl14() -> None:
    source = _row(object_id="child", root_object_id="original-root")
    service, authority, factory, ctx = _setup(source_rows=[source])
    completed = await _applied_job(service, ctx, object_ids=("child",), idempotency_key="cl14")
    with factory() as session:
        edge = session.exec(select(MemoryTransferLineage).where(MemoryTransferLineage.job_id == completed.id)).one()
    assert (edge.relation_type, edge.root_object_type, edge.root_object_id) == ("copy", "paragraph", "original-root")
    assert edge.source_object_id == "child" and edge.object_id == "copy-child"


async def _cl15() -> None:
    service, authority, factory, ctx = _setup(target_space="memory-space-public")
    completed = await _applied_job(
        service, ctx, mode="promote", target_space_id="memory-space-public", idempotency_key="cl15"
    )
    with factory() as session:
        edge = session.exec(select(MemoryTransferLineage).where(MemoryTransferLineage.job_id == completed.id)).one()
    assert edge.relation_type == "promote" and edge.source_space_id == "space-source"
    assert authority.mutations[0][0] == "promote"
    assert authority.mutations[0][1]["source_security_domain"] == "normal"


async def _cl16() -> None:
    body_sentinel = "CL16 authority private body content prompt query token password secret"
    store = AuthorityStore()
    store.conn.execute("UPDATE paragraphs SET content=? WHERE hash='p1'", (body_sentinel,))
    store.conn.commit()
    assert store.conn.execute("SELECT content FROM paragraphs WHERE hash='p1'").fetchone()[0] == body_sentinel

    _engine, factory = database()
    seed_transfer_scope(factory)
    grant(factory, *BASE_CAPABILITIES, "memory.transfer.auto_approve_safe")
    authority = AsyncAuthority(store)
    service = MemoryTransferService(
        repository=MemoryTransferRepository(factory), authorization=MemoryTransferAuthorization(factory),
        authority=authority, projection=Projection(),
    )
    completed = await _applied_job(service, context(), idempotency_key="cl16")
    with factory() as session:
        edge = session.exec(select(MemoryTransferLineage).where(MemoryTransferLineage.job_id == completed.id)).one()
        events = session.exec(select(MemoryTransferEvent).where(MemoryTransferEvent.job_id == completed.id)).all()
        lineage_payload = {column.name: getattr(edge, column.name) for column in MemoryTransferLineage.__table__.columns}

    copied_body = store.conn.execute(
        "SELECT content FROM paragraphs WHERE hash=?", (edge.object_id,)
    ).fetchone()
    assert copied_body is not None and copied_body[0] == body_sentinel
    assert edge.root_object_id == "p1"

    serialized = json.dumps(
        {
            "lineage": lineage_payload,
            "events": [json.loads(event.details_json) for event in events],
        },
        ensure_ascii=False,
        default=str,
    ).lower()
    assert body_sentinel.lower() not in serialized
    for forbidden in ("private body", "content", "prompt", "query", "token", "password", "secret"):
        assert forbidden not in serialized


CASES: tuple[tuple[str, Case], ...] = (
    ("CL01", _cl01), ("CL02", _cl02), ("CL03", _cl03), ("CL04", _cl04),
    ("CL05", _cl05), ("CL06", _cl06), ("CL07", _cl07), ("CL08", _cl08),
    ("CL09", _cl09), ("CL10", _cl10), ("CL11", _cl11), ("CL12", _cl12),
    ("CL13", _cl13), ("CL14", _cl14), ("CL15", _cl15), ("CL16", _cl16),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(("case_id", "case"), CASES, ids=[case_id for case_id, _case in CASES])
async def test_phase6_cl_matrix(case_id: str, case: Case) -> None:
    assert case_id.startswith("CL")
    await case()
