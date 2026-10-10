"""PersonaProfile 管理 API：BotProfile / Workspace 可复用的人设覆盖层。

在此之前人设只能通过数据库迁移写入（只有 Kami 那一条），WebUI 里
`bot-profiles-panel` 只能只读展示 `persona_profile_id`。本路由补齐写入侧，
使「给不同 Bot 单独设置性格」成为可配置项。
"""

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src.webui.dependencies import require_auth, require_auth_with_rate_limit
from src.workspaces import persona_service
from src.workspaces.persona_service import PERSONA_TEXT_FIELDS, apply_text_fields

router = APIRouter(
    prefix="/personas",
    tags=["Personas"],
    dependencies=[Depends(require_auth)],
)


class PersonaUsage(BaseModel):
    """引用该人设的对象，用于删除前提示。"""

    bot_profiles: list[str] = Field(default_factory=list)
    workspaces: list[str] = Field(default_factory=list)


class PersonaItem(BaseModel):
    id: str
    name: str
    description: str
    nickname: str
    alias_names: list[str]
    personality: str
    behavior_style: str
    reply_style: str
    group_chat_prompt: str
    private_chat_prompt: str
    multiple_reply_style: str
    emotion_trait: str
    usage: PersonaUsage
    created_at: datetime
    updated_at: datetime


class PersonaListResponse(BaseModel):
    success: bool = True
    data: list[PersonaItem]


class PersonaResponse(BaseModel):
    success: bool = True
    data: PersonaItem


class PersonaCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    persona_id: Optional[str] = Field(default=None, max_length=64)
    description: str = ""
    nickname: str = ""
    # 同时接受字符串数组与逗号/换行分隔的字符串：界面上别名是一个文本输入框，
    # 直接原样提交更自然，归一化在 PersonaService 里统一做。
    alias_names: list[str] | str = Field(default_factory=list)
    personality: str = ""
    behavior_style: str = ""
    reply_style: str = ""
    group_chat_prompt: str = ""
    private_chat_prompt: str = ""
    multiple_reply_style: str = ""
    emotion_trait: str = ""


class PersonaUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    description: Optional[str] = None
    nickname: Optional[str] = None
    alias_names: Optional[list[str] | str] = None
    personality: Optional[str] = None
    behavior_style: Optional[str] = None
    reply_style: Optional[str] = None
    group_chat_prompt: Optional[str] = None
    private_chat_prompt: Optional[str] = None
    multiple_reply_style: Optional[str] = None
    emotion_trait: Optional[str] = None


class MutationResponse(BaseModel):
    success: bool = True
    removed: bool = False


def _persona_item(persona_id: str) -> PersonaItem:
    profile = persona_service.get_persona(persona_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="人设不存在")
    payload: dict[str, Any] = {
        "id": profile.id,
        "name": profile.name,
        "alias_names": persona_service.alias_names_of(profile),
        "usage": PersonaUsage(**persona_service.list_usage(profile.id)),
        "created_at": profile.created_at,
        "updated_at": profile.updated_at,
    }
    for key in PERSONA_TEXT_FIELDS:
        payload[key] = getattr(profile, key, "") or ""
    return PersonaItem(**payload)


@router.get("", response_model=PersonaListResponse)
async def list_personas() -> PersonaListResponse:
    return PersonaListResponse(data=[_persona_item(profile.id) for profile in persona_service.list_personas()])


@router.get("/{persona_id}", response_model=PersonaResponse)
async def get_persona(persona_id: str) -> PersonaResponse:
    return PersonaResponse(data=_persona_item(persona_id))


@router.post(
    "",
    response_model=PersonaResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_auth_with_rate_limit)],
)
async def create_persona(request: PersonaCreateRequest) -> PersonaResponse:
    fields = request.model_dump()
    fields.pop("persona_id", None)
    name = fields.pop("name")
    alias_names = fields.pop("alias_names", [])
    try:
        profile = persona_service.create_persona(
            name=name,
            persona_id=request.persona_id,
            alias_names=alias_names,
            **apply_text_fields(fields),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return PersonaResponse(data=_persona_item(profile.id))


@router.patch("/{persona_id}", response_model=PersonaResponse, dependencies=[Depends(require_auth_with_rate_limit)])
async def update_persona(persona_id: str, request: PersonaUpdateRequest) -> PersonaResponse:
    changes = request.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="没有需要更新的字段")
    try:
        persona_service.update_persona(persona_id, **changes)
    except ValueError as exc:
        detail = str(exc)
        code = status.HTTP_404_NOT_FOUND if detail.startswith("人设不存在") else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=code, detail=detail) from exc
    return PersonaResponse(data=_persona_item(persona_id))


@router.delete("/{persona_id}", response_model=MutationResponse, dependencies=[Depends(require_auth_with_rate_limit)])
async def delete_persona(persona_id: str) -> MutationResponse:
    try:
        removed = persona_service.delete_persona(persona_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return MutationResponse(removed=removed)
