"""CL01/EX07: item operation keys remain stable across retries."""

from src.workspaces.memory_transfer_models import TransferCreateRequest


def test_canonical_payload_excludes_client_nonce_and_key():
    base = dict(
        mode="copy", source_space_id="s", source_partition_ids=("p",),
        target_space_id="t", target_partition_id="q", object_types=("paragraph",),
    )
    first = TransferCreateRequest(**base, idempotency_key="one", client_nonce="a")
    second = TransferCreateRequest(**base, idempotency_key="two", client_nonce="b")
    assert first.payload_hash() == second.payload_hash()
