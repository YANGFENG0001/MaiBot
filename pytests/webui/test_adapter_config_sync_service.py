from pathlib import Path

import json

from src.webui.services.adapter_config_sync_service import AdapterConfigSyncService


def test_napcat_token_sync_updates_http_and_websocket(tmp_path: Path, monkeypatch) -> None:
    onebot_path = tmp_path / "onebot11_123456.json"
    onebot_path.write_text(
        json.dumps(
            {
                "network": {
                    "httpServers": [{"port": 7999, "token": "old"}],
                    "websocketServers": [
                        {"port": 7998, "token": "old"},
                        {"port": 9000, "token": "untouched"},
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MAIBOT_NAPCAT_CONFIG_DIR", str(tmp_path))

    result = AdapterConfigSyncService().sync_from_plugin_config(
        "maibot-team.napcat-adapter",
        {"napcat_server": {"port": 7998, "token": "new-token"}},
    )

    webui_path = tmp_path / "webui.json"
    saved = json.loads(onebot_path.read_text(encoding="utf-8"))
    assert saved["network"]["httpServers"][0]["token"] == "new-token"
    assert saved["network"]["websocketServers"][0]["token"] == "new-token"
    assert saved["network"]["websocketServers"][1]["token"] == "untouched"
    assert json.loads(webui_path.read_text(encoding="utf-8"))["token"] == "new-token"
    assert result["changed_paths"] == [str(onebot_path.resolve()), str(webui_path.resolve())]


def test_snowluma_uses_its_own_profile(tmp_path: Path, monkeypatch) -> None:
    onebot_path = tmp_path / "onebot_123456.json"
    onebot_path.write_text(
        json.dumps({"networks": {"wsServers": [{"port": 7988, "accessToken": "old"}]}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("MAIBOT_SNOWLUMA_CONFIG_DIR", str(tmp_path))

    AdapterConfigSyncService().sync_from_plugin_config(
        "maibot-team.snowluma-adapter",
        {"luma_client": {"port": 7988, "token": "snow-token"}},
    )

    saved = json.loads(onebot_path.read_text(encoding="utf-8"))
    assert saved["networks"]["wsServers"][0]["accessToken"] == "snow-token"
