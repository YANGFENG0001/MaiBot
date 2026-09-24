"""Legacy v46 transfer rows remain readable but cannot be executed."""

from __future__ import annotations

import pytest

from memory_transfer_test_support import BASE_CAPABILITIES, context, database, grant, seed_transfer_scope
from src.common.database.database_model import MemoryTransferJob
from src.workspaces.memory_transfer_authorization import MemoryTransferAuthorization
from src.workspaces.memory_transfer_models import ApprovalRequest, MemoryTransferError
from src.workspaces.memory_transfer_repository import MemoryTransferRepository
from src.workspaces.memory_transfer_service import MemoryTransferService


class NoMutationAuthority:
    def __init__(self):
        self.calls = 0

    def __getattr__(self, _name):
        async def forbidden(*_args, **_kwargs):
            self.calls += 1
            raise AssertionError("legacy job reached authority")
        return forbidden


def setup_legacy():
    _engine, factory = database()
    seed_transfer_scope(factory)
    grant(factory, *BASE_CAPABILITIES)
    authority = NoMutationAuthority()
    service = MemoryTransferService(
        repository=MemoryTransferRepository(factory),
        authorization=MemoryTransferAuthorization(factory),
        authority=authority,
    )
    with factory() as session:
        session.add(MemoryTransferJob(
            id="legacy-job", source_space_id="space-source", target_space_id="space-target",
            mode="copy", filters_json='{ "ids": ["paragraph-1"] }', selection_json="{}",
            status="pending", created_by="legacy", permission_group_id="group-transfer",
            security_domain="normal",
        ))
        session.commit()
    return service, authority


def test_legacy_list_and_get_are_safe_and_preserve_filters_only():
    service, _authority = setup_legacy()
    job = service.get_job("legacy-job", "reader", request_context=context())
    assert job.selection_json == "{}"
    assert "paragraph-1" in job.filters_json
    assert [row.id for row in service.list_jobs("reader", request_context=context())] == ["legacy-job"]


@pytest.mark.asyncio
async def test_legacy_write_actions_reject_without_authority_rpc():
    service, authority = setup_legacy()
    approval = ApprovalRequest("legacy-plan", 1, 1, "legacy-policy")
    actions = [
        lambda: service.plan_job("legacy-job", context(), "actor"),
        lambda: service.execute_job("legacy-job", context(), "actor"),
        lambda: service.retry_job("legacy-job", context(), "actor"),
        lambda: service.reconcile_job("legacy-job", context(), "actor"),
    ]
    for action in actions:
        with pytest.raises(MemoryTransferError, match="legacy_transfer_job_requires_recreate"):
            await action()
    for action in (
        lambda: service.approve_job("legacy-job", approval, "actor", request_context=context()),
        lambda: service.reject_job("legacy-job", approval, "actor", request_context=context()),
        lambda: service.cancel_job("legacy-job", "actor", request_context=context()),
    ):
        with pytest.raises(MemoryTransferError, match="legacy_transfer_job_requires_recreate"):
            action()
    assert authority.calls == 0
