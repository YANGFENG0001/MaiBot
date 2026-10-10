from typing import Any, Dict, Optional

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.webui.dependencies import require_auth
from src.webui.routers.plugin import stats_proxy


@pytest.fixture
def client(monkeypatch, tmp_path):
    forwarded = []

    async def remote_request(method: str, path: str, payload: Optional[Dict[str, Any]] = None):
        forwarded.append((method, path, payload))
        # 返回 None 表示「远端统计服务不可用」：既记录了实际会被转发的内容，
        # 又让请求继续落到本地 SQLite 兜底，顺带覆盖 fork 自研的兜底分支。
        return None

    # 本仓库合并上游 1.3.5 时保留了 fork 自研的 stats_proxy（带本地 SQLite 兜底），
    # 它没有上游新增的 `_request_stats_service` 接缝，统一走 `_remote_request` 转发，
    # 因此这里改挂到后者上。用例断言的行为语义（匿名不转发用户名、非匿名去空格、
    # 空用户名 422、identity 只读本地昵称）与实现无关，保持原样。
    monkeypatch.setattr(stats_proxy, "_remote_request", remote_request)
    # 兜底分支会真实写库，重定向到临时目录，避免污染仓库 data/。
    monkeypatch.setattr(stats_proxy, "PLUGIN_STATS_DB_PATH", tmp_path / "plugin_stats.db")
    app = FastAPI()
    app.include_router(stats_proxy.router)
    app.dependency_overrides[require_auth] = lambda: "test-token"
    with TestClient(app) as test_client:
        yield test_client, forwarded


def test_anonymous_review_never_forwards_username(client):
    test_client, forwarded = client
    response = test_client.post(
        "/stats-proxy/stats/rate",
        json={
            "plugin_id": "test.demo",
            "user_id": "test-user",
            "comment": "好用",
            "anonymous": True,
            "username": "不应发送的昵称",
        },
    )
    assert response.status_code == 200
    assert forwarded == [
        (
            "POST",
            "/stats/rate",
            {
                "plugin_id": "test.demo",
                "user_id": "test-user",
                "comment": "好用",
                "anonymous": True,
            },
        )
    ]


def test_named_review_forwards_trimmed_username(client):
    test_client, forwarded = client
    response = test_client.post(
        "/stats-proxy/stats/rate",
        json={
            "plugin_id": "test.demo",
            "user_id": "test-user",
            "rating": 5,
            "anonymous": False,
            "username": " 麦麦 ",
        },
    )
    assert response.status_code == 200
    assert forwarded[0][2]["username"] == "麦麦"


def test_default_username_is_local_only(client, monkeypatch):
    test_client, forwarded = client
    monkeypatch.setattr(stats_proxy.global_config.bot, "nickname", "测试麦麦")
    response = test_client.get("/stats-proxy/identity")
    assert response.json() == {"username": "测试麦麦"}
    assert forwarded == []


def test_existing_review_payload_remains_supported(client):
    test_client, forwarded = client
    payload = {"plugin_id": "test.demo", "user_id": "test-user", "comment": "好用"}
    assert test_client.post("/stats-proxy/stats/rate", json=payload).status_code == 200
    assert forwarded[0][2] == payload


def test_blank_named_review_username_is_rejected(client):
    test_client, forwarded = client
    response = test_client.post(
        "/stats-proxy/stats/rate",
        json={
            "plugin_id": "test.demo",
            "user_id": "test-user",
            "comment": "好用",
            "anonymous": False,
            "username": "   ",
        },
    )
    assert response.status_code == 422
    assert forwarded == []
