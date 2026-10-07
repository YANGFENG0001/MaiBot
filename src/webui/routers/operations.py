"""服务器运行中心接口。

提供 OneKey 桌面端中“服务与适配器”能力在服务器 WebUI 的安全子集：
状态、配置一致性、镜像源、快捷入口与故障说明。容器启停仍由宿主机
Docker/编排系统负责，避免把 Docker Socket 暴露给 WebUI。
"""

from pathlib import Path
from typing import Any, Dict

import asyncio
import json
import os

from fastapi import APIRouter, Depends

from src.common.logger import get_logger
from src.webui.dependencies import require_auth
from src.webui.services.adapter_config_sync_service import get_adapter_config_sync_service
from src.webui.services.git_mirror_service import get_git_mirror_service

logger = get_logger("webui.operations")

router = APIRouter(prefix="/operations", tags=["operations"], dependencies=[Depends(require_auth)])

SNOWLUMA_ADAPTER_PLUGIN_ID = "maibot-team.snowluma-adapter"
SNOWLUMA_CONTAINER_HOST = "snowluma"
SNOWLUMA_ONEBOT_PORT = 3001
# core 容器内探测用的端口（SnowLuma 自身监听端口）
SNOWLUMA_WEBUI_PORT = 5099
# 浏览器访问用的端口：默认沿用 NapCat 时代的 6099，避免再动云厂商安全组
SNOWLUMA_WEBUI_PUBLIC_PORT = int(os.getenv("MAIBOT_SNOWLUMA_WEBUI_PUBLIC_PORT", "6099"))

# QQ 扫码登录页：SnowLuma 官方控制台只做配置管理、不提供扫码界面，
# 因此扫码入口由部署侧（nginx 反代 Xvfb 截图服务）提供，这里只负责给出跳转地址。
SNOWLUMA_QR_PATH = os.getenv("MAIBOT_SNOWLUMA_QR_PATH", "/qq-qr").strip() or "/qq-qr"
SNOWLUMA_QR_PUBLIC_PORT = int(os.getenv("MAIBOT_SNOWLUMA_QR_PUBLIC_PORT", "80"))


async def _port_open(host: str, port: int, timeout: float = 0.8) -> bool:
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
        writer.close()
        await writer.wait_closed()
        return reader is not None
    except (OSError, asyncio.TimeoutError):
        return False


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _mask_token(token: str) -> str:
    if not token:
        return "未配置"
    if len(token) <= 8:
        return "*" * len(token)
    return f"{token[:4]}…{token[-4:]}"


def _resolve_snowluma_root() -> Path:
    """定位 SnowLuma 运行时配置目录。

    SnowLuma 把配置写在自身运行根目录下的 ``config/`` 中，容器化部署时该目录
    被挂载进 core 容器；优先取显式环境变量，其次取挂载点，最后回退到未挂载时的
    约定路径（此时 ``runtime_mounted`` 为 False）。
    """

    configured_root = os.getenv("MAIBOT_SNOWLUMA_CONFIG_DIR", "").strip()
    candidates = [Path(configured_root)] if configured_root else []
    candidates.extend(
        (
            Path("/MaiMBot/adapters-config/snowluma/config"),
            Path("/MaiMBot/adapters-config/snowluma"),
        )
    )
    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            return candidate
    return candidates[0]


def _iter_snowluma_config_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    found = {path.resolve() for pattern in ("*.json", "**/*.json") for path in root.glob(pattern) if path.is_file()}
    return sorted(found)


def _snowluma_runtime_summary() -> Dict[str, Any]:
    """汇总 SnowLuma 运行时配置：账号、OneBot 令牌一致性、配置文件数量。"""

    root = _resolve_snowluma_root()
    config_files = _iter_snowluma_config_files(root)
    onebot_tokens: set[str] = set()
    accounts: set[str] = set()
    for config_file in config_files:
        try:
            data = _read_json(config_file)
        except (OSError, json.JSONDecodeError):
            continue
        for key in ("uin", "account", "autoLoginAccount"):
            value = data.get(key)
            if value:
                accounts.add(str(value))
        networks = data.get("networks")
        if not isinstance(networks, dict):
            continue
        for collection_name in ("wsServers", "httpServers"):
            collection = networks.get(collection_name)
            if not isinstance(collection, list):
                continue
            for server in collection:
                if isinstance(server, dict) and server.get("accessToken"):
                    onebot_tokens.add(str(server["accessToken"]))
    return {
        "runtime_root": str(root),
        "runtime_mounted": root.exists(),
        "account": next(iter(accounts), ""),
        "onebot_token": _mask_token(next(iter(onebot_tokens), "")),
        "onebot_token_consistent": len(onebot_tokens) <= 1,
        "onebot_config_count": len(config_files),
    }


def enforce_managed_onebot_token() -> Dict[str, Any]:
    """把 MaiBot 侧的权威 OneBot 令牌强制同步到 SnowLuma 运行时配置。

    这是「统一令牌、且不允许再被自动变更」的落点：WebUI 启动自检与运行中心巡检
    都会执行一次幂等覆盖，SnowLuma 侧一旦重新随机生成或被人为改动，都会被拉回权威值。
    """

    sync_service = get_adapter_config_sync_service()
    token, source = sync_service.resolve_managed_token(SNOWLUMA_ADAPTER_PLUGIN_ID)
    result: Dict[str, Any] = {
        "token_source": source,
        "token_managed": bool(token),
        "enforced": False,
        "changed_paths": [],
        "restart_required": False,
    }
    if not token:
        logger.warning("环境变量与适配器插件均未提供 OneBot 令牌，已跳过强制同步")
        return result

    try:
        result.update(sync_service.enforce_runtime_token(SNOWLUMA_ADAPTER_PLUGIN_ID, token))
    except Exception as exc:
        logger.error(f"强制同步 OneBot 令牌失败: {exc}", exc_info=True)
        return result

    if result.get("changed_paths"):
        logger.info(f"已强制同步 OneBot 令牌，改写运行时配置: {result['changed_paths']}")
    return result


@router.get("/overview")
async def get_operations_overview() -> Dict[str, Any]:
    # 先纠偏再汇总：这样返回的 onebot_token_consistent 反映的是托管后的真实状态。
    enforcement = enforce_managed_onebot_token()
    snowluma_ws, snowluma_webui = await asyncio.gather(
        _port_open(SNOWLUMA_CONTAINER_HOST, SNOWLUMA_ONEBOT_PORT),
        _port_open(SNOWLUMA_CONTAINER_HOST, SNOWLUMA_WEBUI_PORT),
    )
    mirror_service = get_git_mirror_service()
    mirrors = mirror_service.get_mirror_config().get_all_mirrors()
    sync_service = get_adapter_config_sync_service()
    snowluma = _snowluma_runtime_summary()
    snowluma.update(
        {
            "id": "snowluma",
            "name": "SnowLuma 适配器",
            "websocket_ready": snowluma_ws,
            "webui_ready": snowluma_webui,
            "webui_port": SNOWLUMA_WEBUI_PUBLIC_PORT,
            "qr_url": SNOWLUMA_QR_PATH,
            "qr_port": SNOWLUMA_QR_PUBLIC_PORT,
            "state": "ready" if snowluma_ws else "login_required" if snowluma_webui else "unreachable",
            "diagnosis": (
                "OneBot WebSocket 已可用"
                if snowluma_ws
                else "SnowLuma 已启动但 QQ 尚未登录，请打开「QQ 扫码登录」扫码"
                if snowluma_webui
                else "SnowLuma 容器或控制台不可达"
            ),
            "sync_supported": sync_service.get_profile(SNOWLUMA_ADAPTER_PLUGIN_ID) is not None,
            **enforcement,
        }
    )
    return {
        "success": True,
        "services": {
            "maibot": {"state": "ready", "name": "MaiBot Core"},
            "snowluma": snowluma,
        },
        "mirrors": sorted(mirrors, key=lambda item: item.get("priority", 999)),
        "security": {
            "container_control_available": False,
            "message": "服务器 WebUI 不直接挂载 Docker Socket；容器启停由 docker compose 或面板负责。",
        },
    }
