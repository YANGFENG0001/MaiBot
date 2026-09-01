"""BotProfile 请求级插件配置安全覆盖。"""

from copy import deepcopy
from typing import Any, Mapping

from jsonschema import Draft202012Validator

_HARD_DENY_EXACT = frozenset(
    {
        "plugin.config_version",
        "config_version",
        "enabled",
        "enable",
        "host",
        "bind",
        "port",
        "account",
        "account_id",
        "qq",
        "qq_id",
        "self_id",
        "token",
        "access_token",
        "secret",
        "password",
        "api_key",
        "access_key",
        "private_key",
        "data_dir",
        "database_path",
        "db_path",
        "log_dir",
        "executable",
        "executable_path",
        "node_path",
        "python_path",
        "command",
        "proxy_password",
        "certificate_private_key",
    }
)
_HARD_DENY_PARTS = frozenset(
    {
        "token",
        "secret",
        "password",
        "apikey",
        "api_key",
        "accesskey",
        "access_key",
        "private_key",
        "credential",
        "listen",
        "bind",
        "host",
        "port",
        "account",
        "qq",
        "self_id",
        "data_dir",
        "database",
        "db_path",
        "log_dir",
        "executable",
        "node_path",
        "python_path",
        "command",
        "process",
        "enabled",
        "enable",
        "disabled",
        "dependency",
        "dependencies",
        "load",
        "auto_load",
        "path",
        "directory",
        "dir",
        "certificate",
    }
)


class PluginConfigOverlayError(ValueError):
    """请求级插件配置覆盖不安全或不符合 Schema。"""

    def __init__(self, path: str, error_code: str) -> None:
        self.path = path
        self.error_code = error_code
        super().__init__(f"插件请求级配置覆盖无效: path={path or '<root>'} code={error_code}")


def _normalize_name(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def is_hard_denied_path(path: str) -> bool:
    """判断路径是否属于进程、账号、密钥或文件系统硬禁用类别。"""

    normalized = _normalize_name(path)
    if normalized in _HARD_DENY_EXACT:
        return True
    parts = tuple(part for part in normalized.split(".") if part)
    return any(part in _HARD_DENY_PARTS for part in parts)


def _schema_properties(schema: Mapping[str, Any]) -> Mapping[str, Any]:
    properties = schema.get("properties")
    if isinstance(properties, Mapping):
        return properties
    fields = schema.get("fields")
    if isinstance(fields, Mapping):
        return fields
    nested = schema.get("nested")
    if isinstance(nested, Mapping):
        return nested
    return {}


def _field_schema(schema: Mapping[str, Any], key: str) -> Mapping[str, Any] | None:
    raw = _schema_properties(schema).get(key)
    return raw if isinstance(raw, Mapping) else None


def _is_request_overridable(schema: Mapping[str, Any], path: str) -> bool:
    if is_hard_denied_path(path):
        return False
    scope = str(schema.get("x-scope") or "").strip().lower()
    explicit = schema.get("x-bot-profile-overridable")
    legacy = schema.get("x-workspace-overridable")
    if scope in {"process", "secret"}:
        return False
    if explicit is True:
        return scope == "request"
    return explicit is None and legacy is True and not scope


def _validate_leaf(schema: Mapping[str, Any], value: Any, path: str) -> None:
    try:
        Draft202012Validator(dict(schema)).validate(value)
    except Exception as exc:
        raise PluginConfigOverlayError(path, "schema_validation_failed") from exc


def validate_and_collect_override_paths(
    schema: Mapping[str, Any],
    overrides: Mapping[str, Any],
    *,
    prefix: str = "",
) -> tuple[str, ...]:
    """验证覆盖对象并返回允许的叶子路径，不包含覆盖值。"""

    paths: list[str] = []
    for key, value in overrides.items():
        if not isinstance(key, str) or not key.strip():
            raise PluginConfigOverlayError(prefix, "invalid_path")
        path = f"{prefix}.{key}" if prefix else key
        field_schema = _field_schema(schema, key)
        if field_schema is None:
            raise PluginConfigOverlayError(path, "unknown_path")
        if is_hard_denied_path(path):
            raise PluginConfigOverlayError(path, "hard_denied")

        child_properties = _schema_properties(field_schema)
        if isinstance(value, Mapping) and child_properties:
            child_paths = validate_and_collect_override_paths(field_schema, value, prefix=path)
            if not child_paths and not _is_request_overridable(field_schema, path):
                raise PluginConfigOverlayError(path, "not_overridable")
            paths.extend(child_paths)
            continue

        if value is None:
            raise PluginConfigOverlayError(path, "null_not_allowed")
        if not _is_request_overridable(field_schema, path):
            raise PluginConfigOverlayError(path, "not_overridable")
        _validate_leaf(field_schema, value, path)
        paths.append(path)
    return tuple(paths)


def _deep_merge(target: dict[str, Any], overrides: Mapping[str, Any]) -> None:
    for key, value in overrides.items():
        if isinstance(value, Mapping) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = deepcopy(value)


def apply_plugin_config_overrides(
    base_config: Mapping[str, Any],
    schema: Mapping[str, Any],
    overrides: Mapping[str, Any],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """在深拷贝上应用安全覆盖，并返回有效配置及叶子路径。"""

    allowed_paths = validate_and_collect_override_paths(schema, overrides)
    effective = deepcopy(dict(base_config))
    _deep_merge(effective, overrides)
    try:
        Draft202012Validator(dict(schema)).validate(effective)
    except Exception as exc:
        raise PluginConfigOverlayError("", "effective_schema_validation_failed") from exc
    return effective, allowed_paths
