"""Domain DTOs and stable errors for MemoryTransfer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from typing import Any, Literal

import json
import re

SUPPORTED_OBJECT_TYPES = frozenset({"paragraph", "entity", "relation"})
MODES = frozenset({"link", "copy", "promote"})
LINEAGE_MAX_DEPTH = 32
TRANSFER_MAX_ITEMS_PER_JOB = 10_000
RETRYABLE_ERRORS = frozenset(
    {
        "upstream_timeout",
        "upstream_unavailable",
        "upstream_connection_reset",
        "upstream_5xx",
        "local_database_busy",
        "lease_lost",
        "unknown_result_needs_reconcile",
    }
)
SENSITIVE_AUDIT_KEYS = re.compile(r"content|body|query|prompt|token|password|secret|api[_-]?key|filters", re.I)


class MemoryTransferError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


_IDENTIFIER_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_FILTER_KEYS = frozenset({"object_type", "ids", "fingerprint", "updated_before", "updated_after", "limit"})


def _bounded_string(
    value: Any,
    *,
    maximum: int,
    empty_code: str = "invalid_request",
    reject_whitespace: bool = True,
) -> str:
    if not isinstance(value, str):
        raise MemoryTransferError("invalid_request")
    cleaned = value.strip()
    if not cleaned:
        raise MemoryTransferError(empty_code)
    if len(cleaned) > maximum or any(ord(char) < 32 or ord(char) == 127 for char in cleaned):
        raise MemoryTransferError("invalid_request")
    if reject_whitespace and any(char.isspace() for char in cleaned):
        raise MemoryTransferError("invalid_request")
    return cleaned


def _canonical_string_set(
    value: Any,
    *,
    maximum: int,
    member_maximum: int,
    allow_scalar: bool = False,
    reject_unsafe: bool = False,
) -> list[str]:
    if allow_scalar and isinstance(value, str):
        members = (value,)
    elif isinstance(value, (list, tuple)):
        members = value
    else:
        raise MemoryTransferError("invalid_request")
    cleaned: list[str] = []
    for member in members:
        item = _bounded_string(member, maximum=member_maximum)
        if reject_unsafe and (
            _IDENTIFIER_SCHEME.match(item)
            or any(token in item for token in ("<", ">", "'", '"', ";", "\\"))
        ):
            raise MemoryTransferError("invalid_request")
        cleaned.append(item)
    result = sorted(set(cleaned))
    if len(result) > maximum:
        raise MemoryTransferError("invalid_request")
    return result


def _canonical_iso8601(value: Any) -> str:
    raw = _bounded_string(value, maximum=64, reject_whitespace=False)
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise MemoryTransferError("invalid_request") from exc
    return parsed.isoformat()


def _canonical_filters(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - _FILTER_KEYS:
        raise MemoryTransferError("invalid_request")
    result: dict[str, Any] = {}
    if "object_type" in value:
        object_types = _canonical_string_set(
            value["object_type"], maximum=len(SUPPORTED_OBJECT_TYPES), member_maximum=32, allow_scalar=True
        )
        normalized = sorted({item.lower() for item in object_types})
        if not normalized or any(item not in SUPPORTED_OBJECT_TYPES for item in normalized):
            raise MemoryTransferError("invalid_request")
        result["object_type"] = normalized
    if "ids" in value:
        result["ids"] = _canonical_string_set(
            value["ids"], maximum=TRANSFER_MAX_ITEMS_PER_JOB, member_maximum=255,
            allow_scalar=True, reject_unsafe=True,
        )
    if "fingerprint" in value:
        result["fingerprint"] = _canonical_string_set(
            value["fingerprint"], maximum=TRANSFER_MAX_ITEMS_PER_JOB, member_maximum=128,
            allow_scalar=True, reject_unsafe=True,
        )
    for key in ("updated_before", "updated_after"):
        if key in value:
            result[key] = _canonical_iso8601(value[key])
    if "limit" in value:
        limit = value["limit"]
        if type(limit) is not int or not 1 <= limit <= TRANSFER_MAX_ITEMS_PER_JOB:
            raise MemoryTransferError("invalid_request")
        result["limit"] = limit
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > 65_536:
        raise MemoryTransferError("invalid_request")
    return result


@dataclass(frozen=True, slots=True)
class TransferCreateRequest:
    mode: Literal["link", "copy", "promote"]
    source_space_id: str
    source_partition_ids: tuple[str, ...]
    target_space_id: str
    target_partition_id: str
    object_types: tuple[str, ...]
    object_ids: tuple[str, ...] = ()
    filters: dict[str, Any] = field(default_factory=dict)
    approval_policy: Literal["manual", "auto_safe"] = "manual"
    conflict_policy: Literal["skip", "fail"] = "skip"
    idempotency_key: str = ""
    client_nonce: str = ""

    def canonical(self) -> dict[str, Any]:
        if self.mode not in MODES:
            raise MemoryTransferError("invalid_mode")
        source_space_id = _bounded_string(
            self.source_space_id, maximum=64, empty_code="invalid_partition"
        )
        target_space_id = _bounded_string(
            self.target_space_id, maximum=64, empty_code="invalid_partition"
        )
        target_partition_id = _bounded_string(
            self.target_partition_id, maximum=96, empty_code="invalid_partition"
        )
        if not isinstance(self.source_partition_ids, (list, tuple)):
            raise MemoryTransferError("invalid_request")
        source_partitions: list[str] = []
        for value in self.source_partition_ids:
            if not isinstance(value, str):
                raise MemoryTransferError("invalid_request")
            if not value.strip():
                continue
            source_partitions.append(_bounded_string(value, maximum=96))
        source_partitions = sorted(set(source_partitions))
        if not source_partitions:
            raise MemoryTransferError("invalid_partition")
        if len(source_partitions) > 128:
            raise MemoryTransferError("invalid_request")
        if target_partition_id in source_partitions:
            raise MemoryTransferError("same_source_target")
        if source_space_id == target_space_id:
            raise MemoryTransferError("same_source_target_space")
        if not isinstance(self.object_types, (list, tuple)):
            raise MemoryTransferError("invalid_request")
        object_types: list[str] = []
        for value in self.object_types:
            if not isinstance(value, str):
                raise MemoryTransferError("invalid_request")
            if not value.strip():
                continue
            object_types.append(_bounded_string(value, maximum=32).lower())
        object_types = sorted(set(object_types))
        if not object_types or any(value not in SUPPORTED_OBJECT_TYPES for value in object_types):
            raise MemoryTransferError("unsupported_object_type")
        object_ids = _canonical_string_set(
            self.object_ids, maximum=TRANSFER_MAX_ITEMS_PER_JOB, member_maximum=255
        )
        if self.approval_policy not in {"manual", "auto_safe"}:
            raise MemoryTransferError("invalid_request")
        if self.conflict_policy not in {"skip", "fail"}:
            raise MemoryTransferError("invalid_request")
        if self.idempotency_key:
            _bounded_string(self.idempotency_key, maximum=128)
        if self.client_nonce:
            _bounded_string(self.client_nonce, maximum=128)
        filters = _canonical_filters(self.filters)
        return {
            "mode": self.mode,
            "source_space_id": source_space_id,
            "source_partition_ids": source_partitions,
            "target_space_id": target_space_id,
            "target_partition_id": target_partition_id,
            "object_types": object_types,
            "object_ids": object_ids,
            "filters": filters,
            "approval_policy": self.approval_policy,
            "conflict_policy": self.conflict_policy,
        }

    def payload_hash(self) -> str:
        return canonical_hash(self.canonical())

    @classmethod
    def from_canonical(cls, payload: dict[str, Any]) -> "TransferCreateRequest":
        return cls(
            mode=payload["mode"],
            source_space_id=payload["source_space_id"],
            source_partition_ids=tuple(payload["source_partition_ids"]),
            target_space_id=payload["target_space_id"],
            target_partition_id=payload["target_partition_id"],
            object_types=tuple(payload["object_types"]),
            object_ids=tuple(payload.get("object_ids", ())),
            filters=dict(payload.get("filters", {})),
            approval_policy=payload.get("approval_policy", "manual"),
            conflict_policy=payload.get("conflict_policy", "skip"),
        )


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    plan_hash: str
    plan_revision: int
    policy_revision: int
    policy_snapshot_hash: str
    comment: str = ""


def canonical_hash(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def sanitize_comment(value: str) -> str:
    cleaned = "".join(char for char in str(value or "") if char >= " " and char not in "")
    cleaned = cleaned.strip()
    if len(cleaned) > 256 or SENSITIVE_AUDIT_KEYS.search(cleaned):
        raise MemoryTransferError("invalid_request")
    return cleaned


def safe_audit_details(payload: dict[str, Any]) -> str:
    allowed_keys = {
        "mode", "item_count", "policy_revision", "retry_count", "attempt", "status",
        "error_code", "from_status", "to_status", "worker_id", "operation_key",
        "object_type", "object_id", "source_object_id", "target_object_id", "source_space_id",
        "source_partition_id", "target_space_id", "target_partition_id", "relation_type",
        "plan_hash", "plan_revision", "principal_id", "count", "decision", "actor_id", "comment",
    }

    def validate(value: Any, *, key: str = "") -> None:
        if isinstance(value, dict):
            if key not in {""}:
                raise MemoryTransferError("invalid_request")
            if any(str(child_key) not in allowed_keys for child_key in value):
                raise MemoryTransferError("invalid_request")
            for child_key, child_value in value.items():
                validate(child_value, key=str(child_key))
            return
        if isinstance(value, (list, tuple, set)):
            raise MemoryTransferError("invalid_request")
        if not isinstance(value, (str, int, float, bool)) and value is not None:
            raise MemoryTransferError("invalid_request")
        if isinstance(value, str) and len(value) > 256:
            raise MemoryTransferError("invalid_request")

    validate(payload)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
