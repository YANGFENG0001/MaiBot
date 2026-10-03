"""记忆访问审计、Bot 控制审计与 Kami 管理 API。

审计默认遮蔽敏感字段：查询哈希被截断、metadata 不返回；任何接口都不返回记忆正文或消息正文。
"""

from datetime import datetime
from hashlib import sha256
from typing import Any, Optional

import json

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlmodel import col, select

from src.common.database.database import get_db_session
from src.common.database.database_model import BotControlAudit, MemoryAccessAudit
from src.webui.dependencies import require_auth, require_auth_with_rate_limit
from src.workspaces import bot_profile_service
from src.workspaces.kami_service import (
    DEFAULT_KAMI_TTL_SECONDS,
    KAMI_BOT_PROFILE_ID,
    MAX_KAMI_TTL_SECONDS,
    PROCESS_BOOT_ID,
    kami_service,
)

router = APIRouter(tags=["MemoryAudit"], dependencies=[Depends(require_auth)])

kami_router = APIRouter(
    prefix="/kami",
    tags=["Kami"],
    dependencies=[Depends(require_auth)],
)


def _mask_hash(value: str, reveal: bool) -> str:
    if reveal or len(value) <= 12:
        return value
    return f"{value[:12]}…"


def _load_json(raw: str) -> dict[str, Any]:
    try:
        loaded = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


class MemoryAccessAuditItem(BaseModel):
    id: int
    trace_id: str
    session_id: str
    person_id: str
    workspace_id: str
    active_bot_profile_id: str
    permission_group_id: str
    access_mode: str
    security_domain: str
    policy_revision: int
    decision_reason: str
    query_hash: str
    requested_scope: dict[str, Any]
    allowed_scope: dict[str, Any]
    denied_scope: dict[str, Any]
    result_count: int
    latency_ms: int
    created_at: datetime
    redacted: bool


class MemoryAccessAuditResponse(BaseModel):
    success: bool = True
    data: list[MemoryAccessAuditItem]
    limit: int
    offset: int
    total: int


class BotControlAuditItem(BaseModel):
    id: int
    session_id: str
    person_id: str
    platform: str
    command: str
    before_bot_profile_id: str
    after_bot_profile_id: str
    permission_group_id: str
    result: str
    reason: str
    metadata: dict[str, Any]
    created_at: datetime
    redacted: bool


class BotControlAuditResponse(BaseModel):
    success: bool = True
    data: list[BotControlAuditItem]
    limit: int
    offset: int
    total: int


class KamiSessionItem(BaseModel):
    id: str
    session_id: str
    person_id: str
    kami_bot_profile_id: str
    activated_from_bot_profile_id: str
    permission_group_id: str
    status: str
    activated_at: datetime
    expires_at: datetime
    last_used_at: datetime
    revision: int
    expired: bool


class KamiSessionListResponse(BaseModel):
    success: bool = True
    data: list[KamiSessionItem]
    boot_id: str


class KamiProfileItem(BaseModel):
    id: str
    name: str
    profile_type: str
    persona_profile_id: Optional[str]
    home_memory_space_id: str
    inherit_parent_persona: bool
    inherit_parent_tools: bool
    inherit_parent_plugins: bool
    enabled: bool
    is_system: bool
    policy_revision: int
    dangerous: bool = True
    max_ttl_seconds: int
    default_ttl_seconds: int


class KamiProfileUpdateRequest(BaseModel):
    persona_profile_id: Optional[str] = None
    inherit_parent_persona: Optional[bool] = None
    inherit_parent_tools: Optional[bool] = None
    inherit_parent_plugins: Optional[bool] = None
    enabled: Optional[bool] = None
    expected_revision: Optional[int] = Field(default=None, ge=1)


class MutationResponse(BaseModel):
    success: bool = True
    removed: bool = False


@router.get("/memory-access-audit", response_model=MemoryAccessAuditResponse)
async def list_memory_access_audit(
    session_id: str = "",
    person_id: str = "",
    access_mode: str = "",
    trace_id: str = "",
    reveal: bool = Query(False, description="是否展示未遮蔽的哈希与 metadata"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> MemoryAccessAuditResponse:
    with get_db_session() as session:
        query = select(MemoryAccessAudit)
        count_query = select(MemoryAccessAudit)
        if session_id:
            query = query.where(MemoryAccessAudit.session_id == session_id)
            count_query = count_query.where(MemoryAccessAudit.session_id == session_id)
        if person_id:
            query = query.where(MemoryAccessAudit.person_id == person_id)
            count_query = count_query.where(MemoryAccessAudit.person_id == person_id)
        if access_mode:
            query = query.where(MemoryAccessAudit.access_mode == access_mode)
            count_query = count_query.where(MemoryAccessAudit.access_mode == access_mode)
        if trace_id:
            query = query.where(MemoryAccessAudit.trace_id == trace_id)
            count_query = count_query.where(MemoryAccessAudit.trace_id == trace_id)
        rows = session.exec(
            query.order_by(col(MemoryAccessAudit.created_at).desc()).offset(offset).limit(limit)
        ).all()
        total = len(session.exec(count_query).all())
    return MemoryAccessAuditResponse(
        data=[
            MemoryAccessAuditItem(
                id=row.id or 0,
                trace_id=row.trace_id,
                session_id=row.session_id,
                person_id=row.person_id,
                workspace_id=row.workspace_id,
                active_bot_profile_id=row.active_bot_profile_id,
                permission_group_id=row.permission_group_id,
                access_mode=row.access_mode,
                security_domain=row.security_domain,
                policy_revision=row.policy_revision,
                decision_reason=row.decision_reason,
                query_hash=_mask_hash(row.query_hash, reveal),
                requested_scope=_load_json(row.requested_scope_json) if reveal else {},
                allowed_scope=_load_json(row.allowed_scope_json) if reveal else {},
                denied_scope=_load_json(row.denied_scope_json) if reveal else {},
                result_count=row.result_count,
                latency_ms=row.latency_ms,
                created_at=row.created_at,
                redacted=not reveal,
            )
            for row in rows
        ],
        limit=limit,
        offset=offset,
        total=total,
    )


@router.get("/bot-control-audit", response_model=BotControlAuditResponse)
async def list_bot_control_audit(
    session_id: str = "",
    person_id: str = "",
    command: str = "",
    result: str = "",
    reveal: bool = Query(False, description="是否展示未遮蔽的 metadata"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> BotControlAuditResponse:
    with get_db_session() as session:
        query = select(BotControlAudit)
        count_query = select(BotControlAudit)
        if session_id:
            query = query.where(BotControlAudit.session_id == session_id)
            count_query = count_query.where(BotControlAudit.session_id == session_id)
        if person_id:
            query = query.where(BotControlAudit.person_id == person_id)
            count_query = count_query.where(BotControlAudit.person_id == person_id)
        if command:
            query = query.where(BotControlAudit.command == command)
            count_query = count_query.where(BotControlAudit.command == command)
        if result:
            query = query.where(BotControlAudit.result == result)
            count_query = count_query.where(BotControlAudit.result == result)
        rows = session.exec(
            query.order_by(col(BotControlAudit.created_at).desc()).offset(offset).limit(limit)
        ).all()
        total = len(session.exec(count_query).all())
    return BotControlAuditResponse(
        data=[
            BotControlAuditItem(
                id=row.id or 0,
                session_id=row.session_id,
                person_id=row.person_id,
                platform=row.platform,
                command=row.command,
                before_bot_profile_id=row.before_bot_profile_id,
                after_bot_profile_id=row.after_bot_profile_id,
                permission_group_id=row.permission_group_id,
                result=row.result,
                reason=row.reason,
                metadata=_load_json(row.metadata_json) if reveal else {},
                created_at=row.created_at,
                redacted=not reveal,
            )
            for row in rows
        ],
        limit=limit,
        offset=offset,
        total=total,
    )


def _kami_profile_item() -> KamiProfileItem:
    profile = bot_profile_service.get_profile(KAMI_BOT_PROFILE_ID)
    if profile is None:
        raise HTTPException(status_code=404, detail="Kami BotProfile 不存在")
    return KamiProfileItem(
        id=profile.id,
        name=profile.name,
        profile_type=profile.profile_type,
        persona_profile_id=profile.persona_profile_id,
        home_memory_space_id=profile.home_memory_space_id,
        inherit_parent_persona=profile.inherit_parent_persona,
        inherit_parent_tools=profile.inherit_parent_tools,
        inherit_parent_plugins=profile.inherit_parent_plugins,
        enabled=profile.enabled,
        is_system=profile.is_system,
        policy_revision=profile.policy_revision,
        max_ttl_seconds=MAX_KAMI_TTL_SECONDS,
        default_ttl_seconds=DEFAULT_KAMI_TTL_SECONDS,
    )


@kami_router.get("/profile", response_model=KamiProfileItem)
async def get_kami_profile() -> KamiProfileItem:
    return _kami_profile_item()


@kami_router.patch("/profile", response_model=KamiProfileItem, dependencies=[Depends(require_auth_with_rate_limit)])
async def update_kami_profile(request: KamiProfileUpdateRequest) -> KamiProfileItem:
    changes = request.model_dump(exclude_unset=True)
    expected_revision = changes.pop("expected_revision", None)
    profile = bot_profile_service.get_profile(KAMI_BOT_PROFILE_ID)
    if profile is None:
        raise HTTPException(status_code=404, detail="Kami BotProfile 不存在")
    if expected_revision is not None and expected_revision != profile.policy_revision:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Kami 配置已被其他管理员更新，请刷新后重试",
        )
    try:
        bot_profile_service.update_profile(KAMI_BOT_PROFILE_ID, **changes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _kami_profile_item()


@kami_router.get("/sessions", response_model=KamiSessionListResponse)
async def list_kami_sessions(
    status_filter: str = Query("", alias="status"),
    limit: int = Query(100, ge=1, le=500),
) -> KamiSessionListResponse:
    now = datetime.now()
    rows = kami_service.list_session_states(status_filter=status_filter or None, limit=limit)
    return KamiSessionListResponse(
        data=[
            KamiSessionItem(
                id=row.id,
                session_id=row.session_id,
                person_id=row.person_id,
                kami_bot_profile_id=row.kami_bot_profile_id,
                activated_from_bot_profile_id=row.activated_from_bot_profile_id,
                permission_group_id=row.permission_group_id,
                status=row.status,
                activated_at=row.activated_at,
                expires_at=row.expires_at,
                last_used_at=row.last_used_at,
                revision=row.revision,
                expired=row.expires_at <= now,
            )
            for row in rows
        ],
        boot_id=PROCESS_BOOT_ID,
    )


@kami_router.post("/sessions/{state_id}/revoke", response_model=MutationResponse, dependencies=[Depends(require_auth_with_rate_limit)])
async def revoke_kami_session(state_id: str, token: str = Depends(require_auth)) -> MutationResponse:
    # 审计只记录不可逆的管理者标识，绝不写入原始令牌。
    actor = sha256(f"webui-admin:v1\0{token}".encode("utf-8")).hexdigest()[:16]
    try:
        removed = kami_service.revoke_session_state(state_id, actor=actor)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return MutationResponse(removed=removed)


__all__ = ["kami_router", "router"]
