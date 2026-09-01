"""Host 端短生命周期插件调用作用域令牌注册表。"""

from dataclasses import dataclass
from secrets import token_urlsafe
from typing import Optional

from src.plugin_runtime.request_scope import PluginRequestScope


@dataclass(frozen=True, slots=True)
class InvocationScopeRecord:
    """令牌绑定的不可变调用身份，不包含配置值。"""

    supervisor_id: str
    plugin_id: str
    component_full_name: str
    component_type: str
    scope: PluginRequestScope


class InvocationScopeRegistry:
    """只保存当前尚未结束的 Host→Runner 调用令牌。"""

    def __init__(self, supervisor_id: str) -> None:
        self._supervisor_id = supervisor_id
        self._records: dict[str, InvocationScopeRecord] = {}

    def issue(
        self,
        *,
        plugin_id: str,
        component_full_name: str,
        component_type: str,
        scope: PluginRequestScope,
    ) -> str:
        token = token_urlsafe(32)
        self._records[token] = InvocationScopeRecord(
            supervisor_id=self._supervisor_id,
            plugin_id=plugin_id,
            component_full_name=component_full_name,
            component_type=component_type,
            scope=scope,
        )
        return token

    def validate(self, token: str, plugin_id: str) -> Optional[InvocationScopeRecord]:
        record = self._records.get(token)
        if record is None:
            return None
        if record.supervisor_id != self._supervisor_id or record.plugin_id != plugin_id:
            return None
        return record

    def revoke(self, token: str) -> None:
        self._records.pop(token, None)

    def clear(self) -> None:
        self._records.clear()
