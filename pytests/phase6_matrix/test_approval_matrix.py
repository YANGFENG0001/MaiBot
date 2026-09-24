"""AP01-AP12 approval acceptance cases for Phase 6."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
import json

import pytest

from sqlmodel import select

from memory_transfer_test_support import BASE_CAPABILITIES, context, database, grant, seed_transfer_scope
from src.common.database.database_model import (
    MemoryTransferApproval,
    MemoryTransferAttempt,
    MemoryTransferEvent,
    MemoryTransferItem,
    MemoryTransferJob,
)
from src.workspaces.memory_transfer_authorization import MemoryTransferAuthorization
from src.workspaces.memory_transfer_models import ApprovalRequest, MemoryTransferError, TransferCreateRequest
from src.workspaces.memory_transfer_principal import principal_id
from src.workspaces.memory_transfer_repository import MemoryTransferRepository
from src.workspaces.memory_transfer_service import MemoryTransferService

pytestmark = pytest.mark.phase6_matrix

Case = Callable[[], Awaitable[None]]


class AuthoritySpy:
    def __init__(self) -> None:
        self.list_calls: list[dict] = []
        self.mutation_calls: list[tuple[str, dict]] = []

    async def list_scoped_objects(self, **kwargs):
        self.list_calls.append(kwargs)
        if kwargs["memory_space_id"] != "space-source":
            return {"items": [], "has_more": False}
        return {
            "items": [{
                "object_type": "paragraph",
                "object_id": "p1",
                "memory_space_id": "space-source",
                "partition_id": "partition-source",
                "security_domain": "normal",
                "content_fingerprint": "fp1",
                "root_object_type": "paragraph",
                "root_object_id": "p1",
                "source_version": "1",
            }],
            "has_more": False,
        }

    async def copy_object_to_scope(self, **kwargs):
        self.mutation_calls.append(("copy", kwargs))
        return {
            "status": "applied",
            "operation_key": kwargs["operation_key"],
            "object_type": kwargs["object_type"],
            "source_object_id": kwargs["source_object_id"],
            "target_object_id": "copy-p1",
            "target_space_id": kwargs["target_space_id"],
            "target_partition_id": kwargs["target_partition_id"],
            "target_fingerprint": "fp1",
        }

    async def link_object_to_scope(self, **kwargs):
        self.mutation_calls.append(("link", kwargs))
        return {
            "status": "applied",
            "operation_key": kwargs["operation_key"],
            "object_type": kwargs["object_type"],
            "source_object_id": kwargs["object_id"],
            "target_object_id": kwargs["object_id"],
            "target_space_id": kwargs["target_space_id"],
            "target_partition_id": kwargs["target_partition_id"],
            "content_fingerprint": "fp1",
        }

    async def get_transfer_operation(self, operation_key):
        return {"status": "not_applied", "operation_key": operation_key}


class ProjectionSpy:
    def register_object_partition(self, **_kwargs):
        return True


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
        "idempotency_key": "ap-job",
    }
    values.update(overrides)
    return TransferCreateRequest(**values)


def _setup(*, target_space: str = "space-target", auto_safe: bool = False):
    _engine, factory = database()
    seed_transfer_scope(factory, target_space=target_space)
    capabilities = BASE_CAPABILITIES + (("memory.transfer.auto_approve_safe",) if auto_safe else ())
    grant(factory, *capabilities)
    authority = AuthoritySpy()
    service = MemoryTransferService(
        repository=MemoryTransferRepository(factory),
        authorization=MemoryTransferAuthorization(factory),
        authority=authority,
        projection=ProjectionSpy(),
    )
    creator_context = context(home_space_id=target_space)
    reviewer_context = replace(creator_context, person_id="reviewer")
    return service, authority, factory, creator_context, reviewer_context


def _approval(
    job,
    *,
    plan_hash: str | None = None,
    plan_revision: int | None = None,
    revision: int | None = None,
    policy_hash: str | None = None,
    comment="",
) -> ApprovalRequest:
    snapshot = json.loads(job.policy_snapshot_json)
    return ApprovalRequest(
        plan_hash=job.plan_hash if plan_hash is None else plan_hash,
        plan_revision=job.plan_revision if plan_revision is None else plan_revision,
        policy_revision=snapshot["policy_revision"] if revision is None else revision,
        policy_snapshot_hash=job.policy_snapshot_hash if policy_hash is None else policy_hash,
        comment=comment,
    )


def _counts(factory) -> dict[str, int]:
    with factory() as session:
        return {
            "jobs": len(session.exec(select(MemoryTransferJob)).all()),
            "items": len(session.exec(select(MemoryTransferItem)).all()),
            "approvals": len(session.exec(select(MemoryTransferApproval)).all()),
            "attempts": len(session.exec(select(MemoryTransferAttempt)).all()),
            "events": len(session.exec(select(MemoryTransferEvent)).all()),
        }


def _database_state(factory) -> dict[str, list[dict]]:
    models = {
        "jobs": MemoryTransferJob,
        "items": MemoryTransferItem,
        "approvals": MemoryTransferApproval,
        "attempts": MemoryTransferAttempt,
        "events": MemoryTransferEvent,
    }
    with factory() as session:
        return {
            name: [
                row.model_dump(mode="json")
                for row in sorted(session.exec(select(model)).all(), key=lambda value: value.id)
            ]
            for name, model in models.items()
        }


def _assert_no_persisted(factory, *needles: str) -> None:
    serialized = json.dumps(_database_state(factory), ensure_ascii=False, sort_keys=True)
    for needle in needles:
        assert needle not in serialized


async def _planned(service, ctx, *, actor="rotated-creator-token", **request_overrides):
    job = service.create_job(_request(**request_overrides), ctx, actor)
    return await service.plan_job(job.id, ctx, actor)


async def _ap01() -> None:
    service, authority, factory, ctx, _reviewer = _setup()
    job = await _planned(service, ctx, mode="link", idempotency_key="ap01")
    assert (job.status, job.approval_required, job.approval_state) == ("awaiting_approval", True, "pending")
    assert _counts(factory)["approvals"] == _counts(factory)["attempts"] == 0
    assert authority.mutation_calls == []


async def _ap02() -> None:
    service, authority, factory, ctx, _reviewer = _setup()
    job = await _planned(service, ctx, mode="copy", idempotency_key="ap02")
    assert (job.status, job.approval_required, job.approval_state) == ("awaiting_approval", True, "pending")
    assert _counts(factory)["approvals"] == _counts(factory)["attempts"] == 0
    assert authority.mutation_calls == []


async def _ap03() -> None:
    service, authority, factory, ctx, _reviewer = _setup(target_space="memory-space-public", auto_safe=True)
    job = await _planned(
        service, ctx, mode="promote", target_space_id="memory-space-public",
        approval_policy="auto_safe", idempotency_key="ap03",
    )
    assert (job.status, job.approval_required, job.approval_state) == ("awaiting_approval", True, "pending")
    assert _counts(factory)["approvals"] == 0
    assert authority.mutation_calls == []


async def _ap04() -> None:
    service, authority, factory, ctx, _reviewer = _setup()
    job = await _planned(service, ctx, approval_policy="auto_safe", idempotency_key="ap04")
    assert (job.status, job.approval_required, job.approval_state) == ("awaiting_approval", True, "pending")
    assert _counts(factory)["approvals"] == 0
    assert authority.mutation_calls == []


async def _ap05() -> None:
    service, authority, factory, ctx, _reviewer = _setup(auto_safe=True)
    job = await _planned(service, ctx, approval_policy="auto_safe", idempotency_key="ap05")
    assert (job.status, job.approval_required, job.approval_state) == ("approved", False, "not_required")
    assert _counts(factory)["approvals"] == _counts(factory)["attempts"] == 0
    assert authority.mutation_calls == []


async def _ap06() -> None:
    service, authority, factory, creator, _reviewer = _setup(target_space="memory-space-public")
    job = await _planned(
        service, creator, mode="promote", target_space_id="memory-space-public", idempotency_key="ap06",
    )
    assert job.created_by == principal_id(creator)
    before = _counts(factory)
    with pytest.raises(MemoryTransferError) as caught:
        service.approve_job(
            job.id, _approval(job), "different-token-or-display-actor", request_context=creator
        )
    assert caught.value.code == "self_approval_denied"
    assert _counts(factory) == before
    assert service.repository.get(job.id).approval_state == "pending"
    assert authority.mutation_calls == []


async def _ap07() -> None:
    service, authority, factory, ctx, reviewer = _setup()
    first = await _planned(service, ctx, idempotency_key="ap07")
    old_approval = _approval(first)
    old_revision = first.plan_revision
    old_hash = first.plan_hash
    service.replace_selection_for_replan(
        first.id, _request(idempotency_key="ap07"), "rotated-replan-token",
        request_context=ctx,
    )
    replanned = await service.plan_job(first.id, ctx, "rotated-plan-token")
    assert replanned.plan_revision == old_revision + 1
    assert replanned.plan_hash == old_hash
    before = _database_state(factory)
    with pytest.raises(MemoryTransferError) as caught:
        service.approve_job(
            replanned.id, old_approval, "rotated-review-token", request_context=reviewer
        )
    assert caught.value.code == "approval_plan_mismatch"
    assert _database_state(factory) == before
    _assert_no_persisted(
        factory, "rotated-creator-token", "rotated-replan-token",
        "rotated-plan-token", "rotated-review-token",
    )
    assert authority.mutation_calls == []


async def _ap08() -> None:
    service, authority, factory, ctx, reviewer = _setup()
    job = await _planned(service, ctx, idempotency_key="ap08")
    before = _database_state(factory)
    for approval in (
        _approval(job, revision=json.loads(job.policy_snapshot_json)["policy_revision"] + 1),
        _approval(job, policy_hash="f" * 64),
    ):
        with pytest.raises(MemoryTransferError) as caught:
            service.approve_job(job.id, approval, "review-token", request_context=reviewer)
        assert caught.value.code == "approval_policy_mismatch"
        assert _database_state(factory) == before
    _assert_no_persisted(factory, "rotated-creator-token", "review-token")
    assert authority.mutation_calls == []


async def _ap09() -> None:
    service, authority, factory, ctx, reviewer = _setup()
    job = await _planned(service, ctx, idempotency_key="ap09")
    approval = _approval(job, comment="routine rejection")
    rejected = service.reject_job(job.id, approval, "review-token-1", request_context=reviewer)
    assert (rejected.status, rejected.approval_state) == ("rejected", "rejected")
    decided_state = _database_state(factory)

    repeated = service.reject_job(job.id, approval, "review-token-2", request_context=reviewer)
    assert repeated.status == "rejected"
    assert _database_state(factory) == decided_state

    with pytest.raises(MemoryTransferError) as caught:
        service.approve_job(
            job.id, _approval(job, plan_revision=job.plan_revision - 1),
            "review-token-3", request_context=reviewer,
        )
    assert caught.value.code == "invalid_state_transition"
    assert _database_state(factory) == decided_state

    with pytest.raises(MemoryTransferError) as caught:
        await service.execute_job(job.id, ctx, "creator-token")
    assert caught.value.code == "approval_required"
    assert _database_state(factory) == decided_state
    _assert_no_persisted(
        factory, "rotated-creator-token", "review-token-1", "review-token-2",
        "review-token-3", "creator-token",
    )
    assert authority.mutation_calls == []


async def _ap10() -> None:
    service, authority, factory, ctx, reviewer = _setup()
    job = await _planned(service, ctx, idempotency_key="ap10")
    approved = service.approve_job(
        job.id, _approval(job), "review-token-1", request_context=reviewer
    )
    revoked = service.revoke_approval(
        job.id, _approval(approved, comment="routine revocation"),
        "review-token-2", request_context=reviewer,
    )
    assert (revoked.status, revoked.approval_state, revoked.last_error_code) == (
        "awaiting_approval", "revoked", "approval_revoked"
    )
    revoked_state = _database_state(factory)
    mismatches = (
        (_approval(revoked, plan_revision=revoked.plan_revision - 1), "approval_plan_mismatch"),
        (_approval(revoked, plan_hash="0" * 64), "approval_plan_mismatch"),
        (
            _approval(
                revoked,
                revision=json.loads(revoked.policy_snapshot_json)["policy_revision"] + 1,
            ),
            "approval_policy_mismatch",
        ),
        (_approval(revoked, policy_hash="f" * 64), "approval_policy_mismatch"),
    )
    for stale_approval, code in mismatches:
        with pytest.raises(MemoryTransferError) as caught:
            service.approve_job(
                job.id, stale_approval, "stale-review-token", request_context=reviewer
            )
        assert caught.value.code == code
        assert _database_state(factory) == revoked_state

    restored = service.approve_job(
        job.id, _approval(revoked), "review-token-3", request_context=reviewer
    )
    assert (restored.status, restored.approval_state, restored.last_error_code) == (
        "approved", "approved", ""
    )
    with factory() as session:
        approvals = session.exec(
            select(MemoryTransferApproval).where(MemoryTransferApproval.job_id == job.id)
        ).all()
        events = session.exec(
            select(MemoryTransferEvent).where(MemoryTransferEvent.job_id == job.id)
        ).all()
    assert len(approvals) == 1 and approvals[0].decision == "approved"
    assert sum(event.event_type == "approval" for event in events) == 2
    assert sum(event.event_type == "approval_revoked" for event in events) == 1
    assert all("routine revocation" not in event.details_json for event in events)
    _assert_no_persisted(
        factory, "rotated-creator-token", "review-token-1", "review-token-2",
        "review-token-3", "stale-review-token",
    )
    assert authority.mutation_calls == []


async def _ap11() -> None:
    service, authority, factory, ctx, reviewer = _setup()
    job = await _planned(service, ctx, idempotency_key="ap11")
    approval = _approval(job, comment="routine approval")
    first = service.approve_job(job.id, approval, "review-token-1", request_context=reviewer)
    after_first = _database_state(factory)
    second = service.approve_job(job.id, approval, "review-token-2", request_context=reviewer)
    assert first.status == second.status == "approved"
    assert _database_state(factory) == after_first

    with pytest.raises(MemoryTransferError) as caught:
        service.reject_job(
            job.id, _approval(job, plan_revision=job.plan_revision - 1),
            "review-token-3", request_context=reviewer,
        )
    assert caught.value.code == "invalid_state_transition"
    assert _database_state(factory) == after_first

    with factory() as session:
        stored = session.exec(
            select(MemoryTransferApproval).where(MemoryTransferApproval.job_id == job.id)
        ).one()
    assert stored.actor_id == principal_id(reviewer)
    assert stored.plan_revision == job.plan_revision
    assert stored.plan_hash == job.plan_hash
    assert stored.policy_snapshot_hash == job.policy_snapshot_hash
    _assert_no_persisted(
        factory, "rotated-creator-token", "review-token-1", "review-token-2",
        "review-token-3",
    )
    assert authority.mutation_calls == []


async def _ap12() -> None:
    service, authority, factory, ctx, reviewer = _setup()
    rejected_comments = (
        "content=raw-memory-body",
        "x" * 257,
        {"meta": {"body": "raw-memory-body"}},
        '{"meta":{"body":"raw-memory-body"}}',
    )
    for index, comment in enumerate(rejected_comments):
        job = await _planned(service, ctx, idempotency_key=f"ap12-reject-{index}")
        before = _database_state(factory)
        with pytest.raises(MemoryTransferError) as caught:
            service.approve_job(
                job.id, _approval(job, comment=comment),
                "rotated-rejected-review-token", request_context=reviewer,
            )
        assert caught.value.code == "invalid_request"
        assert _database_state(factory) == before

    clean_job = await _planned(service, ctx, idempotency_key="ap12-clean")
    approved = service.approve_job(
        clean_job.id, _approval(clean_job, comment="safe\x00\ncomment"),
        "rotated-clean-review-token", request_context=reviewer,
    )
    executed = await service.execute_job(
        approved.id, ctx, "rotated-executor-token"
    )
    assert executed.status == "completed"

    cancel_job = await _planned(service, ctx, idempotency_key="ap12-cancel")
    cancelled = service.cancel_job(
        cancel_job.id, "rotated-cancel-token", request_context=ctx
    )
    assert cancelled.status == "cancelled"

    with factory() as session:
        approval = session.exec(
            select(MemoryTransferApproval).where(MemoryTransferApproval.job_id == clean_job.id)
        ).one()
        events = session.exec(select(MemoryTransferEvent)).all()
        attempts = session.exec(select(MemoryTransferAttempt)).all()
    assert approval.comment == "safecomment"
    assert attempts and all(attempt.lease_token_hash for attempt in attempts)
    serialized_events = json.dumps(
        [(event.actor_id, event.details_json) for event in events], ensure_ascii=False
    )
    assert "raw-memory-body" not in serialized_events
    assert "safecomment" not in serialized_events
    mutation_payload = json.dumps(authority.mutation_calls, ensure_ascii=False, sort_keys=True)
    for secret in (
        "rotated-creator-token",
        "rotated-rejected-review-token",
        "rotated-clean-review-token",
        "rotated-executor-token",
        "rotated-cancel-token",
        "raw-memory-body",
    ):
        _assert_no_persisted(factory, secret)
        assert secret not in mutation_payload


CASES: tuple[tuple[str, Case], ...] = (
    ("AP01", _ap01), ("AP02", _ap02), ("AP03", _ap03), ("AP04", _ap04),
    ("AP05", _ap05), ("AP06", _ap06), ("AP07", _ap07), ("AP08", _ap08),
    ("AP09", _ap09), ("AP10", _ap10), ("AP11", _ap11), ("AP12", _ap12),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(("case_id", "case"), CASES, ids=[case_id for case_id, _case in CASES])
async def test_phase6_ap_matrix(case_id: str, case: Case) -> None:
    assert case_id.startswith("AP")
    await case()
