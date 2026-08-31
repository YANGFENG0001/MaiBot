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

from src.webui.dependencies import require_auth
from src.webui.services.adapter_config_sync_service import get_adapter_config_sync_service
from src.webui.services.git_mirror_service import get_git_mirror_service

router = APIRouter(prefix="/operations", tags=["operations"], dependencies=[Depends(require_auth)])


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


def _napcat_runtime_summary() -> Dict[str, Any]:
    configured_root = os.getenv("MAIBOT_NAPCAT_CONFIG_DIR", "").strip()
    root = Path(configured_root or "/MaiMBot/adapters-config/napcat")
    webui_config = _read_json(root / "webui.json")
    account = str(webui_config.get("autoLoginAccount") or "")
    onebot_files = sorted(root.glob("onebot11_*.json")) if root.exists() else []
    onebot_tokens: set[str] = set()
    for onebot_file in onebot_files:
        network = _read_json(onebot_file).get("network")
        if not isinstance(network, dict):
            continue
        for collection_name in ("websocketServers", "httpServers"):
            collection = network.get(collection_name)
            if not isinstance(collection, list):
                continue
            for server in collection:
                if isinstance(server, dict) and server.get("token"):
                    onebot_tokens.add(str(server["token"]))
    return {
        "runtime_root": str(root),
        "runtime_mounted": root.exists(),
        "account": account,
        "webui_token": _mask_token(str(webui_config.get("token") or "")),
        "onebot_token": _mask_token(next(iter(onebot_tokens), "")),
        "onebot_token_consistent": len(onebot_tokens) <= 1,
        "onebot_config_count": len(onebot_files),
    }


@router.get("/overview")
async def get_operations_overview() -> Dict[str, Any]:
    napcat_ws, napcat_webui = await asyncio.gather(
        _port_open("napcat", 7998),
        _port_open("napcat", 6099),
    )
    mirror_service = get_git_mirror_service()
    mirrors = mirror_service.get_mirror_config().get_all_mirrors()
    sync_service = get_adapter_config_sync_service()
    napcat = _napcat_runtime_summary()
    napcat.update(
        {
            "id": "napcat",
            "name": "NapCat 适配器",
            "websocket_ready": napcat_ws,
            "webui_ready": napcat_webui,
            "state": "ready" if napcat_ws else "login_required" if napcat_webui else "unreachable",
            "diagnosis": (
                "OneBot WebSocket 已可用"
                if napcat_ws
                else "NapCat 已启动但 QQ 尚未登录，请打开 NapCat WebUI 扫码"
                if napcat_webui
                else "NapCat 容器或 WebUI 不可达"
            ),
            "sync_supported": sync_service.get_profile("maibot-team.napcat-adapter") is not None,
        }
    )
    return {
        "success": True,
        "services": {
            "maibot": {"state": "ready", "name": "MaiBot Core"},
            "napcat": napcat,
        },
        "mirrors": sorted(mirrors, key=lambda item: item.get("priority", 999)),
        "security": {
            "container_control_available": False,
            "message": "服务器 WebUI 不直接挂载 Docker Socket；容器启停由 docker compose 或面板负责。",
        },
    }
