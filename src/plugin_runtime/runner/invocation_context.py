"""Runner 当前插件调用的只读请求上下文。"""

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Generator, Mapping, Optional

from src.plugin_runtime.request_scope import PluginRequestScope


@dataclass(frozen=True, slots=True)
class PluginInvocationContext:
    plugin_id: str
    component_full_name: str
    request_scope: Optional[PluginRequestScope]
    effective_plugin_config: Mapping[str, Any]
    invocation_token: str


_current_invocation: ContextVar[Optional[PluginInvocationContext]] = ContextVar(
    "maibot_plugin_invocation_context",
    default=None,
)


def get_current_invocation_context() -> Optional[PluginInvocationContext]:
    return _current_invocation.get()


def get_current_request_scope() -> Optional[PluginRequestScope]:
    context = get_current_invocation_context()
    return context.request_scope if context is not None else None


def get_current_effective_plugin_config() -> dict[str, Any]:
    context = get_current_invocation_context()
    if context is None:
        return {}
    return deepcopy(dict(context.effective_plugin_config))


def get_current_invocation_token() -> str:
    context = get_current_invocation_context()
    return context.invocation_token if context is not None else ""


@contextmanager
def bind_invocation_context(context: PluginInvocationContext) -> Generator[PluginInvocationContext, None, None]:
    token = _current_invocation.set(context)
    try:
        yield context
    finally:
        _current_invocation.reset(token)
