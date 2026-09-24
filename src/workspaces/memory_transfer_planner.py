"""Non-mutating planner for MemoryTransfer."""

from __future__ import annotations

from uuid import uuid4

import json

from src.common.database.database_model import MemoryTransferItem

from .memory_transfer_models import (
    LINEAGE_MAX_DEPTH,
    TRANSFER_MAX_ITEMS_PER_JOB,
    MemoryTransferError,
    TransferCreateRequest,
    canonical_hash,
)


class MemoryTransferPlanner:
    def __init__(self, repository, authorization, memory_service) -> None:
        self.repository = repository
        self.authorization = authorization
        self.memory_service = memory_service

    async def plan(self, job, context, actor: str):
        request = TransferCreateRequest.from_canonical(json.loads(job.selection_json))
        policy = self.authorization.authorize(request, context)
        self.repository.transition(
            job.id,
            "planning",
            actor=actor,
            expected={"pending", "stale_policy"},
        )
        try:
            source_page = await self.memory_service.list_scoped_objects(
                memory_space_id=request.source_space_id,
                partition_ids=request.source_partition_ids,
                security_domain=policy["source_domain"],
                object_types=request.object_types,
                object_ids=request.object_ids,
                limit=TRANSFER_MAX_ITEMS_PER_JOB,
                filters=policy["effective_filters"],
            )
            if source_page.get("has_more"):
                raise MemoryTransferError("invalid_request")
            target_page = await self.memory_service.list_scoped_objects(
                memory_space_id=request.target_space_id,
                partition_ids=(request.target_partition_id,),
                security_domain=policy["target_domain"],
                object_types=request.object_types,
                object_ids=(),
                limit=TRANSFER_MAX_ITEMS_PER_JOB,
                filters={},
            )
            if target_page.get("has_more"):
                raise MemoryTransferError("invalid_request")
            target_rows = self._validate_rows(
                target_page.get("items", []),
                space_id=request.target_space_id,
                partition_ids={request.target_partition_id},
                security_domain=policy["target_domain"],
                object_types=set(request.object_types),
            )
            source_rows = self._validate_rows(
                source_page.get("items", []),
                space_id=request.source_space_id,
                partition_ids=set(request.source_partition_ids),
                security_domain=policy["source_domain"],
                object_types=set(request.object_types),
            )
            source_rows = self._apply_effective_filters(source_rows, policy["effective_filters"])
            source_rows = sorted(source_rows, key=self._row_key)
            target_rows = sorted(target_rows, key=self._row_key)
            if request.object_ids:
                returned_ids = {row["object_id"] for row in source_rows}
                if not set(request.object_ids).issubset(returned_ids):
                    raise MemoryTransferError("source_object_not_found")
            if len(source_rows) > TRANSFER_MAX_ITEMS_PER_JOB:
                raise MemoryTransferError("invalid_request")
            items = sorted(
                (self._build_item(job.id, request, row, target_rows) for row in source_rows),
                key=self._item_key,
            )
            if request.conflict_policy == "fail" and any(item.status == "conflict" for item in items):
                raise MemoryTransferError("conflict")
            source_snapshot = {
                "space_id": request.source_space_id,
                "partition_ids": sorted(request.source_partition_ids),
                "revision": policy["source_revision"],
                "security_domain": policy["source_domain"],
            }
            target_snapshot = {
                "space_id": request.target_space_id,
                "partition_id": request.target_partition_id,
                "revision": policy["target_revision"],
                "security_domain": policy["target_domain"],
            }
            policy_snapshot = dict(policy["policy_snapshot"])
            policy_snapshot.update({
                "policy_revision": policy["policy_revision"],
                "context_revision": policy["context_revision"],
                "source_revision": policy["source_revision"],
                "target_revision": policy["target_revision"],
                "source_domain": policy["source_domain"],
                "target_domain": policy["target_domain"],
                "approval_required": policy["approval_required"],
                "capabilities": policy["capabilities"],
                "policy_snapshot_hash": policy["policy_snapshot_hash"],
            })
            plan_payload = {
                "request": request.canonical(),
                "source": source_snapshot,
                "target": target_snapshot,
                "policy": policy_snapshot,
                "items": [
                    {
                        "type": item.object_type,
                        "source_id": item.source_object_id,
                        "source_partition": item.source_partition_id,
                        "fingerprint": item.content_fingerprint,
                        "root_type": item.root_object_type,
                        "root_id": item.root_object_id,
                        "ancestry_hash": item.ancestry_hash,
                        "status": item.status,
                        "conflict": item.conflict_code,
                    }
                    for item in items
                ],
            }
            plan_hash = canonical_hash(plan_payload)
            return self.repository.replace_plan(
                job.id,
                items,
                plan_hash=plan_hash,
                source_snapshot=source_snapshot,
                target_snapshot=target_snapshot,
                policy_snapshot=policy_snapshot,
                approval_required=policy["approval_required"],
                policy_revision=policy["policy_revision"],
                policy_snapshot_hash=policy["policy_snapshot_hash"],
                source_revision=policy["source_revision"],
                target_revision=policy["target_revision"],
                actor=actor,
            )
        except Exception:
            try:
                self.repository.transition(job.id, "failed", actor=actor, error_code="planning_failed")
            except MemoryTransferError:
                pass
            raise

    def current_ancestry_hash(self, item: MemoryTransferItem) -> str:
        lineage = self.repository.lineage_for(item.object_type, item.source_object_id)
        return self._ancestry_hash(
            object_type=item.object_type,
            object_id=item.source_object_id,
            source_partition_id=item.source_partition_id,
            target_partition_id=item.target_partition_id,
            lineage=lineage,
            root_object_type=item.root_object_type,
            root_object_id=item.root_object_id,
        )

    def _build_item(
        self,
        job_id: str,
        request: TransferCreateRequest,
        row: dict,
        target_rows: list[dict],
    ) -> MemoryTransferItem:
        if row["partition_id"] == request.target_partition_id:
            raise MemoryTransferError("same_source_target")
        lineage = sorted(
            self.repository.lineage_for(row["object_type"], row["object_id"]),
            key=self._lineage_key,
        )
        ancestry_hash = self._ancestry_hash(
            object_type=row["object_type"],
            object_id=row["object_id"],
            source_partition_id=row["partition_id"],
            target_partition_id=request.target_partition_id,
            lineage=lineage,
            root_object_type=row.get("root_object_type") or row["object_type"],
            root_object_id=row.get("root_object_id") or row["object_id"],
        )
        conflict = self._conflict(request, row, target_rows, lineage)
        item_id = uuid4().hex
        status = "planned"
        if conflict in {"already_applied", "duplicate_same_origin"}:
            status = "skipped"
        elif conflict:
            status = "skipped" if request.conflict_policy == "skip" else "conflict"
        return MemoryTransferItem(
            id=item_id,
            job_id=job_id,
            mode=request.mode,
            object_type=row["object_type"],
            source_object_id=row["object_id"],
            source_space_id=request.source_space_id,
            source_partition_id=row["partition_id"],
            target_space_id=request.target_space_id,
            target_partition_id=request.target_partition_id,
            root_object_type=row.get("root_object_type") or row["object_type"],
            root_object_id=row.get("root_object_id") or row["object_id"],
            content_fingerprint=row.get("content_fingerprint", ""),
            source_version=str(row.get("source_version", "")),
            source_snapshot_hash=canonical_hash({
                "object_type": row["object_type"],
                "object_id": row["object_id"],
                "content_fingerprint": row.get("content_fingerprint", ""),
                "source_version": str(row.get("source_version", "")),
            }),
            operation_key=f"memory-transfer-item:{job_id}:{item_id}",
            status=status,
            conflict_code=conflict,
            ancestry_hash=ancestry_hash,
        )

    def _ancestry_hash(
        self,
        *,
        object_type: str,
        object_id: str,
        source_partition_id: str,
        target_partition_id: str,
        lineage: list,
        root_object_type: str = "",
        root_object_id: str = "",
    ) -> str:
        root_type = root_object_type or object_type
        root_id = root_object_id or object_id
        if lineage and not (root_object_type and root_object_id):
            root_type = lineage[0].root_object_type or object_type
            root_id = lineage[0].root_object_id or object_id
        cycle, maximum_depth, ancestor_partitions = self.repository.directed_lineage_state(
            root_object_type=root_type,
            root_object_id=root_id,
            source_partition_id=source_partition_id,
            target_partition_id=target_partition_id,
        )
        if cycle:
            raise MemoryTransferError("cycle_detected")
        if maximum_depth >= LINEAGE_MAX_DEPTH:
            raise MemoryTransferError("lineage_depth_exceeded")
        return canonical_hash(
            {
                "object_type": root_type,
                "object_id": root_id,
                "source_partition_id": source_partition_id,
                "target_partition_id": target_partition_id,
                "ancestor_partitions": ancestor_partitions,
                "depth": maximum_depth,
            }
        )

    @staticmethod
    def _row_key(row: dict) -> tuple[str, ...]:
        return (
            str(row.get("object_type", "")),
            str(row.get("object_id", "")),
            str(row.get("partition_id", "")),
            str(row.get("content_fingerprint", "")),
            str(row.get("root_object_type", "")),
            str(row.get("root_object_id", "")),
            str(row.get("source_version", "")),
        )

    @staticmethod
    def _lineage_key(edge) -> tuple[str, ...]:
        return (
            str(getattr(edge, "root_object_type", "")),
            str(getattr(edge, "root_object_id", "")),
            str(getattr(edge, "source_object_type", "")),
            str(getattr(edge, "source_object_id", "")),
            str(getattr(edge, "source_partition_id", "")),
            str(getattr(edge, "target_partition_id", "")),
            str(getattr(edge, "relation_type", "")),
            str(getattr(edge, "id", "")),
        )

    @staticmethod
    def _item_key(item: MemoryTransferItem) -> tuple[str, ...]:
        return (
            item.object_type,
            item.source_object_id,
            item.source_partition_id,
            item.target_partition_id,
            item.content_fingerprint,
            item.source_version,
            item.status,
            item.conflict_code,
            item.ancestry_hash,
        )

    @staticmethod
    def _conflict(request, source: dict, targets: list[dict], lineage: list) -> str:
        for edge in lineage:
            if (
                edge.source_object_type == source["object_type"]
                and edge.source_object_id == source["object_id"]
                and edge.target_partition_id == request.target_partition_id
                and edge.relation_type == request.mode
            ):
                return "already_applied"
        for target in targets:
            same_id = target["object_type"] == source["object_type"] and target["object_id"] == source["object_id"]
            same_root = (
                target.get("root_object_type") == (source.get("root_object_type") or source["object_type"])
                and target.get("root_object_id") == (source.get("root_object_id") or source["object_id"])
            )
            if same_id and not same_root:
                return "object_identity_conflict"
            if source.get("content_fingerprint") and target.get("content_fingerprint") == source.get("content_fingerprint"):
                return "duplicate_same_origin" if same_root else "content_conflict"
            if request.mode == "link" and same_id:
                return "already_applied"
        return ""

    @staticmethod
    def _validate_rows(
        rows,
        *,
        space_id: str,
        partition_ids: set[str],
        security_domain: str,
        object_types: set[str],
    ) -> list[dict]:
        result = []
        for raw in rows:
            row = dict(raw)
            required = {"object_type", "object_id", "memory_space_id", "partition_id", "security_domain"}
            if not required.issubset(row):
                raise MemoryTransferError("missing_scope_metadata")
            if (
                row["memory_space_id"] != space_id
                or row["partition_id"] not in partition_ids
                or row["security_domain"] != security_domain
                or row["object_type"] not in object_types
            ):
                continue
            result.append(row)
        return result

    @staticmethod
    def _apply_effective_filters(rows: list[dict], filters: dict) -> list[dict]:
        result = rows
        ids = filters.get("ids")
        if ids:
            allowed = {str(value) for value in (ids if isinstance(ids, list) else [ids])}
            result = [row for row in result if row["object_id"] in allowed]
        fingerprints = filters.get("fingerprint")
        if fingerprints:
            allowed = {str(value) for value in (fingerprints if isinstance(fingerprints, list) else [fingerprints])}
            result = [row for row in result if row.get("content_fingerprint") in allowed]
        types = filters.get("object_type")
        if types:
            allowed = {str(value) for value in (types if isinstance(types, list) else [types])}
            result = [row for row in result if row["object_type"] in allowed]
        limit = filters.get("limit")
        if limit:
            result = result[: int(limit)]
        return result
