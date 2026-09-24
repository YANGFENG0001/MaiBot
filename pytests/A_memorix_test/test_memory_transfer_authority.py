import sqlite3

import pytest

from src.A_memorix.core.runtime.services.memory_transfer_service import (
    MemoryTransferAuthorityError,
    MemoryTransferAuthorityService,
)


class Store:
    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE paragraphs(hash TEXT PRIMARY KEY, content TEXT NOT NULL, created_at REAL, updated_at REAL);
            CREATE TABLE entities(hash TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, created_at REAL);
            CREATE TABLE relations(hash TEXT PRIMARY KEY, subject TEXT, predicate TEXT, object TEXT, created_at REAL);
            CREATE TABLE memory_scope_members(
              object_type TEXT NOT NULL, object_id TEXT NOT NULL, memory_space_id TEXT NOT NULL,
              partition_id TEXT NOT NULL, security_domain TEXT NOT NULL, source_session_id TEXT,
              created_at REAL NOT NULL, PRIMARY KEY(object_type, object_id, partition_id));
            """
        )
        self.conn.execute("INSERT INTO paragraphs VALUES ('p1','secret body',1,1)")
        self.conn.execute("INSERT INTO entities VALUES ('e1','Alice',1)")
        self.conn.execute("INSERT INTO relations VALUES ('r1','Alice','likes','Tea',1)")
        for kind, oid in (("paragraph", "p1"), ("entity", "e1"), ("relation", "r1")):
            self.register_scope_member(object_type=kind, object_id=oid, memory_space_id="s1", partition_id="p-source", security_domain="normal")

    def _resolve_conn(self):
        return self.conn

    def _ensure_memory_scope_tables(self, _cursor):
        return None

    def register_scope_member(self, **kwargs):
        self.conn.execute(
            "INSERT OR IGNORE INTO memory_scope_members VALUES (?,?,?,?,?,?,?)",
            (kwargs["object_type"], kwargs["object_id"], kwargs["memory_space_id"], kwargs["partition_id"], kwargs["security_domain"], None, 1),
        )
        self.conn.commit()


@pytest.fixture
def service():
    return MemoryTransferAuthorityService(Store())


def link_request(**patch):
    data = dict(operation_key="op-link", object_type="paragraph", object_id="p1", source_space_id="s1", source_partition_id="p-source", target_space_id="s2", target_partition_id="p-target", security_domain="normal")
    data.update(patch)
    return data


def copy_request(**patch):
    data = dict(operation_key="op-copy", mode="copy", object_type="paragraph", source_object_id="p1", source_space_id="s1", source_partition_id="p-source", target_space_id="s2", target_partition_id="p-target", source_security_domain="normal", target_security_domain="normal")
    data.update(patch)
    return data


def test_list_and_inspect_are_scoped_and_body_free(service):
    result = service.list_scoped_objects({"memory_space_id": "s1", "partition_ids": ["p-source"], "security_domain": "normal", "object_types": ["paragraph"], "limit": 10})
    assert result["count"] == 1
    assert all("content" not in item and "snapshot" not in item for item in result["items"])
    assert service.inspect_object({"object_type": "paragraph", "object_id": "p1", "memory_space_id": "s1", "partition_id": "p-source", "security_domain": "normal"})["object_id"] == "p1"


def test_link_is_idempotent_and_keeps_identity(service):
    first = service.link_object_to_scope(link_request())
    second = service.link_object_to_scope(link_request())
    assert first["target_object_id"] == "p1"
    assert second["status"] == "already_applied"
    assert service._conn.execute("SELECT COUNT(*) FROM memory_scope_members WHERE partition_id='p-target'").fetchone()[0] == 1


def test_copy_has_independent_identity_and_reconcile(service):
    first = service.copy_object_to_scope(copy_request())
    second = service.copy_object_to_scope(copy_request())
    assert first["target_object_id"] != "p1"
    assert second["status"] == "already_applied"
    service._conn.execute("DELETE FROM paragraphs WHERE hash='p1'")
    assert service._conn.execute("SELECT COUNT(*) FROM memory_transfer_objects WHERE target_object_id=?", (first["target_object_id"],)).fetchone()[0] == 1
    assert service.reconcile_transfer_operation("op-copy")["status"] == "applied"
    assert service.get_transfer_operation("missing")["status"] == "not_applied"


def test_operation_key_payload_conflict(service):
    service.link_object_to_scope(link_request())
    with pytest.raises(MemoryTransferAuthorityError, match="operation_key_payload_conflict"):
        service.link_object_to_scope(link_request(target_partition_id="other"))


def test_cross_domain_link_and_kami_downgrade_are_rejected(service):
    with pytest.raises(MemoryTransferAuthorityError, match="cross_domain_transfer_denied"):
        service.link_object_to_scope(link_request(target_security_domain="kami"))
    service.metadata_store.register_scope_member(object_type="paragraph", object_id="p1", memory_space_id="ks", partition_id="kp", security_domain="kami")
    with pytest.raises(MemoryTransferAuthorityError, match="kami_source_forbidden"):
        service.copy_object_to_scope(copy_request(source_space_id="ks", source_partition_id="kp", source_security_domain="kami", target_security_domain="normal"))


@pytest.mark.parametrize("object_type,object_id", [("paragraph", "p1"), ("entity", "e1"), ("relation", "r1")])
def test_supported_adapters(service, object_type, object_id):
    result = service.copy_object_to_scope(copy_request(operation_key=f"copy-{object_type}", object_type=object_type, source_object_id=object_id))
    assert result["object_type"] == object_type
    assert result["target_object_id"]


def test_unsupported_adapter_and_missing_scope(service):
    with pytest.raises(MemoryTransferAuthorityError, match="unsupported_object_type"):
        service.inspect_object({"object_type": "file", "object_id": "x", "memory_space_id": "s1", "partition_id": "p-source"})
    with pytest.raises(MemoryTransferAuthorityError, match="source_object_not_found"):
        service.inspect_object({"object_type": "paragraph", "object_id": "p1", "memory_space_id": "s1", "partition_id": "wrong"})
