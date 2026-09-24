"""Authenticated backend API for auditable MemoryTransfer workflows."""

from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from src.webui.dependencies import require_auth, require_auth_with_rate_limit
from src.workspaces.memory_transfer_principal import principal_id
from src.workspaces.memory_transfer_models import ApprovalRequest, MemoryTransferError, TransferCreateRequest
from src.workspaces.memory_transfer_service import memory_transfer_service
from src.workspaces.service import workspace_service

router = APIRouter(
    prefix="/memory-transfers",
    tags=["MemoryTransfer"],
    dependencies=[Depends(require_auth)],
)


class RequestScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1, max_length=255)
    person_id: str = Field(min_length=1, max_length=255)
    audience_type: Literal["private", "group"] = "private"


class TransferCreateBody(RequestScope):
    mode: Literal["link", "copy", "promote"]
    source_space_id: str = Field(min_length=1, max_length=64)
    source_partition_ids: list[str] = Field(min_length=1, max_length=128)
    target_space_id: str = Field(min_length=1, max_length=64)
    target_partition_id: str = Field(min_length=1, max_length=96)
    object_types: list[str] = Field(min_length=1)
    object_ids: list[str] = Field(default_factory=list, max_length=10_000)
    filters: dict[str, Any] = Field(default_factory=dict)
    approval_policy: Literal["manual", "auto_safe"] = "manual"
    conflict_policy: Literal["skip", "fail"] = "skip"
    idempotency_key: str = Field(default="", max_length=128)
    client_nonce: str = Field(default="", max_length=128)


class ApprovalBody(RequestScope):
    plan_hash: str = Field(min_length=1, max_length=128)
    plan_revision: int = Field(ge=1)
    policy_revision: int = Field(ge=0)
    policy_snapshot_hash: str = Field(min_length=1, max_length=128)
    comment: str = Field(default="", max_length=256)


class ScopeBody(RequestScope):
    pass


def _actor(scope: RequestScope) -> str:
    return principal_id(_context(scope))


def _context(scope: RequestScope):
    return workspace_service.build_bot_request_context(
        scope.session_id,
        scope.person_id,
        scope.audience_type,
    )


def _job_view(job) -> dict[str, Any]:
    return {
        "id": job.id,
        "status": job.status,
        "mode": job.mode,
        "source_space_id": job.source_space_id,
        "target_space_id": job.target_space_id,
        "approval_required": job.approval_required,
        "approval_state": job.approval_state,
        "plan_hash": job.plan_hash,
        "policy_snapshot_hash": getattr(job, "policy_snapshot_hash", ""),
        "plan_revision": getattr(job, "plan_revision", 1),
        "source_space_revision": job.source_space_revision,
        "target_space_revision": job.target_space_revision,
        "retry_count": job.retry_count,
        "max_retries": job.max_retries,
        "next_retry_at": job.next_retry_at,
        "last_error_code": job.last_error_code,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


def _item_view(item) -> dict[str, Any]:
    return {
        "id": item.id,
        "job_id": item.job_id,
        "mode": item.mode,
        "object_type": item.object_type,
        "source_object_id": item.source_object_id,
        "target_object_id": item.target_object_id,
        "source_space_id": item.source_space_id,
        "source_partition_id": item.source_partition_id,
        "target_space_id": item.target_space_id,
        "target_partition_id": item.target_partition_id,
        "status": item.status,
        "conflict_code": item.conflict_code,
        "error_code": item.error_code,
        "attempt_count": item.attempt_count,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def _error(exc: MemoryTransferError) -> HTTPException:
    code = exc.code
    if code in {"job_not_found", "source_not_found", "target_not_found"}:
        http_status = status.HTTP_404_NOT_FOUND
    elif code in {
        "permission_denied", "transfer_capability_required", "source_not_readable",
        "target_not_writable", "cross_space_write_denied", "kami_source_forbidden",
        "cross_domain_transfer_denied",
    }:
        http_status = status.HTTP_403_FORBIDDEN
    elif code in {
        "invalid_state_transition", "already_completed", "approval_required", "stale_policy",
        "stale_plan", "approval_plan_mismatch", "approval_policy_mismatch",
        "idempotency_key_reused_with_different_payload", "conflict", "cycle_detected",
        "lineage_conflict", "retry_not_due", "source_precondition_failed",
    }:
        http_status = status.HTTP_409_CONFLICT
    elif code in {"unsupported_object_type", "promote_target_invalid", "promote_source_not_normal"}:
        http_status = status.HTTP_422_UNPROCESSABLE_ENTITY
    else:
        http_status = status.HTTP_400_BAD_REQUEST
    return HTTPException(status_code=http_status, detail=code)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_transfer(body: TransferCreateBody, token: str = Depends(require_auth)):
    try:
        request = TransferCreateRequest(
            mode=body.mode,
            source_space_id=body.source_space_id,
            source_partition_ids=tuple(body.source_partition_ids),
            target_space_id=body.target_space_id,
            target_partition_id=body.target_partition_id,
            object_types=tuple(body.object_types),
            object_ids=tuple(body.object_ids),
            filters=body.filters,
            approval_policy=body.approval_policy,
            conflict_policy=body.conflict_policy,
            idempotency_key=body.idempotency_key,
            client_nonce=body.client_nonce,
        )
        return _job_view(memory_transfer_service.create_job(request, _context(body), _actor(body)))
    except MemoryTransferError as exc:
        raise _error(exc) from exc


@router.get("")
async def list_transfers(
    session_id: str,
    person_id: str,
    audience_type: Literal["private", "group"] = "private",
    limit: int = Query(100, ge=1, le=100),
    offset: int = Query(0, ge=0),
    token: str = Depends(require_auth),
):
    scope = RequestScope(session_id=session_id, person_id=person_id, audience_type=audience_type)
    try:
        rows = memory_transfer_service.list_jobs(
            _actor(scope), limit=limit, offset=offset, request_context=_context(scope)
        )
        return {"data": [_job_view(row) for row in rows], "limit": limit, "offset": offset}
    except MemoryTransferError as exc:
        raise _error(exc) from exc


@router.get("/{job_id}")
async def get_transfer(
    job_id: str,
    session_id: str,
    person_id: str,
    audience_type: Literal["private", "group"] = "private",
    include_items: bool = False,
    token: str = Depends(require_auth),
):
    scope = RequestScope(session_id=session_id, person_id=person_id, audience_type=audience_type)
    try:
        result = memory_transfer_service.get_job(
            job_id, _actor(scope), include_items=include_items, request_context=_context(scope)
        )
        if include_items:
            job, items = result
            return {"data": _job_view(job), "items": [_item_view(item) for item in items]}
        return {"data": _job_view(result)}
    except MemoryTransferError as exc:
        raise _error(exc) from exc


@router.get("/{job_id}/items")
async def list_transfer_items(
    job_id: str,
    session_id: str,
    person_id: str,
    audience_type: Literal["private", "group"] = "private",
    item_status: Optional[str] = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=100),
    offset: int = Query(0, ge=0),
    token: str = Depends(require_auth),
):
    scope = RequestScope(session_id=session_id, person_id=person_id, audience_type=audience_type)
    try:
        rows = memory_transfer_service.list_items(
            job_id, _actor(scope), item_status, limit, offset, request_context=_context(scope)
        )
        return {"data": [_item_view(row) for row in rows], "limit": limit, "offset": offset}
    except MemoryTransferError as exc:
        raise _error(exc) from exc


async def _run_action(job_id: str, body: ScopeBody, token: str, action: str):
    actor = _actor(body)
    context = _context(body)
    try:
        handler = getattr(memory_transfer_service, f"{action}_job")
        result = await handler(job_id, context, actor)
        return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=jsonable_encoder({"data": _job_view(result)}))
    except MemoryTransferError as exc:
        raise _error(exc) from exc


@router.post("/{job_id}/plan", dependencies=[Depends(require_auth_with_rate_limit)])
# Mutation endpoints use the authenticated request rate limiter.

async def plan_transfer(job_id: str, body: ScopeBody, token: str = Depends(require_auth)):
    return await _run_action(job_id, body, token, "plan")


@router.post("/{job_id}/execute", dependencies=[Depends(require_auth_with_rate_limit)])
# Mutation endpoints use the authenticated request rate limiter.

async def execute_transfer(job_id: str, body: ScopeBody, token: str = Depends(require_auth)):
    return await _run_action(job_id, body, token, "execute")


@router.post("/{job_id}/retry", dependencies=[Depends(require_auth_with_rate_limit)])
# Mutation endpoints use the authenticated request rate limiter.

async def retry_transfer(job_id: str, body: ScopeBody, token: str = Depends(require_auth)):
    return await _run_action(job_id, body, token, "retry")


@router.post("/{job_id}/reconcile", dependencies=[Depends(require_auth_with_rate_limit)])
# Mutation endpoints use the authenticated request rate limiter.

async def reconcile_transfer(job_id: str, body: ScopeBody, token: str = Depends(require_auth)):
    return await _run_action(job_id, body, token, "reconcile")


@router.post("/{job_id}/approve", dependencies=[Depends(require_auth_with_rate_limit)])
# Mutation endpoints use the authenticated request rate limiter.

async def approve_transfer(job_id: str, body: ApprovalBody, token: str = Depends(require_auth)):
    try:
        result = memory_transfer_service.approve_job(
            job_id,
            ApprovalRequest(
                plan_hash=body.plan_hash,
                plan_revision=body.plan_revision,
                policy_revision=body.policy_revision,
                policy_snapshot_hash=body.policy_snapshot_hash,
                comment=body.comment,
            ),
            _actor(body),
            request_context=_context(body),
        )
        return {"data": _job_view(result)}
    except MemoryTransferError as exc:
        raise _error(exc) from exc


@router.post("/{job_id}/reject", dependencies=[Depends(require_auth_with_rate_limit)])
# Mutation endpoints use the authenticated request rate limiter.

async def reject_transfer(job_id: str, body: ApprovalBody, token: str = Depends(require_auth)):
    try:
        result = memory_transfer_service.reject_job(
            job_id,
            ApprovalRequest(
                plan_hash=body.plan_hash,
                plan_revision=body.plan_revision,
                policy_revision=body.policy_revision,
                policy_snapshot_hash=body.policy_snapshot_hash,
                comment=body.comment,
            ),
            _actor(body),
            request_context=_context(body),
        )
        return {"data": _job_view(result)}
    except MemoryTransferError as exc:
        raise _error(exc) from exc


@router.post("/{job_id}/revoke", dependencies=[Depends(require_auth_with_rate_limit)])
async def revoke_transfer(job_id: str, body: ApprovalBody, token: str = Depends(require_auth)):
    try:
        result = memory_transfer_service.revoke_approval(
            job_id,
            ApprovalRequest(
                plan_hash=body.plan_hash,
                plan_revision=body.plan_revision,
                policy_revision=body.policy_revision,
                policy_snapshot_hash=body.policy_snapshot_hash,
                comment=body.comment,
            ),
            _actor(body),
            request_context=_context(body),
        )
        return {"data": _job_view(result)}
    except MemoryTransferError as exc:
        raise _error(exc) from exc


@router.post("/{job_id}/cancel", dependencies=[Depends(require_auth_with_rate_limit)])
# Mutation endpoints use the authenticated request rate limiter.

async def cancel_transfer(job_id: str, body: ScopeBody, token: str = Depends(require_auth)):
    try:
        result = memory_transfer_service.cancel_job(
            job_id, _actor(body), request_context=_context(body)
        )
        return {"data": _job_view(result)}
    except MemoryTransferError as exc:
        raise _error(exc) from exc
