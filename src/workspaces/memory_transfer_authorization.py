"""Authorization gates for memory transfers."""

from __future__ import annotations

from typing import Any

import json

from sqlmodel import select

from src.common.database.database import get_db_session
from src.common.database.database_model import (
    MemoryPartition,
    MemoryPermissionGroupCapability,
    MemorySpace,
    MemorySpaceACL,
    MemoryPermissionGroup,
    BotProfile,
    Workspace,
)

from .memory_transfer_models import MemoryTransferError, TransferCreateRequest, canonical_hash

MODE_CAPABILITY = {
    "link": "memory.transfer.link",
    "copy": "memory.transfer.copy",
    "promote": "memory.transfer.publish",
}


class MemoryTransferAuthorization:
    def __init__(self, session_factory=get_db_session) -> None:
        self.session_factory = session_factory

    def capabilities(self, group_id: str) -> frozenset[str]:
        if not group_id:
            return frozenset()
        with self.session_factory() as session:
            return frozenset(
                item.capability
                for item in session.exec(
                    select(MemoryPermissionGroupCapability).where(
                        MemoryPermissionGroupCapability.permission_group_id == group_id,
                        MemoryPermissionGroupCapability.enabled == True,  # noqa: E712
                    )
                ).all()
            )

    def require_capability(self, context, capability: str) -> frozenset[str]:
        capabilities = self.capabilities(context.permission_group_id)
        if capability not in capabilities:
            raise MemoryTransferError("transfer_capability_required")
        return capabilities

    def authorize(self, request: TransferCreateRequest, context) -> dict[str, Any]:
        canonical = request.canonical()
        capabilities = self.capabilities(context.permission_group_id)
        required = {
            "memory.transfer.read_source",
            "memory.transfer.write_target",
            MODE_CAPABILITY[request.mode],
        }
        if request.mode in {"link", "copy"} and "memory.transfer.import" in capabilities:
            required.discard(MODE_CAPABILITY[request.mode])
        if not required.issubset(capabilities):
            raise MemoryTransferError("transfer_capability_required")
        if not set(canonical["source_partition_ids"]).issubset(set(context.readable_partition_ids)):
            raise MemoryTransferError("source_not_readable")

        with self.session_factory() as session:
            source = session.get(MemorySpace, canonical["source_space_id"])
            target = session.get(MemorySpace, canonical["target_space_id"])
            target_partition = session.get(MemoryPartition, canonical["target_partition_id"])
            if source is None or not source.enabled:
                raise MemoryTransferError("source_not_found")
            if target is None or not target.enabled:
                raise MemoryTransferError("target_not_found")
            if (
                target_partition is None
                or not target_partition.enabled
                or target_partition.memory_space_id != target.id
            ):
                raise MemoryTransferError("target_not_found")
            source_partitions = [
                session.get(MemoryPartition, partition_id)
                for partition_id in canonical["source_partition_ids"]
            ]
            if any(
                partition is None
                or not partition.enabled
                or partition.memory_space_id != source.id
                for partition in source_partitions
            ):
                raise MemoryTransferError("source_not_found")
            outbound = session.exec(
                select(MemorySpaceACL).where(
                    MemorySpaceACL.owner_space_id == source.id,
                    MemorySpaceACL.peer_space_id == target.id,
                )
            ).first()
            inbound = session.exec(
                select(MemorySpaceACL).where(
                    MemorySpaceACL.owner_space_id == target.id,
                    MemorySpaceACL.peer_space_id == source.id,
                )
            ).first()
            if outbound is None or not outbound.can_publish_to_peer:
                raise MemoryTransferError("source_not_readable")
            if inbound is None or not inbound.can_import_from_peer:
                raise MemoryTransferError("target_not_writable")
            effective_filters = self._intersect_filters(
                canonical["filters"],
                self._load_filters(outbound.filters_json),
                self._load_filters(inbound.filters_json),
            )
            if effective_filters is None:
                raise MemoryTransferError("source_not_readable")

            if target.id == context.home_memory_space_id:
                if target_partition.id not in context.writable_partition_ids:
                    raise MemoryTransferError("target_not_writable")
            elif "memory.transfer.cross_space_write" not in capabilities:
                raise MemoryTransferError("cross_space_write_denied")

            source_domains = {partition.security_domain for partition in source_partitions if partition is not None}
            if len(source_domains) != 1:
                raise MemoryTransferError("cross_domain_transfer_denied")
            source_domain = next(iter(source_domains))
            target_domain = target_partition.security_domain
            if (
                request.mode in {"link", "copy"}
                and MODE_CAPABILITY[request.mode] not in capabilities
                and "memory.transfer.import" in capabilities
                and (source_domain != "normal" or target_domain != "normal")
            ):
                raise MemoryTransferError("cross_domain_transfer_denied")
            if request.mode == "promote" and source_domain != "normal":
                raise MemoryTransferError("promote_source_not_normal")
            self._authorize_domain(
                request,
                source_domain=source_domain,
                target_domain=target_domain,
                capabilities=capabilities,
            )
            if request.mode == "promote":
                if (
                    target.id != "memory-space-public"
                    or target_partition.partition_type != "shared"
                    or target_domain != "normal"
                ):
                    raise MemoryTransferError("promote_target_invalid")
            source_revision = int(source.policy_revision)
            target_revision = int(target.policy_revision)
            context_revision = int(getattr(context, "policy_revision", 1))
            workspace = session.get(Workspace, context.workspace_id) if getattr(context, "workspace_id", "") else None
            profile = session.get(BotProfile, context.active_bot_profile_id) if getattr(context, "active_bot_profile_id", "") else None
            group = session.get(MemoryPermissionGroup, context.permission_group_id) if getattr(context, "permission_group_id", "") else None
            policy_snapshot = self._policy_snapshot(
                request=request,
                context=context,
                source=source,
                source_partitions=source_partitions,
                target=target,
                target_partition=target_partition,
                outbound=outbound,
                inbound=inbound,
                capabilities=capabilities,
                workspace=workspace,
                profile=profile,
                group=group,
                context_revision=context_revision,
            )
            policy_snapshot_hash = canonical_hash(policy_snapshot)
            # Keep the legacy integer API stable; the canonical hash is authoritative.
            policy_revision = context_revision

        low_risk_auto = (
            request.mode in {"link", "copy"}
            and source_domain == "normal"
            and target_domain == "normal"
            and request.approval_policy == "auto_safe"
            and (
                "memory.transfer.auto_approve_safe" in capabilities
                or "memory.transfer.auto_safe" in capabilities
            )
        )
        approval_required = request.mode == "promote" or not low_risk_auto
        return {
            "capabilities": sorted(capabilities),
            "source_domain": source_domain,
            "target_domain": target_domain,
            "source_revision": source_revision,
            "target_revision": target_revision,
            "policy_revision": policy_revision,
            "context_revision": context_revision,
            "policy_snapshot": policy_snapshot,
            "policy_snapshot_hash": policy_snapshot_hash,
            "approval_required": approval_required,
            "effective_filters": effective_filters,
        }

    @staticmethod
    def _policy_snapshot(
        *, request, context, source, source_partitions, target, target_partition,
        outbound, inbound, capabilities, workspace, profile, group, context_revision,
    ) -> dict[str, Any]:
        def acl_snapshot(acl):
            return {
                "owner_space_id": acl.owner_space_id,
                "peer_space_id": acl.peer_space_id,
                "can_read_from_peer": bool(acl.can_read_from_peer),
                "expose_to_peer": bool(acl.expose_to_peer),
                "can_import_from_peer": bool(acl.can_import_from_peer),
                "can_publish_to_peer": bool(acl.can_publish_to_peer),
                "filters": MemoryTransferAuthorization._load_filters(acl.filters_json),
            }

        canonical_request = request.canonical()
        policy_request = {
            key: canonical_request[key]
            for key in (
                "mode",
                "source_space_id",
                "source_partition_ids",
                "target_space_id",
                "target_partition_id",
                "object_types",
                "approval_policy",
            )
        }
        return {
            "schema": "memory-transfer-policy-v2",
            "request": policy_request,
            "context": {
                "workspace_id": getattr(context, "workspace_id", ""),
                "active_bot_profile_id": getattr(context, "active_bot_profile_id", ""),
                "permission_group_id": getattr(context, "permission_group_id", ""),
                "security_domain": getattr(context, "security_domain", "normal"),
                "access_mode": getattr(context, "access_mode", "normal"),
                "audience_type": getattr(context, "audience_type", ""),
                "home_memory_space_id": getattr(context, "home_memory_space_id", ""),
                "readable_space_ids": sorted(map(str, getattr(context, "readable_space_ids", ()))),
                "readable_partition_ids": sorted(map(str, getattr(context, "readable_partition_ids", ()))),
                "writable_partition_ids": sorted(map(str, getattr(context, "writable_partition_ids", ()))),
                "policy_revision": context_revision,
            },
            "ownership": {
                "workspace_policy_revision": int(getattr(workspace, "policy_revision", 0)),
                "workspace_memory_space_id": getattr(workspace, "memory_space_id", ""),
                "workspace_bot_profile_id": getattr(workspace, "bot_profile_id", ""),
                "profile_policy_revision": int(getattr(profile, "policy_revision", 0)),
                "profile_home_memory_space_id": getattr(profile, "home_memory_space_id", ""),
                "group_policy_revision": int(getattr(group, "policy_revision", 0)),
                "group_memory_scope_mode": getattr(group, "memory_scope_mode", ""),
            },
            "source": {
                "space_id": source.id,
                "revision": int(source.policy_revision),
                "enabled": bool(source.enabled),
                "partitions": [
                    {"id": p.id, "revision": int(p.policy_revision), "security_domain": p.security_domain, "enabled": bool(p.enabled)}
                    for p in sorted(source_partitions, key=lambda value: value.id)
                ],
            },
            "target": {
                "space_id": target.id,
                "revision": int(target.policy_revision),
                "enabled": bool(target.enabled),
                "partition": {
                    "id": target_partition.id,
                    "revision": int(target_partition.policy_revision),
                    "security_domain": target_partition.security_domain,
                    "enabled": bool(target_partition.enabled),
                },
            },
            "acl": {"outbound": acl_snapshot(outbound), "inbound": acl_snapshot(inbound)},
            "capabilities": sorted(map(str, capabilities)),
        }

    @staticmethod
    def _authorize_domain(
        request: TransferCreateRequest,
        *,
        source_domain: str,
        target_domain: str,
        capabilities: frozenset[str],
    ) -> None:
        if source_domain == "kami" and target_domain != "kami":
            raise MemoryTransferError("kami_source_forbidden")
        if request.mode == "link" and source_domain != target_domain:
            raise MemoryTransferError("cross_domain_transfer_denied")
        if source_domain == "normal" and target_domain == "kami":
            cross_domain = {
                "memory.transfer.cross_domain",
                "memory.transfer.cross_domain_copy",
            } & capabilities
            if request.mode != "copy" or not cross_domain:
                raise MemoryTransferError("cross_domain_transfer_denied")
        if source_domain == "kami" and target_domain == "kami":
            required = "memory.transfer.kami_link" if request.mode == "link" else "memory.transfer.kami_copy"
            if required not in capabilities:
                raise MemoryTransferError("transfer_capability_required")

    @staticmethod
    def _load_filters(raw: str) -> dict[str, Any]:
        try:
            value = json.loads(raw or "{}")
        except (TypeError, ValueError) as exc:
            raise MemoryTransferError("invalid_request") from exc
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _intersect_filters(*filters: dict[str, Any]) -> dict[str, Any] | None:
        result: dict[str, Any] = {}
        for key in {key for current in filters for key in current}:
            values = [current[key] for current in filters if key in current]
            if key in {"ids", "object_type", "fingerprint"}:
                sets = []
                for value in values:
                    if isinstance(value, (list, tuple, set)):
                        sets.append({str(item) for item in value})
                    else:
                        sets.append({str(value)})
                intersection = set.intersection(*sets) if sets else set()
                if not intersection:
                    return None
                result[key] = sorted(intersection)
            elif key == "limit":
                result[key] = min(int(value) for value in values)
            elif key == "updated_after":
                result[key] = max(str(value) for value in values)
            elif key == "updated_before":
                result[key] = min(str(value) for value in values)
            elif all(value == values[0] for value in values):
                result[key] = values[0]
            else:
                return None
        if result.get("updated_after") and result.get("updated_before"):
            if result["updated_after"] > result["updated_before"]:
                return None
        return result
