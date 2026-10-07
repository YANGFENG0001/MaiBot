from pathlib import Path

import json

from src.webui.services.adapter_config_sync_service import AdapterConfigSyncService


def test_unknown_adapter_reports_unsupported() -> None:
    result = AdapterConfigSyncService().sync_from_plugin_config("someone.unknown-adapter", {})

    assert result["supported"] is False
    assert result["changed_paths"] == []


def test_snowluma_uses_its_own_profile(tmp_path: Path, monkeypatch) -> None:
    onebot_path = tmp_path / "onebot_123456.json"
    onebot_path.write_text(
        json.dumps({"networks": {"wsServers": [{"port": 7988, "accessToken": "old"}]}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("MAIBOT_SNOWLUMA_CONFIG_DIR", str(tmp_path))

    result = AdapterConfigSyncService().sync_from_plugin_config(
        "maibot-team.snowluma-adapter",
        {"client": {"port": 7988, "token": "snow-token"}},
    )

    saved = json.loads(onebot_path.read_text(encoding="utf-8"))
    assert saved["networks"]["wsServers"][0]["accessToken"] == "snow-token"
    assert result["supported"] is True
    assert result["changed_paths"] == [str(onebot_path.resolve())]


def test_snowluma_ignores_other_ports_and_sections(tmp_path: Path, monkeypatch) -> None:
    onebot_path = tmp_path / "onebot_123456.json"
    onebot_path.write_text(
        json.dumps(
            {
                "networks": {
                    "wsServers": [
                        {"port": 7988, "accessToken": "old"},
                        {"port": 9000, "accessToken": "untouched"},
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MAIBOT_SNOWLUMA_CONFIG_DIR", str(tmp_path))

    AdapterConfigSyncService().sync_from_plugin_config(
        "maibot-team.snowluma-adapter",
        {"client": {"port": 7988, "token": "snow-token"}},
    )

    saved = json.loads(onebot_path.read_text(encoding="utf-8"))
    assert saved["networks"]["wsServers"][0]["accessToken"] == "snow-token"
    assert saved["networks"]["wsServers"][1]["accessToken"] == "untouched"


def test_snowluma_requires_client_section(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MAIBOT_SNOWLUMA_CONFIG_DIR", str(tmp_path))

    try:
        AdapterConfigSyncService().sync_from_plugin_config(
            "maibot-team.snowluma-adapter",
            {"luma_client": {"port": 7988, "token": "snow-token"}},
        )
    except ValueError as exc:
        assert "[client]" in str(exc)
    else:  # pragma: no cover - 只有在校验被移除时才会走到
        raise AssertionError("缺少 [client] 段时应当报错")
