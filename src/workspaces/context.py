"""Workspace 子系统运行时上下文模型。"""

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class MemoryScope:
    """一次记忆操作允许访问的逻辑空间快照。"""

    workspace_id: str
    primary_space_id: str
    readable_space_ids: tuple[str, ...]
    writable_space_ids: tuple[str, ...]
    shared_session_ids: tuple[str, ...] = ()
    readable_partition_ids: tuple[str, ...] = ()
    writable_partition_ids: tuple[str, ...] = ()
    access_mode: str = "normal"
    security_domain: str = "normal"
    trace_id: str = ""

    def can_read(self, memory_space_id: str) -> bool:
        return memory_space_id in self.readable_space_ids

    def can_write(self, memory_space_id: str) -> bool:
        return memory_space_id in self.writable_space_ids


@dataclass(frozen=True, slots=True)
class PersonaOverlay:
    """工作区生效的人设覆盖；空字段表示继续使用全局配置。"""

    profile_id: str = ""
    nickname: str = ""
    alias_names: tuple[str, ...] = ()
    personality: str = ""
    behavior_style: str = ""
    reply_style: str = ""
    group_chat_prompt: str = ""
    private_chat_prompt: str = ""
    multiple_reply_style: str = ""
    emotion_trait: str = ""


@dataclass(frozen=True, slots=True)
class BotProfileContext:
    """一次请求使用的可路由 Bot 身份快照。"""

    profile_id: str
    profile_type: str
    home_memory_space_id: str
    policy_revision: int
    parent_profile_id: str = ""


@dataclass(frozen=True, slots=True)
class WorkspaceContext:
    """一次聊天轮次内统一使用的 Workspace 策略快照。"""

    workspace_id: str
    workspace_name: str
    memory_space_id: str
    policy_revision: int
    bot_profile: BotProfileContext = field(default_factory=lambda: BotProfileContext("bot-profile-public", "public", "memory-space-public", 1))
    inherit_global_tools: bool = True
    inherit_global_plugins: bool = True
    allowed_tools: frozenset[str] = field(default_factory=frozenset)
    denied_tools: frozenset[str] = field(default_factory=frozenset)
    persona: PersonaOverlay = field(default_factory=PersonaOverlay)

    def is_tool_allowed(self, tool_name: str) -> bool:
        """根据继承模式和显式规则判断工具是否可用。"""

        if tool_name in self.denied_tools:
            return False
        if self.inherit_global_tools:
            return True
        return tool_name in self.allowed_tools
