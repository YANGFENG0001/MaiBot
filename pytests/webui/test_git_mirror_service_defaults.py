from pathlib import Path

import httpx
import pytest

from src.webui.services import git_mirror_service as mirror_module


def test_current_legacy_defaults_are_migrated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "webui.json"
    config_path.write_text(
        '{"git_mirrors": ['
        '{"id":"gitproxy-mrhjx","enabled":true,"priority":1},'
        '{"id":"ghproxy-vip","enabled":true,"priority":2},'
        '{"id":"github","enabled":true,"priority":3},'
        '{"id":"gh-proxy-com","enabled":true,"priority":4},'
        '{"id":"v6-gh-proxy","enabled":true,"priority":5},'
        '{"id":"cdn-gh-proxy-com","enabled":true,"priority":6}'
        ']}'
    )
    monkeypatch.setattr(mirror_module.GitMirrorConfig, "CONFIG_FILE", config_path)

    config = mirror_module.GitMirrorConfig()
    mirrors = {item["id"]: item for item in config.get_all_mirrors()}

    assert mirrors["ghproxy-vip"]["priority"] == 1
    assert mirrors["github"]["priority"] == 2
    assert mirrors["gitproxy-mrhjx"]["enabled"] is False
    assert mirrors["gitproxy-mrhjx"]["priority"] == 99


@pytest.mark.asyncio
async def test_forbidden_response_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = 0

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url: str):
            nonlocal requests
            requests += 1
            request = httpx.Request("GET", url)
            return httpx.Response(403, request=request)

    monkeypatch.setattr(mirror_module.httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    service = mirror_module.GitMirrorService.__new__(mirror_module.GitMirrorService)
    service.max_retries = 3
    service.timeout = 1

    result = await service._fetch_with_url("https://example.com/file", "blocked")

    assert result["success"] is False
    assert result["attempts"] == 1
    assert requests == 1
