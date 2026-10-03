"""A01 / A02 / W05：审计只保存不可逆哈希，默认遮蔽敏感字段。"""

from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256

import pytest
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, SQLModel, create_engine, select

from src.common.database.database_model import BotControlAudit, MemoryAccessAudit
from src.webui.routers import memory_audit
from src.workspaces import kami_service as kami_service_module
from src.workspaces.kami_service import KamiService


def _database(monkeypatch, *modules):
    """把指定模块的会话工厂替换为隔离的内存数据库。"""

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)

    @contextmanager
    def db(auto_commit=True):
        session = factory()
        try:
            yield session
            if auto_commit:
                session.commit()
        finally:
            session.close()

    for module in modules:
        monkeypatch.setattr(module, "get_db_session", db)
    return db


@pytest.fixture
def audit_db(monkeypatch):
    return _database(monkeypatch, memory_audit, kami_service_module)


def test_a01_query_hash_is_irreversible_and_body_is_never_stored(audit_db) -> None:
    body = "这是一条绝不能进库的私密记忆正文"
    KamiService.record_memory_access_audit(
        trace_id="trace-1",
        session_id="session-1",
        person_id="person-1",
        query=body,
        requested_scope={"spaces": ["space-a"]},
        allowed_scope={"spaces": ["space-a"]},
        denied_scope={"spaces": ["space-b"]},
        result_count=3,
    )
    with audit_db() as session:
        rows = session.exec(select(MemoryAccessAudit)).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.query_hash == sha256(body.encode("utf-8")).hexdigest()
    assert body not in row.query_hash
    assert body not in row.requested_scope_json
    assert "space-b" in row.denied_scope_json


def _list_access(*, reveal: bool = False, session_id: str = "", **filters):
    """直接调用端点函数时需要显式补齐 FastAPI Query 默认值。"""

    return memory_audit.list_memory_access_audit(
        session_id=session_id,
        person_id=filters.get("person_id", ""),
        access_mode=filters.get("access_mode", ""),
        trace_id=filters.get("trace_id", ""),
        reveal=reveal,
        limit=100,
        offset=0,
    )


def _list_control(*, reveal: bool = False, **filters):
    return memory_audit.list_bot_control_audit(
        session_id=filters.get("session_id", ""),
        person_id=filters.get("person_id", ""),
        command=filters.get("command", ""),
        result=filters.get("result", ""),
        reveal=reveal,
        limit=100,
        offset=0,
    )


@pytest.mark.asyncio
async def test_w05_memory_access_audit_masks_by_default(audit_db) -> None:
    KamiService.record_memory_access_audit(
        trace_id="trace-1",
        session_id="session-1",
        person_id="person-1",
        query="secret query",
        requested_scope={"spaces": ["space-a"]},
        allowed_scope={"spaces": ["space-a"]},
    )
    masked = await _list_access()
    assert masked.data[0].redacted is True
    assert masked.data[0].query_hash.endswith("…")
    assert len(masked.data[0].query_hash) == 13
    assert masked.data[0].requested_scope == {}
    assert masked.data[0].allowed_scope == {}

    revealed = await _list_access(reveal=True)
    assert revealed.data[0].redacted is False
    assert revealed.data[0].query_hash == sha256(b"secret query").hexdigest()
    assert revealed.data[0].requested_scope == {"spaces": ["space-a"]}


@pytest.mark.asyncio
async def test_w05_bot_control_audit_masks_metadata_by_default(audit_db) -> None:
    with audit_db() as session:
        session.add(
            BotControlAudit(
                session_id="session-1",
                person_id="person-1",
                platform="webui",
                command="/kami off",
                before_bot_profile_id="bot-profile-kami",
                after_bot_profile_id="bot-profile-public",
                permission_group_id="group-1",
                result="success",
                reason="admin.revoke",
                metadata_json='{"actor": "webui-admin"}',
            )
        )
    masked = await _list_control()
    assert masked.data[0].redacted is True
    assert masked.data[0].metadata == {}
    assert masked.data[0].command == "/kami off"

    revealed = await _list_control(reveal=True)
    assert revealed.data[0].metadata == {"actor": "webui-admin"}


@pytest.mark.asyncio
async def test_audit_filters_are_applied(audit_db) -> None:
    KamiService.record_memory_access_audit(trace_id="trace-a", session_id="session-a", person_id="person-a")
    KamiService.record_memory_access_audit(trace_id="trace-b", session_id="session-b", person_id="person-b")
    filtered = await _list_access(session_id="session-a")
    assert filtered.total == 1
    assert filtered.data[0].session_id == "session-a"


def test_mask_hash_keeps_short_values_intact() -> None:
    assert memory_audit._mask_hash("short", False) == "short"
    assert memory_audit._mask_hash("a" * 64, False) == f"{'a' * 12}…"
    assert memory_audit._mask_hash("a" * 64, True) == "a" * 64
