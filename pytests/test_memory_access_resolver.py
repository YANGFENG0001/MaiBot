from src.services.memory_service import MemoryHit, MemoryService
from src.workspaces import MemoryScope
from src.workspaces.access_resolver import AccessResolver
from src.common.database.database_model import MemoryPermissionRule


def test_memory_service_filters_partition_before_limit():
    scope = MemoryScope(
        workspace_id="w",
        primary_space_id="s",
        readable_space_ids=("s",),
        writable_space_ids=("s",),
        readable_partition_ids=("p-allowed",),
    )
    hits = [
        MemoryHit("secret", metadata={"memory_space_id": "s", "partition_id": "p-denied"}),
        MemoryHit("visible", metadata={"memory_space_id": "s", "partition_id": "p-allowed"}),
    ]
    assert [item.content for item in MemoryService._filter_hits_for_scope(hits, scope, 1)] == ["visible"]


def test_permission_rule_conflict_is_explicit():
    rules = [
        MemoryPermissionRule(permission_group_id="g", effect="allow", priority=10),
        MemoryPermissionRule(permission_group_id="g", effect="deny", priority=10),
    ]
    try:
        AccessResolver.validate_rule_conflicts(rules)
    except ValueError as exc:
        assert "冲突" in str(exc)
    else:
        raise AssertionError("同级规则冲突必须失败")


def test_request_scoped_filter_rejects_hits_without_partition_provenance():
    scope = MemoryScope(
        workspace_id="w",
        primary_space_id="s",
        readable_space_ids=("s",),
        writable_space_ids=("s",),
        readable_partition_ids=("p-allowed",),
        trace_id="trace-secure",
    )
    hits = [MemoryHit("unscoped", metadata={"memory_space_id": "s"})]
    assert MemoryService._filter_hits_for_scope(hits, scope, 5) == []
