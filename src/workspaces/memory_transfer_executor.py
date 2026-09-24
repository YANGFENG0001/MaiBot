"""Idempotent executor, retry and reconciliation for MemoryTransfer."""

from __future__ import annotations

from hashlib import sha256
from uuid import uuid4

import json

from src.workspaces.partition_service import partition_service

from .memory_transfer_models import (
    LINEAGE_MAX_DEPTH,
    RETRYABLE_ERRORS,
    MemoryTransferError,
    TransferCreateRequest,
    canonical_hash,
)


class MemoryTransferExecutor:
    def __init__(self, repository, authorization, memory_service, projection=partition_service) -> None:
        self.repository = repository
        self.authorization = authorization
        self.memory_service = memory_service
        self.projection = projection

    async def execute(self, job, context, actor: str):
        request = TransferCreateRequest.from_canonical(json.loads(job.selection_json))
        policy = self.authorization.authorize(request, context)
        current = self.repository.get(job.id)
        if current.status == "completed":
            raise MemoryTransferError("already_completed")
        if current.status not in {"approved", "retry_wait"}:
            if current.approval_required:
                raise MemoryTransferError("approval_required")
            raise MemoryTransferError("invalid_state_transition")
        self._validate_policy(current, policy)
        if current.approval_required and current.approval_state != "approved":
            raise MemoryTransferError("approval_required")
        worker_id = f"memory-transfer-{uuid4().hex[:16]}"
        lease = self.repository.acquire_lease(current.id, worker_id=worker_id)
        try:
            self.repository.transition(
                current.id,
                "running",
                actor=actor,
                expected={"approved", "retry_wait"},
            )
            for candidate in self.repository.items(current.id):
                cancel_requested = self.repository.get(current.id).cancel_requested
                if candidate.status not in {"planned", "failed_retryable"}:
                    continue
                self.repository.renew_lease(current.id, lease, ttl_seconds=60)
                if candidate.status == "failed_retryable":
                    reconciled = await self._reconcile_item(candidate, actor=actor)
                    if reconciled in {"applied", "unknown"}:
                        continue
                    if cancel_requested:
                        self.repository.cancel_item(candidate.id, actor=actor)
                        continue
                if cancel_requested:
                    self.repository.cancel_unstarted_items(current.id, actor=actor)
                    break
                item = self.repository.claim_item(candidate.id, worker_id=worker_id, lease_token=lease)
                reserved = False
                try:
                    self._validate_item_plan(item)
                    reserved = self.repository.reserve_lineage_edge(item)
                    if not reserved:
                        raise MemoryTransferError("lineage_conflict")
                    result = await self._mutate(item, policy)
                    self._apply_authority_result(item, result, actor=actor)
                except MemoryTransferError as exc:
                    self.repository.release_lineage_reservation(item.id)
                    self.repository.fail_item(
                        item.id,
                        exc.code,
                        exc.retryable or exc.code in RETRYABLE_ERRORS,
                        actor=actor,
                    )
                except PermissionError:
                    self.repository.release_lineage_reservation(item.id)
                    self.repository.fail_item(item.id, "permission_denied", False, actor=actor)
                except TimeoutError:
                    self.repository.release_lineage_reservation(item.id)
                    self.repository.fail_item(item.id, "upstream_timeout", True, actor=actor)
                except ConnectionError:
                    self.repository.release_lineage_reservation(item.id)
                    self.repository.fail_item(item.id, "upstream_connection_reset", True, actor=actor)
                except Exception:
                    self.repository.release_lineage_reservation(item.id)
                    self.repository.fail_item(item.id, "upstream_unavailable", True, actor=actor)
            final = self.repository.get(current.id)
            if final.cancel_requested and not any(
                item.status == "running" for item in self.repository.items(current.id)
            ):
                return self.repository.transition(
                    current.id, "cancelled", actor=actor, expected={"running"}
                )
            return self.repository.summarize(current.id, actor=actor)
        finally:
            try:
                self.repository.release_lease(current.id, lease)
            except MemoryTransferError:
                # A lost lease must never replace the execution result/exception.
                pass

    async def reconcile(self, job, context, actor: str):
        request = TransferCreateRequest.from_canonical(json.loads(job.selection_json))
        policy = self.authorization.authorize(request, context)
        self._validate_policy(job, policy)
        for item in self.repository.items(job.id):
            if item.status not in {"running", "failed_retryable"}:
                continue
            await self._reconcile_item(item, actor=actor)
        current = self.repository.get(job.id)
        if current.status == "running":
            return self.repository.summarize(job.id, actor=actor)
        return current

    def _validate_policy(self, job, policy: dict) -> None:
        snapshot = json.loads(job.policy_snapshot_json or "{}")
        stale = (
            int(snapshot.get("policy_revision", 0)) != int(policy["policy_revision"])
            or int(snapshot.get("context_revision", -1)) != int(policy["context_revision"])
            or int(job.source_space_revision) != int(policy["source_revision"])
            or int(job.target_space_revision) != int(policy["target_revision"])
            or snapshot.get("source_domain") != policy["source_domain"]
            or snapshot.get("target_domain") != policy["target_domain"]
            or str(job.policy_snapshot_hash or snapshot.get("policy_snapshot_hash", "")) != str(policy["policy_snapshot_hash"])
        )
        if stale:
            if job.status in {"approved", "retry_wait", "running"}:
                self.repository.transition(
                    job.id,
                    "stale_policy",
                    error_code="stale_policy",
                    expected={job.status},
                )
            raise MemoryTransferError("stale_policy")

    def _validate_item_plan(self, item) -> None:
        cycle, maximum_depth, _ancestors = self.repository.directed_lineage_state(
            root_object_type=item.root_object_type or item.object_type,
            root_object_id=item.root_object_id or item.source_object_id,
            source_partition_id=item.source_partition_id,
            target_partition_id=item.target_partition_id,
        )
        if cycle:
            raise MemoryTransferError("cycle_detected")
        if maximum_depth >= LINEAGE_MAX_DEPTH:
            raise MemoryTransferError("lineage_depth_exceeded")
        if self.planner_hash(item) != item.ancestry_hash:
            raise MemoryTransferError("stale_plan")

    def planner_hash(self, item) -> str:
        cycle, maximum_depth, ancestor_partitions = self.repository.directed_lineage_state(
            root_object_type=item.root_object_type or item.object_type,
            root_object_id=item.root_object_id or item.source_object_id,
            source_partition_id=item.source_partition_id,
            target_partition_id=item.target_partition_id,
        )
        if cycle:
            raise MemoryTransferError("cycle_detected")
        return canonical_hash({
            "object_type": item.root_object_type or item.object_type,
            "object_id": item.root_object_id or item.source_object_id,
            "source_partition_id": item.source_partition_id,
            "target_partition_id": item.target_partition_id,
            "ancestor_partitions": ancestor_partitions,
            "depth": maximum_depth,
        })

    async def _mutate(self, item, policy: dict) -> dict:
        arguments = {
            "operation_key": item.operation_key,
            "object_type": item.object_type,
            "source_space_id": item.source_space_id,
            "source_partition_id": item.source_partition_id,
            "target_space_id": item.target_space_id,
            "target_partition_id": item.target_partition_id,
            "source_security_domain": policy["source_domain"],
            "target_security_domain": policy["target_domain"],
        }
        if item.mode == "link":
            arguments["object_id"] = item.source_object_id
            return await self.memory_service.link_object_to_scope(**arguments)
        arguments.update(
            {
                "mode": item.mode,
                "source_object_id": item.source_object_id,
                "expected_fingerprint": item.content_fingerprint,
                "expected_source_version": item.source_version,
                "root_object_type": item.root_object_type,
                "root_object_id": item.root_object_id,
            }
        )
        return await self.memory_service.copy_object_to_scope(**arguments)

    def _apply_authority_result(self, item, result: dict, *, actor: str) -> None:
        self._validate_result(item, result)
        object_id = str(result.get("target_object_id") or item.source_object_id)
        try:
            self.projection.register_object_partition(
                object_type=item.object_type,
                object_id=object_id,
                partition_id=item.target_partition_id,
                origin_space_id=item.source_space_id,
                origin_partition_id=item.source_partition_id,
                transfer_job_id=item.job_id,
            )
        except Exception as exc:
            raise MemoryTransferError("local_database_busy", retryable=True) from exc
        self.repository.finish_item(item.id, result, actor=actor)

    async def _reconcile_item(self, item, *, actor: str) -> str:
        try:
            result = await self.memory_service.get_transfer_operation(item.operation_key)
        except (TimeoutError, ConnectionError):
            self.repository.fail_item(item.id, "unknown_result_needs_reconcile", True, actor=actor)
            return "unknown"
        status = str(result.get("status") or "unknown")
        if status in {"applied", "already_applied"}:
            try:
                self._apply_authority_result(item, result, actor=actor)
            except MemoryTransferError as exc:
                self.repository.fail_item(item.id, exc.code, True, actor=actor)
            return "applied"
        if status == "not_applied":
            if item.status == "running":
                self.repository.fail_item(item.id, "upstream_unavailable", True, actor=actor)
            return "not_applied"
        self.repository.fail_item(item.id, "unknown_result_needs_reconcile", True, actor=actor)
        return "unknown"

    @staticmethod
    def _validate_result(item, result: dict) -> None:
        if not isinstance(result, dict):
            raise MemoryTransferError("upstream_invalid_response")
        forbidden = {"content", "query", "prompt", "token", "password", "secret"}
        allowed = {
            "status", "error_code", "operation_key", "mode", "object_type", "object_id",
            "source_object_id", "target_object_id", "source_space_id", "source_partition_id",
            "target_space_id", "target_partition_id", "source_security_domain",
            "target_security_domain", "source_fingerprint", "target_fingerprint",
            "content_fingerprint", "root_object_type", "root_object_id", "source_version",
            "success", "memory_space_id", "partition_id", "security_domain",
        }

        def validate(value):
            if isinstance(value, dict):
                if any(str(key).lower() in forbidden or str(key) not in allowed for key in value):
                    raise MemoryTransferError("upstream_invalid_response")
                for child in value.values():
                    validate(child)
            elif isinstance(value, (list, tuple, set)):
                raise MemoryTransferError("upstream_invalid_response")
            elif value is not None and not isinstance(value, (str, int, float, bool)):
                raise MemoryTransferError("upstream_invalid_response")
            elif isinstance(value, str) and len(value) > 256:
                raise MemoryTransferError("upstream_invalid_response")

        validate(result)
        status = result.get("status")
        if status not in {"applied", "already_applied"}:
            code = str(result.get("error_code") or "unknown_result_needs_reconcile")
            raise MemoryTransferError(code, retryable=status in {"unknown", "pending"})
        expected = {
            "operation_key": item.operation_key,
            "object_type": item.object_type,
            "target_space_id": item.target_space_id,
            "target_partition_id": item.target_partition_id,
        }
        if any(str(result.get(key, "")) != str(value) for key, value in expected.items()):
            raise MemoryTransferError("scope_mismatch")
        source_id = str(result.get("source_object_id") or result.get("object_id") or "")
        if source_id != item.source_object_id:
            raise MemoryTransferError("object_identity_conflict")
        target_id = str(result.get("target_object_id") or "")
        if item.mode == "link" and target_id != item.source_object_id:
            raise MemoryTransferError("object_identity_conflict")
        if item.mode in {"copy", "promote"} and (not target_id or target_id == item.source_object_id):
            raise MemoryTransferError("object_identity_conflict")
        target_fingerprint = str(result.get("target_fingerprint") or result.get("content_fingerprint") or "")
        if item.content_fingerprint and target_fingerprint and target_fingerprint != item.content_fingerprint:
            raise MemoryTransferError("content_conflict")


def deterministic_retry_jitter(job_id: str, item_id: str = "") -> int:
    return int(sha256(f"{job_id}:{item_id}".encode("utf-8")).hexdigest()[:4], 16) % 3
