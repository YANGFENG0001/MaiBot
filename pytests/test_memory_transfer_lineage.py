"""Lineage hash is stable and bounded by the Phase 6 depth limit."""

from src.workspaces.memory_transfer_models import LINEAGE_MAX_DEPTH, canonical_hash


def test_lineage_constants_and_hash_are_stable():
    assert LINEAGE_MAX_DEPTH == 32
    payload = {"object": "p1", "partitions": ["a", "b"], "depth": 2}
    assert canonical_hash(payload) == canonical_hash(dict(reversed(list(payload.items()))))
