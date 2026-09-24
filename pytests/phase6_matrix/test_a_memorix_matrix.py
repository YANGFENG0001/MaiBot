"""AM01-AM12 A-Memorix authority and regression acceptance cases for Phase 6."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import json
import sqlite3
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from src.A_memorix.core.retrieval import (
    DualPathRetriever,
    DualPathRetrieverConfig,
    SparseBM25Config,
    VectorPoolsConfig,
)
from src.A_memorix.core.runtime.services.memory_transfer_service import (
    MemoryTransferAuthorityError,
    MemoryTransferAuthorityService,
)
from src.A_memorix.core.utils.person_profile_service import PersonProfileService
from src.A_memorix.core.utils.profile_text import parse_profile_sections
from src.A_memorix.host_service import AMemorixHostService
from src.services.memory_service import MemoryService

pytestmark = pytest.mark.phase6_matrix

Case = Callable[[], Awaitable[None]]


class AuthorityStore:
    """Small real SQLite authority store; no mocked authority decisions."""

    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.vector_write_count = 0
        self.conn.executescript(
            """
            CREATE TABLE paragraphs(
              hash TEXT PRIMARY KEY, content TEXT NOT NULL, created_at REAL,
              updated_at REAL, is_deleted INTEGER NOT NULL DEFAULT 0, deleted_at REAL
            );
            CREATE TABLE entities(hash TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, created_at REAL);
            CREATE TABLE relations(
              hash TEXT PRIMARY KEY, subject TEXT, predicate TEXT, object TEXT,
              created_at REAL, is_inactive INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE memory_scope_members(
              object_type TEXT NOT NULL, object_id TEXT NOT NULL, memory_space_id TEXT NOT NULL,
              partition_id TEXT NOT NULL, security_domain TEXT NOT NULL, source_session_id TEXT,
              created_at REAL NOT NULL, PRIMARY KEY(object_type, object_id, partition_id)
            );
            CREATE TABLE vector_embeddings(object_id TEXT PRIMARY KEY, embedding BLOB NOT NULL);
            """
        )

    def _resolve_conn(self):
        return self.conn

    def _ensure_memory_scope_tables(self, _cursor) -> None:
        return None

    def register_scope_member(self, **kwargs) -> None:
        connection = kwargs.get("connection") or self.conn
        connection.execute(
            "INSERT OR IGNORE INTO memory_scope_members VALUES (?,?,?,?,?,?,?)",
            (
                kwargs["object_type"], kwargs["object_id"], kwargs["memory_space_id"],
                kwargs["partition_id"], kwargs["security_domain"], None, 1,
            ),
        )
        if kwargs.get("commit", True):
            connection.commit()

    def seed_paragraph(
        self,
        object_id: str,
        *,
        content: str = "authority body",
        space: str = "space-source",
        partition: str = "partition-source",
        domain: str = "normal",
    ) -> None:
        self.conn.execute(
            "INSERT INTO paragraphs(hash,content,created_at,updated_at) VALUES (?,?,1,1)",
            (object_id, content),
        )
        self.register_scope_member(
            object_type="paragraph", object_id=object_id, memory_space_id=space,
            partition_id=partition, security_domain=domain,
        )

    def write_vector(self, object_id: str) -> None:
        self.vector_write_count += 1
        self.conn.execute("INSERT INTO vector_embeddings VALUES (?,?)", (object_id, b"vector"))
        self.conn.commit()


def _authority(*object_ids: str) -> tuple[MemoryTransferAuthorityService, AuthorityStore]:
    store = AuthorityStore()
    for object_id in object_ids or ("p1",):
        store.seed_paragraph(object_id)
    return MemoryTransferAuthorityService(store), store


def _link(operation_key: str = "am-link", **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "operation_key": operation_key,
        "object_type": "paragraph",
        "object_id": "p1",
        "source_space_id": "space-source",
        "source_partition_id": "partition-source",
        "target_space_id": "space-target",
        "target_partition_id": "partition-target",
        "source_security_domain": "normal",
        "target_security_domain": "normal",
    }
    payload.update(overrides)
    return payload


def _copy(operation_key: str = "am-copy", **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "operation_key": operation_key,
        "mode": "copy",
        "object_type": "paragraph",
        "source_object_id": "p1",
        "source_space_id": "space-source",
        "source_partition_id": "partition-source",
        "target_space_id": "space-target",
        "target_partition_id": "partition-target",
        "source_security_domain": "normal",
        "target_security_domain": "normal",
    }
    payload.update(overrides)
    return payload


async def _am01() -> None:
    service, _store = _authority("p-denied-1", "p-denied-2", "p-visible")
    page = service.list_scoped_objects(
        {
            "memory_space_id": "space-source",
            "partition_ids": ["partition-source"],
            "security_domain": "normal",
            "object_types": ["paragraph"],
            "object_ids": ["p-visible"],
            "limit": 1,
        }
    )
    assert [item["object_id"] for item in page["items"]] == ["p-visible"]
    assert page == {"items": page["items"], "has_more": False, "count": 1}
    assert all("content" not in item and "snapshot" not in item for item in page["items"])


async def _am02() -> None:
    authority, _store = _authority("p1")
    memory = MemoryService()

    async def invoke(component_name: str, args=None, **_kwargs):
        assert component_name == "inspect_object"
        return authority.inspect_object(args or {})

    memory._invoke = invoke  # type: ignore[method-assign]
    with pytest.raises(MemoryTransferAuthorityError, match="source_object_not_found"):
        await memory.inspect_object(
            object_type="paragraph", object_id="p1", memory_space_id="space-source",
            partition_id="partition-not-authorized", security_domain="normal",
        )
    assert authority._conn.execute("SELECT COUNT(*) FROM memory_transfer_operations").fetchone()[0] == 0


async def _am03() -> None:
    service, store = _authority("p1")
    first = service.link_object_to_scope(_link("am03"))
    second = service.link_object_to_scope(_link("am03"))
    assert first["status"] == "applied"
    assert second["status"] == "already_applied"
    assert first["target_object_id"] == second["target_object_id"] == "p1"
    assert store.conn.execute(
        "SELECT COUNT(*) FROM memory_scope_members WHERE object_id='p1' AND partition_id='partition-target'"
    ).fetchone()[0] == 1
    assert store.conn.execute(
        "SELECT COUNT(*) FROM memory_transfer_operations WHERE operation_key='am03'"
    ).fetchone()[0] == 1


async def _am04() -> None:
    service, store = _authority("p1")
    service.link_object_to_scope(_link("am04"))
    with pytest.raises(MemoryTransferAuthorityError, match="operation_key_payload_conflict"):
        service.link_object_to_scope(_link("am04", target_partition_id="partition-other"))
    assert store.conn.execute(
        "SELECT COUNT(*) FROM memory_scope_members WHERE partition_id='partition-other'"
    ).fetchone()[0] == 0
    assert store.conn.execute(
        "SELECT COUNT(*) FROM memory_transfer_operations WHERE operation_key='am04'"
    ).fetchone()[0] == 1


async def _am05() -> None:
    service, store = _authority("p1")
    vector_sql_writes: list[str] = []
    store.conn.set_trace_callback(
        lambda statement: vector_sql_writes.append(statement)
        if statement.lstrip().upper().startswith(("INSERT INTO VECTOR", "UPDATE VECTOR"))
        else None
    )
    result = service.link_object_to_scope(_link("am05"))
    store.conn.set_trace_callback(None)
    assert result["status"] == "applied" and result["target_object_id"] == "p1"
    assert store.vector_write_count == 0
    assert vector_sql_writes == []
    assert store.conn.execute("SELECT COUNT(*) FROM vector_embeddings").fetchone()[0] == 0


async def _am06() -> None:
    service, store = _authority("p1")
    copied = service.copy_object_to_scope(_copy("am06"))
    target_id = copied["target_object_id"]
    assert target_id and target_id != "p1"
    before = service.inspect_object(
        {
            "object_type": "paragraph", "object_id": target_id,
            "memory_space_id": "space-target", "partition_id": "partition-target",
            "security_domain": "normal",
        }
    )
    store.conn.execute("DELETE FROM paragraphs WHERE hash='p1'")
    store.conn.execute("DELETE FROM memory_scope_members WHERE object_id='p1'")
    store.conn.commit()
    after = service.inspect_object(
        {
            "object_type": "paragraph", "object_id": target_id,
            "memory_space_id": "space-target", "partition_id": "partition-target",
            "security_domain": "normal",
        }
    )
    assert after == before
    assert store.conn.execute("SELECT content FROM paragraphs WHERE hash=?", (target_id,)).fetchone()[0] == "authority body"
    assert service.get_transfer_operation("am06")["status"] == "applied"


async def _am07() -> None:
    service, store = _authority("p1")
    with pytest.raises(MemoryTransferAuthorityError, match="cross_domain_transfer_denied"):
        service.link_object_to_scope(_link("am07", target_security_domain="kami"))
    assert store.conn.execute("SELECT COUNT(*) FROM memory_transfer_operations").fetchone()[0] == 0
    assert store.conn.execute(
        "SELECT COUNT(*) FROM memory_scope_members WHERE partition_id='partition-target'"
    ).fetchone()[0] == 0


async def _am08() -> None:
    service, store = _authority("p1")
    result = service.copy_object_to_scope(_copy("am08"))
    target_id = result.get("target_object_id")
    assert isinstance(target_id, str) and target_id.strip() and target_id != "p1"
    assert store.conn.execute("SELECT 1 FROM paragraphs WHERE hash=?", (target_id,)).fetchone() is not None
    assert store.conn.execute(
        "SELECT 1 FROM memory_scope_members WHERE object_id=? AND partition_id='partition-target'",
        (target_id,),
    ).fetchone() is not None


async def _am09() -> None:
    service, store = _authority("p1")
    assert service.link_object_to_scope(_link("am09-applied"))["status"] == "applied"
    assert service.reconcile_transfer_operation("am09-applied")["status"] == "applied"
    assert service.reconcile_transfer_operation("am09-missing")["status"] == "not_applied"

    pending_payload = _copy("am09-unknown")
    payload_hash = service._canonical_hash(pending_payload)
    now = 1.0
    store.conn.execute(
        """INSERT INTO memory_transfer_operations
           (operation_key,payload_hash,mode,status,result_json,error_code,created_at,updated_at)
           VALUES (?,?,?,'pending',?,'',?,?)""",
        ("am09-unknown", payload_hash, "copy", json.dumps(pending_payload), now, now),
    )
    target_id = service._canonical_hash({"not": "the authority target id"})
    store.conn.execute(
        """INSERT INTO memory_transfer_objects
           (target_object_id,object_type,source_object_id,source_space_id,source_partition_id,
            target_space_id,target_partition_id,security_domain,content_fingerprint,
            root_object_type,root_object_id,snapshot_json,source_version,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            target_id, "paragraph", "p1", "space-source", "partition-source",
            "space-target", "partition-target", "normal", "fingerprint",
            "paragraph", "p1", "{}", "1", now,
        ),
    )
    store.conn.commit()
    unknown = service.reconcile_transfer_operation("am09-unknown")
    assert unknown["status"] == "not_applied"

    expected_target = __import__("hashlib").sha256(
        b"a-memorix-copy:am09-unknown:paragraph:p1"
    ).hexdigest()
    store.conn.execute(
        "UPDATE memory_transfer_objects SET target_object_id=? WHERE target_object_id=?",
        (expected_target, target_id),
    )
    store.conn.commit()
    unknown = service.reconcile_transfer_operation("am09-unknown")
    assert unknown["status"] == "unknown"
    assert unknown["error_code"] == "unknown_result_needs_reconcile"


async def _am10() -> None:
    _authority_service, store = _authority("p1")
    host = AMemorixHostService()
    host._kernel = SimpleNamespace(metadata_store=store)
    host._runtime_state = "ready"
    host.is_enabled = lambda: True  # type: ignore[method-assign]

    with pytest.raises(MemoryTransferAuthorityError, match="missing_scope_metadata"):
        await host.invoke(
            "list_scoped_objects",
            {
                "model_scope": {
                    "memory_space_id": "space-source",
                    "partition_ids": ["partition-source"],
                    "security_domain": "normal",
                },
                "object_types": ["paragraph"],
            },
        )

    result = await host.invoke(
        "list_scoped_objects",
        {
            "memory_space_id": "space-source",
            "partition_ids": ["partition-source"],
            "security_domain": "normal",
            "object_types": ["paragraph"],
            "model_scope": {
                "memory_space_id": "attacker-space",
                "partition_ids": ["attacker-partition"],
                "security_domain": "kami",
            },
        },
    )
    assert [item["object_id"] for item in result["items"]] == ["p1"]
    assert all(item["memory_space_id"] == "space-source" for item in result["items"])
    assert all(item["partition_id"] == "partition-source" for item in result["items"])
    assert all(item["security_domain"] == "normal" for item in result["items"])


class _VectorStore:
    def __init__(self, ids: list[str], scores: list[float]) -> None:
        self.ids = ids
        self.scores = scores
        self.dimension = 4

    def search(self, query: np.ndarray, k: int = 10, filter_deleted: bool = True):
        del query, filter_deleted
        return self.ids[:k], self.scores[:k]


class _EmbeddingManager:
    async def encode(self, text: Any, **kwargs: Any) -> np.ndarray:
        del text, kwargs
        return np.ones(4, dtype=np.float32)


class _RetrievalMetadata:
    def __init__(self) -> None:
        self.paragraphs = {
            "p-dense": {"hash": "p-dense", "content": "Alice 喜欢红茶", "word_count": 4},
            "p-sparse": {"hash": "p-sparse", "content": "红茶稀疏命中", "word_count": 4},
            "p-graph": {"hash": "p-graph", "content": "Alice 和 Bob 是同事", "word_count": 6},
        }
        self.relations = {
            "r-1": {"hash": "r-1", "subject": "Alice", "predicate": "同事", "object": "Bob", "confidence": 1.0}
        }

    def get_paragraphs_by_hashes(self, hashes):
        return {key: self.paragraphs[key] for key in hashes if key in self.paragraphs}

    def get_relations_by_hashes(self, hashes, include_inactive: bool = True):
        del include_inactive
        return {key: self.relations[key] for key in hashes if key in self.relations}

    @staticmethod
    def get_entities_by_hashes(hashes):
        return {key: {"hash": key, "name": key} for key in hashes}

    def get_paragraphs_by_relation_hashes(self, hashes):
        return {key: [self.paragraphs["p-graph"]] if key == "r-1" else [] for key in hashes}

    @staticmethod
    def get_paragraphs_by_entity_hashes(hashes):
        return {key: [] for key in hashes}

    def get_paragraph_hashes_by_relation_hashes(self, hashes):
        return {key: ["p-graph"] if key == "r-1" else [] for key in hashes}

    @staticmethod
    def get_paragraph_entities_by_hashes(hashes):
        return {key: [] for key in hashes}


class _SparseIndex:
    def __init__(self) -> None:
        self.search_count = 0

    def search(self, query: str, k: int, allowed_ids=None):
        del query, k, allowed_ids
        self.search_count += 1
        return [{"hash": "p-sparse", "score": 0.95, "bm25_score": 4.0}]


async def _am11() -> None:
    paragraph_store = _VectorStore(["p-dense"], [0.4])
    graph_store = _VectorStore(["relation:r-1"], [0.9])
    sparse = _SparseIndex()
    retriever = DualPathRetriever(
        vector_store=paragraph_store,
        paragraph_vector_store=paragraph_store,
        graph_vector_store=graph_store,
        graph_store=SimpleNamespace(get_nodes=lambda: []),
        metadata_store=_RetrievalMetadata(),
        embedding_manager=_EmbeddingManager(),
        sparse_index=sparse,
        config=DualPathRetrieverConfig(
            enable_ppr=False,
            enable_parallel=False,
            sparse=SparseBM25Config(enabled=True, mode="auto"),
            vector_pools=VectorPoolsConfig(mode="dual"),
        ),
    )
    results = await retriever.retrieve("Alice 红茶 同事", top_k=10)
    by_hash = {item.hash_value: item for item in results}
    assert {"p-dense", "p-sparse", "p-graph"} <= set(by_hash)
    assert by_hash["p-dense"].metadata["score_breakdown"]["semantic"] == pytest.approx(0.4)
    assert by_hash["p-sparse"].metadata["bm25_score"] == pytest.approx(4.0)
    assert by_hash["p-graph"].metadata["evidence_items"][0]["type"] == "relation"
    assert by_hash["p-graph"].metadata["evidence_items"][0]["hash"] == "r-1"
    assert sparse.search_count == 1


class _ProfileMetadata:
    def __init__(self) -> None:
        self.snapshots: list[dict[str, Any]] = []

    def get_latest_person_profile_snapshot(self, person_id: str):
        return next((row for row in reversed(self.snapshots) if row["person_id"] == person_id), None)

    @staticmethod
    def get_relations(**kwargs):
        if kwargs.get("subject") == "测试用户":
            return [{
                "hash": "relation-1", "subject": "测试用户", "predicate": "把麦麦当作",
                "object": "搭档", "confidence": 0.99,
                "metadata": {"person_id": "person-1"},
            }]
        return []

    @staticmethod
    def get_paragraphs_by_source(source: str):
        if source == "person_fact:person-1":
            return [{
                "hash": "person-fact-1", "content": "测试用户喜欢猫。", "source": source,
                "metadata": {"source_type": "person_fact"}, "created_at": 2.0, "updated_at": 2.0,
            }]
        return []

    @staticmethod
    def get_paragraph(hash_value: str):
        rows = {
            "chat-summary-1": {
                "hash": "chat-summary-1", "content": "机器人建议测试用户以后叫星灯。",
                "source": "chat_summary:session-1",
                "metadata": {"source_type": "chat_summary", "person_id": "person-1"}, "word_count": 1,
            },
            "person-fact-1": {
                "hash": "person-fact-1", "content": "测试用户喜欢猫。",
                "source": "person_fact:person-1", "metadata": {"source_type": "person_fact"}, "word_count": 1,
            },
        }
        return rows.get(hash_value)

    @staticmethod
    def get_paragraph_stale_relation_marks_batch(paragraph_hashes):
        del paragraph_hashes
        return {}

    @staticmethod
    def get_relation_status_batch(relation_hashes):
        del relation_hashes
        return {}

    @staticmethod
    def list_person_profile_fact_claims(person_id: str, *, effective_at: float, limit: int):
        assert person_id == "person-1" and effective_at > 0 and limit > 0
        return [{
            "claim_id": "claim-1", "value_text": "测试用户喜欢猫。",
            "profile_section": "interaction_preferences", "authority": "direct_user", "status": "active",
        }]

    @staticmethod
    def get_person_profile_override(person_id: str):
        del person_id
        return None

    def upsert_person_profile_snapshot(self, **kwargs):
        snapshot = {
            **kwargs, "snapshot_id": len(self.snapshots) + 1, "profile_version": len(self.snapshots) + 1,
            "updated_at": 1.0,
        }
        self.snapshots.append(snapshot)
        return snapshot

    def refresh_person_profile_snapshot_cache(self, snapshot_id: int, **kwargs):
        snapshot = next(row for row in self.snapshots if row["snapshot_id"] == snapshot_id)
        snapshot.update(kwargs)
        return dict(snapshot)


class _ProfileRetriever:
    async def retrieve(self, query: str, top_k: int):
        del query, top_k
        return [SimpleNamespace(
            hash_value="chat-summary-1", result_type="paragraph", score=0.95,
            content="机器人建议测试用户以后叫星灯。",
            metadata={"source_type": "chat_summary", "person_id": "person-1"},
        )]


async def _am12() -> None:
    metadata = _ProfileMetadata()
    service = PersonProfileService(metadata_store=metadata, retriever=_ProfileRetriever())
    service.get_person_aliases = lambda person_id: (["测试用户"], "测试用户", [])
    service._resolve_profile_classification_model = lambda: None
    payload = await service.query_person_profile(person_id="person-1", top_k=6, force_refresh=True)
    sections = parse_profile_sections(payload["profile_text"])
    stable = "\n".join(
        sections["身份设定"] + sections["关系设定"] + sections["稳定了解"] + sections["相处偏好"]
    )
    assert payload["success"] is True
    assert "测试用户喜欢猫" in "\n".join(sections["相处偏好"])
    relationship_evidence = "\n".join(sections["关系设定"] + sections["不确定信息"])
    assert "测试用户把麦麦当作搭档" in relationship_evidence
    assert "星灯" not in stable
    assert "星灯" in "\n".join(sections["近期互动"])
    assert payload["fact_claim_ids"] == ["claim-1"]


CASES: tuple[tuple[str, Case], ...] = (
    ("AM01", _am01), ("AM02", _am02), ("AM03", _am03), ("AM04", _am04),
    ("AM05", _am05), ("AM06", _am06), ("AM07", _am07), ("AM08", _am08),
    ("AM09", _am09), ("AM10", _am10), ("AM11", _am11), ("AM12", _am12),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(("case_id", "case"), CASES, ids=[case_id for case_id, _case in CASES])
async def test_phase6_am_matrix(case_id: str, case: Case) -> None:
    assert case.__name__ == f"_{case_id.lower()}"
    await case()
