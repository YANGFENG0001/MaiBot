from contextlib import contextmanager

import asyncio
import importlib

import pytest
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, SQLModel, create_engine

from src.common.database.database_model import BotProfile, BotProfilePluginPolicy, MemorySpace
from src.plugin_runtime.config_overlay import (
    PluginConfigOverlayError,
    apply_plugin_config_overrides,
    validate_and_collect_override_paths,
)
from src.plugin_runtime.request_scope import PluginRequestScope
from src.plugin_runtime.runner.runner_main import PluginRunner
from src.plugin_runtime.scope_resolver import PluginScopeResolver


SCHEMA = {
    "type": "object",
    "properties": {
        "plugin": {
            "type": "object",
            "properties": {
                "config_version": {"type": "string", "x-bot-profile-overridable": True, "x-scope": "request"}
            },
            "required": ["config_version"],
        },
        "style": {
            "type": "object",
            "properties": {
                "tone": {"type": "string", "x-bot-profile-overridable": True, "x-scope": "request"},
                "length": {"type": "integer", "x-workspace-overridable": True},
            },
            "required": ["tone", "length"],
        },
        "network": {
            "type": "object",
            "properties": {
                "port": {"type": "integer", "x-bot-profile-overridable": True, "x-scope": "request"},
                "token": {"type": "string", "x-bot-profile-overridable": True, "x-scope": "request"},
            },
            "required": ["port", "token"],
        },
        "process_mode": {"type": "string", "x-bot-profile-overridable": True, "x-scope": "process"},
    },
    "required": ["plugin", "style", "network", "process_mode"],
}
BASE = {
    "plugin": {"config_version": "1.0.0"},
    "style": {"tone": "neutral", "length": 2},
    "network": {"port": 7999, "token": "base-secret"},
    "process_mode": "shared",
}


def _scope(profile_id: str, revision: int = 1) -> PluginRequestScope:
    return PluginRequestScope(
        trace_id=f"trace-{profile_id}",
        session_id="session",
        workspace_id="workspace",
        bot_profile_id=profile_id,
        bot_profile_type="group" if profile_id != "kami" else "kami",
        permission_group_id="",
        access_mode="normal",
        security_domain="normal",
        memory_space_id="space",
        audience_type="private",
        policy_revision=revision,
    )


def _resolver_db(monkeypatch):
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

    module = importlib.import_module("src.plugin_runtime.scope_resolver")
    monkeypatch.setattr(module, "get_db_session", db)
    with db() as session:
        session.add(MemorySpace(id="space", name="space"))
        session.flush()
        session.add(
            BotProfile(
                id="public",
                name="Public",
                profile_type="public",
                home_memory_space_id="space",
                inherit_parent_plugins=True,
            )
        )
        session.flush()
        session.add(
            BotProfile(
                id="a",
                name="A",
                profile_type="group",
                parent_profile_id="public",
                home_memory_space_id="space",
            )
        )
        session.add(
            BotProfile(
                id="b",
                name="B",
                profile_type="group",
                parent_profile_id="public",
                home_memory_space_id="space",
            )
        )
        session.add(
            BotProfile(
                id="kami",
                name="Kami",
                profile_type="kami",
                home_memory_space_id="space",
                inherit_parent_plugins=False,
            )
        )
    return db


def test_cf01_cf11_request_and_legacy_overrides_are_applied_on_copy() -> None:
    effective, paths = apply_plugin_config_overrides(
        BASE,
        SCHEMA,
        {"style": {"tone": "warm", "length": 4}},
    )
    assert effective["style"] == {"tone": "warm", "length": 4}
    assert set(paths) == {"style.tone", "style.length"}
    assert BASE["style"] == {"tone": "neutral", "length": 2}


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"process_mode": "isolated"}, "not_overridable"),
        ({"network": {"token": "leak"}}, "hard_denied"),
        ({"network": {"port": 8000}}, "hard_denied"),
        ({"unknown": True}, "unknown_path"),
        ({"style": {"length": "long"}}, "schema_validation_failed"),
        ({"plugin": {"config_version": "2.0.0"}}, "hard_denied"),
        ({"style": {"tone": None}}, "null_not_allowed"),
    ],
)
def test_cf04_cf05_cf06_cf07_cf08_invalid_overrides_fail_closed(overrides, code) -> None:
    with pytest.raises(PluginConfigOverlayError) as exc_info:
        validate_and_collect_override_paths(SCHEMA, overrides)
    assert exc_info.value.error_code == code
    assert "leak" not in str(exc_info.value)


def test_cf02_cf03_cf09_cf10_profile_overrides_are_isolated(monkeypatch) -> None:
    db = _resolver_db(monkeypatch)
    with db() as session:
        session.add(
            BotProfilePluginPolicy(
                bot_profile_id="public",
                plugin_id="demo",
                effect="allow",
                overrides_json='{"style":{"length":3}}',
            )
        )
        session.add(
            BotProfilePluginPolicy(
                bot_profile_id="a",
                plugin_id="demo",
                effect="inherit",
                overrides_json='{"style":{"tone":"warm"}}',
            )
        )
        session.add(BotProfilePluginPolicy(bot_profile_id="kami", plugin_id="demo", effect="allow"))
    resolver = PluginScopeResolver()
    a = resolver.resolve_effective_plugin_config("demo", _scope("a"), BASE, SCHEMA)
    b = resolver.resolve_effective_plugin_config("demo", _scope("b"), BASE, SCHEMA)
    kami = resolver.resolve_effective_plugin_config("demo", _scope("kami"), BASE, SCHEMA)
    assert a["style"] == {"tone": "warm", "length": 3}
    assert b["style"] == {"tone": "neutral", "length": 3}
    assert kami["style"] == BASE["style"]


def test_cf12_cf13_concurrent_effective_configs_do_not_share_mutable_state(monkeypatch) -> None:
    db = _resolver_db(monkeypatch)
    with db() as session:
        session.add(
            BotProfilePluginPolicy(
                bot_profile_id="a",
                plugin_id="demo",
                effect="allow",
                overrides_json='{"style":{"tone":"warm"}}',
            )
        )
        session.add(
            BotProfilePluginPolicy(
                bot_profile_id="b",
                plugin_id="demo",
                effect="allow",
                overrides_json='{"style":{"tone":"cold"}}',
            )
        )
    resolver = PluginScopeResolver()

    async def resolve(profile_id: str):
        await asyncio.sleep(0)
        return resolver.resolve_effective_plugin_config("demo", _scope(profile_id), BASE, SCHEMA)

    async def run():
        return await asyncio.gather(*(resolve("a" if index % 2 == 0 else "b") for index in range(100)))

    results = asyncio.run(run())
    for index, result in enumerate(results):
        assert result["style"]["tone"] == ("warm" if index % 2 == 0 else "cold")
    results[0]["style"]["tone"] = "mutated"
    assert results[2]["style"]["tone"] == "warm"
    assert BASE["style"]["tone"] == "neutral"


def test_cf14_old_handler_signature_is_not_given_new_kwargs() -> None:
    def old_handler(value):
        return value

    invoke = type(
        "Invoke",
        (),
        {"args": {"value": 1}},
    )()
    context = type(
        "Context",
        (),
        {"effective_plugin_config": {"style": {}}, "request_scope": _scope("a")},
    )()
    kwargs = PluginRunner._build_handler_kwargs(old_handler, invoke, context)
    assert kwargs == {"value": 1}
