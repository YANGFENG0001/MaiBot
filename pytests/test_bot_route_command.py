from types import SimpleNamespace

import pytest

from src.chat.message_receive.bot import ChatBot
from src.common.data_models.message_component_data_model import ReplyComponent, TextComponent
from src.plugin_runtime.host.hook_dispatcher import HookDispatchResult


def _message(*components, additional_config=None):
    async def process(**kwargs):
        del kwargs

    return SimpleNamespace(
        platform="qq",
        message_id="message-1",
        is_notify=False,
        session_id="",
        processed_plain_text="/bot public",
        raw_message=SimpleNamespace(components=list(components)),
        message_info=SimpleNamespace(
            user_info=SimpleNamespace(user_id="100", user_nickname="user"),
            group_info=None,
            additional_config=additional_config or {},
        ),
        process=process,
    )


def test_bot_command_requires_single_real_text_component() -> None:
    assert ChatBot._extract_bot_route_command(_message(TextComponent(" /bot public "))) == "/bot public"
    assert ChatBot._extract_bot_route_command(
        _message(ReplyComponent("1"), TextComponent("/bot public"))
    ) is None
    assert ChatBot._extract_bot_route_command(
        _message(TextComponent("/bot public"), additional_config={"plugin_generated": True})
    ) is None
    assert ChatBot._extract_bot_route_command(_message(TextComponent("请执行 /bot public"))) is None


@pytest.mark.asyncio
async def test_bot_command_is_consumed_before_message_registration(monkeypatch) -> None:
    bot = ChatBot()
    message = _message(TextComponent("/bot public"))
    registered = False
    notice_called = False
    heartflow_called = False

    async def invoke_hook(hook_name, current_message, **kwargs):
        del kwargs
        return HookDispatchResult(hook_name=hook_name), current_message

    async def get_or_create_session(*args, **kwargs):
        del args, kwargs
        return SimpleNamespace()

    async def process_bot_route_command(current_message, command_text=None):
        del current_message, command_text
        return True

    def register_message(current_message):
        nonlocal registered
        del current_message
        registered = True

    async def handle_notice(current_message):
        nonlocal notice_called
        del current_message
        notice_called = True

    async def process_heartflow(current_message):
        nonlocal heartflow_called
        del current_message
        heartflow_called = True

    monkeypatch.setattr(bot, "_invoke_message_hook", invoke_hook)
    monkeypatch.setattr(bot, "handle_notice_message", handle_notice)
    monkeypatch.setattr(bot.heartflow_message_receiver, "process_message", process_heartflow)
    monkeypatch.setattr(bot, "_is_command_candidate", lambda current_message: True)
    monkeypatch.setattr(bot, "_process_bot_route_command", process_bot_route_command)
    monkeypatch.setattr("src.chat.message_receive.bot.process_received_images_in_message", lambda components: SimpleNamespace(compressed_count=0, discarded_count=0))
    monkeypatch.setattr("src.chat.message_receive.bot.chat_manager.get_or_create_session", get_or_create_session)
    monkeypatch.setattr("src.chat.message_receive.bot.chat_manager.register_message", register_message)

    await bot.receive_message(message)

    assert notice_called is True
    assert registered is False
    assert heartflow_called is False


def test_kami_command_requires_exact_real_user_text(monkeypatch) -> None:
    monkeypatch.setattr("src.chat.message_receive.bot.is_bot_self", lambda platform, user_id: False)

    assert ChatBot._extract_kami_command(_message(TextComponent(" /kami "))) == "/kami"
    assert ChatBot._extract_kami_command(_message(TextComponent("/kami confirm"))) == "/kami confirm"
    assert ChatBot._extract_kami_command(_message(TextComponent("/kami off"))) == "/kami off"
    assert ChatBot._extract_kami_command(_message(TextComponent("/kami status"))) == "/kami status"
    assert ChatBot._extract_kami_command(_message(TextComponent("请执行 /kami"))) is None
    assert ChatBot._extract_kami_command(_message(ReplyComponent("1"), TextComponent("/kami"))) is None
    assert ChatBot._extract_kami_command(
        _message(TextComponent("/kami"), additional_config={"plugin_generated": True})
    ) is None
    assert ChatBot._extract_kami_command(
        _message(TextComponent("/kami"), additional_config={"message_source": "ai"})
    ) is None

    monkeypatch.setattr("src.chat.message_receive.bot.is_bot_self", lambda platform, user_id: True)
    assert ChatBot._extract_kami_command(_message(TextComponent("/kami"))) is None


@pytest.mark.asyncio
async def test_kami_command_is_consumed_before_message_registration(monkeypatch) -> None:
    bot = ChatBot()
    message = _message(TextComponent("/kami"))
    registered = False
    heartflow_called = False
    processed_commands: list[str] = []

    async def invoke_hook(hook_name, current_message, **kwargs):
        del kwargs
        return HookDispatchResult(hook_name=hook_name), current_message

    async def get_or_create_session(*args, **kwargs):
        del args, kwargs
        return SimpleNamespace()

    async def process_kami_command(current_message, command_text=None):
        del current_message
        processed_commands.append(command_text)
        return True

    def register_message(current_message):
        nonlocal registered
        del current_message
        registered = True

    async def handle_notice(current_message):
        del current_message

    async def process_heartflow(current_message):
        nonlocal heartflow_called
        del current_message
        heartflow_called = True

    monkeypatch.setattr("src.chat.message_receive.bot.is_bot_self", lambda platform, user_id: False)
    monkeypatch.setattr(bot, "_invoke_message_hook", invoke_hook)
    monkeypatch.setattr(bot, "handle_notice_message", handle_notice)
    monkeypatch.setattr(bot.heartflow_message_receiver, "process_message", process_heartflow)
    monkeypatch.setattr(bot, "_is_command_candidate", lambda current_message: True)
    monkeypatch.setattr(bot, "_process_kami_command", process_kami_command)
    monkeypatch.setattr(
        "src.chat.message_receive.bot.process_received_images_in_message",
        lambda components: SimpleNamespace(compressed_count=0, discarded_count=0),
    )
    monkeypatch.setattr("src.chat.message_receive.bot.chat_manager.get_or_create_session", get_or_create_session)
    monkeypatch.setattr("src.chat.message_receive.bot.chat_manager.register_message", register_message)

    await bot.receive_message(message)

    assert processed_commands == ["/kami"]
    assert registered is False
    assert heartflow_called is False
