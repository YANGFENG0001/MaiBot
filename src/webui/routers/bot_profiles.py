"""BotProfile 管理 API：身份、继承、工具/插件策略与记忆读取规则。"""

from datetime import datetime
from typing import Any, Literal, Optional

import json

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel import select

from src.common.database.database import get_db_session
from src.common.database.database_model import BotProfile, MemorySpace
from src.webui.dependencies import require_auth, require_auth_with_rate_limit
from src.workspaces import bot_profile_service

router = APIRouter(
    prefix="/bot-profiles",
    tags=["BotProfiles"],
    dependencies=[Depends(require_auth)],
)


class LineageItem(BaseModel):
    id: str
    name: str
    profile_type: str


class ToolPolicyItem(BaseModel):
    component_name: str
    effect: str


class PluginPolicyItem(BaseModel):
    plugin_id: str
    effect: str
    overrides: dict[str, Any] = Field(default_factory=dict)


class MemoryRuleItem(BaseModel):
    target_space_id: str
    target_space_name: str
    can_read: bool
    filters: dict[str, Any] = Field(default_factory=dict)


class BotProfileItem(BaseModel):
    id: str
    name: str
    profile_type: str
    parent_profile_id: Optional[str]
    persona_profile_id: Optional[str]
    home_memory_space_id: str
    home_memory_space_name: str
    inherit_parent_persona: bool
    inherit_parent_tools: bool
    inherit_parent_plugins: bool
    enabled: bool
    is_system: bool
    policy_revision: int
    lineage: list[LineageItem]
    tool_policies: list[ToolPolicyItem]
    plugin_policies: list[PluginPolicyItem]
    memory_rules: list[MemoryRuleItem]
    created_at: datetime
    updated_at: datetime


class BotProfileListResponse(BaseModel):
    success: bool = True
    data: list[BotProfileItem]


class BotProfileResponse(BaseModel):
    success: bool = True
    data: BotProfileItem


class BotProfileUpdateRequest(BaseModel):
    parent_profile_id: Optional[str] = None
    persona_profile_id: Optional[str] = None
    inherit_parent_persona: Optional[bool] = None
    inherit_parent_tools: Optional[bool] = None
    inherit_parent_plugins: Optional[bool] = None
    enabled: Optional[bool] = None
    expected_revision: Optional[int] = Field(default=None, ge=1)


class ToolPolicyRequest(BaseModel):
    effect: Literal["allow", "deny"]
    expected_revision: Optional[int] = Field(default=None, ge=1)


class PluginPolicyRequest(BaseModel):
    effect: Literal["allow", "deny", "inherit"]
    overrides: dict[str, Any] = Field(default_factory=dict)
    config_schema: Optional[dict[str, Any]] = None
    expected_revision: Optional[int] = Field(default=None, ge=1)


class MemoryRuleRequest(BaseModel):
    can_read: bool = True
    filters: dict[str, Any] = Field(default_factory=dict)
    expected_revision: Optional[int] = Field(default=None, ge=1)


class MutationResponse(BaseModel):
    success: bool = True
    removed: bool = False


def _conflict(profile: BotProfile, expected_revision: Optional[int]) -> None:
    """乐观锁：版本不匹配时返回 409，禁止静默覆盖他人修改。"""

    if expected_revision is not None and expected_revision != profile.policy_revision:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="配置已被其他管理员更新，请刷新后重试",
        )


def _space_names() -> dict[str, str]:
    with get_db_session() as session:
        return {row.id: row.name for row in session.exec(select(MemorySpace)).all()}


def _profile_item(profile: BotProfile, space_names: dict[str, str]) -> BotProfileItem:
    lineage = bot_profile_service.get_lineage(profile.id)
    tool_policies = bot_profile_service.resolve_tool_policies(profile.id)
    plugin_policies = bot_profile_service.resolve_plugin_policies(profile.id)
    memory_rules = bot_profile_service.list_memory_rules(profile.id)
    return BotProfileItem(
        id=profile.id,
        name=profile.name,
        profile_type=profile.profile_type,
        parent_profile_id=profile.parent_profile_id,
        persona_profile_id=profile.persona_profile_id,
        home_memory_space_id=profile.home_memory_space_id,
        home_memory_space_name=space_names.get(profile.home_memory_space_id, profile.home_memory_space_id),
        inherit_parent_persona=profile.inherit_parent_persona,
        inherit_parent_tools=profile.inherit_parent_tools,
        inherit_parent_plugins=profile.inherit_parent_plugins,
        enabled=profile.enabled,
        is_system=profile.is_system,
        policy_revision=profile.policy_revision,
        lineage=[LineageItem(id=item.id, name=item.name, profile_type=item.profile_type) for item in lineage],
        tool_policies=[ToolPolicyItem(component_name=name, effect=effect) for name, effect in sorted(tool_policies.items())],
        plugin_policies=[
            PluginPolicyItem(plugin_id=plugin_id, effect=policy.effect, overrides=_load_json(policy.overrides_json))
            for plugin_id, policy in sorted(plugin_policies.items())
        ],
        memory_rules=[
            MemoryRuleItem(
                target_space_id=rule.target_space_id,
                target_space_name=space_names.get(rule.target_space_id, rule.target_space_id),
                can_read=rule.can_read,
                filters=_load_json(rule.filters_json),
            )
            for rule in memory_rules
        ],
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def _load_json(raw: str) -> dict[str, Any]:
    try:
        loaded = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _require_profile(profile_id: str) -> BotProfile:
    profile = bot_profile_service.get_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="BotProfile 不存在")
    return profile


@router.get("", response_model=BotProfileListResponse)
async def list_bot_profiles() -> BotProfileListResponse:
    space_names = _space_names()
    return BotProfileListResponse(
        data=[_profile_item(profile, space_names) for profile in bot_profile_service.list_profiles()]
    )


@router.get("/{profile_id}", response_model=BotProfileResponse)
async def get_bot_profile(profile_id: str) -> BotProfileResponse:
    return BotProfileResponse(data=_profile_item(_require_profile(profile_id), _space_names()))


@router.patch("/{profile_id}", response_model=BotProfileResponse, dependencies=[Depends(require_auth_with_rate_limit)])
async def update_bot_profile(profile_id: str, request: BotProfileUpdateRequest) -> BotProfileResponse:
    changes = request.model_dump(exclude_unset=True)
    expected_revision = changes.pop("expected_revision", None)
    _conflict(_require_profile(profile_id), expected_revision)
    try:
        bot_profile_service.update_profile(profile_id, **changes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return BotProfileResponse(data=_profile_item(_require_profile(profile_id), _space_names()))


@router.get("/{profile_id}/tools", response_model=list[ToolPolicyItem])
async def list_bot_profile_tools(profile_id: str) -> list[ToolPolicyItem]:
    _require_profile(profile_id)
    return [
        ToolPolicyItem(component_name=name, effect=effect)
        for name, effect in sorted(bot_profile_service.resolve_tool_policies(profile_id).items())
    ]


@router.put("/{profile_id}/tools/{component_name}", response_model=ToolPolicyItem, dependencies=[Depends(require_auth_with_rate_limit)])
async def set_bot_profile_tool(profile_id: str, component_name: str, request: ToolPolicyRequest) -> ToolPolicyItem:
    _conflict(_require_profile(profile_id), request.expected_revision)
    try:
        policy = bot_profile_service.set_tool_policy(profile_id, component_name, request.effect)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ToolPolicyItem(component_name=policy.component_name, effect=policy.effect)


@router.delete("/{profile_id}/tools/{component_name}", response_model=MutationResponse, dependencies=[Depends(require_auth_with_rate_limit)])
async def delete_bot_profile_tool(profile_id: str, component_name: str) -> MutationResponse:
    try:
        removed = bot_profile_service.remove_tool_policy(profile_id, component_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return MutationResponse(removed=removed)


@router.get("/{profile_id}/plugins", response_model=list[PluginPolicyItem])
async def list_bot_profile_plugins(profile_id: str) -> list[PluginPolicyItem]:
    _require_profile(profile_id)
    resolved = bot_profile_service.resolve_plugin_policies(profile_id)
    return [
        PluginPolicyItem(plugin_id=plugin_id, effect=policy.effect, overrides=_load_json(policy.overrides_json))
        for plugin_id, policy in sorted(resolved.items())
    ]


@router.put("/{profile_id}/plugins/{plugin_id}", response_model=PluginPolicyItem, dependencies=[Depends(require_auth_with_rate_limit)])
async def set_bot_profile_plugin(profile_id: str, plugin_id: str, request: PluginPolicyRequest) -> PluginPolicyItem:
    _conflict(_require_profile(profile_id), request.expected_revision)
    try:
        policy = bot_profile_service.set_plugin_policy(
            profile_id,
            plugin_id,
            request.effect,
            overrides=request.overrides,
            config_schema=request.config_schema,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PluginPolicyItem(
        plugin_id=policy.plugin_id,
        effect=policy.effect,
        overrides=_load_json(policy.overrides_json),
    )


@router.delete("/{profile_id}/plugins/{plugin_id}", response_model=MutationResponse, dependencies=[Depends(require_auth_with_rate_limit)])
async def delete_bot_profile_plugin(profile_id: str, plugin_id: str) -> MutationResponse:
    try:
        removed = bot_profile_service.remove_plugin_policy(profile_id, plugin_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return MutationResponse(removed=removed)


@router.get("/{profile_id}/memory-rules", response_model=list[MemoryRuleItem])
async def list_bot_profile_memory_rules(profile_id: str) -> list[MemoryRuleItem]:
    _require_profile(profile_id)
    space_names = _space_names()
    return [
        MemoryRuleItem(
            target_space_id=rule.target_space_id,
            target_space_name=space_names.get(rule.target_space_id, rule.target_space_id),
            can_read=rule.can_read,
            filters=_load_json(rule.filters_json),
        )
        for rule in bot_profile_service.list_memory_rules(profile_id)
    ]


@router.put("/{profile_id}/memory-rules/{target_space_id}", response_model=MemoryRuleItem, dependencies=[Depends(require_auth_with_rate_limit)])
async def set_bot_profile_memory_rule(
    profile_id: str,
    target_space_id: str,
    request: MemoryRuleRequest,
) -> MemoryRuleItem:
    _conflict(_require_profile(profile_id), request.expected_revision)
    try:
        rule = bot_profile_service.set_memory_rule(profile_id, target_space_id, request.can_read, request.filters)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    space_names = _space_names()
    return MemoryRuleItem(
        target_space_id=rule.target_space_id,
        target_space_name=space_names.get(rule.target_space_id, rule.target_space_id),
        can_read=rule.can_read,
        filters=_load_json(rule.filters_json),
    )


@router.delete("/{profile_id}/memory-rules/{target_space_id}", response_model=MutationResponse, dependencies=[Depends(require_auth_with_rate_limit)])
async def delete_bot_profile_memory_rule(profile_id: str, target_space_id: str) -> MutationResponse:
    try:
        removed = bot_profile_service.remove_memory_rule(profile_id, target_space_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return MutationResponse(removed=removed)
