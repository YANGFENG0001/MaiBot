"""Security regression: no body-like fields enter transfer audit."""

from types import SimpleNamespace

import pytest

from src.workspaces.memory_transfer_executor import MemoryTransferExecutor
from src.workspaces.memory_transfer_models import MemoryTransferError, safe_audit_details, sanitize_comment


@pytest.mark.parametrize("key", ["content", "query", "prompt", "token", "password", "api_key"])
def test_sensitive_audit_keys_are_rejected(key):
    with pytest.raises(MemoryTransferError):
        safe_audit_details({key: "value"})


def test_approval_comment_is_bounded_and_body_free():
    assert sanitize_comment("routine approval") == "routine approval"
    with pytest.raises(MemoryTransferError):
        sanitize_comment("token=secret")
    with pytest.raises(MemoryTransferError):
        sanitize_comment("x" * 257)


def test_authority_result_rejects_nested_or_unknown_body_fields():
    item = SimpleNamespace(
        operation_key="operation", object_type="paragraph", source_object_id="source",
        target_space_id="target-space", target_partition_id="target-partition",
        mode="copy", content_fingerprint="fingerprint",
    )
    base = {
        "status": "applied", "operation_key": "operation", "object_type": "paragraph",
        "source_object_id": "source", "target_object_id": "copy",
        "target_space_id": "target-space", "target_partition_id": "target-partition",
        "target_fingerprint": "fingerprint",
    }
    MemoryTransferExecutor._validate_result(item, base)
    with pytest.raises(MemoryTransferError, match="upstream_invalid_response"):
        MemoryTransferExecutor._validate_result(item, {**base, "metadata": {"content": "secret"}})
    with pytest.raises(MemoryTransferError, match="upstream_invalid_response"):
        MemoryTransferExecutor._validate_result(item, {**base, "unexpected": "value"})
