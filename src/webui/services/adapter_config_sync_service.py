"""适配器连接配置同步服务。

将 MaiBot 适配器插件中的连接字段同步到外部适配器运行时配置。
每种适配器使用独立 Profile，避免 NapCat 与 SnowLuma 的字段互相污染；
未来适配器可以通过 ``data/adapter-sync-profiles.json`` 增加声明式 Profile。
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import json
import os
import tempfile

from src.common.logger import get_logger

logger = get_logger("webui.adapter_config_sync")


@dataclass(frozen=True)
class AdapterSyncProfile:
    """单个适配器的运行时配置映射。"""

    plugin_id: str
    config_section: str
    runtime_root_env: str
    runtime_root_candidates: tuple[str, ...]
    runtime_globs: tuple[str, ...]
    runtime_kind: str


_BUILTIN_PROFILES = (
    AdapterSyncProfile(
        plugin_id="maibot-team.napcat-adapter",
        config_section="napcat_server",
        runtime_root_env="MAIBOT_NAPCAT_CONFIG_DIR",
        runtime_root_candidates=("/MaiMBot/adapters-config/napcat",),
        runtime_globs=("onebot11_*.json",),
        runtime_kind="napcat-onebot11",
    ),
    AdapterSyncProfile(
        plugin_id="maibot-team.snowluma-adapter",
        config_section="luma_client",
        runtime_root_env="MAIBOT_SNOWLUMA_CONFIG_DIR",
        runtime_root_candidates=("/MaiMBot/adapters-config/snowluma/config", "/MaiMBot/adapters-config/snowluma"),
        runtime_globs=("onebot_*.json",),
        runtime_kind="snowluma-onebot",
    ),
)


def _write_json_atomic(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as file_obj:
            json.dump(data, file_obj, ensure_ascii=False, indent=2)
            file_obj.write("\n")
            file_obj.flush()
            os.fsync(file_obj.fileno())
        os.replace(temporary_path, path)
    except Exception:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
        raise


def _profile_from_dict(raw: Dict[str, Any]) -> AdapterSyncProfile:
    return AdapterSyncProfile(
        plugin_id=str(raw["plugin_id"]),
        config_section=str(raw["config_section"]),
        runtime_root_env=str(raw.get("runtime_root_env", "")),
        runtime_root_candidates=tuple(str(item) for item in raw.get("runtime_root_candidates", [])),
        runtime_globs=tuple(str(item) for item in raw.get("runtime_globs", [])),
        runtime_kind=str(raw["runtime_kind"]),
    )


class AdapterConfigSyncService:
    """按适配器 Profile 同步连接配置。"""

    PROFILE_FILE = Path("data/adapter-sync-profiles.json")

    def __init__(self) -> None:
        self._profiles = {profile.plugin_id: profile for profile in _BUILTIN_PROFILES}
        self._load_external_profiles()

    def _load_external_profiles(self) -> None:
        if not self.PROFILE_FILE.exists():
            return
        try:
            raw_profiles = json.loads(self.PROFILE_FILE.read_text(encoding="utf-8"))
            if not isinstance(raw_profiles, list):
                raise ValueError("Profile 文件根节点必须是数组")
            for raw_profile in raw_profiles:
                if not isinstance(raw_profile, dict):
                    raise ValueError("Profile 项必须是对象")
                profile = _profile_from_dict(raw_profile)
                self._profiles[profile.plugin_id] = profile
        except Exception as exc:
            logger.error(f"加载适配器同步 Profile 失败: {exc}", exc_info=True)

    def get_profile(self, plugin_id: str) -> Optional[AdapterSyncProfile]:
        return self._profiles.get(plugin_id)

    @staticmethod
    def _resolve_runtime_root(profile: AdapterSyncProfile) -> Optional[Path]:
        candidates: List[Path] = []
        if profile.runtime_root_env:
            configured_path = os.getenv(profile.runtime_root_env, "").strip()
            if configured_path:
                candidates.append(Path(configured_path))
        candidates.extend(Path(value) for value in profile.runtime_root_candidates)
        return next((path for path in candidates if path.exists() and path.is_dir()), None)

    @staticmethod
    def _iter_runtime_files(root: Path, globs: Iterable[str]) -> List[Path]:
        files = {path.resolve() for pattern in globs for path in root.glob(pattern) if path.is_file()}
        return sorted(files)

    @staticmethod
    def _update_napcat(data: Dict[str, Any], token: str, port: int) -> bool:
        changed = False
        network = data.get("network")
        if not isinstance(network, dict):
            return False
        for collection_name in ("websocketServers", "httpServers"):
            servers = network.get(collection_name)
            if not isinstance(servers, list):
                continue
            for server in servers:
                if not isinstance(server, dict):
                    continue
                if collection_name == "websocketServers" and int(server.get("port") or 0) != port:
                    continue
                if server.get("token") != token:
                    server["token"] = token
                    changed = True
        return changed

    @staticmethod
    def _update_snowluma(data: Dict[str, Any], token: str, port: int) -> bool:
        changed = False
        networks = data.get("networks")
        if not isinstance(networks, dict):
            return False
        servers = networks.get("wsServers")
        if not isinstance(servers, list):
            return False
        for server in servers:
            if not isinstance(server, dict) or int(server.get("port") or 0) != port:
                continue
            if server.get("accessToken") != token:
                server["accessToken"] = token
                changed = True
        return changed

    def sync_from_plugin_config(self, plugin_id: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """以 MaiBot 插件配置为权威源，将连接 Token 写入对应适配器运行时。"""
        profile = self.get_profile(plugin_id)
        if profile is None:
            return {"supported": False, "changed_paths": [], "message": "此适配器未声明运行时同步 Profile"}

        section = config.get(profile.config_section)
        if not isinstance(section, dict):
            raise ValueError(f"适配器配置缺少 [{profile.config_section}] 段")
        token = str(section.get("token") or "")
        port = int(section.get("port") or 0)
        if not token:
            raise ValueError("访问 Token 不能为空")
        if port <= 0:
            raise ValueError("适配器端口必须是正整数")

        root = self._resolve_runtime_root(profile)
        if root is None:
            return {
                "supported": True,
                "available": False,
                "changed_paths": [],
                "message": "未挂载适配器运行时配置目录；MaiBot 配置已保存，但无法同步外部适配器",
            }

        runtime_files = self._iter_runtime_files(root, profile.runtime_globs)
        changed_paths: List[str] = []
        for runtime_file in runtime_files:
            data = json.loads(runtime_file.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError(f"运行时配置根节点不是对象: {runtime_file}")
            if profile.runtime_kind == "napcat-onebot11":
                changed = self._update_napcat(data, token, port)
            elif profile.runtime_kind == "snowluma-onebot":
                changed = self._update_snowluma(data, token, port)
            else:
                raise ValueError(f"不支持的适配器同步类型: {profile.runtime_kind}")
            if changed:
                _write_json_atomic(runtime_file, data)
                changed_paths.append(str(runtime_file))

        return {
            "supported": True,
            "available": True,
            "runtime_root": str(root),
            "checked_paths": [str(path) for path in runtime_files],
            "changed_paths": changed_paths,
            "message": "适配器运行时配置已同步" if changed_paths else "适配器运行时 Token 已一致",
        }


_adapter_config_sync_service: Optional[AdapterConfigSyncService] = None


def get_adapter_config_sync_service() -> AdapterConfigSyncService:
    global _adapter_config_sync_service
    if _adapter_config_sync_service is None:
        _adapter_config_sync_service = AdapterConfigSyncService()
    return _adapter_config_sync_service
