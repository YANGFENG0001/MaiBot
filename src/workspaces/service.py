"""Workspace 子系统数据访问、会话归属解析与策略计算。"""

from datetime import datetime
from hashlib import sha256
from typing import Iterable, Optional
from uuid import uuid4

import json

from sqlmodel import col, func, select

from src.common.database.database import get_db_session
from src.common.database.database_model import (
    ChatSession,
    MemoryObjectSpace,
    MemorySpace,
    MemorySpaceACL,
    MemorySpaceMigrationState,
    PersonaProfile,
    Workspace,
    WorkspaceAuditLog,
    WorkspaceMembership,
    WorkspaceSelector,
    WorkspaceToolPolicy,
)
from src.common.logger import get_logger

from .context import MemoryScope, PersonaOverlay, WorkspaceContext

logger = get_logger("workspace")

DEFAULT_WORKSPACE_ID = "workspace-default"
PUBLIC_MEMORY_SPACE_ID = "memory-space-public"


class WorkspaceService:
    """管理工作区，并将真实 ChatSession 解析为唯一主工作区。"""

    def ensure_defaults(self) -> tuple[Workspace, MemorySpace]:
        """幂等建立兼容现有行为的默认工作区和公共记忆空间。"""

        now = datetime.now()
        with get_db_session() as session:
            memory_space = session.get(MemorySpace, PUBLIC_MEMORY_SPACE_ID)
            if memory_space is None:
                memory_space = MemorySpace(
                    id=PUBLIC_MEMORY_SPACE_ID,
                    name="公共记忆库",
                    description="兼容现有 MaiBot 记忆行为的默认公共空间",
                    space_type="public",
                    enabled=True,
                    created_at=now,
                    updated_at=now,
                )
                session.add(memory_space)
                session.flush()

            workspace = session.get(Workspace, DEFAULT_WORKSPACE_ID)
            if workspace is None:
                workspace = Workspace(
                    id=DEFAULT_WORKSPACE_ID,
                    name="默认子系统",
                    description="所有未显式分配聊天的兼容工作区",
                    memory_space_id=memory_space.id,
                    is_default=True,
                    enabled=True,
                    inherit_global_tools=True,
                    inherit_global_plugins=True,
                    created_at=now,
                    updated_at=now,
                )
                session.add(workspace)
            return workspace, memory_space

    def list_workspaces(self) -> list[Workspace]:
        self.ensure_defaults()
        with get_db_session() as session:
            return list(session.exec(select(Workspace).order_by(col(Workspace.is_default).desc(), Workspace.name)).all())

    def list_memory_spaces(self) -> list[MemorySpace]:
        self.ensure_defaults()
        with get_db_session() as session:
            return list(session.exec(select(MemorySpace).order_by(MemorySpace.name)).all())

    def get_workspace(self, workspace_id: str) -> Optional[Workspace]:
        self.ensure_defaults()
        with get_db_session() as session:
            return session.get(Workspace, workspace_id)

    def get_membership_counts(self) -> dict[str, int]:
        with get_db_session() as session:
            rows = session.exec(
                select(WorkspaceMembership.workspace_id, func.count(WorkspaceMembership.id)).group_by(
                    WorkspaceMembership.workspace_id
                )
            ).all()
        return {str(workspace_id): int(count) for workspace_id, count in rows}

    def create_workspace(
        self,
        *,
        name: str,
        description: str = "",
        memory_mode: str = "private",
        memory_space_id: str = "",
        inherit_global_tools: bool = True,
        inherit_global_plugins: bool = True,
    ) -> Workspace:
        """创建工作区；默认同时建立独立逻辑记忆空间。"""

        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("子系统名称不能为空")
        if memory_mode not in {"private", "public", "existing"}:
            raise ValueError("memory_mode 必须为 private、public 或 existing")

        self.ensure_defaults()
        now = datetime.now()
        workspace_id = f"workspace-{uuid4().hex}"
        with get_db_session() as session:
            existing = session.exec(select(Workspace).where(Workspace.name == normalized_name)).first()
            if existing is not None:
                raise ValueError(f"子系统名称已存在：{normalized_name}")

            selected_space_id = memory_space_id.strip()
            if memory_mode == "public":
                selected_space_id = PUBLIC_MEMORY_SPACE_ID
            elif memory_mode == "private":
                selected_space_id = f"memory-space-{uuid4().hex}"
                memory_space = MemorySpace(
                    id=selected_space_id,
                    name=f"{normalized_name}记忆库",
                    description=f"{normalized_name} 的独立逻辑记忆空间",
                    space_type="private",
                    enabled=True,
                    created_at=now,
                    updated_at=now,
                )
                session.add(memory_space)
            elif not selected_space_id or session.get(MemorySpace, selected_space_id) is None:
                raise ValueError("指定的记忆空间不存在")

            workspace = Workspace(
                id=workspace_id,
                name=normalized_name,
                description=description.strip(),
                memory_space_id=selected_space_id,
                enabled=True,
                inherit_global_tools=inherit_global_tools,
                inherit_global_plugins=inherit_global_plugins,
                created_at=now,
                updated_at=now,
            )
            session.add(workspace)
            session.add(
                WorkspaceAuditLog(
                    workspace_id=workspace_id,
                    action="workspace.create",
                    actor="webui",
                    details_json=json.dumps({"memory_mode": memory_mode}, ensure_ascii=False),
                    created_at=now,
                )
            )
            session.flush()
            return workspace

    def update_workspace(
        self,
        workspace_id: str,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        memory_space_id: Optional[str] = None,
        enabled: Optional[bool] = None,
        inherit_global_tools: Optional[bool] = None,
        inherit_global_plugins: Optional[bool] = None,
    ) -> Workspace:
        now = datetime.now()
        with get_db_session() as session:
            workspace = session.get(Workspace, workspace_id)
            if workspace is None:
                raise LookupError("子系统不存在")
            if name is not None:
                normalized_name = name.strip()
                if not normalized_name:
                    raise ValueError("子系统名称不能为空")
                duplicate = session.exec(
                    select(Workspace).where(Workspace.name == normalized_name, Workspace.id != workspace_id)
                ).first()
                if duplicate is not None:
                    raise ValueError(f"子系统名称已存在：{normalized_name}")
                workspace.name = normalized_name
            if description is not None:
                workspace.description = description.strip()
            if memory_space_id is not None:
                if session.get(MemorySpace, memory_space_id) is None:
                    raise ValueError("指定的记忆空间不存在")
                workspace.memory_space_id = memory_space_id
            if enabled is not None:
                if workspace.is_default and not enabled:
                    raise ValueError("默认子系统不能禁用")
                workspace.enabled = enabled
            if inherit_global_tools is not None:
                workspace.inherit_global_tools = inherit_global_tools
            if inherit_global_plugins is not None:
                workspace.inherit_global_plugins = inherit_global_plugins
            workspace.policy_revision += 1
            workspace.updated_at = now
            session.add(workspace)
            session.add(
                WorkspaceAuditLog(
                    workspace_id=workspace.id,
                    action="workspace.update",
                    actor="webui",
                    details_json="{}",
                    created_at=now,
                )
            )
            session.flush()
            return workspace

    def assign_sessions(self, workspace_id: str, session_ids: Iterable[str]) -> int:
        """把已存在的真实聊天流原子地改派到指定工作区。"""

        normalized_ids = tuple(dict.fromkeys(item.strip() for item in session_ids if item.strip()))
        if not normalized_ids:
            return 0
        now = datetime.now()
        with get_db_session() as session:
            workspace = session.get(Workspace, workspace_id)
            if workspace is None or not workspace.enabled:
                raise LookupError("目标子系统不存在或已禁用")
            existing_sessions = set(
                session.exec(select(ChatSession.session_id).where(col(ChatSession.session_id).in_(normalized_ids))).all()
            )
            missing = sorted(set(normalized_ids) - existing_sessions)
            if missing:
                raise ValueError(f"以下聊天流不存在：{', '.join(missing)}")

            memberships = session.exec(
                select(WorkspaceMembership).where(col(WorkspaceMembership.session_id).in_(normalized_ids))
            ).all()
            by_session_id = {item.session_id: item for item in memberships}
            for session_id in normalized_ids:
                membership = by_session_id.get(session_id)
                if membership is None:
                    membership = WorkspaceMembership(
                        workspace_id=workspace_id,
                        session_id=session_id,
                        assigned_by="manual",
                        created_at=now,
                        updated_at=now,
                    )
                else:
                    membership.workspace_id = workspace_id
                    membership.assigned_by = "manual"
                    membership.updated_at = now
                session.add(membership)
            workspace.policy_revision += 1
            workspace.updated_at = now
            session.add(workspace)
            return len(normalized_ids)

    def unassign_session(self, session_id: str) -> bool:
        with get_db_session() as session:
            membership = session.exec(
                select(WorkspaceMembership).where(WorkspaceMembership.session_id == session_id)
            ).first()
            if membership is None:
                return False
            session.delete(membership)
            return True

    def list_members(self, workspace_id: str) -> list[tuple[WorkspaceMembership, ChatSession]]:
        with get_db_session() as session:
            rows = session.exec(
                select(WorkspaceMembership, ChatSession)
                .join(ChatSession, WorkspaceMembership.session_id == ChatSession.session_id)
                .where(WorkspaceMembership.workspace_id == workspace_id)
                .order_by(col(ChatSession.last_active_timestamp).desc())
            ).all()
            return list(rows)

    def set_tool_policy(self, workspace_id: str, tool_name: str, effect: str) -> WorkspaceToolPolicy:
        if effect not in {"allow", "deny"}:
            raise ValueError("工具策略必须为 allow 或 deny")
        normalized_tool_name = tool_name.strip()
        if not normalized_tool_name:
            raise ValueError("工具名称不能为空")
        now = datetime.now()
        with get_db_session() as session:
            workspace = session.get(Workspace, workspace_id)
            if workspace is None:
                raise LookupError("子系统不存在")
            policy = session.exec(
                select(WorkspaceToolPolicy).where(
                    WorkspaceToolPolicy.workspace_id == workspace_id,
                    WorkspaceToolPolicy.tool_name == normalized_tool_name,
                )
            ).first()
            if policy is None:
                policy = WorkspaceToolPolicy(
                    workspace_id=workspace_id,
                    tool_name=normalized_tool_name,
                    effect=effect,
                    created_at=now,
                    updated_at=now,
                )
            else:
                policy.effect = effect
                policy.updated_at = now
            session.add(policy)
            workspace.policy_revision += 1
            workspace.updated_at = now
            session.add(workspace)
            session.flush()
            return policy

    def remove_tool_policy(self, workspace_id: str, tool_name: str) -> bool:
        with get_db_session() as session:
            policy = session.exec(
                select(WorkspaceToolPolicy).where(
                    WorkspaceToolPolicy.workspace_id == workspace_id,
                    WorkspaceToolPolicy.tool_name == tool_name,
                )
            ).first()
            if policy is None:
                return False
            session.delete(policy)
            workspace = session.get(Workspace, workspace_id)
            if workspace is not None:
                workspace.policy_revision += 1
                workspace.updated_at = datetime.now()
                session.add(workspace)
            return True

    def resolve_context(self, session_id: str) -> WorkspaceContext:
        """按精确成员、动态选择器、默认工作区的顺序解析策略。"""

        self.ensure_defaults()
        with get_db_session() as session:
            workspace = self._resolve_workspace(session, session_id)
            tool_policies = session.exec(
                select(WorkspaceToolPolicy).where(WorkspaceToolPolicy.workspace_id == workspace.id)
            ).all()
            allowed_tools = frozenset(item.tool_name for item in tool_policies if item.effect == "allow")
            denied_tools = frozenset(item.tool_name for item in tool_policies if item.effect == "deny")
            persona = self._resolve_persona(session, workspace.persona_profile_id)
            return WorkspaceContext(
                workspace_id=workspace.id,
                workspace_name=workspace.name,
                memory_space_id=workspace.memory_space_id,
                policy_revision=workspace.policy_revision,
                inherit_global_tools=workspace.inherit_global_tools,
                inherit_global_plugins=workspace.inherit_global_plugins,
                allowed_tools=allowed_tools,
                denied_tools=denied_tools,
                persona=persona,
            )

    def resolve_readable_memory_space_ids(self, owner_space_id: str) -> tuple[str, ...]:
        """执行 read_from + expose_to 双向握手，返回可检索空间集合。"""

        with get_db_session() as session:
            outbound = session.exec(
                select(MemorySpaceACL).where(
                    MemorySpaceACL.owner_space_id == owner_space_id,
                    MemorySpaceACL.can_read_from_peer == True,  # noqa: E712
                )
            ).all()
            if not outbound:
                return (owner_space_id,)
            peer_ids = [item.peer_space_id for item in outbound]
            inbound = session.exec(
                select(MemorySpaceACL).where(
                    col(MemorySpaceACL.owner_space_id).in_(peer_ids),
                    MemorySpaceACL.peer_space_id == owner_space_id,
                    MemorySpaceACL.expose_to_peer == True,  # noqa: E712
                )
            ).all()
            exposed_peer_ids = {item.owner_space_id for item in inbound}
            return tuple(dict.fromkeys([owner_space_id, *[item for item in peer_ids if item in exposed_peer_ids]]))

    def get_memory_space(self, memory_space_id: str) -> Optional[MemorySpace]:
        self.ensure_defaults()
        with get_db_session() as session:
            return session.get(MemorySpace, memory_space_id)

    def create_memory_space(
        self,
        *,
        name: str,
        description: str = "",
        space_type: str = "private",
    ) -> MemorySpace:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("记忆空间名称不能为空")
        if space_type not in {"private", "public"}:
            raise ValueError("记忆空间类型只能是 private 或 public")
        now = datetime.now()
        with get_db_session() as session:
            if session.exec(select(MemorySpace).where(MemorySpace.name == normalized_name)).first() is not None:
                raise ValueError(f"记忆空间名称已存在: {normalized_name}")
            space = MemorySpace(
                id=f"memory-space-{uuid4().hex}",
                name=normalized_name,
                description=description.strip(),
                space_type=space_type,
                enabled=True,
                created_at=now,
                updated_at=now,
            )
            session.add(space)
            session.flush()
            return space

    def update_memory_space(self, memory_space_id: str, **changes: object) -> MemorySpace:
        with get_db_session() as session:
            space = session.get(MemorySpace, memory_space_id)
            if space is None:
                raise LookupError("记忆空间不存在")
            if memory_space_id == PUBLIC_MEMORY_SPACE_ID and changes.get("enabled") is False:
                raise ValueError("默认公共记忆空间不能禁用")
            if "name" in changes:
                name = str(changes["name"] or "").strip()
                if not name:
                    raise ValueError("记忆空间名称不能为空")
                duplicate = session.exec(
                    select(MemorySpace).where(MemorySpace.name == name, MemorySpace.id != memory_space_id)
                ).first()
                if duplicate is not None:
                    raise ValueError(f"记忆空间名称已存在: {name}")
                space.name = name
            if "description" in changes:
                space.description = str(changes["description"] or "").strip()
            if "enabled" in changes:
                space.enabled = bool(changes["enabled"])
            space.policy_revision += 1
            space.updated_at = datetime.now()
            session.add(space)
            session.flush()
            return space

    def set_memory_space_acl(
        self,
        owner_space_id: str,
        peer_space_id: str,
        *,
        can_read_from_peer: bool,
        expose_to_peer: bool,
    ) -> MemorySpaceACL:
        if owner_space_id == peer_space_id:
            raise ValueError("不能为同一个记忆空间建立 ACL")
        now = datetime.now()
        with get_db_session() as session:
            if session.get(MemorySpace, owner_space_id) is None or session.get(MemorySpace, peer_space_id) is None:
                raise LookupError("记忆空间不存在")
            acl = session.exec(
                select(MemorySpaceACL).where(
                    MemorySpaceACL.owner_space_id == owner_space_id,
                    MemorySpaceACL.peer_space_id == peer_space_id,
                )
            ).first()
            if acl is None:
                acl = MemorySpaceACL(
                    owner_space_id=owner_space_id,
                    peer_space_id=peer_space_id,
                    created_at=now,
                    updated_at=now,
                )
            acl.can_read_from_peer = can_read_from_peer
            acl.expose_to_peer = expose_to_peer
            acl.updated_at = now
            session.add(acl)
            owner = session.get(MemorySpace, owner_space_id)
            if owner is not None:
                owner.policy_revision += 1
                owner.updated_at = now
                session.add(owner)
            session.flush()
            return acl

    def list_memory_space_acl(self, owner_space_id: str) -> list[MemorySpaceACL]:
        with get_db_session() as session:
            return list(
                session.exec(
                    select(MemorySpaceACL)
                    .where(MemorySpaceACL.owner_space_id == owner_space_id)
                    .order_by(MemorySpaceACL.peer_space_id)
                ).all()
            )

    def resolve_memory_scope(self, session_id: str = "", memory_space_id: str = "") -> MemoryScope:
        """统一解析写入主空间、ACL 可读空间和这些空间覆盖的聊天流。"""

        clean_session_id = str(session_id or "").strip()
        explicit_space_id = str(memory_space_id or "").strip()
        if explicit_space_id:
            space = self.get_memory_space(explicit_space_id)
            if space is None or not space.enabled:
                raise LookupError("记忆空间不存在或已禁用")
            primary_space_id = space.id
            workspace_id = ""
        else:
            context = self.resolve_context(clean_session_id)
            primary_space_id = context.memory_space_id
            workspace_id = context.workspace_id
        readable_space_ids = self.resolve_readable_memory_space_ids(primary_space_id)
        with get_db_session() as session:
            workspace_rows = session.exec(
                select(Workspace.id).where(col(Workspace.memory_space_id).in_(readable_space_ids), Workspace.enabled == True)  # noqa: E712
            ).all()
            shared_session_ids: tuple[str, ...] = ()
            if workspace_rows:
                sessions = session.exec(
                    select(WorkspaceMembership.session_id).where(
                        col(WorkspaceMembership.workspace_id).in_([str(item) for item in workspace_rows])
                    )
                ).all()
                shared_session_ids = tuple(dict.fromkeys(str(item) for item in sessions if str(item).strip()))
        if clean_session_id and clean_session_id not in shared_session_ids:
            shared_session_ids = (*shared_session_ids, clean_session_id)
        return MemoryScope(
            workspace_id=workspace_id,
            primary_space_id=primary_space_id,
            readable_space_ids=readable_space_ids,
            writable_space_ids=(primary_space_id,),
            shared_session_ids=shared_session_ids,
        )

    def register_memory_objects(
        self,
        *,
        object_type: str,
        object_ids: Iterable[str],
        memory_space_id: str,
        source_session_id: str = "",
        origin_space_id: Optional[str] = None,
    ) -> int:
        normalized_ids = tuple(dict.fromkeys(str(item).strip() for item in object_ids if str(item).strip()))
        if not normalized_ids:
            return 0
        created = 0
        with get_db_session() as session:
            for object_id in normalized_ids:
                existing = session.exec(
                    select(MemoryObjectSpace).where(
                        MemoryObjectSpace.object_type == object_type,
                        MemoryObjectSpace.object_id == object_id,
                        MemoryObjectSpace.memory_space_id == memory_space_id,
                    )
                ).first()
                if existing is not None:
                    continue
                session.add(
                    MemoryObjectSpace(
                        object_type=object_type,
                        object_id=object_id,
                        memory_space_id=memory_space_id,
                        source_session_id=source_session_id,
                        origin_space_id=origin_space_id,
                    )
                )
                created += 1
        return created

    def memory_object_space_ids(self, object_type: str, object_ids: Iterable[str]) -> dict[str, set[str]]:
        normalized_ids = tuple(dict.fromkeys(str(item).strip() for item in object_ids if str(item).strip()))
        if not normalized_ids:
            return {}
        with get_db_session() as session:
            rows = session.exec(
                select(MemoryObjectSpace).where(
                    MemoryObjectSpace.object_type == object_type,
                    col(MemoryObjectSpace.object_id).in_(normalized_ids),
                )
            ).all()
        result: dict[str, set[str]] = {}
        for row in rows:
            result.setdefault(row.object_id, set()).add(row.memory_space_id)
        return result

    def migrate_legacy_shared_memory_groups(self) -> int:
        """把旧 a_memorix.shared_memory_groups 幂等迁移为 Workspace + 私有记忆空间。"""

        from src.common.utils.utils_config import ChatConfigUtils
        from src.config.config import global_config

        groups = list(global_config.a_memorix.shared_memory_groups or [])
        raw_groups: list[set[str]] = []
        for group in groups:
            session_ids: set[str] = set()
            for target in group.targets or []:
                session_ids.update(ChatConfigUtils.get_target_session_ids(target))
            normalized = {item for item in session_ids if item}
            if normalized:
                raw_groups.append(normalized)

        # 旧配置允许共享组相互重叠；Workspace 主归属是一对一，因此先合并为连通分量。
        merged_groups: list[set[str]] = []
        for group_sessions in raw_groups:
            overlapping = [item for item in merged_groups if item.intersection(group_sessions)]
            if not overlapping:
                merged_groups.append(set(group_sessions))
                continue
            combined = set(group_sessions)
            for item in overlapping:
                combined.update(item)
                merged_groups.remove(item)
            merged_groups.append(combined)

        resolved_groups = sorted((sorted(item) for item in merged_groups), key=lambda item: item[0])
        payload_hash = sha256(json.dumps(resolved_groups, ensure_ascii=False).encode("utf-8")).hexdigest()
        migration_key = "legacy-shared-memory-groups-v1"
        with get_db_session() as session:
            state = session.get(MemorySpaceMigrationState, migration_key)
            if state is not None and state.payload_hash == payload_hash:
                return 0

        migrated = 0
        for index, session_ids in enumerate(resolved_groups, start=1):
            workspace_name = f"旧共享记忆组 {index}"
            with get_db_session() as session:
                existing = session.exec(select(Workspace).where(Workspace.name == workspace_name)).first()
            if existing is None:
                workspace = self.create_workspace(
                    name=workspace_name,
                    description="由旧版 a_memorix.shared_memory_groups 自动迁移",
                    memory_mode="private",
                )
                self.assign_sessions(workspace.id, session_ids)
            else:
                workspace = existing
                self.assign_sessions(workspace.id, session_ids)
            migrated += len(session_ids)

        with get_db_session() as session:
            state = session.get(MemorySpaceMigrationState, migration_key)
            if state is None:
                state = MemorySpaceMigrationState(migration_key=migration_key)
            state.payload_hash = payload_hash
            state.completed_at = datetime.now()
            session.add(state)
        return migrated

    @staticmethod
    def _resolve_persona(session, persona_profile_id: Optional[str]) -> PersonaOverlay:
        if not persona_profile_id:
            return PersonaOverlay()
        profile = session.get(PersonaProfile, persona_profile_id)
        if profile is None:
            return PersonaOverlay()
        alias_names = json.loads(profile.alias_names_json)
        if not isinstance(alias_names, list) or not all(isinstance(item, str) for item in alias_names):
            raise ValueError(f"人设 {profile.id} 的 alias_names_json 格式无效")
        return PersonaOverlay(
            profile_id=profile.id,
            nickname=profile.nickname,
            alias_names=tuple(alias_names),
            personality=profile.personality,
            behavior_style=profile.behavior_style,
            reply_style=profile.reply_style,
            group_chat_prompt=profile.group_chat_prompt,
            private_chat_prompt=profile.private_chat_prompt,
            multiple_reply_style=profile.multiple_reply_style,
            emotion_trait=profile.emotion_trait,
        )

    @staticmethod
    def _resolve_workspace(session, session_id: str) -> Workspace:
        membership = session.exec(
            select(WorkspaceMembership).where(WorkspaceMembership.session_id == session_id)
        ).first()
        if membership is not None:
            workspace = session.get(Workspace, membership.workspace_id)
            if workspace is not None and workspace.enabled:
                return workspace

        chat = session.exec(select(ChatSession).where(ChatSession.session_id == session_id)).first()
        if chat is not None:
            selectors = session.exec(
                select(WorkspaceSelector)
                .where(WorkspaceSelector.enabled == True)  # noqa: E712
                .order_by(col(WorkspaceSelector.priority).desc(), WorkspaceSelector.id)
            ).all()
            for selector in selectors:
                if WorkspaceService._selector_matches(selector, chat):
                    workspace = session.get(Workspace, selector.workspace_id)
                    if workspace is not None and workspace.enabled:
                        return workspace

        default_workspace = session.exec(
            select(Workspace).where(Workspace.is_default == True, Workspace.enabled == True)  # noqa: E712
        ).first()
        if default_workspace is None:
            raise RuntimeError("未找到可用的默认子系统")
        return default_workspace

    @staticmethod
    def _selector_matches(selector: WorkspaceSelector, chat: ChatSession) -> bool:
        if selector.platform and selector.platform != chat.platform:
            return False
        if selector.account_id and selector.account_id != (chat.account_id or ""):
            return False
        if selector.chat_type == "group" and not chat.group_id:
            return False
        if selector.chat_type == "private" and chat.group_id:
            return False
        target_id = chat.group_id if chat.group_id else chat.user_id
        return not selector.target_id or selector.target_id == (target_id or "")


workspace_service = WorkspaceService()
