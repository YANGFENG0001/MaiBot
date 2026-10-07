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


def test_snowluma_unifies_every_onebot_token(tmp_path: Path, monkeypatch) -> None:
    """wsServers 与 httpServers 的令牌必须被统一。

    历史实现只覆盖端口匹配的那一条 wsServers 记录，导致两份令牌长期各走各的，
    运行中心一直报「多个 OneBot 配置的 Token 不一致」。
    """

    onebot_path = tmp_path / "onebot.json"
    onebot_path.write_text(
        json.dumps(
            {
                "networks": {
                    "wsServers": [
                        {"port": 3001, "accessToken": "ws-old"},
                        {"port": 9000, "accessToken": "ws-extra"},
                    ],
                    "httpServers": [{"port": 3000, "accessToken": "http-old"}],
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MAIBOT_SNOWLUMA_CONFIG_DIR", str(tmp_path))

    AdapterConfigSyncService().sync_from_plugin_config(
        "maibot-team.snowluma-adapter",
        {"client": {"port": 3001, "token": "snow-token"}},
    )

    saved = json.loads(onebot_path.read_text(encoding="utf-8"))
    tokens: set[str] = set()
    for collection in ("wsServers", "httpServers"):
        for server in saved["networks"][collection]:
            tokens.add(server["accessToken"])
    assert tokens == {"snow-token"}


def test_enforce_runtime_token_pulls_drifted_token_back(tmp_path: Path, monkeypatch) -> None:
    """协议端自行漂移的令牌会被强制拉回，且重复执行保持幂等。"""

    onebot_path = tmp_path / "onebot.json"
    onebot_path.write_text(
        json.dumps(
            {
                "networks": {
                    "wsServers": [{"port": 3001, "accessToken": "drifted"}],
                    "httpServers": [{"port": 3000, "accessToken": "drifted-too"}],
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MAIBOT_SNOWLUMA_CONFIG_DIR", str(tmp_path))
    service = AdapterConfigSyncService()

    first = service.enforce_runtime_token("maibot-team.snowluma-adapter", "managed")
    assert first["enforced"] is True
    assert first["restart_required"] is True
    assert first["changed_paths"] == [str(onebot_path.resolve())]

    second = service.enforce_runtime_token("maibot-team.snowluma-adapter", "managed")
    assert second["changed_paths"] == []
    assert second["restart_required"] is False

    saved = json.loads(onebot_path.read_text(encoding="utf-8"))
    assert saved["networks"]["wsServers"][0]["accessToken"] == "managed"
    assert saved["networks"]["httpServers"][0]["accessToken"] == "managed"


def test_enforce_runtime_token_skips_without_authoritative_token(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MAIBOT_SNOWLUMA_CONFIG_DIR", str(tmp_path))

    result = AdapterConfigSyncService().enforce_runtime_token("maibot-team.snowluma-adapter", "")

    assert result["enforced"] is False
    assert result["changed_paths"] == []


def test_enforce_runtime_token_reports_unsupported_adapter() -> None:
    result = AdapterConfigSyncService().enforce_runtime_token("someone.unknown-adapter", "managed")

    assert result["supported"] is False
    assert result["enforced"] is False


def test_resolve_managed_token_prefers_environment(monkeypatch) -> None:
    """环境变量是运维硬锁定，优先级高于适配器插件配置。"""

    monkeypatch.setenv("MAIBOT_SNOWLUMA_ONEBOT_TOKEN", "from-env")
    monkeypatch.setattr(AdapterConfigSyncService, "_read_plugin_token", lambda self, plugin_id: "from-plugin")

    token, source = AdapterConfigSyncService().resolve_managed_token("maibot-team.snowluma-adapter")

    assert token == "from-env"
    assert source == "environment"


def test_resolve_managed_token_falls_back_to_plugin_config(monkeypatch) -> None:
    monkeypatch.delenv("MAIBOT_SNOWLUMA_ONEBOT_TOKEN", raising=False)
    monkeypatch.setattr(AdapterConfigSyncService, "_read_plugin_token", lambda self, plugin_id: "from-plugin")

    token, source = AdapterConfigSyncService().resolve_managed_token("maibot-team.snowluma-adapter")

    assert token == "from-plugin"
    assert source == "adapter_plugin"


def test_resolve_managed_token_reports_unset(monkeypatch) -> None:
    monkeypatch.delenv("MAIBOT_SNOWLUMA_ONEBOT_TOKEN", raising=False)
    monkeypatch.setattr(AdapterConfigSyncService, "_read_plugin_token", lambda self, plugin_id: "")

    token, source = AdapterConfigSyncService().resolve_managed_token("maibot-team.snowluma-adapter")

    assert token == ""
    assert source == "unset"


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
