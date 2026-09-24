"""AZ01-AZ06 authorization acceptance cases for Phase 6."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime

import pytest

from pydantic import ValidationError
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
from src.webui.routers.memory_transfers import TransferCreateBody
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
        "object_ids": ("p1",),
        "filters": {},
        "approval_policy": "manual",
        "conflict_policy": "skip",
        "idempotency_key": "az-key",
        "client_nonce": "",
    }
    values.update(overrides)
    return TransferCreateRequest(**values)


class AuthoritySpy:
    def __init__(self, *, source_domain: str = "normal") -> None:
        self.source_domain = source_domain
        self.list_calls: list[dict] = []
        self.mutation_calls: list[tuple[str, dict]] = []
        self.reconcile_calls: list[str] = []

    async def list_scoped_objects(self, **kwargs):
        self.list_calls.append(dict(kwargs))
        if kwargs["memory_space_id"] == "space-source":
            rows = [{
                "object_type": "paragraph",
                "object_id": "p1",
                "memory_space_id": "space-source",
                "partition_id": "partition-source",
                "security_domain": self.source_domain,
                "content_fingerprint": "fp1",
                "root_object_type": "paragraph",
                "root_object_id": "p1",
                "source_version": "1",
            }]
        else:
            rows = []
        return {"items": rows, "has_more": False}

    async def copy_object_to_scope(self, **kwargs):
        self.mutation_calls.append(("copy", dict(kwargs)))
        raise AssertionError("authorization matrix must not mutate authority")

    async def link_object_to_scope(self, **kwargs):
        self.mutation_calls.append(("link", dict(kwargs)))
        raise AssertionError("authorization matrix must not mutate authority")

    async def get_transfer_operation(self, operation_key):
        self.reconcile_calls.append(operation_key)
        raise AssertionError("authorization matrix must not reconcile authority")


class ProjectionSpy:
    def __init__(self) -> None:
        self.calls = 0

    def register_object_partition(self, **_kwargs):
        self.calls += 1
        raise AssertionError("authorization matrix must not project objects")


def _setup(
    *,
    capabilities=BASE_CAPABILITIES,
    source_domain: str = "normal",
    target_domain: str = "normal",
    target_space: str = "space-target",
):
    _engine, factory = database()
    seed_transfer_scope(
        factory, source_domain=source_domain, target_domain=target_domain, target_space=target_space
    )
    if capabilities:
        grant(factory, *capabilities)
    authority = AuthoritySpy(source_domain=source_domain)
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


def _assert_rejected(
    service: MemoryTransferService,
    request: TransferCreateRequest,
    ctx,
    expected: str,
) -> None:
    with pytest.raises(MemoryTransferError) as caught:
        service.create_job(request, ctx, principal_id(ctx))
    assert caught.value.code == expected
    assert caught.value.retryable is False


def _assert_zero(factory, authority: AuthoritySpy, projection: ProjectionSpy) -> None:
    assert _counts(factory) == {
        "jobs": 0, "items": 0, "approvals": 0, "attempts": 0, "events": 0,
    }
    assert authority.list_calls == authority.mutation_calls == authority.reconcile_calls == []
    assert projection.calls == 0


async def _az01() -> None:
    factory, service, authority, projection = _setup()
    ctx = context(readable=())
    _assert_rejected(service, _request(idempotency_key="az01"), ctx, "source_not_readable")
    _assert_zero(factory, authority, projection)


async def _az02() -> None:
    factory, service, authority, projection = _setup()
    ctx = context(writable=())
    _assert_rejected(service, _request(idempotency_key="az02"), ctx, "target_not_writable")
    _assert_zero(factory, authority, projection)


async def _az03() -> None:
    factory, service, authority, projection = _setup(capabilities=())
    ctx = context()
    _assert_rejected(service, _request(idempotency_key="az03"), ctx, "transfer_capability_required")
    _assert_zero(factory, authority, projection)


async def _az04() -> None:
    factory, service, authority, projection = _setup(capabilities=("memory.read.force_all",))
    ctx = context()
    _assert_rejected(service, _request(idempotency_key="az04"), ctx, "transfer_capability_required")
    _assert_zero(factory, authority, projection)


async def _allowed_plan(mode: str, scenario: str) -> None:
    factory, service, authority, projection = _setup()
    ctx = context()
    actor = principal_id(ctx)
    job = service.create_job(_request(mode=mode, idempotency_key=scenario), ctx, actor)
    assert job.status == "pending"
    planned = await service.plan_job(job.id, ctx, actor)
    assert planned.status == "awaiting_approval"
    assert planned.approval_state == "pending"
    assert len(service.repository.items(job.id)) == 1
    assert len(authority.list_calls) == 2
    assert authority.mutation_calls == authority.reconcile_calls == []
    assert projection.calls == 0
    counts = _counts(factory)
    assert counts["jobs"] == 1 and counts["items"] == 1
    assert counts["approvals"] == counts["attempts"] == 0


async def _az05() -> None:
    await _allowed_plan("link", "az05")


async def _az06() -> None:
    await _allowed_plan("copy", "az06")


async def _az07() -> None:
    factory, service, authority, projection = _setup(target_domain="kami")
    ctx = context(security_domain="normal")
    _assert_rejected(
        service, _request(mode="link", idempotency_key="az07"), ctx,
        "cross_domain_transfer_denied",
    )
    _assert_zero(factory, authority, projection)


async def _az08() -> None:
    factory, service, authority, projection = _setup(target_domain="kami")
    ctx = context(security_domain="normal")
    _assert_rejected(
        service, _request(mode="copy", idempotency_key="az08"), ctx,
        "cross_domain_transfer_denied",
    )
    _assert_zero(factory, authority, projection)


async def _az09() -> None:
    capabilities = (*BASE_CAPABILITIES, "memory.transfer.cross_domain_copy")
    factory, service, authority, projection = _setup(
        capabilities=capabilities, target_domain="kami"
    )
    ctx = context(security_domain="normal")
    actor = principal_id(ctx)
    job = service.create_job(_request(mode="copy", idempotency_key="az09"), ctx, actor)
    planned = await service.plan_job(job.id, ctx, actor)
    assert planned.status == "awaiting_approval"
    assert planned.approval_required is True
    assert planned.approval_state == "pending"
    assert len(authority.list_calls) == 2
    assert authority.mutation_calls == authority.reconcile_calls == []
    assert projection.calls == 0
    counts = _counts(factory)
    assert counts["jobs"] == counts["items"] == 1

    other_factory, other_service, other_authority, other_projection = _setup(
        capabilities=capabilities, target_domain="kami"
    )
    _assert_rejected(
        other_service, _request(mode="link", idempotency_key="az09-link"), ctx,
        "cross_domain_transfer_denied",
    )
    _assert_zero(other_factory, other_authority, other_projection)


async def _az10() -> None:
    factory, service, authority, projection = _setup(source_domain="kami")
    ctx = context(security_domain="kami")
    _assert_rejected(
        service, _request(mode="copy", idempotency_key="az10"), ctx,
        "kami_source_forbidden",
    )
    _assert_zero(factory, authority, projection)


async def _az11() -> None:
    capabilities = (*BASE_CAPABILITIES, "memory.transfer.kami_copy")
    factory, service, authority, projection = _setup(
        capabilities=capabilities, source_domain="kami", target_space="memory-space-public"
    )
    ctx = context(home_space_id="memory-space-public", security_domain="kami")
    _assert_rejected(
        service,
        _request(
            mode="promote", target_space_id="memory-space-public", idempotency_key="az11"
        ),
        ctx,
        "promote_source_not_normal",
    )
    _assert_zero(factory, authority, projection)


async def _az12() -> None:
    capabilities = tuple(
        capability for capability in BASE_CAPABILITIES
        if capability != "memory.transfer.publish"
    )
    factory, service, authority, projection = _setup(
        capabilities=capabilities, target_space="memory-space-public"
    )
    ctx = context(home_space_id="memory-space-public")
    _assert_rejected(
        service,
        _request(
            mode="promote", target_space_id="memory-space-public", idempotency_key="az12"
        ),
        ctx,
        "transfer_capability_required",
    )
    _assert_zero(factory, authority, projection)


async def _az13() -> None:
    factory, service, authority, projection = _setup(target_space="memory-space-public")
    ctx = context(home_space_id="memory-space-public")
    actor = principal_id(ctx)
    job = service.create_job(
        _request(
            mode="promote", target_space_id="memory-space-public",
            approval_policy="auto_safe", idempotency_key="az13",
        ),
        ctx,
        actor,
    )
    planned = await service.plan_job(job.id, ctx, actor)
    assert planned.status == "awaiting_approval"
    assert planned.approval_required is True
    with pytest.raises(MemoryTransferError) as caught:
        await service.execute_job(job.id, ctx, actor)
    assert caught.value.code == "approval_required"
    assert len(authority.list_calls) == 2
    assert authority.mutation_calls == authority.reconcile_calls == []
    assert projection.calls == 0
    current = service.repository.get(job.id)
    assert current.status == "awaiting_approval"
    assert current.approval_state == "pending"
    counts = _counts(factory)
    assert counts["jobs"] == counts["items"] == 1
    assert counts["approvals"] == counts["attempts"] == 0


async def _az14() -> None:
    factory, service, authority, projection = _setup()
    with factory() as session:
        session.add_all([
            MemoryPartition(
                id="partition-current", memory_space_id="space-source",
                partition_type="conversation", partition_key="session-current",
                security_domain="normal", policy_revision=1,
                created_at=datetime.now(), updated_at=datetime.now(),
            ),
            MemoryPartition(
                id="partition-other", memory_space_id="space-source",
                partition_type="conversation", partition_key="session-other",
                security_domain="normal", policy_revision=1,
                created_at=datetime.now(), updated_at=datetime.now(),
            ),
        ])
        session.commit()
    ctx = context(readable=("partition-current",))
    object.__setattr__(ctx, "audience_type", "group")
    _assert_rejected(
        service,
        _request(
            source_partition_ids=("partition-other",), idempotency_key="az14"
        ),
        ctx,
        "source_not_readable",
    )
    _assert_zero(factory, authority, projection)


async def _az15() -> None:
    capabilities = (*BASE_CAPABILITIES, "memory.transfer.auto_approve_safe")
    factory, service, authority, projection = _setup(capabilities=capabilities)
    with factory() as session:
        session.get(MemorySpace, "space-source").policy_revision = 10
        session.get(MemorySpace, "space-target").policy_revision = 10
        session.commit()
    original_ctx = context(revision=1)
    actor = principal_id(original_ctx)
    job = service.create_job(
        _request(
            approval_policy="auto_safe", idempotency_key="az15"
        ),
        original_ctx,
        actor,
    )
    planned = await service.plan_job(job.id, original_ctx, actor)
    assert planned.status == "approved"
    assert planned.approval_required is False
    before_lists = list(authority.list_calls)
    changed_ctx = replace(original_ctx, policy_revision=2)
    with pytest.raises(MemoryTransferError) as caught:
        await service.execute_job(job.id, changed_ctx, principal_id(changed_ctx))
    assert caught.value.code == "stale_policy"
    current = service.repository.get(job.id)
    assert current.status == "stale_policy"
    assert current.last_error_code == "stale_policy"
    assert authority.list_calls == before_lists
    assert authority.mutation_calls == authority.reconcile_calls == []
    assert projection.calls == 0


async def _az16() -> None:
    factory, service, authority, projection = _setup()
    with factory() as session:
        target = session.get(MemorySpace, "space-target")
        target.enabled = False
        session.commit()
    ctx = context()
    _assert_rejected(service, _request(idempotency_key="az16"), ctx, "target_not_found")
    _assert_zero(factory, authority, projection)


async def _az17() -> None:
    factory, service, authority, projection = _setup()
    with factory() as session:
        source = session.get(MemorySpace, "space-source")
        source.strict_isolation = True
        session.commit()
    unreadable_ctx = context(readable=())
    _assert_rejected(
        service, _request(idempotency_key="az17-unreadable"), unreadable_ctx,
        "source_not_readable",
    )
    _assert_zero(factory, authority, projection)

    no_cap_factory, no_cap_service, no_cap_authority, no_cap_projection = _setup(
        capabilities=("memory.read.force_all",)
    )
    with no_cap_factory() as session:
        session.get(MemorySpace, "space-source").strict_isolation = True
        session.commit()
    readable_ctx = context()
    _assert_rejected(
        no_cap_service, _request(idempotency_key="az17-no-transfer"), readable_ctx,
        "transfer_capability_required",
    )
    _assert_zero(no_cap_factory, no_cap_authority, no_cap_projection)

    allowed_factory, allowed_service, allowed_authority, allowed_projection = _setup()
    with allowed_factory() as session:
        session.get(MemorySpace, "space-source").strict_isolation = True
        session.commit()
    actor = principal_id(readable_ctx)
    allowed = allowed_service.create_job(
        _request(idempotency_key="az17-allowed"), readable_ctx, actor
    )
    assert allowed.status == "pending"
    assert _counts(allowed_factory)["jobs"] == 1
    assert allowed_authority.list_calls == allowed_authority.mutation_calls == []
    assert allowed_authority.reconcile_calls == []
    assert allowed_projection.calls == 0


async def _az18() -> None:
    payload = {
        "session_id": "session-transfer",
        "person_id": "person-transfer",
        "audience_type": "private",
        "mode": "copy",
        "source_space_id": "space-source",
        "source_partition_ids": ["partition-source"],
        "target_space_id": "space-target",
        "target_partition_id": "partition-target",
        "object_types": ["paragraph"],
        "security_domain": "kami",
    }
    with pytest.raises(ValidationError):
        TransferCreateBody.model_validate(payload)
    assert "security_domain" not in TransferCreateBody.model_fields

    factory, service, authority, projection = _setup(target_domain="kami")
    host_context = context(security_domain="normal")
    _assert_rejected(
        service, _request(idempotency_key="az18"), host_context,
        "cross_domain_transfer_denied",
    )
    _assert_zero(factory, authority, projection)


async def _az19() -> None:
    factory, service, authority, projection = _setup()
    with factory() as session:
        outbound = session.exec(
            select(MemorySpaceACL).where(
                MemorySpaceACL.owner_space_id == "space-source",
                MemorySpaceACL.peer_space_id == "space-target",
            )
        ).one()
        outbound.can_publish_to_peer = False
        session.commit()
    ctx = context()
    _assert_rejected(service, _request(idempotency_key="az19"), ctx, "source_not_readable")
    _assert_zero(factory, authority, projection)


async def _az20() -> None:
    factory, service, authority, projection = _setup()
    with factory() as session:
        inbound = session.exec(
            select(MemorySpaceACL).where(
                MemorySpaceACL.owner_space_id == "space-target",
                MemorySpaceACL.peer_space_id == "space-source",
            )
        ).one()
        inbound.can_import_from_peer = False
        session.commit()
    ctx = context()
    _assert_rejected(service, _request(idempotency_key="az20"), ctx, "target_not_writable")
    _assert_zero(factory, authority, projection)


CASES: tuple[tuple[str, Case], ...] = (
    ("AZ01", _az01), ("AZ02", _az02), ("AZ03", _az03),
    ("AZ04", _az04), ("AZ05", _az05), ("AZ06", _az06),
    ("AZ07", _az07), ("AZ08", _az08), ("AZ09", _az09),
    ("AZ10", _az10), ("AZ11", _az11), ("AZ12", _az12),
    ("AZ13", _az13), ("AZ14", _az14), ("AZ15", _az15),
    ("AZ16", _az16), ("AZ17", _az17), ("AZ18", _az18),
    ("AZ19", _az19), ("AZ20", _az20),
)


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario_id,case", CASES, ids=[item[0] for item in CASES])
async def test_phase6_az_matrix(scenario_id: str, case: Case) -> None:
    assert case.__name__ == f"_{scenario_id.lower()}"
    await case()
