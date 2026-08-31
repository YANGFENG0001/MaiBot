import asyncio

import pytest

from src.workspaces.request_context import (
    BotRequestContext,
    bind_request_context,
    create_background_task_without_request_context,
    get_current_request_context,
)


def _context(trace_id: str, profile_id: str) -> BotRequestContext:
    return BotRequestContext(
        trace_id=trace_id,
        session_id=f"session-{trace_id}",
        workspace_id=f"workspace-{trace_id}",
        person_id=f"person-{trace_id}",
        active_bot_profile_id=profile_id,
        active_bot_profile_type="group",
        permission_group_id="",
        access_mode="normal",
        security_domain="normal",
        home_memory_space_id=f"space-{trace_id}",
        readable_space_ids=(f"space-{trace_id}",),
        readable_partition_ids=(),
        writable_partition_ids=(),
        audience_type="private",
        policy_revision=1,
    )


@pytest.mark.asyncio
async def test_concurrent_request_contexts_do_not_leak() -> None:
    ready = asyncio.Event()
    entered = 0
    lock = asyncio.Lock()

    async def worker(context: BotRequestContext) -> tuple[str, str]:
        nonlocal entered
        with bind_request_context(context):
            async with lock:
                entered += 1
                if entered == 2:
                    ready.set()
            await ready.wait()
            await asyncio.sleep(0)
            current = get_current_request_context(required=True)
            assert current is not None
            return current.trace_id, current.active_bot_profile_id

    first, second = await asyncio.gather(
        worker(_context("a", "profile-a")),
        worker(_context("b", "profile-b")),
    )
    assert first == ("a", "profile-a")
    assert second == ("b", "profile-b")
    assert get_current_request_context() is None


def test_request_context_resets_after_exception() -> None:
    context = _context("error", "profile-error")
    with pytest.raises(RuntimeError, match="boom"):
        with bind_request_context(context):
            assert get_current_request_context(required=True) == context
            raise RuntimeError("boom")
    assert get_current_request_context() is None


@pytest.mark.asyncio
async def test_request_context_resets_after_cancellation() -> None:
    started = asyncio.Event()
    blocker = asyncio.Event()
    context = _context("cancel", "profile-cancel")

    async def cancellable() -> None:
        with bind_request_context(context):
            started.set()
            await blocker.wait()

    task = asyncio.create_task(cancellable())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert get_current_request_context() is None


@pytest.mark.asyncio
async def test_background_task_starts_without_request_context() -> None:
    context = _context("parent", "profile-parent")

    async def read_context() -> BotRequestContext | None:
        await asyncio.sleep(0)
        return get_current_request_context()

    with bind_request_context(context):
        task = create_background_task_without_request_context(read_context())
        assert await task is None
        assert get_current_request_context(required=True) == context
    assert get_current_request_context() is None
