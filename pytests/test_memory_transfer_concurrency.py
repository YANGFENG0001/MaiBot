"""File-backed SQLite concurrency gates for MemoryTransfer CAS paths."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import event
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, SQLModel, create_engine, select

from memory_transfer_test_support import context, seed_transfer_scope
from src.common.database.database_model import MemoryTransferItem, MemoryTransferJob
from src.workspaces.memory_transfer_models import MemoryTransferError, TransferCreateRequest
from src.workspaces.memory_transfer_repository import MemoryTransferRepository


def file_repository(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'transfers.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )

    @event.listens_for(engine, "connect")
    def configure(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")
        dbapi_connection.execute("PRAGMA journal_mode=WAL")
        dbapi_connection.execute("PRAGMA busy_timeout=10000")

    SQLModel.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    seed_transfer_scope(factory)
    return factory, MemoryTransferRepository(factory)


def request(key="concurrent"):
    return TransferCreateRequest(
        mode="copy", source_space_id="space-source",
        source_partition_ids=("partition-source",), target_space_id="space-target",
        target_partition_id="partition-target", object_types=("paragraph",),
        idempotency_key=key,
    )


def test_twenty_concurrent_creates_converge_to_one_job(tmp_path):
    factory, repository = file_repository(tmp_path)
    barrier = Barrier(20)

    def create_once(_index):
        barrier.wait()
        return repository.create(request(), context(), "alice")[0].id

    with ThreadPoolExecutor(max_workers=20) as pool:
        job_ids = list(pool.map(create_once, range(20)))

    assert len(set(job_ids)) == 1
    with factory() as session:
        assert len(session.exec(select(MemoryTransferJob)).all()) == 1


def test_concurrent_lease_acquire_has_one_owner(tmp_path):
    _factory, repository = file_repository(tmp_path)
    job, _ = repository.create(request("lease"), context(), "alice")
    barrier = Barrier(2)

    def acquire(worker):
        barrier.wait()
        try:
            return ("ok", repository.acquire_lease(job.id, worker_id=worker))
        except MemoryTransferError as exc:
            return (exc.code, "")

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(acquire, ("worker-a", "worker-b")))

    assert [status for status, _ in outcomes].count("ok") == 1
    assert [status for status, _ in outcomes].count("lease_lost") == 1


def test_concurrent_item_claim_has_one_owner(tmp_path):
    _factory, repository = file_repository(tmp_path)
    job, _ = repository.create(request("claim"), context(), "alice")
    repository.transition(job.id, "planning", actor="alice")
    item = MemoryTransferItem(
        id="item-claim", job_id=job.id, mode="copy", object_type="paragraph",
        source_object_id="paragraph-1", source_space_id="space-source",
        source_partition_id="partition-source", target_space_id="space-target",
        target_partition_id="partition-target", operation_key="operation-claim",
    )
    repository.replace_plan(
        job.id, [item], plan_hash="plan", source_snapshot={}, target_snapshot={},
        policy_snapshot={"policy_revision": 1}, approval_required=False,
        policy_revision=1, source_revision=1, target_revision=1, actor="alice",
    )
    repository.transition(job.id, "running", actor="alice", expected={"approved"})
    lease = repository.acquire_lease(job.id, worker_id="job-worker")
    barrier = Barrier(2)

    def claim(worker):
        barrier.wait()
        try:
            return ("ok", repository.claim_item(item.id, worker_id=worker, lease_token=lease).attempt_count)
        except MemoryTransferError as exc:
            return (exc.code, 0)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(claim, ("worker-a", "worker-b")))

    assert [status for status, _ in outcomes].count("ok") == 1
    assert sum(1 for status, _ in outcomes if status != "ok") == 1


def test_concurrent_reverse_lineage_reservation_has_one_winner(tmp_path):
    _factory, repository = file_repository(tmp_path)
    first, _ = repository.create(request("lineage-a"), context(), "alice")
    second, _ = repository.create(request("lineage-b"), context(), "alice")
    for job, item_id, source, target, operation in (
        (first, "lineage-item-a", "partition-source", "partition-target", "lineage-op-a"),
        (second, "lineage-item-b", "partition-target", "partition-source", "lineage-op-b"),
    ):
        repository.transition(job.id, "planning", actor="alice")
        item = MemoryTransferItem(
            id=item_id, job_id=job.id, mode="link", object_type="paragraph",
            source_object_id="same-root", source_space_id="space-source",
            source_partition_id=source, target_space_id="space-target",
            target_partition_id=target, operation_key=operation,
            root_object_type="paragraph", root_object_id="same-root",
        )
        repository.replace_plan(
            job.id, [item], plan_hash=job.id, source_snapshot={}, target_snapshot={},
            policy_snapshot={"policy_revision": 1}, approval_required=False,
            policy_revision=1, source_revision=1, target_revision=1, actor="alice",
        )
    items = [repository.items(first.id)[0], repository.items(second.id)[0]]
    barrier = Barrier(2)

    def reserve(item):
        barrier.wait()
        return repository.reserve_lineage_edge(item)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(reserve, items))

    assert outcomes.count(True) == 1
