"""每条消息使用的 Bot/Workspace 请求上下文。"""

from contextlib import contextmanager
from contextvars import Context, ContextVar
from dataclasses import dataclass
from typing import Coroutine, Generator, Optional, TypeVar

import asyncio


@dataclass(frozen=True, slots=True)
class SessionWorkspaceContext:
    """仅描述真实聊天流所属 Workspace 的稳定快照。"""

    session_id: str
    workspace_id: str
    workspace_name: str
    group_bot_profile_id: str
    default_memory_space_id: str
    policy_revision: int


@dataclass(frozen=True, slots=True)
class BotRequestContext:
    """一次真实入站消息贯穿 Planner、Replyer、工具和记忆链路的不可变快照。"""

    trace_id: str
    session_id: str
    workspace_id: str
    person_id: str
    active_bot_profile_id: str
    active_bot_profile_type: str
    permission_group_id: str
    access_mode: str
    security_domain: str
    home_memory_space_id: str
    readable_space_ids: tuple[str, ...]
    readable_partition_ids: tuple[str, ...]
    writable_partition_ids: tuple[str, ...]
    audience_type: str
    policy_revision: int


_current_request_context: ContextVar[Optional[BotRequestContext]] = ContextVar(
    "maibot_bot_request_context",
    default=None,
)

T = TypeVar("T")


def get_current_request_context(*, required: bool = False) -> Optional[BotRequestContext]:
    """读取当前异步调用链绑定的请求上下文。"""

    context = _current_request_context.get()
    if required and context is None:
        raise RuntimeError("当前调用链未绑定 BotRequestContext")
    return context


@contextmanager
def bind_request_context(context: BotRequestContext) -> Generator[BotRequestContext, None, None]:
    """临时绑定请求上下文，并在正常、异常或取消退出时可靠恢复。"""

    token = _current_request_context.set(context)
    try:
        yield context
    finally:
        _current_request_context.reset(token)


def create_background_task_without_request_context(
    coroutine: Coroutine[object, object, T],
    *,
    name: Optional[str] = None,
) -> "asyncio.Task[T]":
    """在全新 Context 中创建后台任务，防止复制已经结束的消息上下文。"""

    return asyncio.create_task(coroutine, name=name, context=Context())
