"""AM contract exposure through MaiBot MemoryService."""

import pytest

from src.services.memory_service import MemoryService


@pytest.mark.asyncio
async def test_memory_service_transfer_wrappers_are_thin(monkeypatch):
    calls = []

    async def invoke(self, component_name, args=None, **kwargs):
        calls.append((component_name, args))
        if component_name == "list_scoped_objects":
            return {"items": [], "has_more": False}
        return {"status": "not_applied", "operation_key": (args or {}).get("operation_key", "")}

    monkeypatch.setattr(MemoryService, "_invoke", invoke)
    service = MemoryService()
    await service.list_scoped_objects(
        memory_space_id="s", partition_ids=("p",), security_domain="normal",
        object_types=("paragraph",), limit=10,
    )
    await service.inspect_object(object_type="paragraph", object_id="x")
    await service.link_object_to_scope(operation_key="link")
    await service.copy_object_to_scope(operation_key="copy")
    await service.get_transfer_operation("get")
    await service.reconcile_transfer_operation("reconcile")
    assert [name for name, _ in calls] == [
        "list_scoped_objects", "inspect_object", "link_object_to_scope", "copy_object_to_scope",
        "get_transfer_operation", "reconcile_transfer_operation",
    ]
