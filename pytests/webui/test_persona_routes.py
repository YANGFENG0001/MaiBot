"""人设（PersonaProfile）与 BotProfile 创建/删除、会话路由的 WebUI 路由测试。

这些用例覆盖本次新增的写入侧能力：此前人设只能由数据库迁移写入，
BotProfile 只能由迁移派生，`BotRouteState` 完全没有对外接口。
"""

from contextlib import contextmanager
from typing import Any, Generator

import importlib

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from src.common.database.database_model import (
    BotProfile,
    BotProfileMemoryRule,
    BotProfileToolPolicy,
    BotRouteState,
    MemoryPermissionGroup,
    MemorySpace,
    MemorySpaceBotRule,
    PermissionGroupBotRule,
    PersonaProfile,
    Workspace,
)
from src.webui.dependencies import require_auth, require_auth_with_rate_limit

PUBLIC_SPACE_ID = "memory-space-public"
KAMI_SPACE_ID = "memory-space-kami"
PUBLIC_PROFILE_ID = "bot-profile-public"


def create_test_app() -> FastAPI:
    app = FastAPI(title="Persona Routes Test App")
    from src.webui.routers.bot_profiles import router as bot_profiles_router
    from src.webui.routers.personas import router as personas_router

    main_router = APIRouter(prefix="/api/webui")
    main_router.include_router(bot_profiles_router)
    main_router.include_router(personas_router)
    app.include_router(main_router)
    return app


app = create_test_app()


@pytest.fixture(name="test_engine")
def test_engine_fixture():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture(name="test_session")
def test_session_fixture(test_engine) -> Generator[Session, None, None]:
    connection = test_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)
    _seed(session)
    yield session
    session.close()
    transaction.rollback()
    connection.close()


def _seed(session: Session) -> None:
    """建最小可用的公共记忆空间与兜底 BotProfile。

    必须先 flush 记忆空间再插 BotProfile：SQLite 开着外键约束时，
    同一批 add 的插入顺序不保证满足 FK 依赖。
    """

    session.add(MemorySpace(id=PUBLIC_SPACE_ID, name="公共记忆库", space_type="public"))
    session.add(MemorySpace(id=KAMI_SPACE_ID, name="Kami 管理记忆库", space_type="kami"))
    session.flush()
    session.add(
        BotProfile(
            id=PUBLIC_PROFILE_ID,
            name="公共 Bot",
            profile_type="public",
            home_memory_space_id=PUBLIC_SPACE_ID,
            is_system=True,
        )
    )
    session.commit()


@pytest.fixture(name="client")
def client_fixture(test_session: Session, monkeypatch) -> Generator[TestClient, None, None]:
    @contextmanager
    def get_test_db_session():
        yield test_session
        test_session.commit()

    # 两个服务模块各自持有 `get_db_session` 的引用，必须分别替换。
    # 注意用 importlib 取模块对象：`src.workspaces.persona_service` 这个属性
    # 在包命名空间里已被同名的单例实例遮蔽，直接用字符串路径会拿到实例。
    for module_name in ("src.workspaces.persona_service", "src.workspaces.bot_profile_service"):
        monkeypatch.setattr(importlib.import_module(module_name), "get_db_session", get_test_db_session)

    app.dependency_overrides[require_auth] = lambda: "test-token"
    app.dependency_overrides[require_auth_with_rate_limit] = lambda: "test-token"
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def _create_persona(client: TestClient, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "测试人设",
        "nickname": "小测",
        "personality": "温和、话少",
        "reply_style": "简短",
        "alias_names": ["测试", "小测"],
    }
    payload.update(overrides)
    response = client.post("/api/webui/personas", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["data"]


# --------------------------------------------------------------------------
# 人设 CRUD
# --------------------------------------------------------------------------


def test_list_personas_is_empty_initially(client: TestClient):
    response = client.get("/api/webui/personas")
    assert response.status_code == 200
    assert response.json() == {"success": True, "data": []}


def test_create_persona_returns_all_fields(client: TestClient):
    data = _create_persona(client)
    assert data["id"].startswith("persona-profile-")
    assert data["name"] == "测试人设"
    assert data["nickname"] == "小测"
    assert data["personality"] == "温和、话少"
    assert data["reply_style"] == "简短"
    assert data["alias_names"] == ["测试", "小测"]
    # 未提供的字段保持空串，代表「不覆盖」，不能被填充成全局值。
    assert data["group_chat_prompt"] == ""
    assert data["emotion_trait"] == ""
    assert data["usage"] == {"bot_profiles": [], "workspaces": []}


def test_create_persona_rejects_duplicate_name(client: TestClient):
    _create_persona(client)
    response = client.post("/api/webui/personas", json={"name": "测试人设"})
    assert response.status_code == 400
    assert "已存在" in response.json()["detail"]


def test_create_persona_rejects_blank_name(client: TestClient):
    response = client.post("/api/webui/personas", json={"name": ""})
    assert response.status_code == 422


def test_create_persona_accepts_comma_separated_aliases(client: TestClient):
    data = _create_persona(client, alias_names="甲, 乙 ，丙")
    assert data["alias_names"] == ["甲", "乙", "丙"]


def test_get_persona_404_when_missing(client: TestClient):
    assert client.get("/api/webui/personas/nope").status_code == 404


def test_update_persona_patches_only_given_fields(client: TestClient):
    created = _create_persona(client)
    response = client.patch(
        f"/api/webui/personas/{created['id']}",
        json={"personality": "冷静、克制"},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["personality"] == "冷静、克制"
    # 未提交的字段必须原样保留。
    assert data["nickname"] == "小测"
    assert data["alias_names"] == ["测试", "小测"]


def test_update_persona_rejects_duplicate_name(client: TestClient):
    _create_persona(client, name="甲")
    second = _create_persona(client, name="乙")
    response = client.patch(f"/api/webui/personas/{second['id']}", json={"name": "甲"})
    assert response.status_code == 400


def test_update_persona_404_when_missing(client: TestClient):
    assert client.patch("/api/webui/personas/nope", json={"nickname": "x"}).status_code == 404


def test_update_persona_requires_at_least_one_field(client: TestClient):
    created = _create_persona(client)
    assert client.patch(f"/api/webui/personas/{created['id']}", json={}).status_code == 400


def test_delete_persona_succeeds_when_unused(client: TestClient):
    created = _create_persona(client)
    response = client.delete(f"/api/webui/personas/{created['id']}")
    assert response.status_code == 200
    assert response.json()["removed"] is True
    assert client.get("/api/webui/personas").json()["data"] == []


def test_delete_persona_returns_false_when_missing(client: TestClient):
    response = client.delete("/api/webui/personas/nope")
    assert response.status_code == 200
    assert response.json()["removed"] is False


def test_delete_persona_is_blocked_while_referenced_by_bot_profile(client: TestClient):
    created = _create_persona(client)
    client.post(
        "/api/webui/bot-profiles",
        json={
            "name": "群聊 Bot",
            "home_memory_space_id": PUBLIC_SPACE_ID,
            "persona_profile_id": created["id"],
        },
    )
    response = client.delete(f"/api/webui/personas/{created['id']}")
    assert response.status_code == 409
    assert "仍被引用" in response.json()["detail"]


def test_persona_usage_lists_referencing_bot_profile(client: TestClient):
    created = _create_persona(client)
    profile = client.post(
        "/api/webui/bot-profiles",
        json={
            "name": "群聊 Bot",
            "home_memory_space_id": PUBLIC_SPACE_ID,
            "persona_profile_id": created["id"],
        },
    ).json()["data"]
    listed = client.get("/api/webui/personas").json()["data"]
    usage = next(item for item in listed if item["id"] == created["id"])["usage"]
    assert usage["bot_profiles"] == [profile["id"]]


# --------------------------------------------------------------------------
# BotProfile 创建 / 删除
# --------------------------------------------------------------------------


def test_create_bot_profile_defaults_to_public_parent(client: TestClient):
    response = client.post(
        "/api/webui/bot-profiles",
        json={"name": "群聊 Bot", "home_memory_space_id": PUBLIC_SPACE_ID},
    )
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["profile_type"] == "group"
    assert data["parent_profile_id"] == PUBLIC_PROFILE_ID
    assert data["is_system"] is False
    assert data["persona_profile_id"] is None


def test_create_bot_profile_rejects_non_group_type(client: TestClient):
    response = client.post(
        "/api/webui/bot-profiles",
        json={"name": "冒牌 Kami", "profile_type": "kami", "home_memory_space_id": PUBLIC_SPACE_ID},
    )
    assert response.status_code == 422


def test_create_bot_profile_rejects_unknown_memory_space(client: TestClient):
    response = client.post(
        "/api/webui/bot-profiles",
        json={"name": "群聊 Bot", "home_memory_space_id": "memory-space-nope"},
    )
    assert response.status_code == 400
    assert "主记忆空间不存在" in response.json()["detail"]


def test_create_bot_profile_rejects_unknown_persona(client: TestClient):
    response = client.post(
        "/api/webui/bot-profiles",
        json={
            "name": "群聊 Bot",
            "home_memory_space_id": PUBLIC_SPACE_ID,
            "persona_profile_id": "persona-profile-nope",
        },
    )
    assert response.status_code == 400
    assert "人设不存在" in response.json()["detail"]


def test_create_bot_profile_rejects_duplicate_name(client: TestClient):
    payload = {"name": "群聊 Bot", "home_memory_space_id": PUBLIC_SPACE_ID}
    assert client.post("/api/webui/bot-profiles", json=payload).status_code == 201
    assert client.post("/api/webui/bot-profiles", json=payload).status_code == 400


def test_delete_bot_profile_rejects_system_profile(client: TestClient):
    response = client.delete(f"/api/webui/bot-profiles/{PUBLIC_PROFILE_ID}")
    assert response.status_code == 409
    assert "系统内置" in response.json()["detail"]


def test_delete_bot_profile_rejects_profile_with_children(client: TestClient):
    parent = client.post(
        "/api/webui/bot-profiles",
        json={"name": "父 Bot", "home_memory_space_id": PUBLIC_SPACE_ID},
    ).json()["data"]
    client.post(
        "/api/webui/bot-profiles",
        json={
            "name": "子 Bot",
            "home_memory_space_id": PUBLIC_SPACE_ID,
            "parent_profile_id": parent["id"],
        },
    )
    response = client.delete(f"/api/webui/bot-profiles/{parent['id']}")
    assert response.status_code == 409
    assert "子级" in response.json()["detail"]


def test_delete_bot_profile_rejects_profile_used_by_workspace(client: TestClient, test_session: Session):
    profile = client.post(
        "/api/webui/bot-profiles",
        json={"name": "群聊 Bot", "home_memory_space_id": PUBLIC_SPACE_ID},
    ).json()["data"]
    test_session.add(
        Workspace(id="workspace-a", name="测试子系统", memory_space_id=PUBLIC_SPACE_ID, bot_profile_id=profile["id"])
    )
    test_session.commit()
    response = client.delete(f"/api/webui/bot-profiles/{profile['id']}")
    assert response.status_code == 409
    assert "子系统" in response.json()["detail"]


def test_delete_bot_profile_rejects_profile_used_by_route(client: TestClient):
    profile = client.post(
        "/api/webui/bot-profiles",
        json={"name": "群聊 Bot", "home_memory_space_id": PUBLIC_SPACE_ID},
    ).json()["data"]
    client.put(
        "/api/webui/bot-profiles/routes/session-a",
        json={"active_bot_profile_id": profile["id"]},
    )
    response = client.delete(f"/api/webui/bot-profiles/{profile['id']}")
    assert response.status_code == 409
    assert "会话" in response.json()["detail"]


def test_delete_bot_profile_cascades_policies(client: TestClient, test_session: Session):
    profile = client.post(
        "/api/webui/bot-profiles",
        json={"name": "群聊 Bot", "home_memory_space_id": PUBLIC_SPACE_ID},
    ).json()["data"]
    client.put(
        f"/api/webui/bot-profiles/{profile['id']}/tools/plugin-a.tool_b",
        json={"effect": "deny"},
    )
    assert test_session.get(BotProfileToolPolicy, 1) is not None

    response = client.delete(f"/api/webui/bot-profiles/{profile['id']}")
    assert response.status_code == 200
    assert response.json()["removed"] is True
    assert test_session.get(BotProfile, profile["id"]) is None
    assert test_session.get(BotProfileToolPolicy, 1) is None


def test_delete_bot_profile_returns_false_when_missing(client: TestClient):
    response = client.delete("/api/webui/bot-profiles/bot-profile-nope")
    assert response.status_code == 200
    assert response.json()["removed"] is False


def test_delete_bot_profile_rejects_profile_used_by_permission_group_rule(client: TestClient, test_session: Session):
    """记忆权限组里的 Bot 规则是管理端显式配置，不能随 Bot 静默消失。

    这张表带 `bot_profiles` 外键，不拦就会抛裸 FK 错误（500）而不是可读的 409。
    """
    profile = client.post(
        "/api/webui/bot-profiles",
        json={"name": "群聊 Bot", "home_memory_space_id": PUBLIC_SPACE_ID},
    ).json()["data"]
    test_session.add(MemoryPermissionGroup(id="group-a", name="测试权限组"))
    test_session.flush()
    test_session.add(PermissionGroupBotRule(permission_group_id="group-a", bot_profile_id=profile["id"]))
    test_session.commit()

    response = client.delete(f"/api/webui/bot-profiles/{profile['id']}")
    assert response.status_code == 409
    assert "权限组" in response.json()["detail"]


def test_delete_bot_profile_cascades_both_acl_halves(client: TestClient, test_session: Session):
    """跨空间读取是双向握手，出站与入站两张表必须成对清理，不能留下半条授权。"""
    profile = client.post(
        "/api/webui/bot-profiles",
        json={"name": "群聊 Bot", "home_memory_space_id": PUBLIC_SPACE_ID},
    ).json()["data"]
    test_session.add(BotProfileMemoryRule(bot_profile_id=profile["id"], target_space_id=KAMI_SPACE_ID, can_read=True))
    test_session.add(MemorySpaceBotRule(memory_space_id=KAMI_SPACE_ID, bot_profile_id=profile["id"], can_read=True))
    test_session.commit()

    response = client.delete(f"/api/webui/bot-profiles/{profile['id']}")
    assert response.status_code == 200
    assert response.json()["removed"] is True
    remaining_outbound = test_session.exec(
        select(BotProfileMemoryRule).where(BotProfileMemoryRule.bot_profile_id == profile["id"])
    ).all()
    remaining_inbound = test_session.exec(
        select(MemorySpaceBotRule).where(MemorySpaceBotRule.bot_profile_id == profile["id"])
    ).all()
    assert remaining_outbound == []
    assert remaining_inbound == []


# --------------------------------------------------------------------------
# 会话级 Bot 路由
# --------------------------------------------------------------------------


def test_list_routes_is_empty_initially(client: TestClient):
    response = client.get("/api/webui/bot-profiles/routes")
    assert response.status_code == 200
    assert response.json() == {"success": True, "data": []}


def test_set_route_creates_and_lists_state(client: TestClient):
    profile = client.post(
        "/api/webui/bot-profiles",
        json={"name": "群聊 Bot", "home_memory_space_id": PUBLIC_SPACE_ID},
    ).json()["data"]

    response = client.put(
        "/api/webui/bot-profiles/routes/session-a",
        json={"active_bot_profile_id": profile["id"], "route_mode": "specific"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["active_bot_profile_name"] == "群聊 Bot"

    listed = client.get("/api/webui/bot-profiles/routes").json()["data"]
    assert [item["session_id"] for item in listed] == ["session-a"]


def test_set_route_upserts_existing_state(client: TestClient):
    first = client.post(
        "/api/webui/bot-profiles",
        json={"name": "甲 Bot", "home_memory_space_id": PUBLIC_SPACE_ID},
    ).json()["data"]
    second = client.post(
        "/api/webui/bot-profiles",
        json={"name": "乙 Bot", "home_memory_space_id": PUBLIC_SPACE_ID},
    ).json()["data"]

    client.put("/api/webui/bot-profiles/routes/session-a", json={"active_bot_profile_id": first["id"]})
    client.put("/api/webui/bot-profiles/routes/session-a", json={"active_bot_profile_id": second["id"]})

    listed = client.get("/api/webui/bot-profiles/routes").json()["data"]
    assert len(listed) == 1
    assert listed[0]["active_bot_profile_id"] == second["id"]
    assert listed[0]["policy_revision"] == 2


def test_set_route_rejects_kami_profile(client: TestClient, test_session: Session):
    test_session.add(
        BotProfile(
            id="bot-profile-kami",
            name="Kami 管理 Bot",
            profile_type="kami",
            home_memory_space_id=KAMI_SPACE_ID,
            is_system=True,
        )
    )
    test_session.commit()
    response = client.put(
        "/api/webui/bot-profiles/routes/session-a",
        json={"active_bot_profile_id": "bot-profile-kami"},
    )
    assert response.status_code == 400
    assert "Kami" in response.json()["detail"]


def test_reset_route_removes_state(client: TestClient, test_session: Session):
    profile = client.post(
        "/api/webui/bot-profiles",
        json={"name": "群聊 Bot", "home_memory_space_id": PUBLIC_SPACE_ID},
    ).json()["data"]
    client.put("/api/webui/bot-profiles/routes/session-a", json={"active_bot_profile_id": profile["id"]})

    response = client.delete("/api/webui/bot-profiles/routes/session-a")
    assert response.status_code == 200
    assert response.json()["removed"] is True
    assert client.get("/api/webui/bot-profiles/routes").json()["data"] == []
    assert test_session.get(BotRouteState, "session-a") is None


def test_reset_route_returns_false_when_missing(client: TestClient):
    response = client.delete("/api/webui/bot-profiles/routes/session-nope")
    assert response.status_code == 200
    assert response.json()["removed"] is False


def test_routes_path_is_not_shadowed_by_profile_id(client: TestClient, test_session: Session):
    """/routes 必须优先于 /{profile_id} 匹配，否则会被当成人设 ID 处理。"""

    assert test_session.get(BotRouteState, "session-a") is None
    assert client.get("/api/webui/bot-profiles/routes").status_code == 200
    assert client.get("/api/webui/bot-profiles/routes").json()["data"] == []


def test_route_state_persists_into_session_lookup(client: TestClient, test_session: Session):
    """写入的路由必须能被服务层读回，否则界面改了不生效。"""

    profile = client.post(
        "/api/webui/bot-profiles",
        json={"name": "群聊 Bot", "home_memory_space_id": PUBLIC_SPACE_ID},
    ).json()["data"]
    client.put("/api/webui/bot-profiles/routes/session-a", json={"active_bot_profile_id": profile["id"]})

    from src.workspaces.bot_profile_service import bot_profile_service

    state = bot_profile_service.get_route_state("session-a")
    assert state is not None
    assert state.active_bot_profile_id == profile["id"]


def test_persona_overlay_is_not_materialized_on_create(client: TestClient):
    """新建人设不能把空字段写成全局值，否则会破坏「未覆盖」语义。"""

    created = _create_persona(client, personality="")
    assert created["personality"] == ""


def test_update_bot_profile_rejects_unknown_persona(client: TestClient):
    profile = client.post(
        "/api/webui/bot-profiles",
        json={"name": "群聊 Bot", "home_memory_space_id": PUBLIC_SPACE_ID},
    ).json()["data"]
    response = client.patch(
        f"/api/webui/bot-profiles/{profile['id']}",
        json={"persona_profile_id": "persona-profile-nope"},
    )
    assert response.status_code == 400
    assert "人设不存在" in response.json()["detail"]


def test_binding_persona_to_bot_profile_round_trips(client: TestClient):
    """端到端：建人设 → 绑到 Bot → 列表里能读到绑定关系。"""

    persona = _create_persona(client, name="高冷人设", personality="高冷")
    profile = client.post(
        "/api/webui/bot-profiles",
        json={"name": "群聊 Bot", "home_memory_space_id": PUBLIC_SPACE_ID},
    ).json()["data"]

    response = client.patch(
        f"/api/webui/bot-profiles/{profile['id']}",
        json={"persona_profile_id": persona["id"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["persona_profile_id"] == persona["id"]

    listed = client.get("/api/webui/bot-profiles").json()["data"]
    target = next(item for item in listed if item["id"] == profile["id"])
    assert target["persona_profile_id"] == persona["id"]


def test_persona_update_persists_across_requests(client: TestClient):
    created = _create_persona(client)
    client.patch(f"/api/webui/personas/{created['id']}", json={"group_chat_prompt": "群聊要简短"})
    fetched = client.get(f"/api/webui/personas/{created['id']}").json()["data"]
    assert fetched["group_chat_prompt"] == "群聊要简短"


def test_seeded_kami_persona_is_editable(client: TestClient, test_session: Session):
    """迁移预置的 Kami 人设也应该能在界面上编辑，而不是只能读。"""

    test_session.add(PersonaProfile(id="persona-profile-kami", name="Kami 管理人设", personality="审慎"))
    test_session.commit()
    response = client.patch(
        "/api/webui/personas/persona-profile-kami",
        json={"nickname": "Kami"},
    )
    assert response.status_code == 200
    assert response.json()["data"]["nickname"] == "Kami"


def test_session_route_visible_in_bot_profile_lineage_endpoint(client: TestClient):
    """确认新增端点没有破坏既有的 /{profile_id} 系列。"""

    assert client.get(f"/api/webui/bot-profiles/{PUBLIC_PROFILE_ID}").status_code == 200


def test_persona_route_does_not_break_openapi(client: TestClient):
    schema = client.get("/openapi.json").json()
    assert "/api/webui/personas" in schema["paths"]
    assert "/api/webui/bot-profiles/routes/{session_id}" in schema["paths"]


def test_session_route_mode_defaults_to_specific(client: TestClient):
    profile = client.post(
        "/api/webui/bot-profiles",
        json={"name": "群聊 Bot", "home_memory_space_id": PUBLIC_SPACE_ID},
    ).json()["data"]
    response = client.put(
        "/api/webui/bot-profiles/routes/session-a",
        json={"active_bot_profile_id": profile["id"]},
    )
    assert response.json()["route_mode"] == "specific"


def test_set_route_rejects_unknown_profile(client: TestClient):
    response = client.put(
        "/api/webui/bot-profiles/routes/session-a",
        json={"active_bot_profile_id": "bot-profile-nope"},
    )
    assert response.status_code == 400
