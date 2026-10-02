import asyncio
from unittest.mock import AsyncMock

import pytest

from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.outgoing_gate import (
    configure_outgoing_gate,
    guarded_onebot_action,
    outgoing_messages_enabled,
)
from astrbot.core.platform.platform import Platform


@pytest.fixture(autouse=True)
def reset_gate():
    configure_outgoing_gate({})
    yield
    configure_outgoing_gate({})


def test_switch_is_strict_and_live():
    config = {"platform_settings": {}}
    configure_outgoing_gate(config)
    for value in [None, False, "true", 1]:
        config["platform_settings"]["outgoing_messages_enabled"] = value
        assert not outgoing_messages_enabled()
    config["platform_settings"] = {"outgoing_messages_enabled": True}
    assert outgoing_messages_enabled()
    config["platform_settings"]["outgoing_messages_enabled"] = False
    assert not outgoing_messages_enabled()


def test_event_and_background_sends_do_not_consume_or_send():
    calls = []

    class Event(AstrMessageEvent):
        async def send(self, message):
            calls.append(message)

        async def send_streaming(self, generator, use_fallback=False):
            async for chunk in generator:
                calls.append(chunk)

        @staticmethod
        async def send_with_client(client, message):
            await client(message)

    class Adapter(Platform):
        async def send_by_session(self, session, message_chain):
            calls.append(message_chain)

        def run(self):
            raise NotImplementedError

        def meta(self):
            raise NotImplementedError

    async def exercise():
        consumed = []

        async def stream():
            consumed.append(True)
            yield "stream"

        event = object.__new__(Event)
        adapter = Adapter({}, asyncio.Queue())
        client = AsyncMock()
        await event.send("private")
        await event.send_streaming(stream())
        await Event.send_with_client(client, "plugin")
        await adapter.send_by_session(None, "background")
        assert not calls and not consumed
        client.assert_not_awaited()
        configure_outgoing_gate(
            {"platform_settings": {"outgoing_messages_enabled": True}}
        )
        await event.send("private")
        await event.send_streaming(stream())
        await adapter.send_by_session(None, "background")
        await Event.send_with_client(client, "plugin")
        assert calls == ["private", "stream", "background"]
        client.assert_awaited_once_with("plugin")

    asyncio.run(exercise())


def test_raw_onebot_sends_are_blocked_but_reads_remain_available():
    async def exercise():
        transport = AsyncMock(return_value={"ok": True})
        for action in (
            "send_msg",
            "send_private_msg",
            "send_group_msg_async",
            "send_group_forward_msg_rate_limited",
            "upload_private_file",
            "upload_group_file",
            "set_group_notice",
        ):
            with pytest.raises(PermissionError):
                await guarded_onebot_action(transport, action, message="test")
        transport.assert_not_awaited()
        assert await guarded_onebot_action(transport, "get_group_info") == {"ok": True}
        configure_outgoing_gate(
            {"platform_settings": {"outgoing_messages_enabled": True}}
        )
        await guarded_onebot_action(transport, "send_private_msg", message="test")
        assert transport.await_count == 2

    asyncio.run(exercise())


def test_real_onebot_adapter_raw_client_cannot_bypass_gate():
    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_platform_adapter import (
        AiocqhttpAdapter,
    )

    async def exercise():
        adapter = AiocqhttpAdapter(
            {"id": "offline", "ws_reverse_host": "127.0.0.1", "ws_reverse_port": 0},
            {},
            asyncio.Queue(),
        )
        # No adapter.run() or transport connection is made in this test.
        with pytest.raises(PermissionError):
            await adapter.bot.call_action("send_private_msg", user_id=1, message="test")
        with pytest.raises(PermissionError):
            await adapter.bot.send_group_forward_msg(group_id=1, messages=[])
        with pytest.raises(PermissionError):
            await adapter.bot._api.call_action("send_msg", message="test")

    asyncio.run(exercise())
