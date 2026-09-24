"""Database repository and CAS state machine for MemoryTransfer."""

from __future__ import annotations

from datetime import datetime, timedelta
from hashlib import sha256
from typing import Iterable, Optional
from uuid import uuid4

import json
import secrets

from sqlalchemy import exists, or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, delete, select, update

from src.common.database.database import get_db_session
from src.common.database.database_model import (
    MemoryTransferApproval,
    MemoryTransferAttempt,
    MemoryTransferEvent,
    MemoryTransferItem,
    MemoryTransferJob,
    MemoryTransferLineage,
)

from .memory_transfer_principal import namespaced_idempotency_key, principal_id
from .memory_transfer_models import (
    MemoryTransferError,
    TransferCreateRequest,
    safe_audit_details,
    sanitize_comment,
)

TRANSITIONS = {
    "pending": {"planning", "cancelled"},
    "planning": {"awaiting_approval", "approved", "failed"},
    "awaiting_approval": {"approved", "rejected", "cancelled"},
    "approved": {"running", "cancelled", "stale_policy", "awaiting_approval"},
    "running": {"completed", "partial", "failed", "retry_wait", "cancelled", "stale_policy"},
    "retry_wait": {"running", "failed", "stale_policy", "cancelled"},
    "stale_policy": {"planning"},
}
TERMINAL_JOB_STATES = frozenset({"completed", "partial", "failed", "cancelled", "rejected"})


class MemoryTransferRepository:
    def __init__(self, session_factory=get_db_session) -> None:
        self.session_factory = session_factory

    @staticmethod
    def _detach(session, value):
        session.expunge(value)
        return value

    @staticmethod
    def _idempotency_key(request: TransferCreateRequest, actor: str) -> str:
        explicit = request.idempotency_key.strip()
        if explicit:
            if len(explicit) > 128:
                raise MemoryTransferError("invalid_request")
            return explicit
        nonce = request.client_nonce.strip()
        if not nonce:
            raise MemoryTransferError("idempotency_key_required")
        return sha256(f"{actor}:{request.payload_hash()}:{nonce}".encode("utf-8")).hexdigest()

    def create(self, request: TransferCreateRequest, context, actor: str) -> tuple[MemoryTransferJob, bool]:
        canonical = request.canonical()
        key = namespaced_idempotency_key(context, request.idempotency_key) or self._idempotency_key(request, actor)
        selection_json = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self.session_factory() as session:
            existing = session.exec(
                select(MemoryTransferJob).where(MemoryTransferJob.idempotency_key == key)
            ).first()
            if existing is not None:
                if existing.selection_json != selection_json:
                    raise MemoryTransferError(
                        "idempotency_key_reused_with_different_payload"
                    )
                return self._detach(session, existing), False
            job = MemoryTransferJob(
                id=uuid4().hex,
                source_space_id=canonical["source_space_id"],
                target_space_id=canonical["target_space_id"],
                mode=canonical["mode"],
                filters_json=json.dumps(canonical["filters"], ensure_ascii=False, sort_keys=True),
                selection_json=selection_json,
                approval_policy=canonical["approval_policy"],
                conflict_policy=canonical["conflict_policy"],
                status="pending",
                created_by=str(actor),
                workspace_id=context.workspace_id or None,
                principal_id=principal_id(context),
                bot_profile_id=context.active_bot_profile_id or None,
                permission_group_id=context.permission_group_id or None,
                security_domain=context.security_domain,
                idempotency_key=key,
            )
            session.add(job)
            try:
                session.flush()
                self._add_event(
                    session, job.id, "created", actor_id=actor,
                    to_status="pending", details={"mode": job.mode},
                )
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                existing = session.exec(
                    select(MemoryTransferJob).where(MemoryTransferJob.idempotency_key == key)
                ).first()
                if existing is None:
                    raise
                if existing.selection_json != selection_json:
                    raise MemoryTransferError(
                        "idempotency_key_reused_with_different_payload"
                    ) from exc
                return self._detach(session, existing), False
            session.refresh(job)
            return self._detach(session, job), True

    def replace_selection_for_replan(
        self,
        job_id: str,
        request: TransferCreateRequest,
        *,
        actor: str,
    ) -> MemoryTransferJob:
        canonical = request.canonical()
        selection_json = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self.session_factory() as session:
            job = session.get(MemoryTransferJob, job_id)
            if job is None:
                raise MemoryTransferError("job_not_found")
            if job.status != "awaiting_approval" or job.approval_state != "pending":
                raise MemoryTransferError("invalid_state_transition")
            before = job.status
            job.source_space_id = canonical["source_space_id"]
            job.target_space_id = canonical["target_space_id"]
            job.mode = canonical["mode"]
            job.filters_json = json.dumps(canonical["filters"], ensure_ascii=False, sort_keys=True)
            job.selection_json = selection_json
            job.approval_policy = canonical["approval_policy"]
            job.conflict_policy = canonical["conflict_policy"]
            job.status = "stale_policy"
            job.approval_state = "pending"
            job.plan_hash = ""
            job.policy_snapshot_hash = ""
            job.source_snapshot_json = "{}"
            job.target_snapshot_json = "{}"
            job.policy_snapshot_json = "{}"
            job.updated_at = datetime.now()
            self._add_event(
                session,
                job.id,
                "selection_replaced",
                actor_id=actor,
                from_status=before,
                to_status="stale_policy",
                details={"plan_hash": request.payload_hash(), "status": "stale_policy"},
            )
            session.commit()
            session.refresh(job)
            return self._detach(session, job)

    def get(self, job_id: str) -> MemoryTransferJob:
        with self.session_factory() as session:
            job = session.get(MemoryTransferJob, job_id)
            if job is None:
                raise MemoryTransferError("job_not_found")
            return self._detach(session, job)

    def list_jobs(
        self, *, limit: int = 100, offset: int = 0, principal: str = "",
        workspace_id: str = "", bot_profile_id: str = "", permission_group_id: str = "",
        security_domain: str = "normal",
    ) -> list[MemoryTransferJob]:
        safe_limit = min(max(int(limit), 1), 100)
        safe_offset = max(int(offset), 0)
        statement = select(MemoryTransferJob)
        if principal:
            statement = statement.where(
                or_(MemoryTransferJob.principal_id == principal, MemoryTransferJob.selection_json == "{}")
            )
        if workspace_id:
            statement = statement.where(MemoryTransferJob.workspace_id == workspace_id)
        if bot_profile_id:
            statement = statement.where(MemoryTransferJob.bot_profile_id == bot_profile_id)
        if permission_group_id:
            statement = statement.where(MemoryTransferJob.permission_group_id == permission_group_id)
        statement = statement.where(MemoryTransferJob.security_domain == security_domain)
        with self.session_factory() as session:
            rows = session.exec(
                statement.order_by(col(MemoryTransferJob.created_at).desc())
                .offset(safe_offset).limit(safe_limit)
            ).all()
            for row in rows:
                session.expunge(row)
            return list(rows)

    def transition(
        self,
        job_id: str,
        to_status: str,
        *,
        actor: str = "system",
        error_code: str = "",
        approval_state: Optional[str] = None,
        expected: Optional[set[str]] = None,
        retry_at: Optional[datetime] = None,
    ) -> MemoryTransferJob:
        with self.session_factory() as session:
            job = session.get(MemoryTransferJob, job_id)
            if job is None:
                raise MemoryTransferError("job_not_found")
            allowed_from = expected if expected is not None else {job.status}
            before = job.status
            if before not in allowed_from or to_status not in TRANSITIONS.get(before, set()):
                raise MemoryTransferError("invalid_state_transition")
            now = datetime.now()
            values = {
                "status": to_status, "updated_at": now, "last_error_code": error_code,
                "last_error_detail": "", "next_retry_at": retry_at,
            }
            if approval_state is not None:
                values["approval_state"] = approval_state
            if to_status == "running" and job.started_at is None:
                values["started_at"] = now
            if to_status in {"completed", "partial", "failed"}:
                values["completed_at"] = now
            if to_status == "cancelled":
                values["cancelled_at"] = now
            result = session.exec(
                update(MemoryTransferJob)
                .where(MemoryTransferJob.id == job_id, MemoryTransferJob.status == before)
                .values(**values)
            )
            if result.rowcount != 1:
                session.rollback()
                raise MemoryTransferError("invalid_state_transition")
            self._add_event(
                session, job_id, "state", actor_id=actor, from_status=before,
                to_status=to_status, result_code=error_code,
            )
            session.commit()
            session.expire_all()
            current = session.get(MemoryTransferJob, job_id)
            if current is None:
                raise MemoryTransferError("job_not_found")
            return self._detach(session, current)

    def replace_plan(
        self,
        job_id: str,
        items: Iterable[MemoryTransferItem],
        *,
        plan_hash: str,
        source_snapshot: dict,
        target_snapshot: dict,
        policy_snapshot: dict,
        approval_required: bool,
        policy_revision: int,
        policy_snapshot_hash: str = "",
        source_revision: int = 1,
        target_revision: int = 1,
        actor: str = "system",
    ) -> MemoryTransferJob:
        item_list = list(items)
        with self.session_factory() as session:
            job = session.get(MemoryTransferJob, job_id)
            if job is None or job.status != "planning":
                raise MemoryTransferError("invalid_state_transition")
            next_plan_revision = int(job.plan_revision or 0) + 1
            for old in session.exec(
                select(MemoryTransferItem).where(
                    MemoryTransferItem.job_id == job_id,
                    MemoryTransferItem.superseded == False,  # noqa: E712
                )
            ).all():
                old.superseded = True
                if old.status in {"planned", "conflict", "skipped"}:
                    old.status = "superseded"
                old.updated_at = datetime.now()
            for item in item_list:
                item.plan_revision = next_plan_revision
                item.superseded = False
                session.add(item)
            job.plan_hash = plan_hash
            job.policy_snapshot_hash = policy_snapshot_hash or sha256(
                json.dumps(policy_snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            job.plan_revision = next_plan_revision
            job.source_snapshot_json = json.dumps(source_snapshot, sort_keys=True, separators=(",", ":"))
            job.target_snapshot_json = json.dumps(target_snapshot, sort_keys=True, separators=(",", ":"))
            job.policy_snapshot_json = json.dumps(policy_snapshot, sort_keys=True, separators=(",", ":"))
            job.source_space_revision = source_revision
            job.target_space_revision = target_revision
            job.approval_required = approval_required
            job.approval_state = "pending" if approval_required else "not_required"
            job.status = "awaiting_approval" if approval_required else "approved"
            job.updated_at = datetime.now()
            self._add_event(
                session,
                job.id,
                "planned",
                actor_id=actor,
                from_status="planning",
                to_status=job.status,
                details={"item_count": len(item_list), "policy_revision": policy_revision},
            )
            session.commit()
            session.refresh(job)
            return self._detach(session, job)

    def items(
        self,
        job_id: str,
        status: Optional[str] = None,
        *,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> list[MemoryTransferItem]:
        with self.session_factory() as session:
            statement = select(MemoryTransferItem).where(
                MemoryTransferItem.job_id == job_id,
                MemoryTransferItem.superseded == False,  # noqa: E712
            )
            if status:
                statement = statement.where(MemoryTransferItem.status == status)
            statement = statement.order_by(
                MemoryTransferItem.plan_revision,
                MemoryTransferItem.object_type,
                MemoryTransferItem.source_object_id,
                MemoryTransferItem.source_partition_id,
                MemoryTransferItem.target_partition_id,
                MemoryTransferItem.operation_key,
                MemoryTransferItem.id,
            )
            if limit is not None:
                statement = statement.offset(max(offset, 0)).limit(min(max(limit, 1), 100))
            rows = session.exec(statement).all()
            for row in rows:
                session.expunge(row)
            return list(rows)

    @staticmethod
    def _lineage_edge_key(object_type: str, object_id: str, source_partition_id: str, target_partition_id: str) -> str:
        left, right = sorted((str(source_partition_id), str(target_partition_id)))
        return f"{object_type}:{object_id}:{left}<->{right}"

    def reserve_lineage_edge(self, item: MemoryTransferItem) -> bool:
        """Reserve an undirected lineage pair with a single SQL uniqueness claim."""
        edge_key = self._lineage_edge_key(
            item.root_object_type or item.object_type, item.root_object_id or item.source_object_id,
            item.source_partition_id, item.target_partition_id
        )
        with self.session_factory() as session:
            existing = session.exec(
                select(MemoryTransferLineage).where(MemoryTransferLineage.edge_key == edge_key)
            ).first()
            if existing is not None:
                return existing.item_id == item.id
            session.add(
                MemoryTransferLineage(
                    id=uuid4().hex, item_id=item.id, job_id=item.job_id, relation_type="reservation",
                    object_type=item.object_type, object_id=item.source_object_id,
                    source_object_type=item.object_type, source_object_id=item.source_object_id,
                    source_space_id=item.source_space_id, source_partition_id=item.source_partition_id,
                    target_space_id=item.target_space_id, target_partition_id=item.target_partition_id,
                    root_object_type=item.root_object_type or item.object_type,
                    root_object_id=item.root_object_id or item.source_object_id,
                    depth=0, ancestry_hash=item.ancestry_hash, edge_key=edge_key,
                )
            )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                existing = session.exec(
                    select(MemoryTransferLineage).where(MemoryTransferLineage.edge_key == edge_key)
                ).first()
                return existing is not None and existing.item_id == item.id
            return True

    def release_lineage_reservation(self, item_id: str) -> None:
        with self.session_factory() as session:
            session.exec(
                select(MemoryTransferLineage).where(
                    MemoryTransferLineage.item_id == item_id,
                    MemoryTransferLineage.relation_type == "reservation",
                )
            )
            session.exec(
                delete(MemoryTransferLineage).where(
                    MemoryTransferLineage.item_id == item_id,
                    MemoryTransferLineage.relation_type == "reservation",
                )
            )
            session.commit()

    def lineage_for(self, object_type: str, object_id: str) -> list[MemoryTransferLineage]:
        with self.session_factory() as session:
            rows = session.exec(
                select(MemoryTransferLineage).where(
                    (MemoryTransferLineage.object_type == object_type)
                    & (MemoryTransferLineage.object_id == object_id)
                    | (MemoryTransferLineage.source_object_type == object_type)
                    & (MemoryTransferLineage.source_object_id == object_id)
                    | (MemoryTransferLineage.root_object_type == object_type)
                    & (MemoryTransferLineage.root_object_id == object_id)
                )
            ).all()
            for row in rows:
                session.expunge(row)
            return list(rows)

    def directed_lineage_state(
        self,
        *,
        root_object_type: str,
        root_object_id: str,
        source_partition_id: str,
        target_partition_id: str,
    ) -> tuple[bool, int, tuple[str, ...]]:
        """Return whether source->target would cycle, current path depth and ancestors."""
        with self.session_factory() as session:
            rows = session.exec(
                select(MemoryTransferLineage).where(
                    MemoryTransferLineage.root_object_type == root_object_type,
                    MemoryTransferLineage.root_object_id == root_object_id,
                )
            ).all()
        adjacency: dict[str, set[str]] = {}
        for edge in rows:
            adjacency.setdefault(edge.source_partition_id, set()).add(edge.target_partition_id)
        visited = {target_partition_id}
        frontier = [(target_partition_id, 0)]
        maximum_depth = 0
        while frontier:
            node, depth = frontier.pop(0)
            maximum_depth = max(maximum_depth, depth)
            if node == source_partition_id:
                return True, maximum_depth, tuple(sorted(visited))
            if depth >= 32:
                continue
            for child in sorted(adjacency.get(node, ())):
                if child not in visited:
                    visited.add(child)
                    frontier.append((child, depth + 1))
        reverse_adjacency: dict[str, set[str]] = {}
        for edge in rows:
            reverse_adjacency.setdefault(edge.target_partition_id, set()).add(edge.source_partition_id)
        source_ancestors = {source_partition_id}
        source_frontier = [(source_partition_id, 0)]
        source_depth = 0
        while source_frontier:
            node, depth = source_frontier.pop(0)
            source_depth = max(source_depth, depth)
            if depth >= 32:
                continue
            for parent in sorted(reverse_adjacency.get(node, ())):
                if parent not in source_ancestors:
                    source_ancestors.add(parent)
                    source_frontier.append((parent, depth + 1))
        return False, source_depth, tuple(sorted(source_ancestors))

    def reverse_edge_exists(
        self,
        *,
        object_type: str,
        object_id: str,
        source_partition_id: str,
        target_partition_id: str,
    ) -> bool:
        with self.session_factory() as session:
            row = session.exec(
                select(MemoryTransferLineage.id).where(
                    MemoryTransferLineage.object_type == object_type,
                    MemoryTransferLineage.object_id == object_id,
                    MemoryTransferLineage.source_partition_id == target_partition_id,
                    MemoryTransferLineage.target_partition_id == source_partition_id,
                )
            ).first()
            return row is not None

    def approve(
        self,
        job_id: str,
        *,
        plan_revision: int,
        plan_hash: str,
        policy_revision: int,
        policy_snapshot_hash: str,
        actor: str,
        approved: bool,
        comment: str = "",
    ) -> MemoryTransferJob:
        clean_comment = sanitize_comment(comment)
        decision = "approved" if approved else "rejected"
        opposite = "rejected" if approved else "approved"
        with self.session_factory() as session:
            job = session.get(MemoryTransferJob, job_id)
            if job is None:
                raise MemoryTransferError("job_not_found")
            if job.status not in {"awaiting_approval", "approved", "rejected"}:
                raise MemoryTransferError("invalid_state_transition")
            if job.status == opposite:
                raise MemoryTransferError("invalid_state_transition")
            if int(job.plan_revision) != int(plan_revision) or job.plan_hash != plan_hash:
                raise MemoryTransferError("approval_plan_mismatch")
            policy = json.loads(job.policy_snapshot_json or "{}")
            if int(policy.get("policy_revision", 0)) != int(policy_revision):
                raise MemoryTransferError("approval_policy_mismatch")
            expected_policy_hash = str(job.policy_snapshot_hash or policy.get("policy_snapshot_hash", ""))
            if str(policy_snapshot_hash) != expected_policy_hash:
                raise MemoryTransferError("approval_policy_mismatch")
            if job.mode == "promote" and approved and actor == job.created_by:
                raise MemoryTransferError("self_approval_denied")
            existing = session.exec(
                select(MemoryTransferApproval).where(
                    MemoryTransferApproval.job_id == job_id,
                    MemoryTransferApproval.plan_revision == plan_revision,
                    MemoryTransferApproval.plan_hash == plan_hash,
                    MemoryTransferApproval.policy_revision == policy_revision,
                    MemoryTransferApproval.policy_snapshot_hash == policy_snapshot_hash,
                    MemoryTransferApproval.actor_id == actor,
                    MemoryTransferApproval.decision == decision,
                )
            ).first()
            if job.status == decision:
                if existing is None:
                    raise MemoryTransferError("invalid_state_transition")
                return self._detach(session, job)
            if existing is None:
                session.add(
                    MemoryTransferApproval(
                        id=uuid4().hex,
                        job_id=job_id,
                        decision=decision,
                        actor_id=actor,
                        policy_revision=policy_revision,
                        policy_snapshot_hash=policy_snapshot_hash,
                        plan_revision=plan_revision,
                        plan_hash=plan_hash,
                        comment=clean_comment,
                    )
                )
            job.status = decision
            job.approval_state = decision
            job.last_error_code = ""
            job.updated_at = datetime.now()
            self._add_event(
                session,
                job.id,
                "approval",
                actor_id=actor,
                from_status="awaiting_approval",
                to_status=decision,
                result_code=decision,
                details={
                    "plan_revision": plan_revision,
                    "policy_revision": policy_revision,
                },
            )
            session.commit()
            session.refresh(job)
            return self._detach(session, job)

    def acquire_lease(
        self,
        job_id: str,
        *,
        worker_id: str,
        ttl_seconds: int = 60,
    ) -> str:
        raw = secrets.token_urlsafe(32)
        digest = sha256(raw.encode("utf-8")).hexdigest()
        now = datetime.now()
        with self.session_factory() as session:
            job = session.get(MemoryTransferJob, job_id)
            if job is None:
                raise MemoryTransferError("job_not_found")
            result = session.exec(
                update(MemoryTransferJob)
                .where(
                    MemoryTransferJob.id == job_id,
                    (MemoryTransferJob.lease_expires_at == None)  # noqa: E711
                    | (MemoryTransferJob.lease_expires_at <= now),
                )
                .values(
                    lease_token_hash=digest,
                    lease_expires_at=now + timedelta(seconds=max(ttl_seconds, 1)),
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                raise MemoryTransferError("lease_lost", retryable=True)
            job = session.get(MemoryTransferJob, job_id)
            if job is None:
                raise MemoryTransferError("job_not_found")
            session.add(
                MemoryTransferAttempt(
                    id=uuid4().hex,
                    job_id=job.id,
                    item_id=None,
                    attempt_no=job.retry_count + 1,
                    lease_token_hash=digest,
                    worker_id=worker_id,
                    status="running",
                )
            )
            session.commit()
        return raw

    def renew_lease(self, job_id: str, raw_token: str, *, ttl_seconds: int = 60) -> None:
        digest = sha256(raw_token.encode("utf-8")).hexdigest()
        now = datetime.now()
        with self.session_factory() as session:
            result = session.exec(
                update(MemoryTransferJob)
                .where(
                    MemoryTransferJob.id == job_id,
                    MemoryTransferJob.lease_token_hash == digest,
                    MemoryTransferJob.lease_expires_at > now,
                )
                .values(lease_expires_at=now + timedelta(seconds=max(ttl_seconds, 1)), updated_at=now)
            )
            if result.rowcount != 1:
                session.rollback()
                raise MemoryTransferError("lease_lost", retryable=True)
            session.commit()

    def release_lease(self, job_id: str, raw_token: str) -> None:
        digest = sha256(raw_token.encode("utf-8")).hexdigest()
        with self.session_factory() as session:
            result = session.exec(
                update(MemoryTransferJob)
                .where(MemoryTransferJob.id == job_id, MemoryTransferJob.lease_token_hash == digest)
                .values(lease_token_hash=None, lease_expires_at=None, updated_at=datetime.now())
            )
            if result.rowcount != 1:
                session.rollback()
                raise MemoryTransferError("lease_lost", retryable=True)
            attempt = session.exec(
                select(MemoryTransferAttempt)
                .where(
                    MemoryTransferAttempt.job_id == job_id,
                    MemoryTransferAttempt.item_id == None,  # noqa: E711
                    MemoryTransferAttempt.lease_token_hash == digest,
                    MemoryTransferAttempt.status == "running",
                )
                .order_by(col(MemoryTransferAttempt.started_at).desc())
            ).first()
            if attempt is not None:
                attempt.status = "finished"
                attempt.finished_at = datetime.now()
            session.commit()

    def claim_item(self, item_id: str, *, worker_id: str, lease_token: str) -> MemoryTransferItem:
        digest = sha256(lease_token.encode("utf-8")).hexdigest()
        with self.session_factory() as session:
            item = session.get(MemoryTransferItem, item_id)
            if item is None:
                raise MemoryTransferError("source_not_found")
            job = session.get(MemoryTransferJob, item.job_id)
            if job is None or job.lease_token_hash != digest or not job.lease_expires_at or job.lease_expires_at <= datetime.now():
                raise MemoryTransferError("lease_lost", retryable=True)
            if item.status not in {"planned", "failed_retryable"}:
                raise MemoryTransferError("invalid_state_transition")
            before = item.status
            result = session.exec(
                update(MemoryTransferItem)
                .where(
                    MemoryTransferItem.id == item_id,
                    MemoryTransferItem.status == before,
                    exists().where(
                        MemoryTransferJob.id == item.job_id,
                        MemoryTransferJob.lease_token_hash == digest,
                        MemoryTransferJob.lease_expires_at > datetime.now(),
                    ),
                )
                .values(
                    status="running",
                    attempt_count=MemoryTransferItem.attempt_count + 1,
                    error_code="",
                    updated_at=datetime.now(),
                )
            )
            if result.rowcount != 1:
                raise MemoryTransferError("lease_lost", retryable=True)
            item = session.get(MemoryTransferItem, item_id)
            if item is None:
                raise MemoryTransferError("source_not_found")
            session.add(
                MemoryTransferAttempt(
                    id=uuid4().hex,
                    job_id=item.job_id,
                    item_id=item.id,
                    attempt_no=item.attempt_count,
                    lease_token_hash=digest,
                    worker_id=worker_id,
                    status="running",
                )
            )
            self._add_event(
                session,
                item.job_id,
                "item_claimed",
                item_id=item.id,
                actor_id=worker_id,
                from_status=before,
                to_status="running",
                details={"attempt": item.attempt_count},
            )
            session.commit()
            session.refresh(item)
            return self._detach(session, item)

    def finish_item(self, item_id: str, result: dict, *, actor: str = "system") -> MemoryTransferItem:
        with self.session_factory() as session:
            item = session.get(MemoryTransferItem, item_id)
            if item is None:
                raise MemoryTransferError("source_not_found")
            target_id = str(result.get("target_object_id") or item.source_object_id).strip()
            if not target_id:
                raise MemoryTransferError("object_identity_conflict")
            item.target_object_id = target_id
            item.status = "already_applied" if result.get("status") == "already_applied" else "completed"
            item.error_code = ""
            item.error_detail = ""
            item.updated_at = datetime.now()
            existing = session.exec(
                select(MemoryTransferLineage).where(
                    MemoryTransferLineage.item_id == item.id,
                    MemoryTransferLineage.relation_type == "reservation",
                )
            ).first()
            if existing is not None:
                existing.relation_type = item.mode
                existing.object_id = target_id
                existing.depth = int(existing.depth or 0) + 1
            else:
                parent_depth = session.exec(
                    select(MemoryTransferLineage.depth)
                    .where(
                        MemoryTransferLineage.object_type == item.object_type,
                        MemoryTransferLineage.object_id == item.source_object_id,
                    )
                    .order_by(col(MemoryTransferLineage.depth).desc())
                ).first()
                session.add(
                    MemoryTransferLineage(
                        id=uuid4().hex,
                        item_id=item.id,
                        job_id=item.job_id,
                        relation_type=item.mode,
                        object_type=item.object_type,
                        object_id=target_id,
                        source_object_type=item.object_type,
                        source_object_id=item.source_object_id,
                        source_space_id=item.source_space_id,
                        source_partition_id=item.source_partition_id,
                        target_space_id=item.target_space_id,
                        target_partition_id=item.target_partition_id,
                        root_object_type=item.root_object_type or item.object_type,
                        root_object_id=item.root_object_id or item.source_object_id,
                        depth=int(parent_depth or 0) + 1,
                        ancestry_hash=item.ancestry_hash,
                        edge_key=self._lineage_edge_key(
                            item.root_object_type or item.object_type,
                            item.root_object_id or item.source_object_id,
                            item.source_partition_id, item.target_partition_id,
                        ),
                    )
                )
            self._finish_attempt(session, item, item.status, "")
            self._add_event(
                session,
                item.job_id,
                "item_finished",
                item_id=item.id,
                actor_id=actor,
                from_status="running",
                to_status=item.status,
                result_code=item.status,
            )
            session.commit()
            session.refresh(item)
            return self._detach(session, item)

    def mark_conflict(self, item_id: str, code: str, *, skipped: bool) -> MemoryTransferItem:
        with self.session_factory() as session:
            item = session.get(MemoryTransferItem, item_id)
            if item is None:
                raise MemoryTransferError("source_not_found")
            item.conflict_code = code
            item.status = "skipped" if skipped else "conflict"
            item.updated_at = datetime.now()
            session.commit()
            session.refresh(item)
            return self._detach(session, item)

    def fail_item(self, item_id: str, code: str, retryable: bool, *, actor: str = "system") -> None:
        with self.session_factory() as session:
            item = session.get(MemoryTransferItem, item_id)
            if item is None:
                raise MemoryTransferError("source_not_found")
            before = item.status
            item.status = "failed_retryable" if retryable else "failed_permanent"
            item.error_code = code
            item.error_detail = ""
            item.updated_at = datetime.now()
            self._finish_attempt(session, item, item.status, code)
            self._add_event(
                session,
                item.job_id,
                "item_failed",
                item_id=item.id,
                actor_id=actor,
                from_status=before,
                to_status=item.status,
                result_code=code,
                details={"attempt": item.attempt_count},
            )
            session.commit()

    def revoke_approval(
        self,
        job_id: str,
        *,
        plan_revision: int,
        plan_hash: str,
        policy_revision: int,
        policy_snapshot_hash: str,
        actor: str,
        comment: str = "",
    ) -> MemoryTransferJob:
        sanitize_comment(comment)
        with self.session_factory() as session:
            job = session.get(MemoryTransferJob, job_id)
            if job is None:
                raise MemoryTransferError("job_not_found")
            if job.status != "approved" or job.approval_state != "approved":
                raise MemoryTransferError("invalid_state_transition")
            if int(job.plan_revision) != int(plan_revision) or job.plan_hash != plan_hash:
                raise MemoryTransferError("approval_plan_mismatch")
            policy = json.loads(job.policy_snapshot_json or "{}")
            if int(policy.get("policy_revision", 0)) != int(policy_revision):
                raise MemoryTransferError("approval_policy_mismatch")
            expected_policy_hash = str(job.policy_snapshot_hash or policy.get("policy_snapshot_hash", ""))
            if str(policy_snapshot_hash) != expected_policy_hash:
                raise MemoryTransferError("approval_policy_mismatch")
            result = session.exec(
                update(MemoryTransferJob)
                .where(
                    MemoryTransferJob.id == job_id,
                    MemoryTransferJob.status == "approved",
                    MemoryTransferJob.approval_state == "approved",
                    MemoryTransferJob.plan_revision == plan_revision,
                    MemoryTransferJob.plan_hash == plan_hash,
                    MemoryTransferJob.policy_snapshot_hash == policy_snapshot_hash,
                )
                .values(
                    status="awaiting_approval", approval_state="revoked",
                    updated_at=datetime.now(), last_error_code="approval_revoked",
                )
            )
            if result.rowcount != 1:
                session.rollback()
                raise MemoryTransferError("invalid_state_transition")
            self._add_event(
                session,
                job_id,
                "approval_revoked",
                actor_id=actor,
                from_status="approved",
                to_status="awaiting_approval",
                result_code="approval_revoked",
                details={
                    "plan_revision": plan_revision,
                    "policy_revision": policy_revision,
                },
            )
            session.commit()
            current = session.get(MemoryTransferJob, job_id)
            if current is None:
                raise MemoryTransferError("job_not_found")
            return self._detach(session, current)

    def request_cancel(self, job_id: str, *, actor: str) -> MemoryTransferJob:
        with self.session_factory() as session:
            job = session.get(MemoryTransferJob, job_id)
            if job is None:
                raise MemoryTransferError("job_not_found")
            if job.status not in {"pending", "awaiting_approval", "approved", "retry_wait", "running"}:
                raise MemoryTransferError("invalid_state_transition")
            before = bool(job.cancel_requested)
            result = session.exec(
                update(MemoryTransferJob)
                .where(
                    MemoryTransferJob.id == job_id,
                    MemoryTransferJob.status == job.status,
                    MemoryTransferJob.cancel_requested == before,
                )
                .values(cancel_requested=True, updated_at=datetime.now())
            )
            if result.rowcount != 1:
                session.rollback()
                raise MemoryTransferError("invalid_state_transition")
            self._add_event(
                session, job_id, "cancel_requested", actor_id=actor,
                from_status=job.status, to_status=job.status,
            )
            session.commit()
            session.expire_all()
            current = session.get(MemoryTransferJob, job_id)
            if current is None:
                raise MemoryTransferError("job_not_found")
            return self._detach(session, current)

    def cancel_unstarted_items(self, job_id: str, *, actor: str, include_retryable: bool = False) -> int:
        count = 0
        statuses = ["planned", "conflict"]
        if include_retryable:
            statuses.append("failed_retryable")
        with self.session_factory() as session:
            for item in session.exec(
                select(MemoryTransferItem).where(
                    MemoryTransferItem.job_id == job_id,
                    col(MemoryTransferItem.status).in_(statuses),
                )
            ).all():
                item.status = "cancelled"
                item.updated_at = datetime.now()
                count += 1
            self._add_event(session, job_id, "items_cancelled", actor_id=actor, details={"item_count": count})
            session.commit()
        return count

    def cancel_item(self, item_id: str, *, actor: str) -> MemoryTransferItem:
        with self.session_factory() as session:
            item = session.get(MemoryTransferItem, item_id)
            if item is None:
                raise MemoryTransferError("source_not_found")
            if item.status not in {"planned", "failed_retryable", "conflict"}:
                raise MemoryTransferError("invalid_state_transition")
            before = item.status
            item.status = "cancelled"
            item.updated_at = datetime.now()
            self._add_event(
                session, item.job_id, "item_cancelled", item_id=item.id, actor_id=actor,
                from_status=before, to_status="cancelled",
            )
            session.commit()
            session.refresh(item)
            return self._detach(session, item)

    def schedule_retry(self, job_id: str, *, error_code: str) -> MemoryTransferJob:
        with self.session_factory() as session:
            job = session.get(MemoryTransferJob, job_id)
            if job is None:
                raise MemoryTransferError("job_not_found")
            if job.status != "running":
                raise MemoryTransferError("invalid_state_transition")
            job.retry_count += 1
            if job.retry_count > job.max_retries:
                active_items = session.exec(
                    select(MemoryTransferItem).where(
                        MemoryTransferItem.job_id == job_id,
                        MemoryTransferItem.superseded == False,  # noqa: E712
                    )
                ).all()
                exhausted = [item for item in active_items if item.status == "failed_retryable"]
                for item in exhausted:
                    item.status = "failed_permanent"
                    item.updated_at = datetime.now()
                    attempt = session.exec(
                        select(MemoryTransferAttempt)
                        .where(MemoryTransferAttempt.item_id == item.id)
                        .order_by(col(MemoryTransferAttempt.attempt_no).desc())
                    ).first()
                    if attempt is not None:
                        attempt.status = "failed_permanent"
                        attempt.finished_at = attempt.finished_at or datetime.now()
                    self._add_event(
                        session,
                        job.id,
                        "retry_exhausted",
                        item_id=item.id,
                        from_status="failed_retryable",
                        to_status="failed_permanent",
                        result_code=item.error_code or error_code,
                        details={"retry_count": job.retry_count},
                    )
                completed = any(
                    item.status in {"completed", "already_applied", "skipped"}
                    for item in active_items
                )
                target = "partial" if completed else "failed"
                retry_at = None
            else:
                target = "retry_wait"
                jitter = int(sha256(job.id.encode("utf-8")).hexdigest()[:4], 16) % 3
                delay = min(300, 2 ** job.retry_count * 2 + jitter)
                retry_at = datetime.now() + timedelta(seconds=delay)
            before = job.status
            job.status = target
            job.next_retry_at = retry_at
            job.last_error_code = error_code
            job.updated_at = datetime.now()
            if target in {"failed", "partial"}:
                job.completed_at = datetime.now()
            self._add_event(
                session,
                job.id,
                "retry_scheduled",
                from_status=before,
                to_status=target,
                result_code=error_code,
                details={"retry_count": job.retry_count},
            )
            session.commit()
            session.refresh(job)
            return self._detach(session, job)

    def summarize(self, job_id: str, *, actor: str = "system") -> MemoryTransferJob:
        items = self.items(job_id)
        done = {"completed", "already_applied", "skipped"}
        if not items:
            return self.transition(job_id, "completed", actor=actor, error_code="empty_plan")
        if all(item.status in done for item in items):
            return self.transition(job_id, "completed", actor=actor)
        completed = any(item.status in done for item in items)
        retryable = any(item.status in {"failed_retryable", "running"} for item in items)
        # A completed item does not make a job terminal while another item can still retry.
        if retryable:
            return self.schedule_retry(job_id, error_code="upstream_unavailable")
        if completed:
            return self.transition(job_id, "partial", actor=actor, error_code="partial")
        return self.transition(job_id, "failed", actor=actor, error_code="failed")

    @staticmethod
    def _finish_attempt(session, item: MemoryTransferItem, status: str, error_code: str) -> None:
        attempt = session.exec(
            select(MemoryTransferAttempt).where(
                MemoryTransferAttempt.item_id == item.id,
                MemoryTransferAttempt.attempt_no == item.attempt_count,
            )
        ).first()
        if attempt is not None:
            attempt.status = status
            attempt.error_code = error_code
            attempt.finished_at = datetime.now()

    @staticmethod
    def _add_event(
        session,
        job_id: str,
        event_type: str,
        *,
        item_id: Optional[str] = None,
        actor_id: str = "system",
        from_status: str = "",
        to_status: str = "",
        result_code: str = "",
        details: Optional[dict] = None,
    ) -> None:
        session.add(
            MemoryTransferEvent(
                id=uuid4().hex,
                job_id=job_id,
                item_id=item_id,
                event_type=event_type,
                actor_id=str(actor_id),
                from_status=from_status,
                to_status=to_status,
                result_code=result_code,
                details_json=safe_audit_details(details or {}),
            )
        )
