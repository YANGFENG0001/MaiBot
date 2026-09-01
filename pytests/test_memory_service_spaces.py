from unittest.mock import AsyncMock

import pytest

from src.services.memory_service import MemoryService
from src.workspaces.context import MemoryScope


class FakeWorkspaceService:
    def __init__(self, scope: MemoryScope) -> None:
        self.scope = scope
        self.registrations: list[dict] = []

    def resolve_memory_scope(self, chat_id: str, memory_space_id: str = "") -> MemoryScope:
        return self.scope

    def register_memory_objects(self, **kwargs) -> int:
        self.registrations.append(kwargs)
        return len(kwargs.get("object_ids") or [])

    def memory_object_space_ids(self, object_type: str, object_ids):
        return {}


@pytest.mark.asyncio
async def test_ingest_tags_workspace_space_and_registers_stored_objects(monkeypatch) -> None:
    scope = MemoryScope(
        workspace_id="workspace-a",
        primary_space_id="space-a",
        readable_space_ids=("space-a",),
        writable_space_ids=("space-a",),
        shared_session_ids=("chat-a",),
    )
    fake_workspace = FakeWorkspaceService(scope)
    monkeypatch.setattr("src.services.memory_service.workspace_service", fake_workspace)
    service = MemoryService()
    service._invoke = AsyncMock(return_value={"success": True, "stored_ids": ["paragraph-a"]})

    result = await service.ingest_text(
        external_id="event-a",
        source_type="test",
        text="只属于 A 空间",
        chat_id="chat-a",
        person_ids=["person-a"],
        metadata={"source": "pytest"},
    )

    assert result.success is True
    payload = service._invoke.await_args.args[1]
    assert payload["metadata"]["memory_space_id"] == "space-a"
    assert payload["metadata"]["workspace_id"] == "workspace-a"
    assert {item["object_type"] for item in fake_workspace.registrations} == {"memory", "person_profile"}
    memory_registration = next(item for item in fake_workspace.registrations if item["object_type"] == "memory")
    person_registration = next(item for item in fake_workspace.registrations if item["object_type"] == "person_profile")
    assert memory_registration["partition_type"] == "shared"
    assert person_registration["partition_type"] == "person"


@pytest.mark.asyncio
async def test_search_filters_private_spaces_and_preserves_legacy_records_for_public_space(monkeypatch) -> None:
    scope = MemoryScope(
        workspace_id="workspace-a",
        primary_space_id="space-a",
        readable_space_ids=("space-a", "space-b"),
        writable_space_ids=("space-a",),
        shared_session_ids=("chat-a", "chat-b"),
    )
    fake_workspace = FakeWorkspaceService(scope)
    monkeypatch.setattr("src.services.memory_service.workspace_service", fake_workspace)
    service = MemoryService()
    service._invoke = AsyncMock(
        return_value={
            "success": True,
            "hits": [
                {"content": "A", "hash": "a", "metadata": {"memory_space_id": "space-a"}},
                {"content": "B", "hash": "b", "metadata": {"memory_space_id": "space-b"}},
                {"content": "C", "hash": "c", "metadata": {"memory_space_id": "space-c"}},
                {"content": "legacy", "hash": "legacy", "metadata": {}},
            ],
        }
    )

    result = await service.search("测试", chat_id="chat-a", limit=10)

    assert [item.hash_value for item in result.hits] == ["a", "b"]
    payload = service._invoke.await_args.args[1]
    assert payload["shared_chat_ids"] == ["chat-a", "chat-b"]
    assert payload["limit"] == 10


@pytest.mark.asyncio
async def test_public_scope_keeps_legacy_memories_without_space_metadata(monkeypatch) -> None:
    scope = MemoryScope(
        workspace_id="workspace-default",
        primary_space_id="memory-space-public",
        readable_space_ids=("memory-space-public",),
        writable_space_ids=("memory-space-public",),
        shared_session_ids=("legacy-chat",),
    )
    monkeypatch.setattr("src.services.memory_service.workspace_service", FakeWorkspaceService(scope))
    service = MemoryService()
    service._invoke = AsyncMock(
        return_value={
            "success": True,
            "hits": [
                {"content": "旧版无空间标签的记忆", "hash": "legacy", "metadata": {}},
                {"content": "私有空间记忆", "hash": "private", "metadata": {"memory_space_id": "space-private"}},
            ],
        }
    )

    result = await service.search("旧记忆", chat_id="legacy-chat", limit=5)

    assert [item.hash_value for item in result.hits] == ["legacy"]


@pytest.mark.asyncio
async def test_ingest_summary_registers_conversation_partition(monkeypatch) -> None:
    scope = MemoryScope(
        workspace_id="workspace-a",
        primary_space_id="space-a",
        readable_space_ids=("space-a",),
        writable_space_ids=("space-a",),
        shared_session_ids=("chat-a",),
    )
    fake_workspace = FakeWorkspaceService(scope)
    monkeypatch.setattr("src.services.memory_service.workspace_service", fake_workspace)
    service = MemoryService()
    service._invoke = AsyncMock(return_value={"success": True, "stored_ids": ["summary-a"]})

    result = await service.ingest_summary(external_id="summary-a", chat_id="chat-a", text="会话摘要")

    assert result.success is True
    assert fake_workspace.registrations == [
        {
            "object_type": "memory",
            "object_ids": ["summary-a"],
            "memory_space_id": "space-a",
            "source_session_id": "chat-a",
            "partition_type": "conversation",
            "partition_key": "chat-a",
        }
    ]

