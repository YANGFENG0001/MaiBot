"""Unified MemoryTransfer orchestration service."""

from __future__ import annotations

from datetime import datetime
import json

from src.services.memory_service import memory_service
from src.workspaces.request_context import get_current_request_context
from .memory_transfer_principal import principal_id

from .memory_transfer_authorization import MemoryTransferAuthorization
from .memory_transfer_executor import MemoryTransferExecutor
from .memory_transfer_models import ApprovalRequest, MemoryTransferError, TransferCreateRequest
from .memory_transfer_planner import MemoryTransferPlanner
from .memory_transfer_repository import MemoryTransferRepository


class MemoryTransferService:
    def __init__(self, repository=None, authorization=None, authority=None, projection=None) -> None:
        self.repository = repository or MemoryTransferRepository()
        self.authorization = authorization or MemoryTransferAuthorization()
        self.authority = authority or memory_service
        self.planner = MemoryTransferPlanner(self.repository, self.authorization, self.authority)
        self.executor = MemoryTransferExecutor(
            self.repository,
            self.authorization,
            self.authority,
            projection,
        ) if projection is not None else MemoryTransferExecutor(
            self.repository,
            self.authorization,
            self.authority,
        )

    def create_job(self, request: TransferCreateRequest, request_context, actor: str):
        self.authorization.authorize(request, request_context)
        return self.repository.create(request, request_context, principal_id(request_context))[0]

    def replace_selection_for_replan(
        self,
        job_id: str,
        request: TransferCreateRequest,
        actor: str,
        request_context=None,
    ):
        context = request_context or get_current_request_context(required=True)
        job = self.repository.get(job_id)
        self._ensure_not_legacy(job)
        self._authorize_job(job, context)
        self.authorization.authorize(request, context)
        stable_actor = principal_id(context)
        return self.repository.replace_selection_for_replan(job_id, request, actor=stable_actor)

    def get_job(
        self,
        job_id: str,
        actor: str,
        include_items: bool = False,
        request_context=None,
    ):
        context = request_context or get_current_request_context(required=True)
        job = self.repository.get(job_id)
        if self._is_legacy(job):
            self._authorize_legacy_job(job, context)
        else:
            self._authorize_job(job, context)
        return (job, self.repository.items(job_id)) if include_items else job

    def list_jobs(
        self,
        actor: str,
        *,
        limit: int = 100,
        offset: int = 0,
        request_context=None,
    ):
        context = request_context or get_current_request_context(required=True)
        if not context.readable_partition_ids:
            return []
        return self.repository.list_jobs(
            limit=limit, offset=offset, principal=principal_id(context),
            workspace_id=context.workspace_id, bot_profile_id=context.active_bot_profile_id,
            permission_group_id=context.permission_group_id, security_domain=context.security_domain,
        )

    async def plan_job(self, job_id: str, request_context, actor: str):
        context = request_context or get_current_request_context(required=True)
        job = self.repository.get(job_id)
        self._ensure_not_legacy(job)
        self._authorize_job(job, context)
        return await self.planner.plan(job, context, principal_id(context))

    def approve_job(
        self,
        job_id: str,
        approval: ApprovalRequest,
        actor: str,
        request_context=None,
    ):
        context = request_context or get_current_request_context(required=True)
        self.authorization.require_capability(context, "memory.transfer.approve")
        job = self.repository.get(job_id)
        self._ensure_not_legacy(job)
        if job.status == "rejected":
            raise MemoryTransferError("invalid_state_transition")
        policy = self._authorize_job(job, context, allow_reviewer=True)
        self._require_current_revision(job, policy)
        if approval.plan_revision != job.plan_revision or approval.plan_hash != job.plan_hash:
            raise MemoryTransferError("approval_plan_mismatch")
        if policy["policy_revision"] != approval.policy_revision:
            raise MemoryTransferError("approval_policy_mismatch")
        if approval.policy_snapshot_hash != policy["policy_snapshot_hash"]:
            raise MemoryTransferError("approval_policy_mismatch")
        return self.repository.approve(
            job_id,
            plan_revision=approval.plan_revision,
            plan_hash=approval.plan_hash,
            policy_revision=approval.policy_revision,
            policy_snapshot_hash=approval.policy_snapshot_hash,
            actor=principal_id(context),
            approved=True,
            comment=approval.comment,
        )

    def reject_job(
        self,
        job_id: str,
        approval: ApprovalRequest,
        actor: str,
        request_context=None,
    ):
        context = request_context or get_current_request_context(required=True)
        self.authorization.require_capability(context, "memory.transfer.approve")
        job = self.repository.get(job_id)
        self._ensure_not_legacy(job)
        if job.status == "approved":
            raise MemoryTransferError("invalid_state_transition")
        policy = self._authorize_job(job, context, allow_reviewer=True)
        self._require_current_revision(job, policy)
        if approval.plan_revision != job.plan_revision or approval.plan_hash != job.plan_hash:
            raise MemoryTransferError("approval_plan_mismatch")
        if approval.policy_revision != policy["policy_revision"] or approval.policy_snapshot_hash != policy["policy_snapshot_hash"]:
            raise MemoryTransferError("approval_policy_mismatch")
        return self.repository.approve(
            job_id,
            plan_revision=approval.plan_revision,
            plan_hash=approval.plan_hash,
            policy_revision=approval.policy_revision,
            policy_snapshot_hash=approval.policy_snapshot_hash,
            actor=principal_id(context),
            approved=False,
            comment=approval.comment,
        )

    def revoke_approval(self, job_id: str, approval: ApprovalRequest, actor: str, request_context=None):
        context = request_context or get_current_request_context(required=True)
        self.authorization.require_capability(context, "memory.transfer.approve")
        job = self.repository.get(job_id)
        self._ensure_not_legacy(job)
        policy = self._authorize_job(job, context, allow_reviewer=True)
        self._require_current_revision(job, policy)
        if approval.plan_revision != job.plan_revision or approval.plan_hash != job.plan_hash:
            raise MemoryTransferError("approval_plan_mismatch")
        if approval.policy_revision != policy["policy_revision"] or approval.policy_snapshot_hash != policy["policy_snapshot_hash"]:
            raise MemoryTransferError("approval_policy_mismatch")
        return self.repository.revoke_approval(
            job_id,
            plan_revision=approval.plan_revision,
            plan_hash=approval.plan_hash,
            policy_revision=approval.policy_revision,
            policy_snapshot_hash=approval.policy_snapshot_hash,
            actor=principal_id(context),
            comment=approval.comment,
        )

    def cancel_job(self, job_id: str, actor: str, request_context=None):
        context = request_context or get_current_request_context(required=True)
        self.authorization.require_capability(context, "memory.transfer.cancel")
        job = self.repository.get(job_id)
        self._ensure_not_legacy(job)
        policy = self._authorize_job(job, context)
        self._require_current_revision(job, policy)
        if job.status not in {"pending", "awaiting_approval", "approved", "retry_wait", "running"}:
            raise MemoryTransferError("invalid_state_transition")
        stable_actor = principal_id(context)
        requested = self.repository.request_cancel(job_id, actor=stable_actor)
        self.repository.cancel_unstarted_items(job_id, actor=stable_actor, include_retryable=True)
        if any(item.status == "running" for item in self.repository.items(job_id)):
            return requested
        return self.repository.transition(job_id, "cancelled", actor=stable_actor, expected={job.status})

    async def execute_job(self, job_id: str, request_context, actor: str):
        context = request_context or get_current_request_context(required=True)
        job = self.repository.get(job_id)
        self._ensure_not_legacy(job)
        self._authorize_job(job, context)
        return await self.executor.execute(job, context, principal_id(context))

    async def retry_job(self, job_id: str, request_context, actor: str):
        context = request_context or get_current_request_context(required=True)
        self.authorization.require_capability(context, "memory.transfer.retry")
        job = self.repository.get(job_id)
        self._ensure_not_legacy(job)
        self._authorize_job(job, context)
        if job.status != "retry_wait":
            raise MemoryTransferError("invalid_state_transition")
        if job.next_retry_at is not None and job.next_retry_at > datetime.now():
            raise MemoryTransferError("retry_not_due", retryable=True)
        return await self.executor.execute(job, context, principal_id(context))

    async def reconcile_job(self, job_id: str, request_context, actor: str):
        context = request_context or get_current_request_context(required=True)
        job = self.repository.get(job_id)
        self._ensure_not_legacy(job)
        self._authorize_job(job, context)
        return await self.executor.reconcile(job, context, principal_id(context))

    def list_items(
        self,
        job_id: str,
        actor: str,
        status=None,
        limit: int = 100,
        offset: int = 0,
        request_context=None,
    ):
        context = request_context or get_current_request_context(required=True)
        self._authorize_job(self.repository.get(job_id), context)
        return self.repository.items(job_id, status, limit=limit, offset=offset)

    @staticmethod
    def _authorize_legacy_job(job, context) -> None:
        if job.workspace_id and job.workspace_id != context.workspace_id:
            raise MemoryTransferError("permission_denied")
        if job.bot_profile_id and job.bot_profile_id != context.active_bot_profile_id:
            raise MemoryTransferError("permission_denied")
        if job.permission_group_id and job.permission_group_id != context.permission_group_id:
            raise MemoryTransferError("permission_denied")
        if job.security_domain and job.security_domain != context.security_domain:
            raise MemoryTransferError("permission_denied")

    def _authorize_job(self, job, context, *, allow_reviewer: bool = False):
        if job.workspace_id and job.workspace_id != context.workspace_id:
            raise MemoryTransferError("permission_denied")
        same_principal = not job.principal_id or job.principal_id == principal_id(context)
        if not same_principal:
            reviewer_capabilities = self.authorization.capabilities(context.permission_group_id)
            same_scope = (
                (not job.bot_profile_id or job.bot_profile_id == context.active_bot_profile_id)
                and (not job.permission_group_id or job.permission_group_id == context.permission_group_id)
                and (not job.security_domain or job.security_domain == context.security_domain)
            )
            if not (allow_reviewer and same_scope and "memory.transfer.approve" in reviewer_capabilities):
                raise MemoryTransferError("permission_denied")
        request = TransferCreateRequest.from_canonical(json.loads(job.selection_json))
        return self.authorization.authorize(request, context)

    @staticmethod
    def _is_legacy(job) -> bool:
        if not job.selection_json or job.selection_json == "{}":
            return True
        try:
            payload = json.loads(job.selection_json)
        except (TypeError, ValueError):
            return True
        return not isinstance(payload, dict) or not {"mode", "source_space_id", "source_partition_ids", "target_space_id", "target_partition_id", "object_types"}.issubset(payload)

    @classmethod
    def _ensure_not_legacy(cls, job) -> None:
        if cls._is_legacy(job):
            raise MemoryTransferError("legacy_transfer_job_requires_recreate")

    @staticmethod
    def _require_current_revision(job, policy) -> None:
        snapshot = json.loads(job.policy_snapshot_json or "{}")
        if job.plan_hash and (
            int(snapshot.get("policy_revision", 0)) != int(policy["policy_revision"])
            or int(snapshot.get("context_revision", -1)) != int(policy["context_revision"])
            or int(job.source_space_revision) != int(policy["source_revision"])
            or int(job.target_space_revision) != int(policy["target_revision"])
            or str(job.policy_snapshot_hash or snapshot.get("policy_snapshot_hash", "")) != str(policy["policy_snapshot_hash"])
        ):
            raise MemoryTransferError("stale_policy")


memory_transfer_service = MemoryTransferService()
