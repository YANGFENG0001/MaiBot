"""Authenticated backend API contract for Phase 6 MemoryTransfer."""

from datetime import datetime
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.webui.dependencies import require_auth, require_auth_with_rate_limit
from src.webui.routers import memory_transfers


class FakeService:
    def __init__(self):
        self.calls = []
        self.job = SimpleNamespace(
            id="job-1", status="pending", mode="copy", source_space_id="source",
            target_space_id="target", approval_required=True, approval_state="pending",
            plan_hash="plan", source_space_revision=1, target_space_revision=1,
            retry_count=0, max_retries=3, next_retry_at=None, last_error_code="",
            policy_snapshot_hash="policy", plan_revision=1,
            created_at=datetime(2026, 9, 1), updated_at=datetime(2026, 9, 1),
        )
        self.item = SimpleNamespace(
            id="item-1", job_id="job-1", mode="copy", object_type="paragraph",
            source_object_id="source-1", target_object_id=None, source_space_id="source",
            source_partition_id="p-source", target_space_id="target", target_partition_id="p-target",
            status="planned", conflict_code="", error_code="", attempt_count=0,
            created_at=datetime(2026, 9, 1), updated_at=datetime(2026, 9, 1),
        )

    def create_job(self, request, request_context, actor):
        self.calls.append(("create", actor, request_context, request))
        return self.job

    def list_jobs(self, actor, **kwargs):
        self.calls.append(("list", actor, kwargs))
        return [self.job]

    def get_job(self, job_id, actor, **kwargs):
        self.calls.append(("get", actor, kwargs))
        if kwargs.get("include_items"):
            return self.job, [self.item]
        return self.job

    def list_items(self, job_id, actor, *args, **kwargs):
        self.calls.append(("items", actor, kwargs))
        return [self.item]

    async def plan_job(self, job_id, context, actor):
        self.calls.append(("plan", actor, context))
        return self.job

    async def execute_job(self, job_id, context, actor):
        self.calls.append(("execute", actor, context))
        return self.job

    async def retry_job(self, job_id, context, actor):
        self.calls.append(("retry", actor, context))
        return self.job

    async def reconcile_job(self, job_id, context, actor):
        self.calls.append(("reconcile", actor, context))
        return self.job

    def approve_job(self, job_id, approval, actor, request_context=None):
        self.calls.append(("approve", actor, request_context, approval))
        return self.job

    def reject_job(self, job_id, approval, actor, request_context=None):
        self.calls.append(("reject", actor, request_context, approval))
        return self.job

    def cancel_job(self, job_id, actor, request_context=None):
        self.calls.append(("cancel", actor, request_context))
        return self.job


def client(monkeypatch):
    fake = FakeService()
    context = SimpleNamespace(workspace_id="workspace", permission_group_id="group")
    monkeypatch.setattr(memory_transfers, "memory_transfer_service", fake)
    monkeypatch.setattr(memory_transfers, "_context", lambda scope: context)
    app = FastAPI()
    app.include_router(memory_transfers.router, prefix="/api/webui")
    app.dependency_overrides[require_auth] = lambda: "real-secret-token"
    app.dependency_overrides[require_auth_with_rate_limit] = lambda: "real-secret-token"
    return TestClient(app), fake


def scope():
    return {"session_id": "session", "person_id": "person", "audience_type": "private"}


def create_payload():
    return {
        **scope(), "mode": "copy", "source_space_id": "source",
        "source_partition_ids": ["p-source"], "target_space_id": "target",
        "target_partition_id": "p-target", "object_types": ["paragraph"],
        "idempotency_key": "request-key",
    }


def test_api_requires_auth_without_override() -> None:
    app = FastAPI()
    app.include_router(memory_transfers.router, prefix="/api/webui")
    response = TestClient(app).get(
        "/api/webui/memory-transfers",
        params={"session_id": "s", "person_id": "p"},
    )
    assert response.status_code == 401


def test_create_list_get_and_items_are_authenticated_and_body_free(monkeypatch) -> None:
    api, fake = client(monkeypatch)
    assert api.post("/api/webui/memory-transfers", json=create_payload()).status_code == 201
    query = {"session_id": "session", "person_id": "person"}
    assert api.get("/api/webui/memory-transfers", params=query).status_code == 200
    assert api.get("/api/webui/memory-transfers/job-1", params=query).status_code == 200
    result = api.get("/api/webui/memory-transfers/job-1/items", params=query)
    assert result.status_code == 200
    payload = result.json()
    assert "content" not in str(payload).lower()
    actors = [call[1] for call in fake.calls]
    assert all(actor and "real-secret-token" not in actor for actor in actors)
    assert len(set(actors)) == 1


def test_all_mutation_actions_use_verified_context_and_service(monkeypatch) -> None:
    api, fake = client(monkeypatch)
    for action in ("plan", "execute", "retry", "reconcile", "cancel"):
        response = api.post(f"/api/webui/memory-transfers/job-1/{action}", json=scope())
        assert response.status_code in {200, 202}
    approval = {
        **scope(),
        "plan_hash": "plan",
        "plan_revision": 1,
        "policy_revision": 1,
        "policy_snapshot_hash": "policy",
        "comment": "routine",
    }
    assert api.post("/api/webui/memory-transfers/job-1/approve", json=approval).status_code == 200
    assert api.post("/api/webui/memory-transfers/job-1/reject", json=approval).status_code == 200
    assert {call[0] for call in fake.calls} == {
        "plan", "execute", "retry", "reconcile", "cancel", "approve", "reject",
    }


def test_api_enforces_pagination_and_does_not_accept_actor(monkeypatch) -> None:
    api, fake = client(monkeypatch)
    response = api.get(
        "/api/webui/memory-transfers",
        params={"session_id": "session", "person_id": "person", "limit": 101},
    )
    assert response.status_code == 422
    payload = {**create_payload(), "created_by": "attacker"}
    response = api.post("/api/webui/memory-transfers", json=payload)
    assert response.status_code == 422
    for forged in ("approved_by", "actor"):
        response = api.post("/api/webui/memory-transfers", json={**create_payload(), forged: "attacker"})
        assert response.status_code == 422
