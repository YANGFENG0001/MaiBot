from pathlib import Path

import json
import os
import stat

import pytest

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


@pytest.mark.skipif(os.name != "posix", reason="Windows 没有 POSIX 权限位，无从比较")
def test_write_keeps_runtime_config_mode(tmp_path: Path, monkeypatch) -> None:
    """覆盖运行时配置不能改动它的权限位。

    ``mkstemp`` 建出来的是 600，``os.replace`` 会把这个权限一并带到目标文件上。
    协议端以另一个 uid 读这个文件，600 会让它 EACCES 读不到，随即静默回落到
    内置默认值（host=127.0.0.1 + 每次随机生成的令牌），表现为「明明登录了却
    显示未登录」。这里锁死「写完之后权限位不变」。
    """

    onebot_path = tmp_path / "onebot.json"
    onebot_path.write_text(
        json.dumps({"networks": {"wsServers": [{"port": 3001, "accessToken": "old"}]}}),
        encoding="utf-8",
    )
    os.chmod(onebot_path, 0o644)
    monkeypatch.setenv("MAIBOT_SNOWLUMA_CONFIG_DIR", str(tmp_path))

    AdapterConfigSyncService().sync_from_plugin_config(
        "maibot-team.snowluma-adapter",
        {"client": {"port": 3001, "token": "snow-token"}},
    )

    saved = json.loads(onebot_path.read_text(encoding="utf-8"))
    assert saved["networks"]["wsServers"][0]["accessToken"] == "snow-token"
    assert stat.S_IMODE(onebot_path.stat().st_mode) == 0o644


@pytest.mark.skipif(os.name != "posix", reason="Windows 没有 POSIX 权限位，无从比较")
def test_write_keeps_runtime_config_owner(tmp_path: Path, monkeypatch) -> None:
    """属主同样要保住：容器里 core 是 root，协议端是 1001，换属主即等于换读取权限。"""

    onebot_path = tmp_path / "onebot.json"
    onebot_path.write_text(
        json.dumps({"networks": {"wsServers": [{"port": 3001, "accessToken": "old"}]}}),
        encoding="utf-8",
    )
    before = onebot_path.stat()
    monkeypatch.setenv("MAIBOT_SNOWLUMA_CONFIG_DIR", str(tmp_path))

    AdapterConfigSyncService().sync_from_plugin_config(
        "maibot-team.snowluma-adapter",
        {"client": {"port": 3001, "token": "snow-token"}},
    )

    after = onebot_path.stat()
    assert (after.st_uid, after.st_gid) == (before.st_uid, before.st_gid)


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


def _write_drifted_onebot(tmp_path: Path) -> Path:
    onebot_path = tmp_path / "onebot_2980639690.json"
    onebot_path.write_text(
        json.dumps(
            {
                "mode": "snapshot",
                "networks": {
                    "wsServers": [{"name": "ws-default", "port": 3001, "accessToken": "drifted"}],
                    "httpServers": [{"name": "http-default", "port": 3000, "accessToken": "drifted"}],
                },
            }
        ),
        encoding="utf-8",
    )
    return onebot_path


def test_enforce_runtime_token_does_not_ask_for_restart_when_runtime_reads_env(tmp_path: Path, monkeypatch) -> None:
    """令牌来自环境变量、且协议端也从环境变量读令牌时，改回即生效，不该提示重启。

    这正是「漂移 → 改回 → 重启」这条链被切断的地方：自建 SnowLuma 镜像在
    ``loadOneBotConfig()`` 收尾处叠加 ``SNOWLUMA_ONEBOT_TOKEN``（只在内存生效、
    不落盘），所以磁盘上的令牌写错也影响不了进程实际持有的令牌。
    """

    onebot_path = _write_drifted_onebot(tmp_path)
    monkeypatch.setenv("MAIBOT_SNOWLUMA_CONFIG_DIR", str(tmp_path))

    result = AdapterConfigSyncService().enforce_runtime_token(
        "maibot-team.snowluma-adapter", "managed", token_source="environment"
    )

    assert result["changed_paths"] == [str(onebot_path.resolve())]
    assert result["token_from_env"] is True
    assert result["restart_required"] is False
    assert "SNOWLUMA_ONEBOT_TOKEN" in result["message"]

    saved = json.loads(onebot_path.read_text(encoding="utf-8"))
    assert saved["networks"]["wsServers"][0]["accessToken"] == "managed"
    assert saved["networks"]["httpServers"][0]["accessToken"] == "managed"


def test_enforce_runtime_token_keeps_restart_for_plugin_sourced_token(tmp_path: Path, monkeypatch) -> None:
    """令牌来自适配器插件配置时不假设协议端有环境变量覆盖，保守地照旧提示重启。"""

    onebot_path = _write_drifted_onebot(tmp_path)
    monkeypatch.setenv("MAIBOT_SNOWLUMA_CONFIG_DIR", str(tmp_path))

    result = AdapterConfigSyncService().enforce_runtime_token(
        "maibot-team.snowluma-adapter", "managed", token_source="adapter_plugin"
    )

    assert result["changed_paths"] == [str(onebot_path.resolve())]
    assert result["token_from_env"] is False
    assert result["restart_required"] is True


@pytest.mark.parametrize("flag", ["0", "false", "no", "off", "OFF"])
def test_enforce_runtime_token_escape_hatch_restores_restart_hint(tmp_path: Path, monkeypatch, flag: str) -> None:
    """换回不认识该环境变量的镜像时，用逃生阀恢复保守提示。"""

    _write_drifted_onebot(tmp_path)
    monkeypatch.setenv("MAIBOT_SNOWLUMA_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("MAIBOT_SNOWLUMA_TOKEN_FROM_ENV", flag)

    result = AdapterConfigSyncService().enforce_runtime_token(
        "maibot-team.snowluma-adapter", "managed", token_source="environment"
    )

    assert result["token_from_env"] is False
    assert result["restart_required"] is True


def test_enforce_runtime_token_without_changes_never_asks_for_restart(tmp_path: Path, monkeypatch) -> None:
    onebot_path = _write_drifted_onebot(tmp_path)
    onebot_path.write_text(
        json.dumps(
            {
                "mode": "snapshot",
                "networks": {
                    "wsServers": [{"name": "ws-default", "port": 3001, "accessToken": "managed"}],
                    "httpServers": [{"name": "http-default", "port": 3000, "accessToken": "managed"}],
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MAIBOT_SNOWLUMA_CONFIG_DIR", str(tmp_path))

    result = AdapterConfigSyncService().enforce_runtime_token(
        "maibot-team.snowluma-adapter", "managed", token_source="environment"
    )

    assert result["changed_paths"] == []
    assert result["restart_required"] is False


def test_snowluma_profile_declares_token_env_var() -> None:
    """内置 Profile 必须声明协议端读取令牌的环境变量名，否则上面那条捷径会失效。"""

    profile = AdapterConfigSyncService().get_profile("maibot-team.snowluma-adapter")

    assert profile is not None
    assert profile.token_env_var == "SNOWLUMA_ONEBOT_TOKEN"
