"""Tests for the session detail/transcript page."""

from __future__ import annotations

from nicegui.testing import User

from hermes_nicegui import web
from hermes_nicegui.plugin import PluginContext
from hermes_nicegui.plugins.sessions import SessionsPlugin


async def test_detail_page_loads_directly(user: User, context: PluginContext) -> None:
    web.build(context, [SessionsPlugin(context)])
    await user.open("/sessions/sess-1")
    await user.should_see("How do I access your API?")


async def test_detail_page_shows_all_messages(user: User, context: PluginContext) -> None:
    web.build(context, [SessionsPlugin(context)])
    await user.open("/sessions/sess-1")
    await user.should_see("How do I access your API?")
    await user.should_see("Here is the answer.")


async def test_detail_page_has_actions(user: User, context: PluginContext) -> None:
    web.build(context, [SessionsPlugin(context)])
    await user.open("/sessions/sess-1")
    await user.should_see("Rename")
    await user.should_see("Fork")
    await user.should_see("Delete")


async def test_send_message_streams_reply(user: User, context: PluginContext) -> None:
    web.build(context, [SessionsPlugin(context)])
    await user.open("/sessions/sess-1")
    await user.should_see("How do I access your API?")

    user.find(marker="chat-input").type("What's the weather?")
    user.find(marker="chat-send").click()

    await user.should_see("What's the weather?")
    await user.should_see("Hello world")


async def test_send_message_refreshes_message_count(user: User, context: PluginContext) -> None:
    web.build(context, [SessionsPlugin(context)])
    await user.open("/sessions/sess-1")
    await user.should_see("3 messages")

    user.find(marker="chat-input").type("another turn")
    user.find(marker="chat-send").click()

    await user.should_see("5 messages")
