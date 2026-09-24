"""EX01-EX18 executor acceptance cases for Phase 6."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
import json
import multiprocessing
import sqlite3
from threading import Barrier

import pytest

from sqlalchemy import event
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, SQLModel, create_engine, select

from memory_transfer_test_support import BASE_CAPABILITIES, context, database, grant, seed_transfer_scope
from src.A_memorix.core.runtime.services.memory_transfer_service import MemoryTransferAuthorityService
from src.common.database.database_model import (
    MemoryPermissionGroupCapability,
    MemoryTransferAttempt,
    MemoryTransferEvent,
    MemoryTransferItem,
    MemoryTransferLineage,
)
from src.workspaces.memory_transfer_authorization import MemoryTransferAuthorization
from src.workspaces.memory_transfer_models import ApprovalRequest, MemoryTransferError, TransferCreateRequest
from src.workspaces.memory_transfer_repository import MemoryTransferRepository
from src.workspaces.memory_transfer_service import MemoryTransferService

pytestmark = pytest.mark.phase6_matrix

Case = Callable[[], Awaitable[None]]


class CoreStore:
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
        self.conn.execute("INSERT INTO paragraphs VALUES ('p1','authority body',1,1)")
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


class AsyncCoreAuthority:
    def __init__(self) -> None:
        self.core = MemoryTransferAuthorityService(CoreStore())
        self.mutation_keys: list[str] = []
        self.reconcile_keys: list[str] = []

    async def list_scoped_objects(self, **kwargs):
        return self.core.list_scoped_objects(kwargs)

    async def link_object_to_scope(self, **kwargs):
        self.mutation_keys.append(kwargs["operation_key"])
        return self.core.link_object_to_scope(kwargs)

    async def copy_object_to_scope(self, **kwargs):
        self.mutation_keys.append(kwargs["operation_key"])
        return self.core.copy_object_to_scope(kwargs)

    async def get_transfer_operation(self, operation_key):
        self.reconcile_keys.append(operation_key)
        return self.core.get_transfer_operation(operation_key)


class ScriptedAuthority:
    def __init__(self, outcomes=None, *, rows=None) -> None:
        self.outcomes = list(outcomes or [])
        self.rows = rows or [self.row("p1")]
        self.mutation_keys: list[str] = []
        self.mutation_objects: list[str] = []
        self.reconcile_keys: list[str] = []
        self.operations: dict[str, dict] = {}
        self.forced_reconcile: str | None = None

    @staticmethod
    def row(object_id: str):
        return {
            "object_type": "paragraph", "object_id": object_id,
            "memory_space_id": "space-source", "partition_id": "partition-source",
            "security_domain": "normal", "content_fingerprint": f"fp-{object_id}",
            "root_object_type": "paragraph", "root_object_id": object_id, "source_version": "1",
        }

    async def list_scoped_objects(self, **kwargs):
        if kwargs["memory_space_id"] != "space-source":
            return {"items": [], "has_more": False}
        return {"items": [dict(row) for row in self.rows], "has_more": False}

    def _result(self, kwargs, *, status="applied"):
        source_id = kwargs.get("source_object_id") or kwargs.get("object_id")
        target_id = source_id if "object_id" in kwargs else f"copy-{source_id}"
        return {
            "status": status, "operation_key": kwargs["operation_key"],
            "object_type": kwargs["object_type"], "source_object_id": source_id,
            "target_object_id": target_id, "target_space_id": kwargs["target_space_id"],
            "target_partition_id": kwargs["target_partition_id"],
            "target_fingerprint": f"fp-{source_id}", "content_fingerprint": f"fp-{source_id}",
        }

    async def copy_object_to_scope(self, **kwargs):
        self.mutation_keys.append(kwargs["operation_key"])
        self.mutation_objects.append(kwargs["source_object_id"])
        outcome = self.outcomes.pop(0) if self.outcomes else "success"
        result = self._result(kwargs)
        if outcome == "applied_timeout":
            self.operations[kwargs["operation_key"]] = result
            raise TimeoutError
        if outcome == "timeout":
            raise TimeoutError
        if outcome == "forbidden":
            raise PermissionError
        if outcome == "server_error":
            raise RuntimeError("upstream private body")
        if outcome == "body_response":
            return {**result, "metadata": {"content": "executor-private-sentinel"}}
        self.operations[kwargs["operation_key"]] = result
        return result

    async def link_object_to_scope(self, **kwargs):
        self.mutation_keys.append(kwargs["operation_key"])
        self.mutation_objects.append(kwargs["object_id"])
        result = self._result(kwargs)
        self.operations[kwargs["operation_key"]] = result
        return result

    async def get_transfer_operation(self, operation_key):
        self.reconcile_keys.append(operation_key)
        if self.forced_reconcile == "unknown":
            return {"status": "unknown", "operation_key": operation_key}
        return self.operations.get(operation_key, {"status": "not_applied", "operation_key": operation_key})


class Projection:
    def __init__(self, failures=0) -> None:
        self.failures = failures
        self.rows: set[tuple[str, str, str]] = set()

    def register_object_partition(self, **kwargs):
        if self.failures:
            self.failures -= 1
            raise RuntimeError("local commit failed")
        self.rows.add((kwargs["object_type"], kwargs["object_id"], kwargs["partition_id"]))
        return True


def _request(*, key: str, mode="copy", target_space="space-target", object_ids=("p1",)):
    return TransferCreateRequest(
        mode=mode, source_space_id="space-source", source_partition_ids=("partition-source",),
        target_space_id=target_space, target_partition_id="partition-target",
        object_types=("paragraph",), object_ids=object_ids, approval_policy="auto_safe",
        conflict_policy="skip", idempotency_key=key,
    )


def _setup(authority, *, projection=None, target_space="space-target"):
    _engine, factory = database()
    seed_transfer_scope(factory, target_space=target_space)
    grant(factory, *BASE_CAPABILITIES, "memory.transfer.auto_approve_safe")
    service = MemoryTransferService(
        repository=MemoryTransferRepository(factory), authorization=MemoryTransferAuthorization(factory),
        authority=authority, projection=projection or Projection(),
    )
    ctx = context(home_space_id=target_space)
    return factory, service, ctx


async def _prepared(service, ctx, *, key, mode="copy", target_space="space-target", object_ids=("p1",)):
    job = service.create_job(
        _request(key=key, mode=mode, target_space=target_space, object_ids=object_ids), ctx, "creator-token"
    )
    job = await service.plan_job(job.id, ctx, "creator-token")
    if job.status == "awaiting_approval":
        policy = json.loads(job.policy_snapshot_json)
        job = service.approve_job(
            job.id, ApprovalRequest(job.plan_hash, job.plan_revision, policy["policy_revision"], job.policy_snapshot_hash),
            "review-token", request_context=replace(ctx, person_id="reviewer"),
        )
    return job


def _retry_due(service, job_id):
    with service.repository.session_factory() as session:
        job = session.get(type(service.repository.get(job_id)), job_id)
        job.next_retry_at = datetime.now() - timedelta(seconds=1)
        session.commit()


def _events(factory, job_id):
    with factory() as session:
        return session.exec(select(MemoryTransferEvent).where(MemoryTransferEvent.job_id == job_id)).all()


async def _ex01() -> None:
    authority, projection = AsyncCoreAuthority(), Projection()
    factory, service, ctx = _setup(authority, projection=projection)
    job = await _prepared(service, ctx, key="ex01", mode="link")
    completed = await service.execute_job(job.id, ctx, "actor")
    item = service.repository.items(job.id)[0]
    assert completed.status == "completed" and item.target_object_id == "p1"
    assert ("paragraph", "p1", "partition-target") in projection.rows
    assert authority.core._conn.execute("SELECT COUNT(*) FROM memory_scope_members WHERE object_id='p1' AND partition_id='partition-target'").fetchone()[0] == 1
    with factory() as session:
        edge = session.exec(select(MemoryTransferLineage).where(MemoryTransferLineage.job_id == job.id)).one()
    assert edge.relation_type == "link" and edge.root_object_id == "p1"


async def _ex02() -> None:
    authority, projection = AsyncCoreAuthority(), Projection()
    factory, service, ctx = _setup(authority, projection=projection)
    job = await _prepared(service, ctx, key="ex02")
    completed = await service.execute_job(job.id, ctx, "actor")
    item = service.repository.items(job.id)[0]
    assert completed.status == "completed" and item.target_object_id != item.source_object_id
    assert authority.core._conn.execute("SELECT COUNT(*) FROM paragraphs WHERE hash=?", (item.target_object_id,)).fetchone()[0] == 1
    assert authority.core._conn.execute("SELECT COUNT(*) FROM memory_scope_members WHERE object_id=? AND partition_id='partition-target'", (item.target_object_id,)).fetchone()[0] == 1
    assert authority.core._conn.execute("SELECT COUNT(*) FROM memory_transfer_objects WHERE target_object_id=?", (item.target_object_id,)).fetchone()[0] == 1
    assert ("paragraph", item.target_object_id, "partition-target") in projection.rows
    with factory() as session:
        assert session.exec(select(MemoryTransferLineage).where(MemoryTransferLineage.job_id == job.id)).one().relation_type == "copy"


async def _ex03() -> None:
    authority, projection = AsyncCoreAuthority(), Projection()
    factory, service, ctx = _setup(authority, projection=projection, target_space="memory-space-public")
    job = await _prepared(service, ctx, key="ex03", mode="promote", target_space="memory-space-public")
    completed = await service.execute_job(job.id, ctx, "actor")
    item = service.repository.items(job.id)[0]
    assert completed.status == "completed" and item.target_object_id != "p1"
    assert authority.core._conn.execute("SELECT COUNT(*) FROM memory_scope_members WHERE object_id=? AND memory_space_id='memory-space-public'", (item.target_object_id,)).fetchone()[0] == 1
    with factory() as session:
        edge = session.exec(select(MemoryTransferLineage).where(MemoryTransferLineage.job_id == job.id)).one()
    assert edge.relation_type == "promote" and ("paragraph", item.target_object_id, "partition-target") in projection.rows


async def _ex04() -> None:
    authority = ScriptedAuthority(["timeout"])
    factory, service, ctx = _setup(authority)
    job = await _prepared(service, ctx, key="ex04")
    result = await service.execute_job(job.id, ctx, "actor")
    item = service.repository.items(job.id)[0]
    assert result.status == "retry_wait" and item.status == "failed_retryable"
    assert item.error_code == "upstream_timeout" and authority.mutation_keys == [item.operation_key]
    assert len({attempt.lease_token_hash for attempt in _attempts(factory, job.id)}) == 1


def _attempts(factory, job_id):
    with factory() as session:
        return session.exec(select(MemoryTransferAttempt).where(MemoryTransferAttempt.job_id == job_id)).all()


async def _ex05() -> None:
    authority = ScriptedAuthority(["forbidden"])
    _factory, service, ctx = _setup(authority)
    job = await _prepared(service, ctx, key="ex05")
    result = await service.execute_job(job.id, ctx, "actor")
    item = service.repository.items(job.id)[0]
    assert result.status == "failed" and (item.status, item.error_code) == ("failed_permanent", "permission_denied")
    with pytest.raises(MemoryTransferError):
        await service.retry_job(job.id, ctx, "actor")
    assert len(authority.mutation_keys) == 1


async def _ex06() -> None:
    authority = ScriptedAuthority(["server_error"] * 10)
    _factory, service, ctx = _setup(authority)
    job = await _prepared(service, ctx, key="ex06")
    result = await service.execute_job(job.id, ctx, "actor")
    while result.status == "retry_wait":
        _retry_due(service, job.id)
        result = await service.retry_job(job.id, ctx, "actor")
    item = service.repository.items(job.id)[0]
    assert result.status == "failed" and item.status == "failed_permanent"
    assert item.error_code == "upstream_unavailable" and len(authority.mutation_keys) == job.max_retries + 1

    mixed = ScriptedAuthority(["success"] + ["server_error"] * 10, rows=[ScriptedAuthority.row("p1"), ScriptedAuthority.row("p2")])
    _factory2, service2, ctx2 = _setup(mixed)
    job2 = await _prepared(service2, ctx2, key="ex06-partial", object_ids=("p1", "p2"))
    result2 = await service2.execute_job(job2.id, ctx2, "actor")
    while result2.status == "retry_wait":
        _retry_due(service2, job2.id)
        result2 = await service2.retry_job(job2.id, ctx2, "actor")
    statuses = {item.source_object_id: item.status for item in service2.repository.items(job2.id)}
    assert result2.status == "partial"
    assert sorted(statuses.values()) == ["completed", "failed_permanent"]


async def _ex07() -> None:
    authority = ScriptedAuthority(["applied_timeout"])
    _factory, service, ctx = _setup(authority)
    job = await _prepared(service, ctx, key="ex07")
    assert (await service.execute_job(job.id, ctx, "actor")).status == "retry_wait"
    item = service.repository.items(job.id)[0]
    _retry_due(service, job.id)
    completed = await service.retry_job(job.id, ctx, "actor")
    assert completed.status == "completed" and authority.mutation_keys == [item.operation_key]
    assert authority.reconcile_keys == [item.operation_key]


async def _ex08() -> None:
    authority = ScriptedAuthority(["timeout"])
    _factory, service, ctx = _setup(authority)
    job = await _prepared(service, ctx, key="ex08")
    await service.execute_job(job.id, ctx, "actor")
    item = service.repository.items(job.id)[0]
    _retry_due(service, job.id)
    authority.forced_reconcile = "unknown"
    result = await service.retry_job(job.id, ctx, "actor")
    current = service.repository.items(job.id)[0]
    assert result.status == "retry_wait" and current.error_code == "unknown_result_needs_reconcile"
    assert current.operation_key == item.operation_key and authority.mutation_keys == [item.operation_key]


async def _ex09() -> None:
    authority, projection = ScriptedAuthority(), Projection(failures=1)
    _factory, service, ctx = _setup(authority, projection=projection)
    job = await _prepared(service, ctx, key="ex09")
    assert (await service.execute_job(job.id, ctx, "actor")).status == "retry_wait"
    item = service.repository.items(job.id)[0]
    assert item.status == "failed_retryable" and item.error_code == "local_database_busy"
    assert len(authority.mutation_keys) == 1
    _retry_due(service, job.id)
    completed = await service.retry_job(job.id, ctx, "actor")
    assert completed.status == "completed" and len(authority.mutation_keys) == 1
    assert authority.reconcile_keys == [item.operation_key]


def _file_repository(db_path):
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False, "timeout": 10})
    @event.listens_for(engine, "connect")
    def configure(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False), MemoryTransferRepository(
        sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    )


def _lease_process(db_path, job_id, start, output):
    _factory, repository = _file_repository(db_path)
    start.wait()
    try:
        output.put(("ok", repository.acquire_lease(job_id, worker_id=str(multiprocessing.current_process().pid))))
    except MemoryTransferError as exc:
        output.put((exc.code, ""))


async def _ex10() -> None:
    from pathlib import Path
    import tempfile
    db_path = str(Path(tempfile.mkdtemp()) / "lease.db")
    factory, repository = _file_repository(db_path)
    SQLModel.metadata.create_all(factory.kw["bind"])
    seed_transfer_scope(factory)
    job, _created = repository.create(_request(key="ex10"), context(), "actor")
    first = repository.acquire_lease(job.id, worker_id="dead-worker")
    with factory() as session:
        stored = session.get(type(job), job.id)
        stored.lease_expires_at = datetime.now() - timedelta(seconds=1)
        session.commit()
    mp = multiprocessing.get_context("spawn")
    start, output = mp.Event(), mp.Queue()
    workers = [mp.Process(target=_lease_process, args=(db_path, job.id, start, output)) for _ in range(2)]
    for worker in workers:
        worker.start()
    start.set()
    results = [output.get(timeout=20) for _ in workers]
    for worker in workers:
        worker.join(timeout=20)
        assert worker.exitcode == 0
    assert [status for status, _token in results].count("ok") == 1
    assert [status for status, _token in results].count("lease_lost") == 1
    assert next(token for status, token in results if status == "ok") != first


async def _ex11() -> None:
    from pathlib import Path
    import tempfile
    db_path = str(Path(tempfile.mkdtemp()) / "claim.db")
    factory, repository = _file_repository(db_path)
    SQLModel.metadata.create_all(factory.kw["bind"])
    seed_transfer_scope(factory)
    job, _created = repository.create(_request(key="ex11"), context(), "actor")
    repository.transition(job.id, "planning", actor="actor")
    item = MemoryTransferItem(
        id="ex11-item", job_id=job.id, mode="copy", object_type="paragraph",
        source_object_id="p1", source_space_id="space-source",
        source_partition_id="partition-source", target_space_id="space-target",
        target_partition_id="partition-target", operation_key="ex11-operation",
    )
    repository.replace_plan(
        job.id, [item], plan_hash="ex11-plan", source_snapshot={}, target_snapshot={},
        policy_snapshot={"policy_revision": 1}, approval_required=False, policy_revision=1,
        source_revision=1, target_revision=1, actor="actor",
    )
    repository.transition(job.id, "running", actor="actor", expected={"approved"})
    lease = repository.acquire_lease(job.id, worker_id="job-worker")
    barrier = Barrier(2)
    def claim(worker):
        barrier.wait()
        try:
            return "ok", repository.claim_item(item.id, worker_id=worker, lease_token=lease)
        except MemoryTransferError as exc:
            return exc.code, None
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(claim, ("worker-a", "worker-b")))
    assert [status for status, _item in outcomes].count("ok") == 1
    assert repository.items(job.id)[0].attempt_count == 1


async def _ex12() -> None:
    authority = ScriptedAuthority(["success", "timeout", "success"], rows=[ScriptedAuthority.row("p1"), ScriptedAuthority.row("p2")])
    _factory, service, ctx = _setup(authority)
    job = await _prepared(service, ctx, key="ex12", object_ids=("p1", "p2"))
    assert (await service.execute_job(job.id, ctx, "actor")).status == "retry_wait"
    first_keys = {item.source_object_id: item.operation_key for item in service.repository.items(job.id)}
    _retry_due(service, job.id)
    completed = await service.retry_job(job.id, ctx, "actor")
    assert completed.status == "completed" and authority.mutation_objects.count("p1") == 1
    assert authority.mutation_objects.count("p2") == 2
    assert {item.source_object_id: item.operation_key for item in service.repository.items(job.id)} == first_keys


async def _ex13() -> None:
    permanent = ScriptedAuthority(["success", "forbidden"], rows=[ScriptedAuthority.row("p1"), ScriptedAuthority.row("p2")])
    _factory, service, ctx = _setup(permanent)
    job = await _prepared(service, ctx, key="ex13-partial", object_ids=("p1", "p2"))
    result = await service.execute_job(job.id, ctx, "actor")
    assert result.status == "partial"
    item_statuses = {item.source_object_id: item.status for item in service.repository.items(job.id)}
    assert sorted(item_statuses.values()) == ["completed", "failed_permanent"]

    retryable = ScriptedAuthority(["success", "timeout"], rows=[ScriptedAuthority.row("p1"), ScriptedAuthority.row("p2")])
    _factory2, service2, ctx2 = _setup(retryable)
    job2 = await _prepared(service2, ctx2, key="ex13-retry", object_ids=("p1", "p2"))
    result2 = await service2.execute_job(job2.id, ctx2, "actor")
    assert result2.status == "retry_wait"


async def _ex14() -> None:
    authority = ScriptedAuthority()
    _factory, service, ctx = _setup(authority)
    job = await _prepared(service, ctx, key="ex14")
    cancelled = service.cancel_job(job.id, "actor", request_context=ctx)
    assert cancelled.status == "cancelled" and service.repository.items(job.id)[0].status == "cancelled"
    assert authority.mutation_keys == authority.reconcile_keys == []


async def _ex15() -> None:
    authority = ScriptedAuthority()
    _factory, service, ctx = _setup(authority)
    job = await _prepared(service, ctx, key="ex15")
    service.repository.transition(job.id, "running", actor="worker", expected={"approved"})
    lease = service.repository.acquire_lease(job.id, worker_id="worker")
    item = service.repository.claim_item(service.repository.items(job.id)[0].id, worker_id="worker", lease_token=lease)
    authority.operations[item.operation_key] = authority._result({
        "operation_key": item.operation_key, "object_type": item.object_type,
        "source_object_id": item.source_object_id, "target_space_id": item.target_space_id,
        "target_partition_id": item.target_partition_id,
    })
    service.repository.release_lease(job.id, lease)
    still_running = service.cancel_job(job.id, "actor", request_context=ctx)
    assert still_running.status == "running" and item.status == "running"
    reconciled = await service.reconcile_job(job.id, ctx, "actor")
    assert reconciled.status == "completed" and authority.reconcile_keys == [item.operation_key]
    assert service.repository.items(job.id)[0].status == "completed"


async def _ex16() -> None:
    authority = ScriptedAuthority(["timeout", "success"])
    _factory, service, ctx = _setup(authority)
    job = await _prepared(service, ctx, key="ex16")
    await service.execute_job(job.id, ctx, "actor")
    item = service.repository.items(job.id)[0]
    _retry_due(service, job.id)
    completed = await service.retry_job(job.id, ctx, "actor")
    assert completed.status == "completed" and authority.mutation_keys == [item.operation_key, item.operation_key]


async def _ex17() -> None:
    authority = ScriptedAuthority(["timeout"])
    factory, service, ctx = _setup(authority)
    job = await _prepared(service, ctx, key="ex17")
    await service.execute_job(job.id, ctx, "actor")
    with factory() as session:
        capability = session.exec(select(MemoryPermissionGroupCapability).where(
            MemoryPermissionGroupCapability.capability == "memory.transfer.read_source"
        )).one()
        capability.enabled = False
        session.commit()
    before = list(authority.mutation_keys)
    with pytest.raises(MemoryTransferError) as caught:
        await service.retry_job(job.id, ctx, "actor")
    assert caught.value.code == "transfer_capability_required" and authority.mutation_keys == before


async def _ex18() -> None:
    authority = ScriptedAuthority(["body_response"])
    factory, service, ctx = _setup(authority)
    job = await _prepared(service, ctx, key="ex18")
    result = await service.execute_job(job.id, ctx, "actor")
    item = service.repository.items(job.id)[0]
    assert result.status == "failed" and item.error_code == "upstream_invalid_response"
    serialized = json.dumps([event.details_json for event in _events(factory, job.id)]).lower()
    assert "executor-private-sentinel" not in serialized and "content" not in serialized
    assert item.error_detail == ""


CASES: tuple[tuple[str, Case], ...] = (
    ("EX01", _ex01), ("EX02", _ex02), ("EX03", _ex03), ("EX04", _ex04),
    ("EX05", _ex05), ("EX06", _ex06), ("EX07", _ex07), ("EX08", _ex08),
    ("EX09", _ex09), ("EX10", _ex10), ("EX11", _ex11), ("EX12", _ex12),
    ("EX13", _ex13), ("EX14", _ex14), ("EX15", _ex15), ("EX16", _ex16),
    ("EX17", _ex17), ("EX18", _ex18),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(("case_id", "case"), CASES, ids=[case_id for case_id, _case in CASES])
async def test_phase6_ex_matrix(case_id: str, case: Case) -> None:
    assert case_id.startswith("EX")
    await case()
