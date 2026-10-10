"""PersonaProfile 数据访问：可被 BotProfile / Workspace 复用的人设覆盖层。

人设是**覆盖层**语义：字段为空表示继续沿用全局 `bot.personality` 配置，
只有非空字段才会在运行期顶替全局值（见 `WorkspaceService._resolve_persona` 与
`PersonaOverlay`）。因此这里的 CRUD 只负责存取，不做任何「补全默认值」的动作，
否则会把「未覆盖」悄悄变成「显式覆盖成全局值」，破坏继承语义。
"""

from datetime import datetime
from typing import Any, Mapping, Optional, Sequence

import json
import uuid

from sqlmodel import select

from src.common.database.database import get_db_session
from src.common.database.database_model import BotProfile, PersonaProfile, Workspace

# 全部纯文本字段。`alias_names` 单独处理（以 JSON 数组落库）。
PERSONA_TEXT_FIELDS: tuple[str, ...] = (
    "description",
    "nickname",
    "personality",
    "behavior_style",
    "reply_style",
    "group_chat_prompt",
    "private_chat_prompt",
    "multiple_reply_style",
    "emotion_trait",
)

PERSONA_WRITABLE_FIELDS: frozenset[str] = frozenset({"name", "alias_names", *PERSONA_TEXT_FIELDS})

PERSONA_ID_PREFIX = "persona-profile-"


def new_persona_id() -> str:
    """生成新的人设 ID。

    用随机后缀而不是名称派生：人设名允许中文与空格，直接拿名称做 ID 会在
    URL 路径与数据库主键上引入转义问题。
    """

    return f"{PERSONA_ID_PREFIX}{uuid.uuid4().hex[:12]}"


# 分隔别名的标点：半角/全角逗号、顿号、换行。中文用户默认会打全角标点，
# 只按半角逗号切会把「乙 ，丙」当成一个别名。
ALIAS_SEPARATORS = str.maketrans({"，": ",", "、": ",", "\n": ",", "\r": ","})


def _normalize_alias_names(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = [item.strip() for item in value.translate(ALIAS_SEPARATORS).split(",")]
        return [item for item in parts if item]
    if isinstance(value, Sequence):
        normalized: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError("alias_names 只能是字符串数组")
            stripped = item.strip()
            if stripped:
                normalized.append(stripped)
        return normalized
    raise ValueError("alias_names 只能是字符串数组")


class PersonaService:
    def list_personas(self) -> list[PersonaProfile]:
        with get_db_session() as session:
            return list(session.exec(select(PersonaProfile).order_by(PersonaProfile.name)).all())

    def get_persona(self, persona_id: str) -> Optional[PersonaProfile]:
        with get_db_session() as session:
            return session.get(PersonaProfile, persona_id)

    def list_usage(self, persona_id: str) -> dict[str, list[str]]:
        """列出引用该人设的 BotProfile 与 Workspace，供删除前提示。"""

        with get_db_session() as session:
            bots = session.exec(select(BotProfile).where(BotProfile.persona_profile_id == persona_id)).all()
            workspaces = session.exec(select(Workspace).where(Workspace.persona_profile_id == persona_id)).all()
        return {
            "bot_profiles": sorted(profile.id for profile in bots),
            "workspaces": sorted(workspace.id for workspace in workspaces),
        }

    def create_persona(
        self,
        *,
        name: str,
        persona_id: Optional[str] = None,
        alias_names: Any = None,
        **fields: Any,
    ) -> PersonaProfile:
        normalized_name = (name or "").strip()
        if not normalized_name:
            raise ValueError("人设名称不能为空")
        unknown = set(fields) - set(PERSONA_TEXT_FIELDS)
        if unknown:
            raise ValueError(f"不支持的人设字段: {', '.join(sorted(unknown))}")

        with get_db_session() as session:
            if session.exec(select(PersonaProfile).where(PersonaProfile.name == normalized_name)).first():
                raise ValueError(f"人设名称已存在: {normalized_name}")

            profile = PersonaProfile(
                id=(persona_id or new_persona_id()),
                name=normalized_name,
                alias_names_json=json.dumps(_normalize_alias_names(alias_names), ensure_ascii=False),
            )
            if session.get(PersonaProfile, profile.id) is not None:
                raise ValueError(f"人设 ID 已存在: {profile.id}")
            for key in PERSONA_TEXT_FIELDS:
                setattr(profile, key, str(fields.get(key) or ""))
            session.add(profile)
            return profile

    def update_persona(self, persona_id: str, **changes: Any) -> PersonaProfile:
        unknown = set(changes) - set(PERSONA_WRITABLE_FIELDS)
        if unknown:
            raise ValueError(f"不支持的人设字段: {', '.join(sorted(unknown))}")

        with get_db_session() as session:
            profile = session.get(PersonaProfile, persona_id)
            if profile is None:
                raise ValueError(f"人设不存在: {persona_id}")

            if "name" in changes:
                normalized_name = str(changes["name"] or "").strip()
                if not normalized_name:
                    raise ValueError("人设名称不能为空")
                conflict = session.exec(
                    select(PersonaProfile).where(
                        PersonaProfile.name == normalized_name,
                        PersonaProfile.id != persona_id,
                    )
                ).first()
                if conflict is not None:
                    raise ValueError(f"人设名称已存在: {normalized_name}")
                profile.name = normalized_name

            if "alias_names" in changes:
                profile.alias_names_json = json.dumps(
                    _normalize_alias_names(changes["alias_names"]), ensure_ascii=False
                )

            for key in PERSONA_TEXT_FIELDS:
                if key in changes:
                    setattr(profile, key, str(changes[key] or ""))

            profile.updated_at = datetime.now()
            session.add(profile)
            return profile

    def delete_persona(self, persona_id: str) -> bool:
        """删除人设；仍被 BotProfile / Workspace 引用时拒绝。

        不级联清空引用：`persona_profile_id` 变成悬空外键会让运行期
        `_resolve_persona` 静默退回空覆盖层，表现为「人设莫名消失」，
        比直接报错更难排查。
        """

        usage = self.list_usage(persona_id)
        if usage["bot_profiles"] or usage["workspaces"]:
            targets = ", ".join([*usage["bot_profiles"], *usage["workspaces"]])
            raise ValueError(f"人设仍被引用，无法删除: {targets}")

        with get_db_session() as session:
            profile = session.get(PersonaProfile, persona_id)
            if profile is None:
                return False
            session.delete(profile)
            return True

    def alias_names_of(self, profile: PersonaProfile) -> list[str]:
        """读取 `alias_names_json`，格式异常时抛错而不是静默返回空列表。"""

        try:
            loaded = json.loads(profile.alias_names_json or "[]")
        except json.JSONDecodeError as exc:
            raise ValueError(f"人设 {profile.id} 的 alias_names_json 不是合法 JSON") from exc
        if not isinstance(loaded, list) or not all(isinstance(item, str) for item in loaded):
            raise ValueError(f"人设 {profile.id} 的 alias_names_json 格式无效")
        return loaded


def apply_text_fields(target: Mapping[str, Any]) -> dict[str, str]:
    """从请求体中筛出纯文本字段，忽略未提供的键。"""

    return {key: target[key] for key in PERSONA_TEXT_FIELDS if key in target}


persona_service = PersonaService()
