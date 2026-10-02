"""记忆权限组管理 API 与权限模拟器。

模拟器必须调用运行期真实的 AccessResolver，前端不得复制任何权限算法。
"""

from datetime import datetime
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlmodel import select

from src.common.database.database import get_db_session
from src.common.database.database_model import (
    BotProfile,
    MemoryPermissionGroup,
    MemoryPermissionGroupCapability,
    MemoryPermissionGroupContext,
    MemoryPermissionGroupMember,
    MemoryPermissionRule,
    PermissionGroupBotRule,
    PersonInfo,
)
from src.common.database.migrations.v43_to_v44 import build_partition_id
from src.webui.dependencies import require_auth, require_auth_with_rate_limit
from src.workspaces import access_resolver, workspace_service
from src.workspaces.access_resolver import NORMAL_CAPABILITIES
from src.workspaces.permission_group_service import (
    BOT_SELECTORS,
    MANAGER_PRESET_CAPABILITIES,
    PARTITION_SELECTORS,
    PARTITION_TYPES,
    SCOPE_TYPES,
    SPACE_SELECTORS,
    permission_group_service,
)

router = APIRouter(
    prefix="/memory-permission-groups",
    tags=["MemoryPermissions"],
    dependencies=[Depends(require_auth)],
)


class GroupCounts(BaseModel):
    members: int = 0
    contexts: int = 0
    capabilities: int = 0
    rules: int = 0
    bot_rules: int = 0


class PermissionGroupItem(BaseModel):
    id: str
    name: str
    description: str
    enabled: bool
    priority: int
    memory_scope_mode: str
    is_manager_mode: bool
    policy_revision: int
    counts: GroupCounts
    created_at: datetime
    updated_at: datetime


class PermissionGroupListResponse(BaseModel):
    success: bool = True
    data: list[PermissionGroupItem]
    capabilities: list[str]
    manager_preset_capabilities: list[str]
    scope_types: list[str]
    space_selectors: list[str]
    partition_types: list[str]
    partition_selectors: list[str]
    bot_selectors: list[str]


class GroupMemberItem(BaseModel):
    person_id: str
    person_name: str
    platform: str
    account_id: str


class GroupContextItem(BaseModel):
    id: int
    scope_type: str
    workspace_id: Optional[str]
    session_id: Optional[str]
    channel_type: Optional[str]
    allow_group_disclosure: bool
    enabled: bool


class GroupCapabilityItem(BaseModel):
    capability: str
    enabled: bool


class GroupRuleItem(BaseModel):
    id: int
    effect: str
    space_selector: str
    memory_space_id: Optional[str]
    partition_type: str
    partition_selector: str
    partition_key: Optional[str]
    memory_types: list[Any]
    tags: list[Any]
    sensitivity_max: Optional[int]
    time_start: Optional[datetime]
    time_end: Optional[datetime]
    priority: int
    enabled: bool


class GroupBotRuleItem(BaseModel):
    id: int
    effect: str
    bot_selector: str
    bot_profile_id: Optional[str]


class PermissionGroupDetailResponse(BaseModel):
    success: bool = True
    data: PermissionGroupItem
    members: list[GroupMemberItem]
    contexts: list[GroupContextItem]
    capabilities: list[GroupCapabilityItem]
    rules: list[GroupRuleItem]
    bot_rules: list[GroupBotRuleItem]


class GroupCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)
    priority: int = 0
    memory_scope_mode: Literal["inherit", "override"] = "inherit"
    is_manager_mode: bool = False
    enabled: bool = True


class GroupUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=2000)
    priority: Optional[int] = None
    memory_scope_mode: Optional[Literal["inherit", "override"]] = None
    is_manager_mode: Optional[bool] = None
    enabled: Optional[bool] = None
    expected_revision: Optional[int] = Field(default=None, ge=1)


class MemberRequest(BaseModel):
    person_id: str = Field(min_length=1, max_length=255)
    expected_revision: Optional[int] = Field(default=None, ge=1)


class ContextRequest(BaseModel):
    scope_type: Literal["global", "workspace", "session", "channel"]
    workspace_id: Optional[str] = None
    session_id: Optional[str] = None
    channel_type: Optional[Literal["private", "group"]] = None
    allow_group_disclosure: bool = False
    enabled: bool = True
    expected_revision: Optional[int] = Field(default=None, ge=1)


class CapabilityRequest(BaseModel):
    enabled: bool = True
    expected_revision: Optional[int] = Field(default=None, ge=1)


class RuleCreateRequest(BaseModel):
    effect: Literal["allow", "deny"] = "allow"
    space_selector: Literal["current", "public", "specific", "all_normal"] = "current"
    memory_space_id: Optional[str] = None
    partition_type: Literal["any", "shared", "person", "conversation"] = "any"
    partition_selector: Literal["any", "self", "current", "specific"] = "any"
    partition_key: Optional[str] = None
    memory_types: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    sensitivity_max: Optional[int] = None
    time_start: Optional[datetime] = None
    time_end: Optional[datetime] = None
    priority: int = 0
    enabled: bool = True


class RuleUpdateRequest(BaseModel):
    effect: Optional[Literal["allow", "deny"]] = None
    space_selector: Optional[Literal["current", "public", "specific", "all_normal"]] = None
    memory_space_id: Optional[str] = None
    partition_type: Optional[Literal["any", "shared", "person", "conversation"]] = None
    partition_selector: Optional[Literal["any", "self", "current", "specific"]] = None
    partition_key: Optional[str] = None
    sensitivity_max: Optional[int] = None
    time_start: Optional[datetime] = None
    time_end: Optional[datetime] = None
    priority: Optional[int] = None
    enabled: Optional[bool] = None
    expected_revision: Optional[int] = Field(default=None, ge=1)


class BotRuleRequest(BaseModel):
    effect: Literal["allow", "deny"]
    bot_selector: Literal["public", "current_group", "kami", "specific"]
    bot_profile_id: Optional[str] = None
    expected_revision: Optional[int] = Field(default=None, ge=1)


class PersonOption(BaseModel):
    person_id: str
    person_name: str
    platform: str
    account_id: str
    user_nickname: str


class PersonListResponse(BaseModel):
    success: bool = True
    data: list[PersonOption]


class SimulateRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=255)
    person_id: str = Field(min_length=1, max_length=255)
    audience_type: Literal["private", "group"] = "private"
    bot_profile_id: str = ""


class SimulateScope(BaseModel):
    space_ids: list[str]
    partition_ids: list[str]


class SimulateResponse(BaseModel):
    success: bool = True
    allowed: bool
    denied_reason: str = ""
    trace_id: str = ""
    workspace_id: str
    workspace_name: str
    active_bot_profile_id: str
    active_bot_profile_type: str
    bot_profile_name: str
    permission_group_id: str
    permission_group_name: str
    matched_context: Optional[GroupContextItem]
    access_mode: str
    security_domain: str
    policy_revision: int
    capabilities: list[str]
    requested_scope: SimulateScope
    allowed_scope: SimulateScope
    denied_scope: SimulateScope
    writable_partition_ids: list[str]
    allow_group_disclosure: bool


class MutationResponse(BaseModel):
    success: bool = True
    removed: bool = False


def _conflict(group: MemoryPermissionGroup, expected_revision: Optional[int]) -> None:
    if expected_revision is not None and expected_revision != group.policy_revision:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="权限组已被其他管理员更新，请刷新后重试",
        )


def _counts() -> dict[str, dict[str, int]]:
    """一次性统计各权限组子资源数量，避免逐组查询。"""

    result: dict[str, dict[str, int]] = {}
    models = {
        "members": MemoryPermissionGroupMember,
        "contexts": MemoryPermissionGroupContext,
        "capabilities": MemoryPermissionGroupCapability,
        "rules": MemoryPermissionRule,
        "bot_rules": PermissionGroupBotRule,
    }
    with get_db_session() as session:
        for key, model in models.items():
            rows = session.exec(
                select(model.permission_group_id, func.count()).group_by(model.permission_group_id)
            ).all()
            for group_id, count in rows:
                result.setdefault(group_id, {})[key] = int(count)
    return result


def _group_item(group: MemoryPermissionGroup, counts: dict[str, dict[str, int]]) -> PermissionGroupItem:
    return PermissionGroupItem(
        id=group.id,
        name=group.name,
        description=group.description,
        enabled=group.enabled,
        priority=group.priority,
        memory_scope_mode=group.memory_scope_mode,
        is_manager_mode=group.is_manager_mode,
        policy_revision=group.policy_revision,
        counts=GroupCounts(**counts.get(group.id, {})),
        created_at=group.created_at,
        updated_at=group.updated_at,
    )


def _require_group(group_id: str) -> MemoryPermissionGroup:
    group = permission_group_service.get_group(group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="权限组不存在")
    return group


def _context_item(row: MemoryPermissionGroupContext) -> GroupContextItem:
    return GroupContextItem(
        id=row.id or 0,
        scope_type=row.scope_type,
        workspace_id=row.workspace_id,
        session_id=row.session_id,
        channel_type=row.channel_type,
        allow_group_disclosure=row.allow_group_disclosure,
        enabled=row.enabled,
    )


def _rule_item(rule: MemoryPermissionRule) -> GroupRuleItem:
    return GroupRuleItem(**permission_group_service.rule_view(rule))


def _person_map(person_ids: list[str]) -> dict[str, PersonInfo]:
    if not person_ids:
        return {}
    with get_db_session() as session:
        rows = session.exec(select(PersonInfo).where(PersonInfo.person_id.in_(person_ids))).all()
    return {row.person_id: row for row in rows}


# ---- 静态路径必须先于 /{group_id} 声明 ----

@router.get("/capabilities", response_model=list[str])
async def list_capability_catalog() -> list[str]:
    return sorted(NORMAL_CAPABILITIES)


@router.get("/persons", response_model=PersonListResponse)
async def list_person_options(limit: int = Query(200, ge=1, le=1000)) -> PersonListResponse:
    with get_db_session() as session:
        rows = session.exec(select(PersonInfo).limit(limit)).all()
    return PersonListResponse(
        data=[
            PersonOption(
                person_id=row.person_id,
                person_name=row.person_name or row.user_nickname,
                platform=row.platform,
                account_id=row.user_id,
                user_nickname=row.user_nickname,
            )
            for row in rows
        ]
    )


@router.post("/simulate", response_model=SimulateResponse, dependencies=[Depends(require_auth_with_rate_limit)])
async def simulate_access(request: SimulateRequest) -> SimulateResponse:
    """用真实 AccessResolver 解析一次访问，返回命中原因与读写范围。"""

    try:
        session_context = workspace_service.resolve_session_workspace_context(request.session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    workspace_id = session_context.workspace_id
    workspace_name = session_context.workspace_name
    requested_partitions = {
        build_partition_id(session_context.default_memory_space_id, "shared", "shared", "normal"),
        build_partition_id(session_context.default_memory_space_id, "person", request.person_id, "normal"),
        build_partition_id(session_context.default_memory_space_id, "conversation", request.session_id, "normal"),
    }

    try:
        request_context = workspace_service.build_bot_request_context(
            request.session_id,
            request.person_id,
            request.audience_type,
        )
    except PermissionError as exc:
        return _denied_response(request, workspace_id, workspace_name, str(exc), requested_partitions)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    active_profile_id = request.bot_profile_id or request_context.active_bot_profile_id
    with get_db_session() as session:
        profile = session.get(BotProfile, active_profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail=f"BotProfile 不存在: {active_profile_id}")
        if request.bot_profile_id and request.bot_profile_id != request_context.active_bot_profile_id:
            # 指定 BotProfile 时按该身份重新解析，仍然只使用真实 AccessResolver。
            try:
                decision = access_resolver.resolve(
                    session,
                    person_id=request.person_id,
                    session_id=request.session_id,
                    workspace_id=workspace_id,
                    home_space_id=profile.home_memory_space_id,
                    bot_profile_id=profile.id,
                    bot_profile_type=profile.profile_type,
                    audience_type=request.audience_type,
                )
            except (PermissionError, ValueError) as exc:
                return _denied_response(
                    request, workspace_id, workspace_name, str(exc), requested_partitions,
                    bot_profile_id=profile.id, bot_profile_type=profile.profile_type, bot_profile_name=profile.name,
                )
            access_mode = decision.access_mode
            security_domain = decision.security_domain
            permission_group_id = decision.permission_group_id
            readable_spaces = decision.readable_space_ids
            readable_partitions = decision.readable_partition_ids
            writable_partitions = decision.writable_partition_ids
            capabilities = sorted(decision.capabilities)
            allow_group_disclosure = decision.allow_group_disclosure
            policy_revision = decision.policy_revision
        else:
            access_mode = request_context.access_mode
            security_domain = request_context.security_domain
            permission_group_id = request_context.permission_group_id
            readable_spaces = request_context.readable_space_ids
            readable_partitions = request_context.readable_partition_ids
            writable_partitions = request_context.writable_partition_ids
            capabilities = sorted(
                _group_capabilities(session, permission_group_id)
            )
            allow_group_disclosure = False
            policy_revision = request_context.policy_revision

        matched_group, matched_context = access_resolver.describe_selection(
            session,
            person_id=request.person_id,
            session_id=request.session_id,
            workspace_id=workspace_id,
            audience_type=request.audience_type,
        )
        group_name = matched_group.name if matched_group else ""
        context_item = _context_item(matched_context) if matched_context is not None else None
        if matched_context is not None:
            allow_group_disclosure = matched_context.allow_group_disclosure

    requested_space_ids = sorted({session_context.default_memory_space_id})
    allowed_space_ids = sorted(readable_spaces)
    allowed_partition_ids = sorted(readable_partitions)
    denied_partition_ids = sorted(requested_partitions - set(allowed_partition_ids))

    return SimulateResponse(
        allowed=True,
        trace_id=request_context.trace_id,
        workspace_id=workspace_id,
        workspace_name=workspace_name,
        active_bot_profile_id=active_profile_id,
        active_bot_profile_type=profile.profile_type,
        bot_profile_name=profile.name,
        permission_group_id=permission_group_id,
        permission_group_name=group_name,
        matched_context=context_item,
        access_mode=access_mode,
        security_domain=security_domain,
        policy_revision=policy_revision,
        capabilities=capabilities,
        requested_scope=SimulateScope(
            space_ids=requested_space_ids,
            partition_ids=sorted(requested_partitions),
        ),
        allowed_scope=SimulateScope(space_ids=allowed_space_ids, partition_ids=allowed_partition_ids),
        denied_scope=SimulateScope(space_ids=sorted(set(requested_space_ids) - set(allowed_space_ids)), partition_ids=denied_partition_ids),
        writable_partition_ids=sorted(writable_partitions),
        allow_group_disclosure=allow_group_disclosure,
    )


def _group_capabilities(session, group_id: str) -> list[str]:
    if not group_id:
        return []
    return [
        row.capability
        for row in session.exec(
            select(MemoryPermissionGroupCapability).where(
                MemoryPermissionGroupCapability.permission_group_id == group_id,
                MemoryPermissionGroupCapability.enabled == True,  # noqa: E712
            )
        ).all()
    ]


def _denied_response(
    request: SimulateRequest,
    workspace_id: str,
    workspace_name: str,
    reason: str,
    requested_partitions: set[str],
    *,
    bot_profile_id: str = "",
    bot_profile_type: str = "",
    bot_profile_name: str = "",
) -> SimulateResponse:
    return SimulateResponse(
        allowed=False,
        denied_reason=reason,
        workspace_id=workspace_id,
        workspace_name=workspace_name,
        active_bot_profile_id=bot_profile_id,
        active_bot_profile_type=bot_profile_type,
        bot_profile_name=bot_profile_name,
        permission_group_id="",
        permission_group_name="",
        matched_context=None,
        access_mode="denied",
        security_domain="normal",
        policy_revision=0,
        capabilities=[],
        requested_scope=SimulateScope(space_ids=[], partition_ids=sorted(requested_partitions)),
        allowed_scope=SimulateScope(space_ids=[], partition_ids=[]),
        denied_scope=SimulateScope(space_ids=[], partition_ids=sorted(requested_partitions)),
        writable_partition_ids=[],
        allow_group_disclosure=False,
    )


# ---- 权限组 CRUD ----

@router.get("", response_model=PermissionGroupListResponse)
async def list_permission_groups() -> PermissionGroupListResponse:
    counts = _counts()
    return PermissionGroupListResponse(
        data=[_group_item(group, counts) for group in permission_group_service.list_groups()],
        capabilities=sorted(NORMAL_CAPABILITIES),
        manager_preset_capabilities=list(MANAGER_PRESET_CAPABILITIES),
        scope_types=list(SCOPE_TYPES),
        space_selectors=list(SPACE_SELECTORS),
        partition_types=list(PARTITION_TYPES),
        partition_selectors=list(PARTITION_SELECTORS),
        bot_selectors=list(BOT_SELECTORS),
    )


@router.post("", response_model=PermissionGroupItem, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_auth_with_rate_limit)])
async def create_permission_group(request: GroupCreateRequest) -> PermissionGroupItem:
    try:
        group = permission_group_service.create_group(**request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _group_item(group, _counts())


@router.get("/{group_id}", response_model=PermissionGroupDetailResponse)
async def get_permission_group(group_id: str) -> PermissionGroupDetailResponse:
    group = _require_group(group_id)
    members = permission_group_service.list_members(group_id)
    person_map = _person_map([item.person_id for item in members])
    return PermissionGroupDetailResponse(
        data=_group_item(group, _counts()),
        members=[
            GroupMemberItem(
                person_id=item.person_id,
                person_name=(person_map[item.person_id].person_name or person_map[item.person_id].user_nickname)
                if item.person_id in person_map
                else item.person_id,
                platform=person_map[item.person_id].platform if item.person_id in person_map else "",
                account_id=person_map[item.person_id].user_id if item.person_id in person_map else "",
            )
            for item in members
        ],
        contexts=[_context_item(row) for row in permission_group_service.list_contexts(group_id)],
        capabilities=[
            GroupCapabilityItem(capability=row.capability, enabled=row.enabled)
            for row in permission_group_service.list_capabilities(group_id)
        ],
        rules=[_rule_item(row) for row in permission_group_service.list_rules(group_id)],
        bot_rules=[
            GroupBotRuleItem(
                id=row.id or 0,
                effect=row.effect,
                bot_selector=row.bot_selector,
                bot_profile_id=row.bot_profile_id,
            )
            for row in permission_group_service.list_bot_rules(group_id)
        ],
    )


@router.patch("/{group_id}", response_model=PermissionGroupItem, dependencies=[Depends(require_auth_with_rate_limit)])
async def update_permission_group(group_id: str, request: GroupUpdateRequest) -> PermissionGroupItem:
    changes = request.model_dump(exclude_unset=True)
    expected_revision = changes.pop("expected_revision", None)
    _conflict(_require_group(group_id), expected_revision)
    try:
        group = permission_group_service.update_group(group_id, **changes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _group_item(group, _counts())


@router.delete("/{group_id}", response_model=MutationResponse, dependencies=[Depends(require_auth_with_rate_limit)])
async def delete_permission_group(group_id: str) -> MutationResponse:
    return MutationResponse(removed=permission_group_service.delete_group(group_id))


# ---- 成员 ----

@router.get("/{group_id}/members", response_model=list[GroupMemberItem])
async def list_group_members(group_id: str) -> list[GroupMemberItem]:
    _require_group(group_id)
    members = permission_group_service.list_members(group_id)
    person_map = _person_map([item.person_id for item in members])
    return [
        GroupMemberItem(
            person_id=item.person_id,
            person_name=(person_map[item.person_id].person_name or person_map[item.person_id].user_nickname)
            if item.person_id in person_map
            else item.person_id,
            platform=person_map[item.person_id].platform if item.person_id in person_map else "",
            account_id=person_map[item.person_id].user_id if item.person_id in person_map else "",
        )
        for item in members
    ]


@router.post("/{group_id}/members", response_model=list[GroupMemberItem], dependencies=[Depends(require_auth_with_rate_limit)])
async def add_group_member(group_id: str, request: MemberRequest) -> list[GroupMemberItem]:
    _conflict(_require_group(group_id), request.expected_revision)
    try:
        permission_group_service.add_member(group_id, request.person_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await list_group_members(group_id)


@router.delete("/{group_id}/members/{person_id}", response_model=MutationResponse, dependencies=[Depends(require_auth_with_rate_limit)])
async def remove_group_member(group_id: str, person_id: str) -> MutationResponse:
    _require_group(group_id)
    return MutationResponse(removed=permission_group_service.remove_member(group_id, person_id))


# ---- 上下文 ----

@router.get("/{group_id}/contexts", response_model=list[GroupContextItem])
async def list_group_contexts(group_id: str) -> list[GroupContextItem]:
    _require_group(group_id)
    return [_context_item(row) for row in permission_group_service.list_contexts(group_id)]


@router.post("/{group_id}/contexts", response_model=list[GroupContextItem], dependencies=[Depends(require_auth_with_rate_limit)])
async def add_group_context(group_id: str, request: ContextRequest) -> list[GroupContextItem]:
    _conflict(_require_group(group_id), request.expected_revision)
    payload = request.model_dump(exclude={"expected_revision"})
    try:
        permission_group_service.add_context(group_id, **payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await list_group_contexts(group_id)


@router.delete("/{group_id}/contexts/{context_id}", response_model=MutationResponse, dependencies=[Depends(require_auth_with_rate_limit)])
async def remove_group_context(group_id: str, context_id: int) -> MutationResponse:
    _require_group(group_id)
    return MutationResponse(removed=permission_group_service.remove_context(group_id, context_id))


# ---- 能力 ----

@router.get("/{group_id}/capabilities", response_model=list[GroupCapabilityItem])
async def list_group_capabilities(group_id: str) -> list[GroupCapabilityItem]:
    _require_group(group_id)
    return [
        GroupCapabilityItem(capability=row.capability, enabled=row.enabled)
        for row in permission_group_service.list_capabilities(group_id)
    ]


@router.put("/{group_id}/capabilities/{capability}", response_model=list[GroupCapabilityItem], dependencies=[Depends(require_auth_with_rate_limit)])
async def set_group_capability(group_id: str, capability: str, request: CapabilityRequest) -> list[GroupCapabilityItem]:
    _conflict(_require_group(group_id), request.expected_revision)
    try:
        permission_group_service.set_capability(group_id, capability, request.enabled)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await list_group_capabilities(group_id)


@router.delete("/{group_id}/capabilities/{capability}", response_model=MutationResponse, dependencies=[Depends(require_auth_with_rate_limit)])
async def remove_group_capability(group_id: str, capability: str) -> MutationResponse:
    _require_group(group_id)
    return MutationResponse(removed=permission_group_service.remove_capability(group_id, capability))


# ---- 记忆规则 ----

@router.get("/{group_id}/rules", response_model=list[GroupRuleItem])
async def list_group_rules(group_id: str) -> list[GroupRuleItem]:
    _require_group(group_id)
    return [_rule_item(row) for row in permission_group_service.list_rules(group_id)]


@router.post("/{group_id}/rules", response_model=list[GroupRuleItem], dependencies=[Depends(require_auth_with_rate_limit)])
async def add_group_rule(group_id: str, request: RuleCreateRequest) -> list[GroupRuleItem]:
    _require_group(group_id)
    try:
        permission_group_service.add_rule(group_id, **request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await list_group_rules(group_id)


@router.patch("/{group_id}/rules/{rule_id}", response_model=list[GroupRuleItem], dependencies=[Depends(require_auth_with_rate_limit)])
async def update_group_rule(group_id: str, rule_id: int, request: RuleUpdateRequest) -> list[GroupRuleItem]:
    changes = request.model_dump(exclude_unset=True)
    changes.pop("expected_revision", None)
    try:
        permission_group_service.update_rule(group_id, rule_id, **changes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await list_group_rules(group_id)


@router.delete("/{group_id}/rules/{rule_id}", response_model=MutationResponse, dependencies=[Depends(require_auth_with_rate_limit)])
async def delete_group_rule(group_id: str, rule_id: int) -> MutationResponse:
    _require_group(group_id)
    return MutationResponse(removed=permission_group_service.delete_rule(group_id, rule_id))


# ---- Bot 规则 ----

@router.get("/{group_id}/bot-rules", response_model=list[GroupBotRuleItem])
async def list_group_bot_rules(group_id: str) -> list[GroupBotRuleItem]:
    _require_group(group_id)
    return [
        GroupBotRuleItem(
            id=row.id or 0,
            effect=row.effect,
            bot_selector=row.bot_selector,
            bot_profile_id=row.bot_profile_id,
        )
        for row in permission_group_service.list_bot_rules(group_id)
    ]


@router.put("/{group_id}/bot-rules", response_model=list[GroupBotRuleItem], dependencies=[Depends(require_auth_with_rate_limit)])
async def set_group_bot_rule(group_id: str, request: BotRuleRequest) -> list[GroupBotRuleItem]:
    _conflict(_require_group(group_id), request.expected_revision)
    try:
        permission_group_service.set_bot_rule(
            group_id,
            effect=request.effect,
            bot_selector=request.bot_selector,
            bot_profile_id=request.bot_profile_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await list_group_bot_rules(group_id)


@router.delete("/{group_id}/bot-rules/{bot_rule_id}", response_model=MutationResponse, dependencies=[Depends(require_auth_with_rate_limit)])
async def remove_group_bot_rule(group_id: str, bot_rule_id: int) -> MutationResponse:
    _require_group(group_id)
    return MutationResponse(removed=permission_group_service.remove_bot_rule(group_id, bot_rule_id))
