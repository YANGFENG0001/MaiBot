"""Authoritative, idempotent A-Memorix memory transfer operations.

The service owns object inspection, scope linking, lifecycle-isolated copies and
operation reconciliation. Responses intentionally exclude memory body content.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from hashlib import sha256
from typing import Any, Dict

import json
import sqlite3

SUPPORTED_OBJECT_TYPES = frozenset({"paragraph", "entity", "relation"})
TRANSFER_STATUSES = frozenset({"applied", "already_applied", "not_applied", "unknown", "rejected"})


class MemoryTransferAuthorityError(ValueError):
    """Stable authority error with an explicit non-sensitive code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ObjectTransferMetadata:
    object_type: str
    object_id: str
    memory_space_id: str
    partition_id: str
    security_domain: str
    content_fingerprint: str
    root_object_type: str
    root_object_id: str
    source_version: str


class MemoryTransferAuthorityService:
    """Executes transfer operations against the A-Memorix metadata authority."""

    def __init__(self, metadata_store: Any) -> None:
        self.metadata_store = metadata_store
        self._conn = metadata_store._resolve_conn()
        self._ensure_tables()

    @staticmethod
    def _canonical_hash(payload: Dict[str, Any]) -> str:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return sha256(encoded).hexdigest()

    def _ensure_tables(self) -> None:
        cursor = self._conn.cursor()
        ensure = getattr(self.metadata_store, "_ensure_memory_scope_tables", None)
        if callable(ensure):
            ensure(cursor)
        cursor.execute(
            """CREATE TABLE IF NOT EXISTS memory_transfer_objects (
                target_object_id TEXT PRIMARY KEY, object_type TEXT NOT NULL,
                source_object_id TEXT NOT NULL, source_space_id TEXT NOT NULL,
                source_partition_id TEXT NOT NULL, target_space_id TEXT NOT NULL,
                target_partition_id TEXT NOT NULL, security_domain TEXT NOT NULL,
                content_fingerprint TEXT NOT NULL DEFAULT '', root_object_type TEXT NOT NULL DEFAULT '',
                root_object_id TEXT NOT NULL DEFAULT '', snapshot_json TEXT NOT NULL DEFAULT '{}',
                source_version TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL
            )"""
        )
        cursor.execute(
            """CREATE TABLE IF NOT EXISTS memory_transfer_operations (
                operation_key TEXT PRIMARY KEY, payload_hash TEXT NOT NULL, mode TEXT NOT NULL,
                status TEXT NOT NULL, result_json TEXT NOT NULL DEFAULT '{}', error_code TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL, updated_at REAL NOT NULL
            )"""
        )
        self._conn.commit()

    @staticmethod
    def _normalized_type(value: Any) -> str:
        object_type = str(value or "").strip().lower()
        if object_type not in SUPPORTED_OBJECT_TYPES:
            raise MemoryTransferAuthorityError("unsupported_object_type")
        return object_type

    @staticmethod
    def _normalized_domain(value: Any) -> str:
        domain = str(value or "normal").strip().lower()
        if domain not in {"normal", "kami"}:
            raise MemoryTransferAuthorityError("security_domain_violation")
        return domain

    @staticmethod
    def _require(value: Any, code: str) -> str:
        token = str(value or "").strip()
        if not token:
            raise MemoryTransferAuthorityError(code)
        return token

    def _scope_row(
        self, *, object_type: str, object_id: str, memory_space_id: str, partition_id: str, security_domain: str
    ) -> Any:
        return self._conn.execute(
            """SELECT object_type, object_id, memory_space_id, partition_id, security_domain
               FROM memory_scope_members
               WHERE object_type=? AND object_id=? AND memory_space_id=? AND partition_id=? AND security_domain=?""",
            (object_type, object_id, memory_space_id, partition_id, security_domain),
        ).fetchone()

    def _object_snapshot(self, object_type: str, object_id: str) -> Dict[str, Any]:
        transfer = self._conn.execute(
            """SELECT snapshot_json, content_fingerprint, root_object_type, root_object_id, source_version
               FROM memory_transfer_objects WHERE target_object_id=? AND object_type=?""",
            (object_id, object_type),
        ).fetchone()
        if transfer is not None:
            return {
                "snapshot": json.loads(str(transfer[0] or "{}")),
                "fingerprint": str(transfer[1] or ""),
                "root_object_type": str(transfer[2] or object_type),
                "root_object_id": str(transfer[3] or object_id),
                "source_version": str(transfer[4] or "1"),
            }
        table = {"paragraph": "paragraphs", "entity": "entities", "relation": "relations"}[object_type]
        row = self._conn.execute(f"SELECT * FROM {table} WHERE hash=?", (object_id,)).fetchone()
        if row is None:
            raise MemoryTransferAuthorityError("source_object_not_found")
        columns = [str(item[0]) for item in self._conn.execute(f"SELECT name FROM pragma_table_info('{table}')")]
        snapshot = {columns[index]: row[index] for index in range(min(len(columns), len(row)))}
        fingerprint = sha256(
            json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        version_value = snapshot.get("updated_at") or snapshot.get("created_at") or 1
        return {
            "snapshot": snapshot,
            "fingerprint": fingerprint,
            "root_object_type": object_type,
            "root_object_id": object_id,
            "source_version": str(version_value),
        }

    def inspect_object(self, request: Dict[str, Any]) -> Dict[str, Any]:
        object_type = self._normalized_type(request.get("object_type"))
        object_id = self._require(request.get("object_id"), "source_object_not_found")
        space_id = self._require(request.get("memory_space_id"), "missing_scope_metadata")
        partition_id = self._require(request.get("partition_id"), "missing_scope_metadata")
        domain = self._normalized_domain(request.get("security_domain"))
        if self._scope_row(
            object_type=object_type,
            object_id=object_id,
            memory_space_id=space_id,
            partition_id=partition_id,
            security_domain=domain,
        ) is None:
            raise MemoryTransferAuthorityError("source_object_not_found")
        snapshot = self._object_snapshot(object_type, object_id)
        return asdict(
            ObjectTransferMetadata(
                object_type=object_type,
                object_id=object_id,
                memory_space_id=space_id,
                partition_id=partition_id,
                security_domain=domain,
                content_fingerprint=snapshot["fingerprint"],
                root_object_type=snapshot["root_object_type"],
                root_object_id=snapshot["root_object_id"],
                source_version=snapshot["source_version"],
            )
        )

    def list_scoped_objects(self, request: Dict[str, Any]) -> Dict[str, Any]:
        space_id = self._require(request.get("memory_space_id"), "missing_scope_metadata")
        partitions = tuple(dict.fromkeys(str(x).strip() for x in request.get("partition_ids", ()) if str(x).strip()))
        domain = self._normalized_domain(request.get("security_domain"))
        object_types = tuple(dict.fromkeys(self._normalized_type(x) for x in request.get("object_types", SUPPORTED_OBJECT_TYPES)))
        limit = int(request.get("limit", 100) or 100)
        if not partitions or limit < 1 or limit > 10000:
            raise MemoryTransferAuthorityError("invalid_request")
        requested_ids = {str(x).strip() for x in request.get("object_ids", ()) if str(x).strip()}
        filters = request.get("filters") or {}
        if not isinstance(filters, dict):
            raise MemoryTransferAuthorityError("invalid_request")
        allowed_filters = {"object_type", "ids", "fingerprint", "updated_before", "updated_after", "limit"}
        if set(filters) - allowed_filters:
            raise MemoryTransferAuthorityError("invalid_request")
        filter_ids = filters.get("ids")
        if filter_ids:
            values = filter_ids if isinstance(filter_ids, (list, tuple, set)) else (filter_ids,)
            filter_id_set = {str(value).strip() for value in values if str(value).strip()}
            requested_ids = requested_ids & filter_id_set if requested_ids else filter_id_set
        filter_limit = filters.get("limit")
        if filter_limit is not None:
            filter_limit = int(filter_limit)
            if filter_limit < 1 or filter_limit > 10000:
                raise MemoryTransferAuthorityError("invalid_request")
            limit = min(limit, filter_limit)
        placeholders_p = ",".join("?" for _ in partitions)
        placeholders_t = ",".join("?" for _ in object_types)
        params: list[Any] = [space_id, domain, *partitions, *object_types, limit + 1]
        rows = self._conn.execute(
            f"""SELECT object_type, object_id, memory_space_id, partition_id, security_domain
                FROM memory_scope_members
                WHERE memory_space_id=? AND security_domain=?
                  AND partition_id IN ({placeholders_p}) AND object_type IN ({placeholders_t})
                ORDER BY object_type, object_id LIMIT ?""",
            params,
        ).fetchall()
        items = []
        for row in rows:
            if requested_ids and str(row[1]) not in requested_ids:
                continue
            metadata = self.inspect_object(
                {
                    "object_type": row[0],
                    "object_id": row[1],
                    "memory_space_id": row[2],
                    "partition_id": row[3],
                    "security_domain": row[4],
                }
            )
            if not self._matches_filters(metadata, filters):
                continue
            items.append(metadata)
            if len(items) > limit:
                break
        return {"items": items[:limit], "has_more": len(items) > limit, "count": min(len(items), limit)}

    @staticmethod
    def _matches_filters(metadata: Dict[str, Any], filters: Dict[str, Any]) -> bool:
        for key, field in (("object_type", "object_type"), ("fingerprint", "content_fingerprint")):
            expected = filters.get(key)
            if expected:
                values = expected if isinstance(expected, (list, tuple, set)) else (expected,)
                if str(metadata.get(field, "")) not in {str(value) for value in values}:
                    return False
        source_version = str(metadata.get("source_version", ""))
        if filters.get("updated_after") and source_version < str(filters["updated_after"]):
            return False
        if filters.get("updated_before") and source_version > str(filters["updated_before"]):
            return False
        return True

    def _stored_operation(self, operation_key: str) -> Dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT payload_hash, mode, status, result_json, error_code FROM memory_transfer_operations WHERE operation_key=?",
            (operation_key,),
        ).fetchone()
        if row is None:
            return None
        result = json.loads(str(row[3] or "{}"))
        return {**result, "operation_key": operation_key, "mode": str(row[1]), "status": str(row[2]), "error_code": str(row[4] or ""), "payload_hash": str(row[0])}

    def _begin_operation(self, *, operation_key: str, mode: str, payload_hash: str) -> Dict[str, Any] | None:
        stored = self._stored_operation(operation_key)
        if stored is not None:
            if stored["payload_hash"] != payload_hash or stored["mode"] != mode:
                raise MemoryTransferAuthorityError("operation_key_payload_conflict")
            stored.pop("payload_hash", None)
            if stored.get("status") == "applied":
                stored["status"] = "already_applied"
            return stored
        now = datetime.now().timestamp()
        try:
            self._conn.execute(
                """INSERT INTO memory_transfer_operations
                   (operation_key,payload_hash,mode,status,result_json,error_code,created_at,updated_at)
                   VALUES (?,?,?,'pending','{}','',?,?)""",
                (operation_key, payload_hash, mode, now, now),
            )
            self._conn.commit()
        except sqlite3.IntegrityError:
            self._conn.rollback()
            stored = self._stored_operation(operation_key)
            if stored is None:
                raise
            if stored["payload_hash"] != payload_hash or stored["mode"] != mode:
                raise MemoryTransferAuthorityError("operation_key_payload_conflict")
            stored.pop("payload_hash", None)
            if stored.get("status") == "applied":
                stored["status"] = "already_applied"
            return stored
        return None

    def _finish_operation(self, operation_key: str, result: Dict[str, Any]) -> Dict[str, Any]:
        clean = {key: value for key, value in result.items() if key not in {"content", "query", "prompt", "token", "password"}}
        self._conn.execute(
            """UPDATE memory_transfer_operations SET status=?, result_json=?, error_code=?, updated_at=?
               WHERE operation_key=?""",
            (clean["status"], json.dumps(clean, ensure_ascii=False, sort_keys=True), clean.get("error_code", ""), datetime.now().timestamp(), operation_key),
        )
        self._conn.commit()
        return clean

    def link_object_to_scope(self, request: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(request)
        operation_key = self._require(payload.get("operation_key"), "invalid_request")
        payload["object_type"] = self._normalized_type(payload.get("object_type"))
        source_domain = self._normalized_domain(payload.get("source_security_domain", payload.get("security_domain")))
        target_domain = self._normalized_domain(payload.get("target_security_domain", source_domain))
        if source_domain != target_domain:
            raise MemoryTransferAuthorityError("cross_domain_transfer_denied")
        payload_hash = self._canonical_hash(payload)
        existing = self._begin_operation(operation_key=operation_key, mode="link", payload_hash=payload_hash)
        if existing is not None:
            return existing
        metadata = self.inspect_object(
            {
                "object_type": payload["object_type"],
                "object_id": payload.get("object_id"),
                "memory_space_id": payload.get("source_space_id"),
                "partition_id": payload.get("source_partition_id"),
                "security_domain": source_domain,
            }
        )
        target_space = self._require(payload.get("target_space_id"), "target_not_found")
        target_partition = self._require(payload.get("target_partition_id"), "target_not_found")
        self.metadata_store.register_scope_member(
            object_type=metadata["object_type"], object_id=metadata["object_id"], memory_space_id=target_space,
            partition_id=target_partition, security_domain=target_domain, connection=self._conn, commit=False
        )
        return self._finish_operation(operation_key, {**metadata, "success": True, "operation_key": operation_key, "mode": "link", "status": "applied", "source_object_id": metadata["object_id"], "target_object_id": metadata["object_id"], "target_space_id": target_space, "target_partition_id": target_partition, "target_security_domain": target_domain})

    def copy_object_to_scope(self, request: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(request)
        operation_key = self._require(payload.get("operation_key"), "invalid_request")
        payload["object_type"] = self._normalized_type(payload.get("object_type"))
        source_domain = self._normalized_domain(payload.get("source_security_domain", payload.get("security_domain")))
        target_domain = self._normalized_domain(payload.get("target_security_domain", source_domain))
        if source_domain == "kami" and target_domain != "kami":
            raise MemoryTransferAuthorityError("kami_source_forbidden")
        payload_hash = self._canonical_hash(payload)
        existing = self._begin_operation(operation_key=operation_key, mode=str(payload.get("mode") or "copy"), payload_hash=payload_hash)
        if existing is not None:
            return existing
        metadata = self.inspect_object({"object_type": payload["object_type"], "object_id": payload.get("source_object_id"), "memory_space_id": payload.get("source_space_id"), "partition_id": payload.get("source_partition_id"), "security_domain": source_domain})
        target_space = self._require(payload.get("target_space_id"), "target_not_found")
        target_partition = self._require(payload.get("target_partition_id"), "target_not_found")
        snapshot = self._object_snapshot(metadata["object_type"], metadata["object_id"])
        expected_fingerprint = str(payload.get("expected_fingerprint") or "").strip()
        expected_version = str(payload.get("expected_source_version") or "").strip()
        if expected_fingerprint and expected_fingerprint != metadata["content_fingerprint"]:
            raise MemoryTransferAuthorityError("source_precondition_failed")
        if expected_version and expected_version != metadata["source_version"]:
            raise MemoryTransferAuthorityError("source_precondition_failed")
        target_object_id = sha256(f"a-memorix-copy:{operation_key}:{metadata['object_type']}:{metadata['object_id']}".encode()).hexdigest()
        root_type = str(payload.get("root_object_type") or metadata["root_object_type"])
        root_id = str(payload.get("root_object_id") or metadata["root_object_id"])
        self._conn.execute(
            """INSERT OR IGNORE INTO memory_transfer_objects
               (target_object_id,object_type,source_object_id,source_space_id,source_partition_id,target_space_id,
                target_partition_id,security_domain,content_fingerprint,root_object_type,root_object_id,snapshot_json,
                source_version,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (target_object_id, metadata["object_type"], metadata["object_id"], metadata["memory_space_id"], metadata["partition_id"], target_space, target_partition, target_domain, metadata["content_fingerprint"], root_type, root_id, json.dumps(snapshot["snapshot"], ensure_ascii=False, sort_keys=True, default=str), metadata["source_version"], datetime.now().timestamp()),
        )
        self._materialize_copy(metadata["object_type"], target_object_id, snapshot["snapshot"])
        self.metadata_store.register_scope_member(
            object_type=metadata["object_type"], object_id=target_object_id, memory_space_id=target_space,
            partition_id=target_partition, security_domain=target_domain, connection=self._conn, commit=False
        )
        self._conn.commit()
        return self._finish_operation(operation_key, {"success": True, "operation_key": operation_key, "mode": str(payload.get("mode") or "copy"), "status": "applied", "object_type": metadata["object_type"], "source_object_id": metadata["object_id"], "target_object_id": target_object_id, "source_space_id": metadata["memory_space_id"], "source_partition_id": metadata["partition_id"], "target_space_id": target_space, "target_partition_id": target_partition, "source_security_domain": source_domain, "target_security_domain": target_domain, "source_fingerprint": metadata["content_fingerprint"], "target_fingerprint": metadata["content_fingerprint"], "root_object_type": root_type, "root_object_id": root_id, "source_version": metadata["source_version"]})

    def _materialize_copy(self, object_type: str, target_object_id: str, snapshot: Dict[str, Any]) -> None:
        table = {"paragraph": "paragraphs", "entity": "entities", "relation": "relations"}[object_type]
        columns = [str(item[0]) for item in self._conn.execute(f"SELECT name FROM pragma_table_info('{table}')")]
        values = {key: value for key, value in snapshot.items() if key in columns}
        values["hash"] = target_object_id
        if object_type == "paragraph":
            values["is_deleted"] = 0
            values["deleted_at"] = None
        if object_type == "relation" and "is_inactive" in columns:
            values["is_inactive"] = 0
        insert_columns = [column for column in columns if column in values]
        if not insert_columns:
            raise MemoryTransferAuthorityError("copy_projection_failed")
        placeholders = ",".join("?" for _ in insert_columns)
        self._conn.execute(
            f"INSERT OR IGNORE INTO {table} ({','.join(insert_columns)}) VALUES ({placeholders})",
            tuple(values[column] for column in insert_columns),
        )

    def get_transfer_operation(self, operation_key: str) -> Dict[str, Any]:
        key = self._require(operation_key, "invalid_request")
        stored = self._stored_operation(key)
        if stored is None:
            return {"success": True, "operation_key": key, "status": "not_applied", "error_code": ""}
        stored.pop("payload_hash", None)
        return stored

    def reconcile_transfer_operation(self, operation_key: str) -> Dict[str, Any]:
        result = self.get_transfer_operation(operation_key)
        if result["status"] == "pending":
            return {**result, "status": "unknown", "error_code": "unknown_result_needs_reconcile"}
        return result
