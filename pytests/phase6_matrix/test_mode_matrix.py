"""MD01-MD12 transfer mode acceptance cases for Phase 6."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
import json
import sqlite3

import pytest


from memory_transfer_test_support import BASE_CAPABILITIES, context, database, grant, seed_transfer_scope
from src.A_memorix.core.runtime.services.memory_transfer_service import (
    MemoryTransferAuthorityError,
    MemoryTransferAuthorityService,
)
from src.workspaces.memory_transfer_authorization import MemoryTransferAuthorization
from src.workspaces.memory_transfer_models import ApprovalRequest, MemoryTransferError, TransferCreateRequest
from src.workspaces.memory_transfer_principal import principal_id
from src.workspaces.memory_transfer_repository import MemoryTransferRepository
from src.workspaces.memory_transfer_service import MemoryTransferService

pytestmark = pytest.mark.phase6_matrix

Case = Callable[[], Awaitable[None]]


class AuthorityStore:
    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE paragraphs(
              hash TEXT PRIMARY KEY, content TEXT NOT NULL, created_at REAL, updated_at REAL
            );
            CREATE TABLE entities(hash TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, created_at REAL);
            CREATE TABLE relations(
              hash TEXT PRIMARY KEY, subject TEXT, predicate TEXT, object TEXT, created_at REAL
            );
            CREATE TABLE memory_scope_members(
              object_type TEXT NOT NULL, object_id TEXT NOT NULL, memory_space_id TEXT NOT NULL,
              partition_id TEXT NOT NULL, security_domain TEXT NOT NULL, source_session_id TEXT,
              created_at REAL NOT NULL, PRIMARY KEY(object_type, object_id, partition_id)
            );
            """
        )
        self.conn.execute("INSERT INTO paragraphs VALUES ('p1','source body',1,1)")
        self.conn.execute("INSERT INTO entities VALUES ('e1','Alice',1)")
        self.conn.execute("INSERT INTO relations VALUES ('r1','Alice','likes','Tea',1)")
        for object_type, object_id in (("paragraph", "p1"), ("entity", "e1"), ("relation", "r1")):
            self.register_scope_member(
                object_type=object_type,
                object_id=object_id,
                memory_space_id="space-source",
                partition_id="partition-source",
                security_domain="normal",
            )

    def _resolve_conn(self):
        return self.conn

    def _ensure_memory_scope_tables(self, _cursor):
        return None

    def register_scope_member(self, **kwargs):
        connection = kwargs.get("connection") or self.conn
        connection.execute(
            "INSERT OR IGNORE INTO memory_scope_members VALUES (?,?,?,?,?,?,?)",
            (
                kwargs["object_type"], kwargs["object_id"], kwargs["memory_space_id"],
                kwargs["partition_id"], kwargs["security_domain"], None, 1,
            ),
        )
        if kwargs.get("commit", True):
            connection.commit()


def _authority() -> MemoryTransferAuthorityService:
    return MemoryTransferAuthorityService(AuthorityStore())


def _link_request(**overrides) -> dict:
    values = {
        "operation_key": "md-link",
        "object_type": "paragraph",
        "object_id": "p1",
        "source_space_id": "space-source",
        "source_partition_id": "partition-source",
        "target_space_id": "space-target",
        "target_partition_id": "partition-target",
        "source_security_domain": "normal",
        "target_security_domain": "normal",
    }
    values.update(overrides)
    return values


def _copy_request(**overrides) -> dict:
    values = {
        "operation_key": "md-copy",
        "mode": "copy",
        "object_type": "paragraph",
        "source_object_id": "p1",
        "source_space_id": "space-source",
        "source_partition_id": "partition-source",
        "target_space_id": "space-target",
        "target_partition_id": "partition-target",
        "source_security_domain": "normal",
        "target_security_domain": "normal",
    }
    values.update(overrides)
    return values


def _request(**overrides) -> TransferCreateRequest:
    values = {
        "mode": "copy",
        "source_space_id": "space-source",
        "source_partition_ids": ("partition-source",),
        "target_space_id": "space-target",
        "target_partition_id": "partition-target",
        "object_types": ("paragraph",),
        "object_ids": ("p1",),
        "filters": {},
        "approval_policy": "manual",
        "conflict_policy": "skip",
        "idempotency_key": "md-job",
        "client_nonce": "",
    }
    values.update(overrides)
    return TransferCreateRequest(**values)


class WorkspaceAuthoritySpy:
    def __init__(self, *, source_domain: str = "normal") -> None:
        self.source_domain = source_domain
        self.list_calls: list[dict] = []
        self.copy_calls: list[dict] = []
        self.link_calls: list[dict] = []
        self.reconcile_calls: list[str] = []

    async def list_scoped_objects(self, **kwargs):
        self.list_calls.append(dict(kwargs))
        rows = []
        if kwargs["memory_space_id"] == "space-source":
            rows = [{
                "object_type": "paragraph", "object_id": "p1",
                "memory_space_id": "space-source", "partition_id": "partition-source",
                "security_domain": self.source_domain, "content_fingerprint": "fp1",
                "root_object_type": "paragraph", "root_object_id": "p1",
                "source_version": "1",
            }]
        return {"items": rows, "has_more": False}

    async def copy_object_to_scope(self, **kwargs):
        self.copy_calls.append(dict(kwargs))
        return {
            "status": "applied", "operation_key": kwargs["operation_key"],
            "mode": kwargs["mode"], "object_type": kwargs["object_type"],
            "source_object_id": kwargs["source_object_id"],
            "target_object_id": f"copy-{kwargs['source_object_id']}",
            "source_space_id": kwargs["source_space_id"],
            "source_partition_id": kwargs["source_partition_id"],
            "target_space_id": kwargs["target_space_id"],
            "target_partition_id": kwargs["target_partition_id"],
            "source_security_domain": kwargs["source_security_domain"],
            "target_security_domain": kwargs["target_security_domain"],
            "source_fingerprint": kwargs["expected_fingerprint"],
            "target_fingerprint": kwargs["expected_fingerprint"],
            "root_object_type": kwargs["root_object_type"],
            "root_object_id": kwargs["root_object_id"],
        }

    async def link_object_to_scope(self, **kwargs):
        self.link_calls.append(dict(kwargs))
        raise AssertionError("promote must not call link")

    async def get_transfer_operation(self, operation_key):
        self.reconcile_calls.append(operation_key)
        return {"status": "not_applied", "operation_key": operation_key}


class ProjectionSpy:
    def __init__(self) -> None:
        self.rows: set[tuple[str, str, str]] = set()
        self.calls = 0

    def register_object_partition(self, **kwargs):
        self.calls += 1
        self.rows.add((kwargs["object_type"], kwargs["object_id"], kwargs["partition_id"]))
        return True


def _workspace_setup(*, source_domain="normal", target_domain="normal", target_space="space-target"):
    _engine, factory = database()
    seed_transfer_scope(
        factory, source_domain=source_domain, target_domain=target_domain, target_space=target_space
    )
    grant(factory, *BASE_CAPABILITIES, "memory.transfer.kami_copy")
    authority = WorkspaceAuthoritySpy(source_domain=source_domain)
    projection = ProjectionSpy()
    repository = MemoryTransferRepository(factory)
    service = MemoryTransferService(
        repository=repository,
        authorization=MemoryTransferAuthorization(factory),
        authority=authority,
        projection=projection,
    )
    return service, authority, projection


async def _md01() -> None:
    service = _authority()
    before_paragraphs = service._conn.execute("SELECT COUNT(*) FROM paragraphs").fetchone()[0]
    result = service.link_object_to_scope(_link_request(operation_key="md01"))
    assert result["status"] == "applied"
    assert result["source_object_id"] == result["target_object_id"] == "p1"
    assert service._conn.execute(
        "SELECT COUNT(*) FROM memory_scope_members WHERE object_type='paragraph' AND object_id='p1' AND partition_id='partition-target'"
    ).fetchone()[0] == 1
    assert service._conn.execute("SELECT COUNT(*) FROM paragraphs").fetchone()[0] == before_paragraphs


async def _md02() -> None:
    service = _authority()
    request = _link_request(operation_key="md02")
    first = service.link_object_to_scope(request)
    membership_count = service._conn.execute(
        "SELECT COUNT(*) FROM memory_scope_members WHERE object_id='p1' AND partition_id='partition-target'"
    ).fetchone()[0]
    paragraph_count = service._conn.execute("SELECT COUNT(*) FROM paragraphs").fetchone()[0]
    operation_count = service._conn.execute("SELECT COUNT(*) FROM memory_transfer_operations").fetchone()[0]
    second = service.link_object_to_scope(request)
    assert first["status"] == "applied"
    assert second["status"] == "already_applied"
    assert service._conn.execute(
        "SELECT COUNT(*) FROM memory_scope_members WHERE object_id='p1' AND partition_id='partition-target'"
    ).fetchone()[0] == membership_count == 1
    assert service._conn.execute("SELECT COUNT(*) FROM paragraphs").fetchone()[0] == paragraph_count
    assert service._conn.execute("SELECT COUNT(*) FROM memory_transfer_operations").fetchone()[0] == operation_count == 1
    assert service._conn.execute("SELECT COUNT(*) FROM memory_transfer_objects").fetchone()[0] == 0


async def _md03() -> None:
    service = _authority()
    result = service.link_object_to_scope(
        _link_request(
            operation_key="md03", target_space_id="space-normal-peer",
            target_partition_id="partition-normal-peer",
        )
    )
    assert result["status"] == "applied"
    assert result["target_object_id"] == "p1"
    assert service.inspect_object({
        "object_type": "paragraph", "object_id": "p1",
        "memory_space_id": "space-normal-peer", "partition_id": "partition-normal-peer",
        "security_domain": "normal",
    })["object_id"] == "p1"


async def _md04() -> None:
    service = _authority()
    before = service._conn.execute("SELECT COUNT(*) FROM memory_scope_members").fetchone()[0]
    with pytest.raises(MemoryTransferAuthorityError) as caught:
        service.link_object_to_scope(
            _link_request(
                operation_key="md04", target_security_domain="kami",
                target_space_id="space-kami", target_partition_id="partition-kami",
            )
        )
    assert str(caught.value) == "cross_domain_transfer_denied"
    assert service._conn.execute("SELECT COUNT(*) FROM memory_scope_members").fetchone()[0] == before
    assert service._conn.execute("SELECT COUNT(*) FROM memory_transfer_operations").fetchone()[0] == 0


async def _md05() -> None:
    service = _authority()
    result = service.copy_object_to_scope(_copy_request(operation_key="md05"))
    target_id = result["target_object_id"]
    assert target_id and target_id != "p1"
    metadata = service.inspect_object({
        "object_type": "paragraph", "object_id": target_id,
        "memory_space_id": "space-target", "partition_id": "partition-target",
        "security_domain": "normal",
    })
    assert metadata["object_id"] == target_id
    listed = service.list_scoped_objects({
        "memory_space_id": "space-target", "partition_ids": ["partition-target"],
        "security_domain": "normal", "object_types": ["paragraph"],
        "object_ids": [target_id], "limit": 10,
    })
    assert [item["object_id"] for item in listed["items"]] == [target_id]
    transfer = service._conn.execute(
        "SELECT source_object_id,target_object_id FROM memory_transfer_objects WHERE target_object_id=?",
        (target_id,),
    ).fetchone()
    assert tuple(transfer) == ("p1", target_id)


async def _md06() -> None:
    service = _authority()
    source = service.inspect_object({
        "object_type": "paragraph", "object_id": "p1", "memory_space_id": "space-source",
        "partition_id": "partition-source", "security_domain": "normal",
    })
    result = service.copy_object_to_scope(
        _copy_request(
            operation_key="md06", expected_fingerprint=source["content_fingerprint"],
            expected_source_version=source["source_version"],
        )
    )
    target = service.inspect_object({
        "object_type": "paragraph", "object_id": result["target_object_id"],
        "memory_space_id": "space-target", "partition_id": "partition-target",
        "security_domain": "normal",
    })
    assert result["source_fingerprint"] == result["target_fingerprint"]
    assert target["content_fingerprint"] == source["content_fingerprint"]
    assert service.inspect_object({
        "object_type": "paragraph", "object_id": "p1", "memory_space_id": "space-source",
        "partition_id": "partition-source", "security_domain": "normal",
    })["content_fingerprint"] == source["content_fingerprint"]


async def _md07() -> None:
    service = _authority()
    result = service.copy_object_to_scope(_copy_request(operation_key="md07"))
    target_id = result["target_object_id"]
    service._conn.execute("DELETE FROM memory_scope_members WHERE object_type='paragraph' AND object_id='p1'")
    service._conn.execute("DELETE FROM paragraphs WHERE hash='p1'")
    service._conn.commit()
    with pytest.raises(MemoryTransferAuthorityError) as caught:
        service.inspect_object({
            "object_type": "paragraph", "object_id": "p1", "memory_space_id": "space-source",
            "partition_id": "partition-source", "security_domain": "normal",
        })
    assert str(caught.value) == "source_object_not_found"
    target = service.inspect_object({
        "object_type": "paragraph", "object_id": target_id, "memory_space_id": "space-target",
        "partition_id": "partition-target", "security_domain": "normal",
    })
    assert target["object_id"] == target_id
    assert service._conn.execute("SELECT content FROM paragraphs WHERE hash=?", (target_id,)).fetchone()[0] == "source body"
    listed = service.list_scoped_objects({
        "memory_space_id": "space-target", "partition_ids": ["partition-target"],
        "security_domain": "normal", "object_types": ["paragraph"], "limit": 10,
    })
    assert target_id in {item["object_id"] for item in listed["items"]}


async def _md08() -> None:
    service, authority, projection = _workspace_setup(target_space="memory-space-public")
    ctx = context(home_space_id="memory-space-public")
    actor = principal_id(ctx)
    job = service.create_job(
        _request(
            mode="promote", target_space_id="memory-space-public", idempotency_key="md08"
        ),
        ctx,
        actor,
    )
    planned = await service.plan_job(job.id, ctx, actor)
    policy = json.loads(planned.policy_snapshot_json)
    approved = service.approve_job(
        planned.id,
        ApprovalRequest(
            planned.plan_hash, planned.plan_revision, policy["policy_revision"], planned.policy_snapshot_hash
        ),
        "rotated-reviewer-token",
        request_context=replace(ctx, person_id="reviewer"),
    )
    completed = await service.execute_job(approved.id, ctx, actor)
    assert completed.status == "completed"
    assert len(authority.copy_calls) == 1
    assert authority.copy_calls[0]["mode"] == "promote"
    assert authority.link_calls == authority.reconcile_calls == []
    assert projection.calls == 1


async def _md09() -> None:
    service, authority, projection = _workspace_setup()
    ctx = context()
    with pytest.raises(MemoryTransferError) as caught:
        service.create_job(
            _request(mode="promote", idempotency_key="md09"), ctx, principal_id(ctx)
        )
    assert caught.value.code == "promote_target_invalid"
    assert authority.list_calls == authority.copy_calls == authority.link_calls == authority.reconcile_calls == []
    assert projection.calls == 0


async def _md10() -> None:
    service, authority, projection = _workspace_setup(
        source_domain="kami", target_space="memory-space-public"
    )
    ctx = context(home_space_id="memory-space-public", security_domain="kami")
    with pytest.raises(MemoryTransferError) as caught:
        service.create_job(
            _request(
                mode="promote", target_space_id="memory-space-public", idempotency_key="md10"
            ),
            ctx,
            principal_id(ctx),
        )
    assert caught.value.code == "promote_source_not_normal"
    assert authority.list_calls == authority.copy_calls == authority.link_calls == authority.reconcile_calls == []
    assert projection.calls == 0


async def _md11() -> None:
    for object_type in ("memory", "summary"):
        with pytest.raises(MemoryTransferError) as caught:
            _request(object_types=(object_type,), idempotency_key=f"md11-{object_type}").canonical()
        assert caught.value.code == "unsupported_object_type"


async def _md12() -> None:
    with pytest.raises(MemoryTransferError) as caught:
        _request(object_types=("person_profile",), idempotency_key="md12").canonical()
    assert caught.value.code == "unsupported_object_type"
    with pytest.raises(MemoryTransferAuthorityError) as authority_caught:
        _authority().inspect_object({
            "object_type": "person_profile", "object_id": "p1",
            "memory_space_id": "space-source", "partition_id": "partition-source",
            "security_domain": "normal",
        })
    assert str(authority_caught.value) == "unsupported_object_type"


CASES: tuple[tuple[str, Case], ...] = (
    ("MD01", _md01), ("MD02", _md02), ("MD03", _md03),
    ("MD04", _md04), ("MD05", _md05), ("MD06", _md06),
    ("MD07", _md07), ("MD08", _md08), ("MD09", _md09),
    ("MD10", _md10), ("MD11", _md11), ("MD12", _md12),
)


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario_id,case", CASES, ids=[item[0] for item in CASES])
async def test_phase6_md_matrix(scenario_id: str, case: Case) -> None:
    assert case.__name__ == f"_{scenario_id.lower()}"
    await case()
