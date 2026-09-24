"""RQ01-RQ13: request and ORM boundary validation."""

import pytest
from sqlmodel import SQLModel, create_engine

from src.workspaces.memory_transfer_models import MemoryTransferError, TransferCreateRequest, safe_audit_details


def request(**overrides):
    values = {
        "mode": "copy", "source_space_id": "source", "source_partition_ids": ("p-source",),
        "target_space_id": "target", "target_partition_id": "p-target",
        "object_types": ("paragraph",), "object_ids": (), "filters": {},
        "approval_policy": "manual", "conflict_policy": "skip", "idempotency_key": "request-1",
    }
    values.update(overrides)
    return TransferCreateRequest(**values)


def test_rq01_rq02_rq03_canonical_is_stable_and_bounded() -> None:
    first = request(object_ids=("b", "a", "a"), source_partition_ids=("z", "a", "a"))
    second = request(object_ids=("a", "b"), source_partition_ids=("a", "z"))
    assert first.canonical() == second.canonical()
    assert first.payload_hash() == second.payload_hash()


@pytest.mark.parametrize(
    "overrides,code",
    [
        ({"mode": "overwrite"}, "invalid_mode"),
        ({"source_space_id": "target"}, "same_source_target_space"),
        ({"object_types": ("memory",)}, "unsupported_object_type"),
        ({"object_ids": tuple(str(i) for i in range(10001))}, "invalid_request"),
        ({"source_partition_ids": tuple(str(i) for i in range(129))}, "invalid_request"),
        ({"filters": {"sql": "select *"}}, "invalid_request"),
        ({"filters": {"limit": 10001}}, "invalid_request"),
        ({"conflict_policy": "replace"}, "invalid_request"),
    ],
)
def test_rq04_rq11_invalid_requests(overrides, code) -> None:
    with pytest.raises(MemoryTransferError, match=code):
        request(**overrides).canonical()


def test_rq12_audit_rejects_sensitive_keys() -> None:
    with pytest.raises(MemoryTransferError):
        safe_audit_details({"prompt": "secret"})
    assert safe_audit_details({"item_count": 2, "mode": "copy"}) == '{"item_count":2,"mode":"copy"}'


def test_rq13_models_create_all() -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with engine.begin() as connection:
        tables = {row[0] for row in connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"memory_transfer_jobs", "memory_transfer_items", "memory_transfer_lineage"} <= tables
