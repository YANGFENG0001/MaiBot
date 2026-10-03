"""作用域成员登记：写入路径不得触发 legacy 回填，回填不得覆盖已登记对象。

回归背景
--------
``metadata_schema._ensure_memory_scope_tables`` 在创建 ``memory_scope_members`` 时
会把 ``paragraphs`` / ``relations`` 里"还没有成员行"的对象按 legacy 默认值补一条
``memory-space-public`` + 裸分区类型名（``shared`` / ``conversation``）的成员行。

问题在于 ``metadata_store.register_scope_member`` 在插入真实成员行**之前**也会调用
同一个方法。于是 ingest 的每个对象都会先被回填补上一条幻影行，再写入真实行：

* Kami 安全域写入的对象会同时拥有一条 ``security_domain='normal'`` 的
  ``memory-space-public`` 成员行，按 ``memory_space_id`` 解析候选时就会跨空间泄漏；
* 同一对象出现两条成员行，检索与审计看到的落点取决于行顺序。

修复后：

1. 写入路径（``register_scope_member``）只建表、不回填；
2. 回填只处理"完全没有成员行"的对象；
3. 回填会清掉早期版本留下的、被真实成员行遮蔽的幻影行，但保留真正的
   legacy 对象（它们只有这一条回填行，删掉反而会失去作用域）。
"""

from __future__ import annotations

from pathlib import Path

from src.A_memorix.core.storage.metadata_store import MetadataStore

SPACE_A = "memory-space-a"
SPACE_KAMI = "memory-space-kami"
PART_A_PERSON_Y = "memory-partition-a-person-y"
PART_KAMI = "memory-partition-kami-conv"


def _build_store(tmp_path: Path) -> MetadataStore:
    store = MetadataStore(data_dir=tmp_path / "memory" / "metadata")
    store.connect()
    return store


def _members(store: MetadataStore, object_id: str) -> list[tuple[str, str, str]]:
    rows = store._conn.execute(
        "SELECT memory_space_id, partition_id, security_domain FROM memory_scope_members "
        "WHERE object_id = ? ORDER BY partition_id",
        (object_id,),
    ).fetchall()
    return [(str(r[0]), str(r[1]), str(r[2])) for r in rows]


def test_register_scope_member_does_not_backfill_phantom(tmp_path: Path) -> None:
    """写入路径登记受限安全域对象时，不得被 legacy 回填补出 normal 域幻影行。"""

    store = _build_store(tmp_path)
    paragraph = store.add_paragraph(
        "Kami 写入的测试内容",
        source="person_fact:person-y",
        metadata={"source_type": "person_fact", "chat_id": "session-b"},
    )

    store.register_scope_member(
        object_type="paragraph",
        object_id=paragraph,
        memory_space_id=SPACE_KAMI,
        partition_id=PART_KAMI,
        security_domain="kami",
        source_session_id="session-b",
    )

    assert _members(store, paragraph) == [(SPACE_KAMI, PART_KAMI, "kami")]


def test_register_scope_member_is_idempotent(tmp_path: Path) -> None:
    """重复登记同一对象不产生第二条成员行。"""

    store = _build_store(tmp_path)
    paragraph = store.add_paragraph(
        "Y 在 A 里喜欢喝红茶",
        source="person_fact:person-y",
        metadata={"source_type": "person_fact", "chat_id": "session-a"},
    )
    for _ in range(2):
        store.register_scope_member(
            object_type="paragraph",
            object_id=paragraph,
            memory_space_id=SPACE_A,
            partition_id=PART_A_PERSON_Y,
            security_domain="normal",
        )

    assert _members(store, paragraph) == [(SPACE_A, PART_A_PERSON_Y, "normal")]


def test_backfill_removes_shadowed_phantom_but_keeps_legacy_row(tmp_path: Path) -> None:
    """回填清掉被真实成员行遮蔽的幻影行，同时保留只有回填行的 legacy 对象。"""

    store = _build_store(tmp_path)
    shadowed = store.add_paragraph(
        "被遮蔽的对象",
        source="person_fact:person-y",
        metadata={"source_type": "person_fact", "chat_id": "session-a"},
    )
    legacy = store.add_paragraph(
        "只有 legacy 回填行的对象",
        source="person_fact:person-legacy",
        metadata={"source_type": "person_fact", "chat_id": "session-legacy"},
    )
    store.register_scope_member(
        object_type="paragraph",
        object_id=shadowed,
        memory_space_id=SPACE_A,
        partition_id=PART_A_PERSON_Y,
        security_domain="normal",
    )
    # 手工补一条早期版本会留下的幻影行。
    store._conn.execute(
        "INSERT INTO memory_scope_members "
        "(object_type, object_id, memory_space_id, partition_id, security_domain, created_at) "
        "VALUES ('paragraph', ?, 'memory-space-public', 'conversation', 'normal', 0)",
        (shadowed,),
    )
    store._conn.commit()

    store._ensure_memory_scope_tables(store._conn.cursor(), backfill=True)
    store._conn.commit()

    assert _members(store, shadowed) == [(SPACE_A, PART_A_PERSON_Y, "normal")]
    assert _members(store, legacy) == [("memory-space-public", "conversation", "normal")]


def test_backfill_does_not_duplicate_registered_objects(tmp_path: Path) -> None:
    """回填是幂等的：已登记对象不会被补第二条 public 默认行。"""

    store = _build_store(tmp_path)
    paragraph = store.add_paragraph(
        "已正确登记的对象",
        source="person_fact:person-y",
        metadata={"source_type": "person_fact", "chat_id": "session-a"},
    )
    store.register_scope_member(
        object_type="paragraph",
        object_id=paragraph,
        memory_space_id=SPACE_A,
        partition_id=PART_A_PERSON_Y,
        security_domain="normal",
    )

    for _ in range(3):
        store._ensure_memory_scope_tables(store._conn.cursor(), backfill=True)
    store._conn.commit()

    assert _members(store, paragraph) == [(SPACE_A, PART_A_PERSON_Y, "normal")]
