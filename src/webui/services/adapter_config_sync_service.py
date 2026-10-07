"""适配器连接配置同步服务。

将 MaiBot 适配器插件中的连接字段同步到外部适配器运行时配置。
每种适配器使用独立 Profile，避免不同协议端的字段互相污染；
未来适配器可以通过 ``data/adapter-sync-profiles.json`` 增加声明式 Profile。
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import json
import os
import stat
import tempfile

import tomlkit

from src.common.logger import get_logger

logger = get_logger("webui.adapter_config_sync")

# 运维硬锁定：一旦设置，OneBot 令牌以该环境变量为准，且不再回落到插件配置。
# 目的是让「统一令牌」这件事在服务器上只依赖一处声明，避免被插件页误改。
MANAGED_TOKEN_ENV = "MAIBOT_SNOWLUMA_ONEBOT_TOKEN"

# 逃生阀：协议端若换回**不认识** ``SNOWLUMA_ONEBOT_TOKEN`` 的镜像（例如官方
# ``motricseven7/snowluma:latest``），环境变量覆盖就不存在了，此时必须恢复
# 「漂移后要重启」的保守提示。把它设为 0/false/no/off 即可。
TOKEN_FROM_ENV_ENV = "MAIBOT_SNOWLUMA_TOKEN_FROM_ENV"

_FALSY_FLAGS = {"0", "false", "no", "off"}


def _token_from_env_allowed() -> bool:
    """协议端是否确实会从环境变量读取令牌（默认是，可用逃生阀关掉）。"""

    return os.getenv(TOKEN_FROM_ENV_ENV, "").strip().lower() not in _FALSY_FLAGS


@dataclass(frozen=True)
class AdapterSyncProfile:
    """单个适配器的运行时配置映射。"""

    plugin_id: str
    config_section: str
    runtime_root_env: str
    runtime_root_candidates: tuple[str, ...]
    runtime_globs: tuple[str, ...]
    runtime_kind: str
    # 协议端运行时读取 OneBot 令牌所用的环境变量名（配置文件只是兜底）。
    # 非空即表示「运行时支持环境变量覆盖令牌」：这种情况下改写运行时文件
    # 不会影响进程实际生效的令牌，因此不需要重启协议端。
    token_env_var: str = ""


_BUILTIN_PROFILES = (
    AdapterSyncProfile(
        plugin_id="maibot-team.snowluma-adapter",
        # snowluma-adapter v1.1.1 的正式段名是 [client]；
        # luma_client / napcat_server / connection 仅作为历史配置的迁移来源保留。
        config_section="client",
        runtime_root_env="MAIBOT_SNOWLUMA_CONFIG_DIR",
        # SnowLuma 把配置写在自身运行根目录的 config/ 下，容器化时挂载进 core。
        runtime_root_candidates=("/MaiMBot/adapters-config/snowluma/config", "/MaiMBot/adapters-config/snowluma"),
        runtime_globs=("*.json", "**/*.json"),
        runtime_kind="snowluma-onebot",
        # 自建镜像（YANGFENG0001/SnowLuma fork）在 loadOneBotConfig() 收尾处叠加
        # SNOWLUMA_ONEBOT_HOST / SNOWLUMA_ONEBOT_TOKEN，只在内存生效、不落盘。
        token_env_var="SNOWLUMA_ONEBOT_TOKEN",
    ),
)


def _write_json_atomic(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # ``mkstemp`` 建出来的文件属主是当前进程、权限固定 600，而 ``os.replace``
    # 会把这份属主/权限一并带到目标文件上 —— 运行时配置目录通常是 bind mount，
    # 协议端以另一个 uid 读取（SnowLuma 容器内是 1001）。一旦权限被改成 600，
    # 协议端就 EACCES 读不到配置，随即**静默回落到内置默认值**
    # （host=127.0.0.1 + 每次随机生成的 accessToken），表现为「明明登录了却显示
    # 未登录」。所以覆盖前先记住原文件的属主/权限，写完再还原。
    try:
        previous_stat: Optional[os.stat_result] = path.stat()
    except FileNotFoundError:
        previous_stat = None

    descriptor, temporary_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
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

    if previous_stat is not None:
        _restore_owner_and_mode(path, previous_stat)


def _restore_owner_and_mode(path: Path, previous: os.stat_result) -> None:
    """把 ``os.replace`` 冲掉的属主/权限还原回去。

    两步都是尽力而为：还原失败说明当前进程没有权限（非 root），此时配置内容
    本身已经写对了，不该因为改不动属主就让整次同步失败 —— 只记日志。
    """
    try:
        os.chmod(path, stat.S_IMODE(previous.st_mode))
    except OSError as error:
        logger.warning(f"还原运行时配置权限失败（内容已写入）: {path} -> {error}")

    if not hasattr(os, "chown"):
        return
    try:
        os.chown(path, previous.st_uid, previous.st_gid)
    except OSError as error:
        logger.warning(f"还原运行时配置属主失败（内容已写入）: {path} -> {error}")


def _profile_from_dict(raw: Dict[str, Any]) -> AdapterSyncProfile:
    return AdapterSyncProfile(
        plugin_id=str(raw["plugin_id"]),
        config_section=str(raw["config_section"]),
        runtime_root_env=str(raw.get("runtime_root_env", "")),
        runtime_root_candidates=tuple(str(item) for item in raw.get("runtime_root_candidates", [])),
        runtime_globs=tuple(str(item) for item in raw.get("runtime_globs", [])),
        runtime_kind=str(raw["runtime_kind"]),
        token_env_var=str(raw.get("token_env_var", "")),
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
    def _update_snowluma(data: Dict[str, Any], token: str) -> bool:
        """把权威令牌写入 SnowLuma 运行时配置。

        SnowLuma 会在 ``wsServers``（MaiBot 反连的 WebSocket）与 ``httpServers``
        （HTTP API）两个集合里各存一份 ``accessToken``。历史实现只覆盖
        ``wsServers`` 里端口匹配的那一条，于是两份令牌长期各走各的，
        运行中心一直报「Token 不一致」。这里按「统一托管」的语义同时覆盖
        两个集合的全部条目，令牌一旦漂移就被拉回。
        """

        networks = data.get("networks")
        if not isinstance(networks, dict):
            return False
        changed = False
        for collection_name in ("wsServers", "httpServers"):
            collection = networks.get(collection_name)
            if not isinstance(collection, list):
                continue
            for server in collection:
                if not isinstance(server, dict):
                    continue
                if server.get("accessToken") != token:
                    server["accessToken"] = token
                    changed = True
        return changed

    def _read_plugin_token(self, plugin_id: str) -> str:
        """读取适配器插件配置里的连接令牌。"""

        profile = self.get_profile(plugin_id)
        if profile is None:
            return ""

        try:
            # 延迟导入：插件路由模块会反过来引用本服务，放在模块顶部会形成循环。
            from src.webui.routers.plugin.support import find_plugin_path_by_id

            plugin_path = find_plugin_path_by_id(plugin_id)
            if plugin_path is None:
                return ""
            config_path = plugin_path / "config.toml"
            if not config_path.exists():
                return ""
            with open(config_path, "r", encoding="utf-8") as file_obj:
                config = tomlkit.load(file_obj).unwrap()
        except Exception as exc:
            logger.warning(f"读取适配器插件 Token 失败: {plugin_id} ({exc})")
            return ""

        if not isinstance(config, dict):
            return ""
        section = config.get(profile.config_section)
        if not isinstance(section, dict):
            return ""
        return str(section.get("token") or "").strip()

    def resolve_managed_token(self, plugin_id: str) -> tuple[str, str]:
        """解析权威 OneBot 令牌及其来源。

        优先级：``MAIBOT_SNOWLUMA_ONEBOT_TOKEN`` 环境变量（运维硬锁定，优先级最高，
        避免被插件页误改） → 适配器插件 ``config.toml`` 的连接令牌。
        """

        env_token = os.getenv(MANAGED_TOKEN_ENV, "").strip()
        if env_token:
            return env_token, "environment"

        plugin_token = self._read_plugin_token(plugin_id)
        if plugin_token:
            return plugin_token, "adapter_plugin"

        return "", "unset"

    def enforce_runtime_token(self, plugin_id: str, token: str, *, token_source: str = "") -> Dict[str, Any]:
        """把权威令牌强制写入适配器运行时配置（幂等）。

        与 :meth:`sync_from_plugin_config` 的区别：这里不读插件配置、不校验端口，
        只做「MaiBot 侧说了算」的覆盖，供启动自检与运行中心巡检调用；
        即使 SnowLuma 自行重新生成了随机令牌，也会被拉回权威值。

        ``token_source`` 是 :meth:`resolve_managed_token` 给出的令牌来源。只有当它
        是 ``environment`` 时才认定「协议端也从环境变量读取令牌」——因为 compose
        用**同一个** ``MAIBOT_SNOWLUMA_ONEBOT_TOKEN`` 变量、同一个 ``:?`` 守卫分别
        注入 core 与协议端：变量在 core 侧解析得出来，协议端侧就必然也被注入。
        令牌来自插件配置时不做这个假设，保守地照旧提示重启；换回官方镜像这类
        「协议端不认识该环境变量」的部署，可用 ``MAIBOT_SNOWLUMA_TOKEN_FROM_ENV=0``
        关掉这条捷径。
        """

        profile = self.get_profile(plugin_id)
        if profile is None:
            return {
                "supported": False,
                "enforced": False,
                "changed_paths": [],
                "message": "此适配器未声明运行时同步 Profile",
            }
        if not token:
            return {
                "supported": True,
                "enforced": False,
                "changed_paths": [],
                "message": "未解析到权威 Token，已跳过强制同步",
            }

        root = self._resolve_runtime_root(profile)
        if root is None:
            return {
                "supported": True,
                "enforced": False,
                "changed_paths": [],
                "message": "未挂载适配器运行时配置目录；无法强制同步外部适配器",
            }

        # 协议端支持从环境变量读令牌时，磁盘上的令牌写错也影响不了进程实际行为，
        # 于是「漂移 → 改回 → 重启」这条链被彻底切断：改回即可，无需重启。
        token_from_env = bool(profile.token_env_var) and token_source == "environment" and _token_from_env_allowed()

        runtime_files = self._iter_runtime_files(root, profile.runtime_globs)
        changed_paths: List[str] = []
        for runtime_file in runtime_files:
            try:
                data = json.loads(runtime_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning(f"跳过无法解析的适配器运行时配置: {runtime_file} ({exc})")
                continue
            if not isinstance(data, dict):
                continue
            if profile.runtime_kind != "snowluma-onebot":
                continue
            if self._update_snowluma(data, token):
                _write_json_atomic(runtime_file, data)
                changed_paths.append(str(runtime_file))

        if not changed_paths:
            message = "适配器运行时 Token 已一致"
        elif token_from_env:
            message = f"已强制同步适配器运行时 Token；协议端从 {profile.token_env_var} 读取令牌，无需重启"
        else:
            message = "已强制同步适配器运行时 Token"

        return {
            "supported": True,
            "enforced": True,
            "runtime_root": str(root),
            "checked_paths": [str(path) for path in runtime_files],
            "changed_paths": changed_paths,
            # 运行时从环境变量读令牌 ⇒ 文件被改写不影响进程持有的令牌，不必重启。
            "token_from_env": token_from_env,
            "restart_required": bool(changed_paths) and not token_from_env,
            "message": message,
        }

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
            if profile.runtime_kind == "snowluma-onebot":
                changed = self._update_snowluma(data, token)
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
