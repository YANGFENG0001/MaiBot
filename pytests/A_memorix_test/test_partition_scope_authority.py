"""检索范围：显式分区范围由 AccessResolver 逐请求裁定，是权威授权来源。

回归背景
--------
A-Memorix 原先在候选生成前把「聊天流范围」与「分区范围」做交集。分区范围来自
MaiBot 的 AccessResolver（逐请求裁定），而聊天流范围来自 metadata 里的 chat_id。
两者取交集会让跨会话/跨空间被**显式授权**的记忆永远召回不到，例如：

* ``B.person:Y`` 里的「Y 喜欢咖啡」是在 B 的会话里学到的（chat_id=session-b），
  A 的会话（session-a）即使拿到该 person 分区许可也读不到；
* 显式授权 ``B.conversation:session-b`` 后同理读不到该会话记忆。

因此：给出显式 ``allowed_partition_ids`` 时，分区范围直接作为候选集合，不再被
聊天流范围二次收窄；未给出分区范围时保持原有严格聊天流隔离，避免独立使用泄漏。

同时回归 ``coerce_metadata_dict`` 对 JSON 文本列的解析：metadata 在 SQLite 里是
JSON 字符串，若不解析会让聊天流范围判定整体退化为空集。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.A_memorix.core.runtime.sdk_memory_kernel import KernelSearchRequest, SDKMemoryKernel
from src.A_memorix.core.storage.metadata_store import MetadataStore

SPACE_A = "memory-space-a"
SPACE_B = "memory-space-b"
PART_A_PERSON_Y = "memory-partition-a-person-y"
PART_A_CONV = "memory-partition-a-conv"
PART_B_PERSON_Y = "memory-partition-b-person-y"
PART_B_CONV = "memory-partition-b-conv"


def _build_store(tmp_path: Path) -> tuple[SDKMemoryKernel, MetadataStore, dict[str, str]]:
    data_dir = tmp_path / "memory"
    kernel = SDKMemoryKernel(
        plugin_root=tmp_path,
        config={"storage": {"data_dir": str(data_dir)}},
    )
    store = MetadataStore(data_dir=data_dir / "metadata")
    store.connect()

    hashes: dict[str, str] = {}
    hashes["person_y_b"] = store.add_paragraph(
        "Y 在 B 里喜欢喝咖啡（coffee）",
        source="person_fact:person-y",
        metadata={"source_type": "person_fact", "chat_id": "session-b", "chat_ids": ["session-b"]},
    )
    hashes["person_y_a"] = store.add_paragraph(
        "Y 在 A 里喜欢喝红茶（black tea）",
        source="person_fact:person-y",
        metadata={"source_type": "person_fact", "chat_id": "session-a", "chat_ids": ["session-a"]},
    )
    hashes["conv_b"] = store.add_paragraph(
        "B 讨论过项目密钥轮换（project key rotation）",
        source="chat_summary:session-b",
        metadata={"scope_type": "chat", "chat_id": "session-b"},
    )
    hashes["conv_a"] = store.add_paragraph(
        "A 的普通会话摘要",
        source="chat_summary:session-a",
        metadata={"scope_type": "chat", "chat_id": "session-a"},
    )

    for key, space, partition, session in (
        ("person_y_a", SPACE_A, PART_A_PERSON_Y, "session-a"),
        ("conv_a", SPACE_A, PART_A_CONV, "session-a"),
        ("person_y_b", SPACE_B, PART_B_PERSON_Y, "session-b"),
        ("conv_b", SPACE_B, PART_B_CONV, "session-b"),
    ):
        store.register_scope_member(
            object_type="paragraph",
            object_id=hashes[key],
            memory_space_id=space,
            partition_id=partition,
            source_session_id=session,
        )

    kernel.metadata_store = store
    return kernel, store, hashes


def test_retrieval_scope_stays_chat_strict_without_partition_scope(tmp_path: Path) -> None:
    """未下推分区范围时保持原有严格聊天流隔离，避免独立使用跨会话泄漏。"""
    kernel, _, hashes = _build_store(tmp_path)
    service = kernel._get_search_hit_service()

    scope = type(service)._resolve_retrieval_scope(service, "session-a")
    assert scope is not None
    assert hashes["conv_a"] in scope.paragraph_ids
    assert hashes["person_y_a"] in scope.paragraph_ids
    assert hashes["person_y_b"] not in scope.paragraph_ids
    assert hashes["conv_b"] not in scope.paragraph_ids


async def _capture_scope(kernel: SDKMemoryKernel, monkeypatch: pytest.MonkeyPatch, request: KernelSearchRequest):
    """拦截检索执行，直接断言传给检索器的 RetrievalScope。"""
    captured: dict[str, object] = {}

    async def fake_exec(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(success=True, error="", chat_filtered=False, results=[])

    monkeypatch.setattr(kernel, "_search_execution_for_chat_scope", fake_exec)
    await kernel.search_memory(request)
    return captured["scope"]


@pytest.mark.asyncio
async def test_explicit_partition_scope_supersedes_chat_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """显式分区范围是权威授权：跨会话/跨空间被授权的记忆必须进入候选集。"""
    kernel, _, hashes = _build_store(tmp_path)

    scope = await _capture_scope(
        kernel,
        monkeypatch,
        KernelSearchRequest(
            query="喜欢 咖啡 红茶 密钥轮换",
            limit=10,
            mode="search",
            chat_id="session-a",
            respect_filter=False,
            allowed_memory_space_ids=(SPACE_A, SPACE_B),
            allowed_partition_ids=(PART_A_PERSON_Y, PART_A_CONV, PART_B_PERSON_Y),
        ),
    )

    assert scope is not None
    # 被显式授权的 B.person:Y 可以跨会话召回（原先被聊天流范围挡住）
    assert hashes["person_y_b"] in scope.paragraph_ids
    assert hashes["person_y_a"] in scope.paragraph_ids
    assert hashes["conv_a"] in scope.paragraph_ids
    # 未被授权的 B.conversation 不得进入候选集
    assert hashes["conv_b"] not in scope.paragraph_ids


@pytest.mark.asyncio
async def test_explicit_partition_scope_does_not_leak_unauthorized_space(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """分区范围之外的记忆即使聊天流匹配也不得进入候选集。"""
    kernel, _, hashes = _build_store(tmp_path)

    scope = await _capture_scope(
        kernel,
        monkeypatch,
        KernelSearchRequest(
            query="喜欢 咖啡 红茶 密钥轮换",
            limit=10,
            mode="search",
            chat_id="session-a",
            respect_filter=False,
            allowed_memory_space_ids=(SPACE_A,),
            allowed_partition_ids=(PART_A_PERSON_Y, PART_A_CONV),
        ),
    )

    assert scope is not None
    assert hashes["person_y_a"] in scope.paragraph_ids
    assert hashes["person_y_b"] not in scope.paragraph_ids
    assert hashes["conv_b"] not in scope.paragraph_ids


@pytest.mark.asyncio
async def test_memory_space_only_scope_keeps_chat_isolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """只给出记忆空间范围时仍保持聊天流隔离，避免同空间跨会话泄漏。"""
    kernel, _, hashes = _build_store(tmp_path)

    scope = await _capture_scope(
        kernel,
        monkeypatch,
        KernelSearchRequest(
            query="喜欢 咖啡 红茶 密钥轮换",
            limit=10,
            mode="search",
            chat_id="session-a",
            respect_filter=False,
            allowed_memory_space_ids=(SPACE_A, SPACE_B),
        ),
    )

    assert scope is not None
    assert hashes["person_y_a"] in scope.paragraph_ids
    assert hashes["person_y_b"] not in scope.paragraph_ids
    assert hashes["conv_b"] not in scope.paragraph_ids


def test_resolve_scope_object_partitions_maps_objects_to_allowed_partitions(tmp_path: Path) -> None:
    kernel, store, hashes = _build_store(tmp_path)

    only_person_b = store.resolve_scope_object_partitions(partition_ids=(PART_B_PERSON_Y,), memory_space_ids=(SPACE_B,))
    assert only_person_b.get(hashes["person_y_b"]) == PART_B_PERSON_Y
    assert hashes["conv_b"] not in only_person_b

    both_b = store.resolve_scope_object_partitions(
        partition_ids=(PART_B_PERSON_Y, PART_B_CONV), memory_space_ids=(SPACE_B,)
    )
    assert both_b.get(hashes["person_y_b"]) == PART_B_PERSON_Y
    assert both_b.get(hashes["conv_b"]) == PART_B_CONV

    space_only = store.resolve_scope_object_partitions(memory_space_ids=(SPACE_B,))
    assert hashes["person_y_b"] in space_only


def test_resolve_scope_object_partitions_prefers_explicit_partition(tmp_path: Path) -> None:
    """同一对象同时登记在默认分区与真实分区时，优先返回显式允许的分区。"""
    kernel, store, hashes = _build_store(tmp_path)
    store.register_scope_member(
        object_type="paragraph",
        object_id=hashes["person_y_b"],
        memory_space_id="memory-space-public",
        partition_id="conversation",
    )

    resolved = store.resolve_scope_object_partitions(
        partition_ids=(PART_B_PERSON_Y,), memory_space_ids=(SPACE_B, "memory-space-public")
    )
    assert resolved[hashes["person_y_b"]] == PART_B_PERSON_Y
